"""后台目标跟踪：读事件流只认本 Agent 的目标，变化时取完整样子，换了世界如实说不在了。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.agents.minecraft.goals import GoalWatch
from src.agents.minecraft.maicraft import GoalRun, MaicraftReply, goal_run_of, reply_of
from src.modules.tools.models import ToolInvocation

from .minecraft_v1_fakes import FakeMaicraft


class _Recorder:
    """记下跟踪器交出来的东西。"""

    def __init__(self) -> None:
        self.changed: List[GoalRun] = []
        self.gone: List[tuple[int, str]] = []
        self.body: List[Dict[str, Any]] = []

    async def on_changed(self, run: GoalRun) -> None:
        self.changed.append(run)

    async def on_gone(self, goal_id: int, reason: str) -> None:
        self.gone.append((goal_id, reason))

    async def on_body(self, event: Dict[str, Any]) -> None:
        self.body.append(event)


def _watch(server: FakeMaicraft, recorder: _Recorder) -> GoalWatch:
    async def call(name: str, arguments: Dict[str, Any]) -> MaicraftReply:
        return reply_of(await server.invoke(ToolInvocation(tool_name=name, arguments=arguments, source="test")))

    watch = GoalWatch(
        call=call,
        connected=lambda: True,
        on_goal_changed=recorder.on_changed,
        on_goal_gone=recorder.on_gone,
        on_body_event=recorder.on_body,
        wait_ms=1000,
    )

    async def no_sleep() -> None:
        return None

    watch._back_off = no_sleep  # type: ignore[method-assign]  # 失败重试不真睡
    return watch


async def _launch(server: FakeMaicraft, watch: GoalWatch, ability: str = "maicraft:gather") -> int:
    reply = await server._execute({"goal": {"ability": ability}})
    run = goal_run_of(reply["data"])
    assert run is not None
    watch.track(run)
    return run.goal_id


@pytest.mark.asyncio
async def test_first_read_only_sets_the_cursor_and_does_not_replay_old_events() -> None:
    """接手前就有的事件不补发：首次读取只建立游标。"""
    server, recorder = FakeMaicraft(), _Recorder()
    server.body_event("need_unhandled", "饿了，身上没有吃的")
    watch = _watch(server, recorder)

    await watch.step()

    assert recorder.body == [] and recorder.changed == []
    assert watch._cursor == 1


@pytest.mark.asyncio
async def test_finished_goal_is_read_in_full_and_no_longer_tracked() -> None:
    """目标结束：取它此刻的完整样子交出去，之后不再跟踪。"""
    server, recorder = FakeMaicraft(), _Recorder()
    watch = _watch(server, recorder)
    await watch.step()
    goal_id = await _launch(server, watch)
    server.finish(goal_id, summary="砍了 5 块原木")

    await watch.step()

    assert [run.goal_id for run in recorder.changed] == [goal_id]
    finished = recorder.changed[0]
    assert finished.finished and finished.result_status == "done"
    assert finished.result["changes"][0]["count"] == 5
    assert not watch.tracking(goal_id)


@pytest.mark.asyncio
async def test_questions_are_handed_over_and_the_goal_stays_tracked() -> None:
    server, recorder = FakeMaicraft(), _Recorder()
    watch = _watch(server, recorder)
    await watch.step()
    goal_id = await _launch(server, watch)
    server.ask(goal_id)

    await watch.step()

    assert recorder.changed[0].question["options"][0]["id"] == "b5"
    assert watch.tracking(goal_id)


@pytest.mark.asyncio
async def test_other_goals_are_ignored_and_body_events_pass_through() -> None:
    """别人下达的目标不管；生存需求的事件原样交给身体回调。"""
    server, recorder = FakeMaicraft(), _Recorder()
    watch = _watch(server, recorder)
    await watch.step()
    reply = await server._execute({"goal": {"ability": "maicraft:gather"}})
    server.finish(reply["data"]["task_id"])
    server.body_event("temporary_task_started", "自卫：有僵尸打过来")
    server.body_event("character_died", "角色死了，等重生")

    await watch.step()

    assert recorder.changed == []
    assert [event["kind"] for event in recorder.body] == ["temporary_task_started", "character_died"]
    assert server.calls_to("task") == [], "没在跟踪的目标不去查"


@pytest.mark.asyncio
async def test_after_a_world_change_missing_goals_are_reported_gone() -> None:
    """事件流换了：逐个查在跟踪的目标，查不到的如实说不在了。"""
    server, recorder = FakeMaicraft(), _Recorder()
    watch = _watch(server, recorder)
    await watch.step()
    goal_id = await _launch(server, watch)
    server.change_world()

    await watch.step()

    assert [gone[0] for gone in recorder.gone] == [goal_id]
    assert not watch.tracking(goal_id)


@pytest.mark.asyncio
async def test_a_reused_id_for_another_ability_is_not_mistaken_for_the_old_goal() -> None:
    """换世界后编号撞上别的目标：能力都不一样，按原来的不在了处理。"""
    server, recorder = FakeMaicraft(), _Recorder()
    watch = _watch(server, recorder)
    await watch.step()
    goal_id = await _launch(server, watch, "maicraft:gather")
    server.change_world()
    await server._execute({"goal": {"ability": "maicraft:fight"}})

    await watch.step()

    assert recorder.gone and recorder.gone[0][0] == goal_id
    assert "另一个目标" in recorder.gone[0][1]
    assert recorder.changed == []


@pytest.mark.asyncio
async def test_unreadable_or_failed_reads_do_not_raise() -> None:
    """读事件失败：退避后重试，不抛出、不改游标。"""
    recorder = _Recorder()

    async def failing(name: str, arguments: Dict[str, Any]) -> MaicraftReply:
        return MaicraftReply(ok=False, error_code="no_reply", error_message="连不上")

    watch = GoalWatch(
        call=failing,
        connected=lambda: True,
        on_goal_changed=recorder.on_changed,
        on_goal_gone=recorder.on_gone,
        on_body_event=recorder.on_body,
    )

    async def no_sleep() -> None:
        return None

    watch._back_off = no_sleep  # type: ignore[method-assign]
    await watch.step()

    assert watch._stream_id is None and recorder.changed == []
