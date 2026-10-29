"""流程单持久化接线：transitions 落行 / 实例写穿（桥）与崩溃恢复（coordinator.start）。

桩仓储只记录调用，验证"何时调、传什么"；SQL 语义由 storage 层测试守护。
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock

from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.storage.models.rundown import (
    DEFAULT_RUNDOWN,
    Rundown,
    RundownInstance,
    RundownInstanceSegment,
    RundownTransition,
)
from tests.agents.streamer.helpers import make_streamer_agent

# 登记（只读/驱动穿透，合并时进底账）：断言读取流程单状态机的内部态
# （agent._rundown_coord._state / _bg_tasks），并直接驱动协调器的内部发布
# 例程（_emit_rundown_changed）以编排变更事实；协调器尚无对应公开观测面。


class _StubSessionManager:
    """resolve_pk 可编排的场次管理器桩。"""

    def __init__(self, pk: Optional[int]) -> None:
        self.pk = pk
        self.calls = 0

    async def resolve_pk(self) -> Optional[int]:
        self.calls += 1
        return self.pk


class _StubInstanceRepo:
    def __init__(self) -> None:
        self.snapshots: List[RundownInstance] = []
        self.fail = False

    async def get_by_session(self, live_session_id: int) -> Optional[RundownInstance]:
        self.last_get_session: int = live_session_id
        return self.snapshots[0] if self.snapshots else None

    async def upsert(self, snapshot: RundownInstance) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.snapshots.append(snapshot)


class _StubTransitionRepo:
    def __init__(self) -> None:
        self.rows: List[RundownTransition] = []
        self.fail = False
        # 设了闸门时落行停在半路，等测试放行（编排"停止时还有落库在飞"）
        self.gate: Optional[asyncio.Event] = None
        self.gate_reached = asyncio.Event()

    async def insert(self, transition: RundownTransition) -> None:
        if self.fail:
            raise RuntimeError("db down")
        if self.gate is not None:
            self.gate_reached.set()
            await self.gate.wait()
        self.rows.append(transition)


class _StubRundownRepo:
    """get_rundown 桩：返回构造时给定的流程单。"""

    def __init__(self, rundown: Optional[Rundown]) -> None:
        self.rundown = rundown

    async def get_rundown(self, rundown_id: str) -> Optional[Rundown]:
        return self.rundown


def _payload(action: str, *, seg_id: str = "", index: int = 0, by: str = "system") -> RundownChangedPayload:
    return RundownChangedPayload(
        action=action,
        rundown_id="default_first_stream",
        segment_id=seg_id,
        segment_title=seg_id,
        index=index,
        total=4,
        by=by,  # type: ignore[arg-type]
        at_ms=1_000_000,
    )


def _make_agent(
    *,
    session_manager: Any,
    instance_repo: Any = None,
    transition_repo: Any = None,
    rundown_repo: Any = None,
) -> Any:
    return make_streamer_agent(
        session_manager=session_manager,
        rundown_instance_repo=instance_repo,
        rundown_transition_repo=transition_repo,
        rundown_repo=rundown_repo,
    )


async def _make_restored_agent(**kwargs: Any) -> Any:
    """带流程单模板仓储构造并启动协调器（崩溃恢复路径，仓储经构造注入）。"""
    agent = _make_agent(rundown_repo=_StubRundownRepo(DEFAULT_RUNDOWN), **kwargs)
    await agent._rundown_coord.start()
    return agent


async def _drain(agent: Any) -> None:
    """等待 fire-and-forget 后台任务全部落定。"""
    for _ in range(20):
        pending = [t for t in agent._rundown_coord._bg_tasks if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending)


def test_persist_skipped_without_repos_or_session() -> None:
    session = _StubSessionManager(None)
    agent = _make_agent(session_manager=session)
    # 两仓储都未注入：桥不做任何事（也不解析场次）
    agent._rundown_coord._state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    agent._rundown_coord._emit_rundown_changed("rundown.changed", _payload("load", seg_id="opening"))
    assert session.calls == 0


async def test_persist_skipped_without_active_session() -> None:
    session = _StubSessionManager(None)
    instance_repo = _StubInstanceRepo()
    transition_repo = _StubTransitionRepo()
    agent = _make_agent(session_manager=session, instance_repo=instance_repo, transition_repo=transition_repo)
    agent._rundown_coord._state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    await _drain(agent)
    before = (len(instance_repo.snapshots), len(transition_repo.rows))

    agent._rundown_coord._emit_rundown_changed("rundown.changed", _payload("pause", seg_id="opening", index=0))
    await _drain(agent)
    # 无活跃场次（resolve_pk None）：落行与写穿都跳过
    assert (len(instance_repo.snapshots), len(transition_repo.rows)) == before


async def test_persist_inserts_transition_and_upserts_instance() -> None:
    session = _StubSessionManager(42)
    instance_repo = _StubInstanceRepo()
    transition_repo = _StubTransitionRepo()
    agent = _make_agent(session_manager=session, instance_repo=instance_repo, transition_repo=transition_repo)
    state = agent._rundown_coord._state
    state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    await _drain(agent)
    # armed 态（load 只装载不起算）不写实例快照：锚点未起算无从落列
    assert len(instance_repo.snapshots) == 0

    # 真实驱动状态机：armed 态 goto 隐含起算，桥读到的当前环节是 self_intro
    assert state.goto("self_intro", by="human", now_ms=1_000_000) is None
    await _drain(agent)

    # 变更事实行：payload 字段原样 + 当前环节 expected_ms；非 append 行无描述
    assert len(transition_repo.rows) == 2  # load + goto
    row = transition_repo.rows[-1]
    assert row.action == "goto"
    assert (row.live_session_id, row.seg_id, row.seg_index, row.by, row.at_ms) == (
        42,
        "self_intro",
        1,
        "human",
        1_000_000,
    )
    assert row.expected_ms == 600_000
    assert row.task_description is None

    # 实例写穿：头字段来自 export_progress（起算后非空），环节行全量
    assert len(instance_repo.snapshots) == 1
    snap = instance_repo.snapshots[-1]
    assert (snap.live_session_id, snap.template_id, snap.seg_index) == (42, "default_first_stream", 1)
    assert [s.seg_id for s in snap.segments] == [s.id for s in DEFAULT_RUNDOWN.segments]


async def test_persist_append_row_carries_task_description() -> None:
    session = _StubSessionManager(42)
    transition_repo = _StubTransitionRepo()
    agent = _make_agent(session_manager=session, transition_repo=transition_repo)
    state = agent._rundown_coord._state
    state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    await _drain(agent)

    # append 行从 state 取新环节描述（payload.segment_id 指向 state 中的新环节）
    agent._rundown_coord._emit_rundown_changed(
        "rundown.changed", _payload("append", seg_id="self_intro", index=1, by="agent")
    )
    await _drain(agent)
    row = transition_repo.rows[-1]
    assert row.action == "append"
    # append 行从 state 取新环节描述
    assert (
        row.task_description
        == "围绕自己的角色定位、开播初衷、未来内容规划展开，让观众快速了解主播是谁、为什么开播、接下来打算做什么。\n要点：角色身份、开播初衷、未来内容方向"
    )


async def test_persist_failures_are_isolated_and_soft() -> None:
    session = _StubSessionManager(42)
    instance_repo = _StubInstanceRepo()
    transition_repo = _StubTransitionRepo()
    transition_repo.fail = True
    agent = _make_agent(session_manager=session, instance_repo=instance_repo, transition_repo=transition_repo)
    agent._rundown_coord._state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    await _drain(agent)
    # 落行失败不影响写穿（各自独立 try/except）；armed 态不写实例快照
    assert len(transition_repo.rows) == 0
    assert len(instance_repo.snapshots) == 0


async def test_stop_waits_inflight_persist_then_drops_later_changes() -> None:
    """停止等在途落库做完（含停止途中引起的变更），停止之后再来的变更不广播不落库。"""
    session = _StubSessionManager(42)
    transition_repo = _StubTransitionRepo()
    mouth = MagicMock()
    agent = make_streamer_agent(
        {"mouth": {"enabled": True}}, mouth=mouth, session_manager=session, rundown_transition_repo=transition_repo
    )
    # 嘴停下时引起一次流程单变更：代表停止途中还在收尾的决策轮改了流程单
    mouth.stop = AsyncMock(
        side_effect=lambda: agent._rundown_coord._emit_rundown_changed(
            "rundown.changed", _payload("next", seg_id="self_intro", index=1)
        )
    )
    agent._rundown_coord._state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    await _drain(agent)

    # 一次变更正在落库，写到一半停住
    transition_repo.gate = asyncio.Event()
    agent._rundown_coord._emit_rundown_changed("rundown.changed", _payload("pause", seg_id="opening"))
    await transition_repo.gate_reached.wait()

    stop = asyncio.create_task(agent.stop())
    # 让停止流程把不依赖落库的步骤都走完（只让出事件循环，不等真实时间）；
    # 落库没放行，停止必须还停在等它这一步
    for _ in range(100):
        await asyncio.sleep(0)
    assert not stop.done()

    transition_repo.gate.set()
    await stop
    assert [row.action for row in transition_repo.rows] == ["load", "pause", "next"]

    # 停止之后再来的变更：不派任务、不解析场次、不落行
    resolved = session.calls
    agent._rundown_coord._emit_rundown_changed("rundown.changed", _payload("resume", seg_id="opening"))
    assert not agent._rundown_coord._bg_tasks
    assert session.calls == resolved and len(transition_repo.rows) == 3


# ---------------------------------------------------------------------------
# 崩溃恢复（coordinator.start → _try_restore）
# ---------------------------------------------------------------------------


def _snapshot(
    template_id: str, seg_index: int, *, segments: Optional[List[RundownInstanceSegment]] = None
) -> RundownInstance:
    if segments is None:
        segments = [
            RundownInstanceSegment(
                seq=i, seg_id=s.id, title=s.title, task_description=s.task_description, expected_ms=s.expected_ms
            )
            for i, s in enumerate(DEFAULT_RUNDOWN.segments)
        ]
    return RundownInstance(
        live_session_id=42,
        template_id=template_id,
        seg_index=seg_index,
        segment_started_at_ms=900_000,
        paused_at_ms=None,
        rundown_started_at_ms=900_000,
        accumulated_pause_ms=0,
        accumulated_session_pause_ms=0,
        updated_at_ms=1_000_000,
        segments=segments,
    )


async def test_restore_recovers_cursor_from_snapshot() -> None:
    session = _StubSessionManager(42)
    instance_repo = _StubInstanceRepo()
    instance_repo.snapshots.append(_snapshot("default_first_stream", 2))
    agent = await _make_restored_agent(session_manager=session, instance_repo=instance_repo)

    state = agent._rundown_coord._state
    assert (state.status, state.index, state.current_segment_id) == ("running", 2, "chat")
    # 恢复清单来自实例快照（防模板漂移），模板标题保留
    assert state.rundown.title == DEFAULT_RUNDOWN.title
    assert [s.id for s in state.rundown.segments] == [s.id for s in DEFAULT_RUNDOWN.segments]
    assert state.segment_started_at_ms == 900_000  # 绝对时刻锚点原样还原


async def test_restore_uses_snapshot_segments_including_appended() -> None:
    session = _StubSessionManager(42)
    instance_repo = _StubInstanceRepo()
    appended = RundownInstanceSegment(seq=4, seg_id="bonus", title="加场", task_description="x", expected_ms=120_000)
    instance_repo.snapshots.append(
        _snapshot(
            "default_first_stream",
            4,
            segments=[
                *(
                    RundownInstanceSegment(
                        seq=i,
                        seg_id=s.id,
                        title=s.title,
                        task_description=s.task_description,
                        expected_ms=s.expected_ms,
                    )
                    for i, s in enumerate(DEFAULT_RUNDOWN.segments)
                ),
                appended,
            ],
        )
    )
    agent = await _make_restored_agent(session_manager=session, instance_repo=instance_repo)

    state = agent._rundown_coord._state
    # 快照含 Agent 追加环节且 seg_index 指向它 → 续命态还原
    assert (state.status, state.index, state.current_segment_id) == ("running", 4, "bonus")


async def test_no_snapshot_falls_back_to_load() -> None:
    session = _StubSessionManager(42)
    instance_repo = _StubInstanceRepo()  # 无快照
    agent = await _make_restored_agent(session_manager=session, instance_repo=instance_repo)
    assert (agent._rundown_coord._state.status, agent._rundown_coord._state.index) == ("armed", 0)


async def test_template_mismatch_and_corruption_fall_back_to_load() -> None:
    session = _StubSessionManager(42)
    # 模板不匹配：导演开播前换了单
    instance_repo = _StubInstanceRepo()
    instance_repo.snapshots.append(_snapshot("other_rd", 2))
    agent = await _make_restored_agent(session_manager=session, instance_repo=instance_repo)
    assert (agent._rundown_coord._state.status, agent._rundown_coord._state.index) == ("armed", 0)

    # 快照损坏：游标越界
    corrupt_repo = _StubInstanceRepo()
    corrupt_repo.snapshots.append(_snapshot("default_first_stream", 99))
    agent2 = await _make_restored_agent(session_manager=session, instance_repo=corrupt_repo)
    assert (agent2._rundown_coord._state.status, agent2._rundown_coord._state.index) == ("armed", 0)


async def test_no_active_session_falls_back_to_load() -> None:
    session = _StubSessionManager(None)
    instance_repo = _StubInstanceRepo()
    instance_repo.snapshots.append(_snapshot("default_first_stream", 2))
    agent = await _make_restored_agent(session_manager=session, instance_repo=instance_repo)
    assert (agent._rundown_coord._state.status, agent._rundown_coord._state.index) == ("armed", 0)
