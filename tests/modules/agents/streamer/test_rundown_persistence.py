"""流程单持久化接线：transitions 落行 / 实例写穿（桥）与崩溃恢复（_start_rundown）。

桩仓储只记录调用，验证"何时调、传什么"；SQL 语义由 storage 层测试守护。
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.storage.models.rundown import (
    DEFAULT_RUNDOWN,
    Rundown,
    RundownInstance,
    RundownInstanceSegment,
    RundownTransition,
)
from tests.agents.streamer.helpers import make_streamer_agent


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

    async def insert(self, transition: RundownTransition) -> None:
        if self.fail:
            raise RuntimeError("db down")
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
) -> Any:
    return make_streamer_agent(
        session_manager=session_manager,
        rundown_instance_repo=instance_repo,
        rundown_transition_repo=transition_repo,
    )


async def _drain(agent: Any) -> None:
    """等待 fire-and-forget 后台任务全部落定。"""
    for _ in range(20):
        pending = [t for t in agent._bg_tasks if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending)


def test_persist_skipped_without_repos_or_session() -> None:
    session = _StubSessionManager(None)
    agent = _make_agent(session_manager=session)
    # 两仓储都未注入：桥不做任何事（也不解析场次）
    agent._rundown_state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    agent._emit_rundown_changed("rundown.changed", _payload("load", seg_id="opening"))
    assert session.calls == 0


async def test_persist_skipped_without_active_session() -> None:
    session = _StubSessionManager(None)
    instance_repo = _StubInstanceRepo()
    transition_repo = _StubTransitionRepo()
    agent = _make_agent(session_manager=session, instance_repo=instance_repo, transition_repo=transition_repo)
    agent._rundown_state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    await _drain(agent)
    before = (len(instance_repo.snapshots), len(transition_repo.rows))

    agent._emit_rundown_changed("rundown.changed", _payload("pause", seg_id="opening", index=0))
    await _drain(agent)
    # 无活跃场次（resolve_pk None）：落行与写穿都跳过
    assert (len(instance_repo.snapshots), len(transition_repo.rows)) == before


async def test_persist_inserts_transition_and_upserts_instance() -> None:
    session = _StubSessionManager(42)
    instance_repo = _StubInstanceRepo()
    transition_repo = _StubTransitionRepo()
    agent = _make_agent(session_manager=session, instance_repo=instance_repo, transition_repo=transition_repo)
    state = agent._rundown_state
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
    state = agent._rundown_state
    state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    await _drain(agent)

    # append 行从 state 取新环节描述（payload.segment_id 指向 state 中的新环节）
    agent._emit_rundown_changed("rundown.changed", _payload("append", seg_id="self_intro", index=1, by="agent"))
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
    agent._rundown_state.load(DEFAULT_RUNDOWN, now_ms=1_000_000)
    await _drain(agent)
    # 落行失败不影响写穿（各自独立 try/except）；armed 态不写实例快照
    assert len(transition_repo.rows) == 0
    assert len(instance_repo.snapshots) == 0


# ---------------------------------------------------------------------------
# 崩溃恢复（_start_rundown → _try_restore_rundown）
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


async def _start(agent: Any, rundown_repo: Any) -> None:
    agent._rundowns = rundown_repo
    await agent._start_rundown()


async def test_restore_recovers_cursor_from_snapshot() -> None:
    session = _StubSessionManager(42)
    instance_repo = _StubInstanceRepo()
    instance_repo.snapshots.append(_snapshot("default_first_stream", 2))
    agent = _make_agent(session_manager=session, instance_repo=instance_repo)
    await _start(agent, _StubRundownRepo(DEFAULT_RUNDOWN))

    state = agent._rundown_state
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
    agent = _make_agent(session_manager=session, instance_repo=instance_repo)
    await _start(agent, _StubRundownRepo(DEFAULT_RUNDOWN))

    state = agent._rundown_state
    # 快照含 Agent 追加环节且 seg_index 指向它 → 续命态还原
    assert (state.status, state.index, state.current_segment_id) == ("running", 4, "bonus")


async def test_no_snapshot_falls_back_to_load() -> None:
    session = _StubSessionManager(42)
    instance_repo = _StubInstanceRepo()  # 无快照
    agent = _make_agent(session_manager=session, instance_repo=instance_repo)
    await _start(agent, _StubRundownRepo(DEFAULT_RUNDOWN))
    assert (agent._rundown_state.status, agent._rundown_state.index) == ("armed", 0)


async def test_template_mismatch_and_corruption_fall_back_to_load() -> None:
    session = _StubSessionManager(42)
    # 模板不匹配：导演开播前换了单
    instance_repo = _StubInstanceRepo()
    instance_repo.snapshots.append(_snapshot("other_rd", 2))
    agent = _make_agent(session_manager=session, instance_repo=instance_repo)
    await _start(agent, _StubRundownRepo(DEFAULT_RUNDOWN))
    assert (agent._rundown_state.status, agent._rundown_state.index) == ("armed", 0)

    # 快照损坏：游标越界
    corrupt_repo = _StubInstanceRepo()
    corrupt_repo.snapshots.append(_snapshot("default_first_stream", 99))
    agent2 = _make_agent(session_manager=session, instance_repo=corrupt_repo)
    await _start(agent2, _StubRundownRepo(DEFAULT_RUNDOWN))
    assert (agent2._rundown_state.status, agent2._rundown_state.index) == ("armed", 0)


async def test_no_active_session_falls_back_to_load() -> None:
    session = _StubSessionManager(None)
    instance_repo = _StubInstanceRepo()
    instance_repo.snapshots.append(_snapshot("default_first_stream", 2))
    agent = _make_agent(session_manager=session, instance_repo=instance_repo)
    await _start(agent, _StubRundownRepo(DEFAULT_RUNDOWN))
    assert (agent._rundown_state.status, agent._rundown_state.index) == ("armed", 0)
