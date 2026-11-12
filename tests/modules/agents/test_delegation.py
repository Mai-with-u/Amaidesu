"""委派 / 递话 / 结单原语（framework_delegate / framework_prompt / framework_task_settle / framework_task_status）

覆盖 D1 状态机核心契约：
- 任何时刻每个执行者至多一件【在做】，其余在【待办】（还没开工 / 搁下了带进度），无 suspended 状态；
- 换活必须结单（when_busy="replace" + current 三选一：done -> closed_by_initiator / shelve -> 回待办最前 / drop -> cancelled）；
- 新件被拒收时原在做件连同收手进度一并送回执行者回滚；
- 按短编号（"在做" / "待办N"）结单与提拔待办，不暴露内部任务号给模型；
- 组合根单例 DelegationCore 供 LLM 工具面与运营 REST 控制面共享同一份队列与看板；
- 看门狗盯实际活动（有在跑 provider 子目标不误叫，工具/目标活动刷新计时，无活动超时带"在做"提醒定夺）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Callable, List
from unittest.mock import MagicMock

import pytest

from src.agents.minecraft.agent import SUPPLEMENT_DELIVERY_REMINDER, MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.agents.base import BaseAgent
from src.modules.agents.control import AgentControl, DelegationCore, build_agent_control_provider
from src.modules.agents.delegation_board import read_board
from src.modules.agents.delegation_text import DELEGATE_DIALOGUE_HEAD, DELEGATE_HEAD
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import AgentPromptedPayload, LiveEndedPayload, TaskChangedPayload
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import REPLY_NOTE_AT_KEY, REPLY_NOTE_KEY, TaskLedger, TaskTracker


def _inv(tool: str, args: dict, source: str = "agent_a") -> ToolInvocation:
    return ToolInvocation(tool_name=tool, arguments=args, source=source)


class _StubAgent(BaseAgent):
    """可配置拒收原因的最小 Agent 桩；name 用动态子类隔离。"""

    name = ""
    description = "stub"

    def __init__(self, name: str, *, refuse: str | None = None) -> None:
        self.__class__ = type(f"_StubAgent_{name}", (type(self),), {"name": name, "description": f"stub {name}"})
        super().__init__()
        self._refuse = refuse
        self.received: List[tuple[str, str]] = []  # (task_id, instruction)
        self.prompts_received: List[tuple[str, str]] = []  # (content, source)
        self.cancel_calls: List[tuple[str, str]] = []  # (task_id, source)

    def list_tools(self):
        return []

    def receive_delegation(self, *, instruction: str, task_id: str):
        if self._refuse:
            return self._refuse
        self.received.append((task_id, instruction))
        return None

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        self.prompts_received.append((content, source))
        return True

    def cancel_task(self, task_id: str, source: str = "") -> bool:
        if any(tid == task_id for tid, _ in self.received) and not any(tid == task_id for tid, _ in self.cancel_calls):
            self.cancel_calls.append((task_id, source))
            return True
        return False

    def finish_latest(self, ledger: TaskLedger, *, failed: bool = False) -> None:
        task_id, _ = self.received[-1]
        ledger.update(task_id, "running", summary="开始执行")
        ledger.update(task_id, "failed" if failed else "succeeded", summary="执行失败" if failed else "全部完成")


class _ReleasableAgent(_StubAgent):
    """支持收手协议（release_delegation）的执行 Agent 桩，可配置特定新指令拒收。"""

    def __init__(self, name: str, *, progress_note: str = "墙砌了一半", refuse_when: str = "") -> None:
        super().__init__(name)
        self.progress_note = progress_note
        self.refuse_when = refuse_when
        self.released: List[str] = []

    def receive_delegation(self, *, instruction: str, task_id: str):
        if self.refuse_when and self.refuse_when in instruction:
            return f"做不了：{self.refuse_when}"
        return super().receive_delegation(instruction=instruction, task_id=task_id)

    async def release_delegation(self, task_id: str, *, reason: str = "") -> str | None:
        del reason
        self.released.append(task_id)
        return self.progress_note


class _Rig:
    def __init__(self, *agents: BaseAgent, clock: Callable[[], int] | None = None) -> None:
        self.bus = EventBus(enable_stats=False)
        self.registry = ToolRegistry(event_bus=self.bus)
        self.ledger = TaskLedger(event_bus=self.bus)
        self.manager = AgentManager()
        for agent in agents:
            self.manager.register(agent)
        self.core = DelegationCore(self.manager, self.ledger, event_bus=self.bus, clock=clock)
        self.control = AgentControl(self.manager, self.ledger, event_bus=self.bus, delegation_core=self.core)
        self.registry.register_provider(
            build_agent_control_provider(
                self.manager, self.ledger, event_bus=self.bus, clock=clock, delegation_core=self.core
            )
        )

    async def call(self, tool: str, args: dict, source: str = "agent_a"):
        return await self.registry.invoke(_inv(tool, args, source))


@pytest.fixture
def rig() -> _Rig:
    return _Rig(_StubAgent("agent_a"), _StubAgent("agent_b"))


def _agent(rig: _Rig, name: str) -> _StubAgent:
    return rig.manager.get_agent_by_name(name)  # type: ignore[return-value]


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"等待事件送达超时（{timeout_s}s）")
        await asyncio.sleep(0.005)


async def test_delegation_full_chain_and_status_query_by_short_label(rig: _Rig) -> None:
    received: List[TaskChangedPayload] = []

    async def on_changed(name: str, payload: TaskChangedPayload, source: str) -> None:
        received.append(payload)

    rig.bus.on(CoreEvents.TASK_CHANGED, on_changed, model_class=TaskChangedPayload)

    res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})
    assert res.success
    task_id = res.structured_content["task_id"]
    assert res.structured_content["item"] == "在做"

    by_item = await rig.call("framework_task_status", {"item": "在做"})
    assert by_item.success and by_item.structured_content["task_id"] == task_id
    assert by_item.structured_content["item"] == "在做"

    _agent(rig, "agent_b").finish_latest(rig.ledger)
    await _wait_until(lambda: any(p.status == "succeeded" for p in received))
    assert rig.ledger.get(task_id) is None


async def test_execution_failure_ends_in_failed_terminal(rig: _Rig) -> None:
    res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "不可能完成的活"})
    task_id = res.structured_content["task_id"]
    _agent(rig, "agent_b").finish_latest(rig.ledger, failed=True)
    await rig.bus.wait_until_drained()
    assert rig.ledger.get(task_id) is None


@pytest.mark.parametrize(
    "args,needle",
    [
        ({"agent": "ghost", "instruction": "干活"}, "不在名册"),
        ({"agent": "agent_b", "instruction": "干活"}, "忙不过来"),
        ({"agent": "agent_a", "instruction": "自己干"}, "自派"),
        ({"agent": "agent_b"}, "instruction"),
    ],
)
async def test_acceptance_failures_do_not_register(args: dict, needle: str) -> None:
    local = _Rig(_StubAgent("agent_a"), _StubAgent("agent_b", refuse="忙不过来"))
    res = await local.call("framework_delegate", args)
    assert res.success is False and needle in res.error_message and len(local.ledger) == 0
    assert res.failure_kind == "business"


async def test_business_rejection_does_not_trip_the_tool() -> None:
    local = _Rig(_StubAgent("agent_a"), _StubAgent("agent_b", refuse="忙不过来"))
    for _ in range(4):
        res = await local.call("framework_delegate", {"agent": "agent_b", "instruction": "干活"})
        assert res.success is False and res.failure_kind == "business"
    assert not local.registry.is_tripped("framework_delegate")


async def test_missing_task_ledger_stays_an_execution_failure() -> None:
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager()
    manager.register(_StubAgent("agent_a"))
    registry.register_provider(build_agent_control_provider(manager, None, event_bus=bus))

    res = await registry.invoke(_inv("framework_task_status", {"task_id": "deleg_1700000000000_1"}))
    assert res.success is False and res.failure_kind != "business"


async def test_empty_target_resolves_sole_game_agent_or_demands_a_name(rig: _Rig) -> None:
    res = await rig.call("framework_delegate", {"agent": "", "instruction": "去挖矿"})
    assert res.success and res.structured_content["executor"] == "agent_b"

    ambiguous = _Rig(_StubAgent("agent_a"), _StubAgent("agent_b"), _StubAgent("agent_c"))
    res = await ambiguous.call("framework_delegate", {"agent": "", "instruction": "去挖矿"})
    assert res.success is False and "点名" in res.error_message and len(ambiguous.ledger) == 0


async def test_prompt_marks_chat_kind_and_does_not_touch_ledger(rig: _Rig) -> None:
    prompted: List[AgentPromptedPayload] = []

    async def on_prompted(name: str, payload: AgentPromptedPayload, source: str) -> None:
        prompted.append(payload)

    rig.bus.on(CoreEvents.AGENT_PROMPTED, on_prompted, model_class=AgentPromptedPayload)
    res = await rig.call("framework_prompt", {"agent": "agent_b", "content": "回玩家甲：「来了」", "kind": "chat"})
    await _wait_until(lambda: len(prompted) == 1)
    assert res.success and len(rig.ledger) == 0
    assert _agent(rig, "agent_b").prompts_received[-1][0].startswith("[聊天回话]\n")


async def test_busy_target_queues_with_stable_backlog_numbers_and_delivers_on_terminal(rig: _Rig) -> None:
    b = _agent(rig, "agent_b")
    await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})

    q1 = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "再去挖个矿"})
    q2 = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "开一块菜地"})
    assert q1.structured_content["item"] == "待办1" and q1.structured_content["queue_position"] == 1
    assert q2.structured_content["item"] == "待办2" and q2.structured_content["queue_position"] == 2

    # 撤掉待办1，待办2 的短编号不重排，仍叫"待办2"
    settled = await rig.call("framework_task_settle", {"item": "待办1", "outcome": "drop"})
    assert settled.success and settled.structured_content["status"] == "cancelled"
    board = read_board(rig.ledger, "agent_b")
    assert [item.label for item in board.backlog] == ["待办2"]

    b.finish_latest(rig.ledger)
    await _wait_until(lambda: len(b.received) == 2)
    assert b.received[-1][1] == "开一块菜地"


async def test_replace_requires_current_outcome_and_distinguishes_done_shelve_drop() -> None:
    target = _ReleasableAgent("agent_b", progress_note="已栽满四格")
    local = _Rig(_StubAgent("agent_a"), target)
    changed: List[TaskChangedPayload] = []

    async def on_changed(name: str, payload: TaskChangedPayload, source: str) -> None:
        changed.append(payload)

    local.bus.on(CoreEvents.TASK_CHANGED, on_changed, model_class=TaskChangedPayload)

    first = (
        await local.call("framework_delegate", {"agent": "agent_b", "instruction": "栽四棵树苗"})
    ).structured_content["task_id"]

    # 换活没交代 current：拒收，原在做单保持不动
    missing = await local.call(
        "framework_delegate", {"agent": "agent_b", "instruction": "造刷铁机", "when_busy": "replace"}
    )
    assert missing.success is False and "current" in missing.error_message
    assert local.ledger.get(first) is not None and target.released == []

    # 换活且交代原件 done：原件记 closed_by_initiator（不是 succeeded），新件成为唯一在做
    replaced = await local.call(
        "framework_delegate",
        {"agent": "agent_b", "instruction": "造刷铁机", "when_busy": "replace", "current": "done"},
    )
    assert replaced.success and not replaced.structured_content.get("queued")
    second = replaced.structured_content["task_id"]
    await _wait_until(lambda: any(p.task_id == first and p.status == "closed_by_initiator" for p in changed))
    assert local.ledger.get(first) is None
    assert read_board(local.ledger, "agent_b").in_hand.task_id == second

    # 再来急事（when_busy=replace + current=shelve）：刷铁机搁回待办最前，保留进度；急事办完后按新任务重送达
    target.progress_note = "水车已立好"
    urgent = (
        await local.call(
            "framework_delegate",
            {"agent": "agent_b", "instruction": "跟玩家去看新家", "when_busy": "replace", "current": "shelve"},
        )
    ).structured_content["task_id"]
    board = read_board(local.ledger, "agent_b")
    assert board.in_hand.task_id == urgent
    assert len(board.backlog) == 1 and board.backlog[0].task_id == second
    assert board.backlog[0].shelved is True and "水车已立好" in board.backlog[0].shelved_progress
    assert local.ledger.get(second).status == "queued"  # 不存在 suspended 状态

    target.finish_latest(local.ledger)
    await _wait_until(lambda: len(target.received) == 4)
    redelivered_id, redelivered_text = target.received[-1]
    assert redelivered_id == second
    assert "造刷铁机" in redelivered_text and "水车已立好" in redelivered_text


async def test_replace_rolls_back_with_progress_when_new_delegation_is_refused() -> None:
    """新件被拒收时，刚收手的原任务连同收手进度一并送回执行者继续做，不卡死在空手或挂起态。"""
    target = _ReleasableAgent("agent_b", progress_note="已铺好地基", refuse_when="违禁指令")
    local = _Rig(_StubAgent("agent_a"), target)

    first = (
        await local.call("framework_delegate", {"agent": "agent_b", "instruction": "建一座木屋"})
    ).structured_content["task_id"]
    refused = await local.call(
        "framework_delegate",
        {"agent": "agent_b", "instruction": "违禁指令", "when_busy": "replace", "current": "shelve"},
    )
    assert refused.success is False and "已送回继续执行" in refused.error_message
    board = read_board(local.ledger, "agent_b")
    assert board.in_hand is not None and board.in_hand.task_id == first
    assert board.backlog == ()
    restored_id, restored_text = target.received[-1]
    assert restored_id == first and "建一座木屋" in restored_text and "已铺好地基" in restored_text


async def test_promote_backlog_item_replaces_finished_in_hand_without_duplicating() -> None:
    """第 65 场 21:58 场景：栽树单已做完但未交付，刷铁机在待办1 -> 直接把待办1 提上来并结掉栽树单。"""
    target = _ReleasableAgent("agent_b", progress_note="四格树苗已栽满")
    local = _Rig(_StubAgent("agent_a"), target)

    sapling_id = (
        await local.call("framework_delegate", {"agent": "agent_b", "instruction": "把树场四格栽满树苗"})
    ).structured_content["task_id"]
    iron_res = await local.call("framework_delegate", {"agent": "agent_b", "instruction": "给刷铁机加压锭工序"})
    iron_id = iron_res.structured_content["task_id"]
    assert iron_res.structured_content["item"] == "待办1"

    promoted = await local.call(
        "framework_delegate",
        {"agent": "agent_b", "item": "待办1", "when_busy": "replace", "current": "done"},
    )
    assert promoted.success and promoted.structured_content["task_id"] == iron_id
    assert not promoted.structured_content.get("queued")
    assert local.ledger.get(sapling_id) is None
    board = read_board(local.ledger, "agent_b")
    assert board.in_hand is not None and board.in_hand.task_id == iron_id
    assert board.backlog == ()


async def test_shared_delegation_core_unifies_operator_and_streamer_queues(rig: _Rig) -> None:
    """组合根单例：运营直派与主播工具委派共用同一份看板、排位与上限。"""
    await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "主播派的第一件"})
    op_res = await rig.control.delegate("agent_b", "运营补的第二件", initiator="operator")
    assert op_res["ok"] and op_res["queued"] is True and op_res["queue_position"] == 1 and op_res["item"] == "待办1"

    st_res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "主播派的第三件"})
    assert st_res.structured_content["queue_position"] == 2 and st_res.structured_content["item"] == "待办2"


async def test_queue_full_refusal_lists_only_own_words_and_hides_raw_reasoning(rig: _Rig) -> None:
    own = "把营地棚子装一扇木门，" + "门外垫平台阶，" * 10 + "收尾"
    instruction = DELEGATE_HEAD + own + DELEGATE_DIALOGUE_HEAD + '逐字引用]\n[{"content": "观众乙: 别泄露"}]\n取舍规则'
    first = (await rig.call("framework_delegate", {"agent": "agent_b", "instruction": instruction})).structured_content[
        "task_id"
    ]
    rig.ledger.annotate(first, {REPLY_NOTE_KEY: "先 maicraft_place(12, 64, -30)", REPLY_NOTE_AT_KEY: 1793977796538})
    for idx in range(5):
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": f"待办凑数{idx}"})

    refused = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "第6件"})
    assert refused.success is False
    assert f"「{own}」" in refused.error_message
    assert "观众乙" not in refused.error_message
    assert "maicraft_place" not in refused.error_message


# -------------------- 看门狗：看实际活动，不只盯 accepted --------------------


async def test_watchdog_monitors_running_delegation_and_respects_active_goals_and_tools(fake_clock: Any) -> None:
    """看门狗对 running 单同样生效；有在跑 provider 目标或工具调用时不误报，静默超 5 分钟带'在做'提醒。"""
    initiator = _StubAgent("streamer")
    executor = _ReleasableAgent("minecraft")
    local = _Rig(initiator, executor, clock=fake_clock.now_ms)

    res = await local.call(
        "framework_delegate", {"agent": "minecraft", "instruction": "捡一朵蒲公英"}, source="planner-react"
    )
    task_id = res.structured_content["task_id"]
    local.ledger.update(task_id, "running", summary="执行中")

    # 挂一个在跑的 provider 子目标：过 10 分钟也不该误叫
    local.ledger.register(
        task_id="maicraft-goal-1",
        provider="maicraft",
        tool="maicraft_execute",
        initiator="minecraft",
        executor="maicraft",
        status="running",
        source="provider",
    )
    fake_clock.tick(10 * 60_000)
    local.core.check_watchdog()
    assert initiator.prompts_received == []

    # 子目标结束，开始无活动计时；3 分钟时来一次工具调用刷新活动基准
    local.ledger.update("maicraft-goal-1", "succeeded", summary="走到了")
    await local.bus.wait_until_drained()
    fake_clock.tick(3 * 60_000)
    await local.bus.emit(
        "tool.result.minecraft_todo",
        ToolResultPayload(tool_name="minecraft_todo", caller_source="minecraft-react", status="success"),
        source="ToolRegistry",
    )
    await local.bus.wait_until_drained()
    fake_clock.tick(3 * 60_000)
    local.core.check_watchdog()
    assert initiator.prompts_received == []  # 距上次工具活动仅 3 分钟，未超 5 分钟阈值

    fake_clock.tick(3 * 60_000)
    local.core.check_watchdog()
    assert len(initiator.prompts_received) == 1
    text, src = initiator.prompts_received[0]
    assert src == "委派看门狗"
    assert "【在做】" in text and "蒲公英" in text and "framework_task_settle" in text
    assert task_id not in text  # 不把内部任务号念给模型


# -------------------- 真实 MinecraftAgent 基础接线 --------------------


class _McRig(_Rig):
    def __init__(self, *, live_active: bool = True) -> None:
        bus = EventBus(enable_stats=False)
        registry = ToolRegistry(event_bus=bus)
        ledger = TaskLedger(event_bus=bus)
        llm = MagicMock()
        llm.is_profile_visual = MagicMock(return_value=False)
        self.minecraft = MinecraftAgent(
            MinecraftConfig(mcp=McpServerConfig(enabled=False)),
            llm_manager=llm,
            event_bus=bus,
            tool_registry=registry,
            task_tracker=TaskTracker(registry, ledger, poll_interval_ms=20),
            mcp_provider=SimpleNamespace(list_tools=lambda: ["maicraft_observe"]),
            live_active=live_active,
        )
        self.bus, self.registry, self.ledger = bus, registry, ledger
        self.manager = AgentManager()
        self.manager.register(self.minecraft)
        self.core = DelegationCore(self.manager, ledger, event_bus=bus)
        self.control = AgentControl(self.manager, ledger, event_bus=bus, delegation_core=self.core)
        registry.register_provider(
            build_agent_control_provider(self.manager, ledger, event_bus=bus, delegation_core=self.core)
        )


async def test_minecraft_supplement_reminder_attached_only_to_non_chat_prompts() -> None:
    """D1 第 4 条机械兜底：手上在做委派时，非聊天补充自动附带交付提醒；聊天回话不附。"""
    mc = _McRig()
    res = await mc.call("framework_delegate", {"agent": "minecraft", "instruction": "栽四棵树苗"}, source="streamer")
    assert res.success
    # 吸收进本批
    tid, _ = mc.minecraft._message_queue.popleft()
    mc.minecraft._delegations.absorb(tid)
    mc.minecraft._delegations.mark_running()

    await mc.call("framework_prompt", {"agent": "minecraft", "content": "顺手把刷铁机接上"}, source="planner-react")
    _, req_msg = mc.minecraft._message_queue.popleft()
    assert SUPPLEMENT_DELIVERY_REMINDER.strip() in req_msg

    await mc.call(
        "framework_prompt",
        {"agent": "minecraft", "content": "在游戏聊天里回 玩家甲：「马上来」", "kind": "chat"},
        source="planner-react",
    )
    _, chat_msg = mc.minecraft._message_queue.popleft()
    assert SUPPLEMENT_DELIVERY_REMINDER.strip() not in chat_msg


async def test_live_ended_records_distinct_summaries_for_in_hand_and_backlog() -> None:
    mc = _McRig()
    changed: List[TaskChangedPayload] = []

    async def _capture(name: str, payload: TaskChangedPayload, source: str) -> None:
        changed.append(payload)

    mc.bus.on(CoreEvents.TASK_CHANGED, _capture, model_class=TaskChangedPayload)

    first = (
        await mc.call("framework_delegate", {"agent": "minecraft", "instruction": "盖房子"}, source="streamer")
    ).structured_content["task_id"]
    tid, _ = mc.minecraft._message_queue.popleft()
    mc.minecraft._delegations.absorb(tid)
    mc.minecraft._delegations.mark_running()

    await mc.call("framework_delegate", {"agent": "minecraft", "instruction": "开菜地"}, source="streamer")
    await mc.call(
        "framework_delegate",
        {
            "agent": "minecraft",
            "instruction": "跟玩家走",
            "when_busy": "replace",
            "current": "shelve",
            "progress": "墙砌好",
        },
        source="streamer",
    )

    await mc.minecraft._on_live_ended(
        CoreEvents.LIVE_ENDED,
        LiveEndedPayload(live_session_id=1, reason="手动下播"),
        source="LiveSessionManager",
    )
    await _wait_until(lambda: sum(1 for p in changed if p.status == "cancelled") == 3)
    await mc.bus.wait_until_drained()
    assert len(mc.ledger) == 0
    cancelled_summaries = [p.summary for p in changed if p.status == "cancelled"]
    assert any("当时在做" in s for s in cancelled_summaries)
    assert any("还没开工" in s for s in cancelled_summaries)
    assert any("搁下了，做到：" in s and "墙砌好" in s for s in cancelled_summaries)
    assert mc.ledger.get(first) is None
