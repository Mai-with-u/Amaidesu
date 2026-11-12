"""委派 / 递话原语（framework_delegate / framework_prompt / framework_task_status）

受理失败与任务失败分开（受理失败不登记）；目标留空解析为唯一游戏 Agent；递话受理不记账；
最后用真实 minecraft Agent 验证入队带任务号、批次状态写回、硬取消与断连恢复续跑。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Callable, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.agents.base import BaseAgent
from src.modules.agents.control import AgentControl, build_agent_control_provider
from src.modules.agents.delegation_text import DELEGATE_DIALOGUE_HEAD, DELEGATE_HEAD
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import AgentPromptedPayload, LiveEndedPayload, TaskChangedPayload
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import REPLY_NOTE_AT_KEY, REPLY_NOTE_KEY, TaskLedger, TaskTracker


def _inv(tool: str, args: dict, source: str = "agent_a") -> ToolInvocation:
    return ToolInvocation(tool_name=tool, arguments=args, source=source)


class _StubAgent(BaseAgent):
    """可配置拒收原因的最小 Agent 桩；name 用动态子类隔离（name 是类属性，同类多实例会互相覆盖）。"""

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

    def finish(self, ledger: TaskLedger, *, failed: bool = False) -> None:
        for task_id, _ in self.received:
            ledger.update(task_id, "running", summary="开始执行")
            ledger.update(task_id, "failed" if failed else "succeeded", summary="执行失败" if failed else "全部完成")


class _Rig:
    def __init__(self, *agents: BaseAgent) -> None:
        self.bus = EventBus(enable_stats=False)
        self.registry = ToolRegistry(event_bus=self.bus)
        self.ledger = TaskLedger(event_bus=self.bus)
        self.manager = AgentManager()
        for agent in agents:
            self.manager.register(agent)
        self.registry.register_provider(build_agent_control_provider(self.manager, self.ledger, event_bus=self.bus))

    async def call(self, tool: str, args: dict, source: str = "agent_a"):
        return await self.registry.invoke(_inv(tool, args, source))


@pytest.fixture
def rig() -> _Rig:
    """agent_a（调用方）+ agent_b（接收方）。"""
    return _Rig(_StubAgent("agent_a"), _StubAgent("agent_b"))


def _agent(rig: _Rig, name: str) -> _StubAgent:
    return rig.manager.get_agent_by_name(name)  # type: ignore[return-value]


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 2.0) -> None:
    """轮询等待事件经后台调度送达，代替固定 sleep：全量高负载下事件迟到属正常时序，不应误判为缺陷。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"等待事件送达超时（{timeout_s}s）")
        await asyncio.sleep(0.005)


async def test_delegation_full_chain_and_status_query(rig: _Rig) -> None:
    received: List[TaskChangedPayload] = []

    async def on_changed(name: str, payload: TaskChangedPayload, source: str) -> None:
        received.append(payload)

    rig.bus.on(CoreEvents.TASK_CHANGED, on_changed, model_class=TaskChangedPayload)
    b = _agent(rig, "agent_b")

    res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})
    task_id = res.structured_content["task_id"]
    assert (
        res.success and res.structured_content["accepted"] is True and res.structured_content["executor"] == "agent_b"
    )
    assert b.received == [(task_id, "盖一座房子")]
    record = rig.ledger.get(task_id)
    assert (record.initiator, record.executor, record.source) == ("agent_a", "agent_b", "agent")

    # 进行中可查：状态 + 快照（指令在快照里）；尚未开始执行
    status = (await rig.call("framework_task_status", {"task_id": task_id})).structured_content
    assert (status["status"], status["initiator"], status["executor"]) == ("accepted", "agent_a", "agent_b")
    assert status["snapshot"]["instruction"] == "盖一座房子"

    b.finish(rig.ledger)
    await _wait_until(lambda: any(p.task_id == task_id and p.status == "succeeded" for p in received))
    # 受理广播一次起点（落库记受理行用）；accepted→running 属安静组（发起方已持回执，不唤醒）；终态必达
    assert [p.status for p in received if p.task_id == task_id] == ["accepted", "succeeded"]
    assert received[0].snapshot == {"instruction": "盖一座房子", "own_words": "盖一座房子"}
    assert all(p.initiator == "agent_a" and p.executor == "agent_b" for p in received)
    gone = await rig.call("framework_task_status", {"task_id": task_id})  # 终态已移除
    assert gone.success is False and "任务不存在" in gone.error_message


async def test_execution_failure_ends_in_failed_terminal_after_being_registered(rig: _Rig) -> None:
    task_id = (
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "会失败的工作"})
    ).structured_content["task_id"]
    statuses: List[str] = []

    async def on_changed(name: str, payload: TaskChangedPayload, source: str) -> None:
        statuses.append(payload.status)

    rig.bus.on(CoreEvents.TASK_CHANGED, on_changed, model_class=TaskChangedPayload)
    _agent(rig, "agent_b").finish(rig.ledger, failed=True)
    await _wait_until(lambda: bool(statuses))
    assert statuses[-1] == "failed" and rig.ledger.get(task_id) is None
    unknown = await rig.call("framework_task_status", {"task_id": "nope-123"})
    assert unknown.success is False and "任务不存在" in unknown.error_message


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"agent": "ghost", "instruction": "干活"}, "不在名册"),
        ({"agent": "agent_b", "instruction": "干活"}, "忙不过来"),  # 目标明确拒收
        ({"agent": "agent_a", "instruction": "自己干"}, "自派"),
    ],
)
async def test_acceptance_failures_do_not_register(args: dict, needle: str) -> None:
    rig = _Rig(_StubAgent("agent_a"), _StubAgent("agent_b", refuse="忙不过来"))
    res = await rig.call("framework_delegate", args)
    assert res.success is False and needle in res.error_message and len(rig.ledger) == 0
    # 受理失败是"对方/调用方纠正后能重派"的业务拒绝，不是工具坏了——不进熔断计数
    assert res.failure_kind == "business"


async def test_business_rejection_does_not_trip_the_tool() -> None:
    """连续业务拒绝不计入健康计数、不熔断：熔断阈值 3 次。

    未分类的失败会被当成执行故障，连续三次就能把 framework_delegate 摘掉——
    委派能力从此不可用（本缺陷的原始形态）。
    """
    rig = _Rig(_StubAgent("agent_a"), _StubAgent("agent_b", refuse="忙不过来"))
    for _ in range(4):
        res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "干活"})
        assert res.success is False and res.failure_kind == "business"
    assert not rig.registry.is_tripped("framework_delegate")
    state = rig.registry.health_state_of("framework_delegate")
    assert state is not None and state.consecutive_failures == 0  # 服务已应答：故障计数保持 0


async def test_prompt_rejection_is_a_business_failure() -> None:
    """递话被拒（对方通道堵了）是业务拒绝：标 business、不熔断。"""

    class _PromptRefuser(_StubAgent):
        def receive_prompt(self, *, content: str, source: str = "") -> bool:
            return False

    rig = _Rig(_StubAgent("agent_a"), _PromptRefuser("agent_b"))
    res = await rig.call("framework_prompt", {"agent": "agent_b", "content": "在吗"})
    assert res.success is False and "拒收递话" in res.error_message
    assert res.failure_kind == "business"
    assert not rig.registry.is_tripped("framework_prompt")


async def test_missing_task_ledger_stays_an_execution_failure() -> None:
    """任务基建未装配是依赖不可用（真执行故障）：不标业务拒绝，照常计入故障计数。

    反向约束：若把真故障也标成 business，健康监控就彻底失效（永不熔断）。
    """
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager()
    manager.register(_StubAgent("agent_a"))
    registry.register_provider(build_agent_control_provider(manager, None, event_bus=bus))

    res = await registry.invoke(_inv("framework_task_status", {"task_id": "deleg_1700000000000_1"}))
    assert res.success is False and res.failure_kind != "business"
    state = registry.health_state_of("framework_task_status")
    assert state is not None and state.consecutive_failures == 1  # 真故障照常计数


async def test_default_refusal_without_receive_entry_is_acceptance_failure() -> None:
    class _NoEntry(_StubAgent):
        receive_delegation = BaseAgent.receive_delegation  # type: ignore[assignment]
        receive_prompt = BaseAgent.receive_prompt  # type: ignore[assignment]

    rig = _Rig(_StubAgent("agent_a"), _NoEntry("agent_c"))
    res = await rig.call("framework_delegate", {"agent": "agent_c", "instruction": "试试"})
    assert res.success is False and "拒收" in res.error_message and len(rig.ledger) == 0
    res = await rig.call("framework_prompt", {"agent": "agent_c", "content": "试试"})
    assert res.success is False and "拒收" in res.error_message


async def test_empty_target_resolves_sole_game_agent_or_demands_a_name(rig: _Rig) -> None:
    res = await rig.call("framework_delegate", {"agent": "", "instruction": "去挖矿"})
    assert res.success and res.structured_content["executor"] == "agent_b"
    assert _agent(rig, "agent_b").received[0][1] == "去挖矿" and len(rig.ledger) == 1

    ambiguous = _Rig(_StubAgent("agent_a"), _StubAgent("agent_b"), _StubAgent("agent_c"))
    res = await ambiguous.call("framework_delegate", {"agent": "", "instruction": "去挖矿"})
    assert res.success is False and "点名" in res.error_message and "agent_b" in res.error_message
    assert len(ambiguous.ledger) == 0  # 不猜目标，避免派错游戏


async def test_prompt_is_acknowledged_without_ledger_entry_and_broadcast(rig: _Rig) -> None:
    prompted: List[AgentPromptedPayload] = []

    async def on_prompted(name: str, payload: AgentPromptedPayload, source: str) -> None:
        prompted.append(payload)

    rig.bus.on(CoreEvents.AGENT_PROMPTED, on_prompted, model_class=AgentPromptedPayload)
    res = await rig.call("framework_prompt", {"agent": "agent_b", "content": "先去东侧看看"})
    await _wait_until(lambda: len(prompted) == 1)

    assert res.success and res.structured_content == {"delivered": True, "executor": "agent_b"}  # 无任务号
    assert _agent(rig, "agent_b").prompts_received == [("先去东侧看看", "agent_a")]
    assert [(p.target, p.content, p.source) for p in prompted] == [("agent_b", "先去东侧看看", "agent_a")]
    assert len(rig.ledger) == 0  # 记账分家


async def test_prompt_target_resolution_matches_delegate(rig: _Rig) -> None:
    ghost = await rig.call("framework_prompt", {"agent": "ghost", "content": "在吗"})
    assert ghost.success is False and "不在名册" in ghost.error_message
    selfie = await rig.call("framework_prompt", {"agent": "agent_a", "content": "自言自语"})
    assert selfie.success is False and "自派" in selfie.error_message
    blank_target = await rig.call("framework_prompt", {"content": "留空目标"})
    assert blank_target.success and blank_target.structured_content["executor"] == "agent_b"
    assert _agent(rig, "agent_b").prompts_received[-1] == ("留空目标", "agent_a")
    blank_content = await rig.call("framework_prompt", {"agent": "agent_b", "content": ""})
    assert blank_content.success is False and "content" in blank_content.error_message
    assert len(rig.ledger) == 0


# 委派队列：目标忙时普通件自动排队（上限 5，满员拒收附清单）；急件插队/置顶；
# 在途件走到终态时按序送达。framework_prompt 不受队列影响


async def test_busy_target_queues_normal_delegation_and_delivers_on_terminal(rig: _Rig) -> None:
    """目标忙时新普通件不拒收：入队带排位，在途件终态后按序送达。"""
    b = _agent(rig, "agent_b")
    await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})

    res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "再去挖个矿"})
    assert res.success and res.structured_content["accepted"] is True
    assert res.structured_content["queued"] is True and res.structured_content["queue_position"] == 1
    queued_id = res.structured_content["task_id"]
    assert rig.ledger.get(queued_id).status == "queued"
    assert len(b.received) == 1, "排队的还没送到执行方"

    # 在途不拦递话：补充要求的合法通道照常
    res = await rig.call("framework_prompt", {"agent": "agent_b", "content": "屋顶用石砖"})
    assert res.success and rig.ledger.get(queued_id).status == "queued"

    # 在途件终态：排队件自动送达（账面转回受理态，执行方开跑再写 running）
    b.finish(rig.ledger)
    await _wait_until(lambda: len(b.received) == 2)
    assert b.received[-1] == (queued_id, "再去挖个矿")
    assert rig.ledger.get(queued_id).status == "accepted"


async def test_queue_full_refusal_lists_queue_and_progress(rig: _Rig) -> None:
    """队列排满后普通件拒收：理由带在途那件的原话与已知进展 + 全部队列清单，不带执行侧判断原文。"""
    first = (
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})
    ).structured_content["task_id"]
    rig.ledger.update(first, "running", summary="开始", snapshot={"instruction": "盖一座房子", "goals": "地基已挖好"})
    rig.ledger.annotate(
        first, {REPLY_NOTE_KEY: "先 maicraft_place(12, 64, -30) 再说", REPLY_NOTE_AT_KEY: 1793977796538}
    )

    for index in range(5):
        res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": f"排队第{index}件"})
        assert res.success and res.structured_content["queue_position"] == index + 1

    refused = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "第6件排不进"})
    assert refused.success is False and refused.failure_kind == "business"
    assert "排满 5 件" in refused.error_message
    assert "地基已挖好" in refused.error_message  # 在途已知进展照带
    assert "排队第0件" in refused.error_message and "排队第4件" in refused.error_message  # 清单带各条原话
    assert "maicraft_place" not in refused.error_message and "1793977796538" not in refused.error_message
    assert "own_words" not in refused.error_message
    assert len(rig.ledger) == 6  # 拒单不登记（1 在途 + 5 排队）


async def test_ledger_keeps_full_instruction_and_queue_lists_only_own_words(rig: _Rig) -> None:
    """账面存指令全文（附带的来源对话也在），另记发起方原话；队列清单与拒收理由只回原话：
    原话长也不截，附带的观众原话与取舍规则不塞回去。"""
    own = "把营地棚子装一扇木门，" + "门外垫平台阶，" * 40 + "最后一句是收尾要求"
    assert len(own) > 200
    instruction = (
        DELEGATE_HEAD + own + DELEGATE_DIALOGUE_HEAD + '逐字引用]\n[{"content": "观众甲: 要橡木门"}]\n取舍规则'
    )
    first = (await rig.call("framework_delegate", {"agent": "agent_b", "instruction": instruction})).structured_content[
        "task_id"
    ]

    snapshot = rig.ledger.get(first).snapshot
    assert snapshot == {"instruction": instruction, "own_words": own}
    status = (await rig.call("framework_task_status", {"task_id": first})).structured_content
    assert status["snapshot"]["instruction"] == instruction
    queued = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "换个活"})
    assert queued.success, "目标忙时改排队，不再是拒收"

    for index in range(4):  # 排满触发拒收理由，核对清单只回原话
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": f"凑数{index}"})
    refused = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "挤一挤"})
    assert refused.success is False
    assert f"「{own}」" in refused.error_message
    assert "观众甲" not in refused.error_message and "取舍规则" not in refused.error_message
    assert "own_words" not in refused.error_message


async def test_waiting_in_flight_still_queues_and_full_refusal_lists_it(rig: _Rig) -> None:
    """等定夺的在途不拦排队；队列满时的拒收理由照常带在途那件与队列清单。"""
    first = (
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "把女仆救回来"})
    ).structured_content["task_id"]
    rig.ledger.update(first, "waiting_for_decision", summary="卡住了，等主播拿主意")

    busy = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "放一只新女仆"})
    assert busy.success and busy.structured_content["queued"] is True, "等定夺也算忙，普通件排队"

    for index in range(4):
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": f"凑数{index}"})
    full = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "挤一挤"})
    assert full.success is False
    assert "排满 5 件" in full.error_message and "把女仆救回来" in full.error_message


class _SelectiveAgent(_StubAgent):
    """只对特定指令拒收的桩：排队的件送达被拒时，账面当场转 failed 不留孤儿。"""

    def __init__(self, name: str, *, refuse_when: str) -> None:
        super().__init__(name)
        self._refuse_when = refuse_when

    def receive_delegation(self, *, instruction: str, task_id: str):
        if self._refuse_when in instruction:
            return f"做不了：{self._refuse_when}"
        return super().receive_delegation(instruction=instruction, task_id=task_id)


async def test_queued_delivery_refusal_becomes_failed_terminal(rig: _Rig) -> None:
    """排队件送达被目标拒收：账面当场转 failed 带原因，不留排不到终态的孤儿条目。"""
    rig = _Rig(_StubAgent("agent_a"), _SelectiveAgent("agent_b", refuse_when="旧楼"))
    await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})
    queued = (
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "把旧楼拆了"})
    ).structured_content["task_id"]
    assert rig.ledger.get(queued).status == "queued"

    _agent(rig, "agent_b").finish(rig.ledger)  # 在途件终态 → 出队送达 → 被拒
    await _wait_until(lambda: rig.ledger.get(queued) is None)
    assert len(rig.ledger) == 0


async def test_cancelled_queued_entry_is_skipped_on_drain(rig: _Rig) -> None:
    """排队件被撤（账面已了结）后出队时跳过，队列里的下一件照常送达。"""
    b = _agent(rig, "agent_b")
    await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})
    first_queued = (
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "排第一的活"})
    ).structured_content["task_id"]
    second_queued = (
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "排第二的活"})
    ).structured_content["task_id"]

    # 排队件撤单：不经执行 Agent（它从没见过这条指令），账面直接 cancelled
    res = await rig.call("framework_task_cancel", {"task_id": first_queued}, source="streamer")
    assert res.success and res.structured_content["cancelled"] is True
    assert rig.ledger.get(first_queued) is None

    b.finish(rig.ledger)  # 在途件终态 → 出队：撤掉的跳过，第二件送达
    await _wait_until(lambda: len(b.received) == 2)
    assert b.received[-1] == (second_queued, "排第二的活")


    def __init__(self, *, live_active: bool = True) -> None:
        bus = EventBus(enable_stats=False)
        registry = ToolRegistry(event_bus=bus)
        ledger = TaskLedger(event_bus=bus)
        llm = MagicMock()
        llm.chat_messages = AsyncMock(side_effect=[])
        # 游戏连上了：maicraft 有工具才接委派（没连上时直接拒收，另有测试）。这里的工具只用来过这道门，
        # 没有连接，宿主不会经它读 MaiCraft——私有 MCP 面经构造注入。
        self.minecraft = MinecraftAgent(
            MinecraftConfig(mcp=McpServerConfig(enabled=False)),
            llm_manager=llm,
            event_bus=bus,
            tool_registry=registry,
            task_tracker=TaskTracker(registry, ledger, poll_interval_ms=20),
            mcp_provider=SimpleNamespace(list_tools=lambda: ["maicraft_observe"]),
            live_active=live_active,
        )
        super().__init__(self.minecraft)
        # 复用上面 _Rig 构造出的 bus/registry/ledger 会与 Agent 持有的不一致，这里改为沿用 Agent 的那一套
        self.bus, self.registry, self.ledger = bus, registry, ledger
        registry.register_provider(build_agent_control_provider(self.manager, ledger, event_bus=bus))

    async def delegate(self, instruction: str = "建一座木头房子") -> str:
        res = await self.call(
            "framework_delegate", {"agent": "minecraft", "instruction": instruction}, source="streamer"
        )
        assert res.success is True
        return res.structured_content["task_id"]

    def absorb_into_batch(self) -> None:
        """复刻 _run_task 的消息 flush：委派指令吸收进本批并标记 running。"""
        queue = self.minecraft._message_queue
        while queue:
            tid, _content = queue.popleft()
            if tid:
                self.minecraft._delegations.absorb(tid)
        self.minecraft._delegations.mark_running()

    def queued_notices(self) -> List[str]:
        return [content for tid, content in self.minecraft._message_queue if not tid]


@pytest.fixture
def mc() -> _McRig:
    return _McRig()


@pytest.fixture
def mc_offline() -> _McRig:
    """收工态的 minecraft agent（没开播）：委派要被拒收并回一句理由。"""
    return _McRig(live_active=False)


async def test_offline_minecraft_refuses_delegation_with_reason_and_leaves_no_zombie(mc_offline: _McRig) -> None:
    """收工态拒收：理由回给派活的一方，账面不出现"已受理"的僵尸条目。

    缺陷形态：收工态分支返回 None（框架把 None 当受理成功）→ 账面登记 accepted，
    可执行侧从没收到这条指令——账面有、没人做、也不报错。
    """
    res = await mc_offline.call(
        "framework_delegate", {"agent": "minecraft", "instruction": "建一座木头房子"}, source="streamer"
    )
    assert res.success is False
    assert "收工" in res.error_message  # 拒收理由原样回传，不是一句"受理成功"
    assert res.failure_kind == "business"  # 对方拒收属业务结果，不进熔断计数
    assert len(mc_offline.ledger) == 0  # 拒收不登记：没有僵尸条目
    assert len(mc_offline.minecraft._message_queue) == 0  # 执行侧确实没收到指令


async def test_minecraft_receives_delegation_and_reports_terminal(mc: _McRig) -> None:
    task_id = await mc.delegate()
    record = mc.ledger.get(task_id)
    assert (record.initiator, record.executor) == ("streamer", "minecraft")
    assert list(mc.minecraft._message_queue) == [(task_id, "建一座木头房子")]  # 指令入队带任务号

    mc.absorb_into_batch()
    assert mc.ledger.get(task_id).status == "running"
    await mc.minecraft._handle_report("delivery", "房子建好了", scene="0,0,0")
    assert mc.ledger.get(task_id) is None  # delivery 后终态移除


async def test_minecraft_hard_cancel_clears_ledger_tracking_and_notifies(mc: _McRig) -> None:
    task_id = await mc.delegate()
    mc.absorb_into_batch()

    assert mc.minecraft.cancel_task(task_id, source="operator") is True
    assert mc.ledger.get(task_id) is None
    assert mc.minecraft._delegations.finished_ids == ()
    assert any(task_id in notice and "已被取消" in notice for notice in mc.queued_notices())  # 停手通知已入队

    mc.minecraft._delegations.finish("succeeded", summary="迟到的收尾")  # 追踪清单已清空，账面不被污染
    assert mc.ledger.get(task_id) is None
    assert mc.minecraft.cancel_task(task_id, source="operator") is False  # 已终态再取消（REST 映射 404）


async def test_minecraft_cancel_clears_suspended_zombie(mc: _McRig) -> None:
    task_id = await mc.delegate()
    mc.absorb_into_batch()
    await mc.minecraft._suspend_with_report("迷宫尽头卡住，需要决策")
    assert mc.ledger.get(task_id).status == "waiting_for_decision"

    assert mc.minecraft.cancel_task(task_id, source="operator") is True
    assert mc.ledger.get(task_id) is None and mc.minecraft._delegations.finished_ids == ()


async def test_framework_task_cancel_reaches_minecraft(mc: _McRig) -> None:
    """撤单工具走全链路：provider → minecraft.cancel_task → 账面 cancelled + 停手通知入队。"""
    task_id = await mc.delegate()
    mc.absorb_into_batch()

    res = await mc.call("framework_task_cancel", {"task_id": task_id}, source="streamer")
    assert res.success and res.structured_content["cancelled"] is True
    assert mc.ledger.get(task_id) is None
    assert any(task_id in notice and "已被取消" in notice for notice in mc.queued_notices())

    # 撤掉在途后同目标可再派
    res = await mc.call("framework_delegate", {"agent": "minecraft", "instruction": "再挖个矿"}, source="streamer")
    assert res.success


async def test_escalation_waits_for_decision_and_mcp_recovery_resumes(mc: _McRig) -> None:
    """escalation = 受阻上报待定夺：账面 waiting_for_decision（非 failed 终态），追踪保留；MCP 恢复后解锁续跑直至交付。"""
    task_id = await mc.delegate()
    mc.absorb_into_batch()
    await mc.minecraft._handle_report("escalation", "连接失败受阻", scene="")

    record = mc.ledger.get(task_id)
    assert record is not None and record.status == "waiting_for_decision"
    assert record.snapshot.get("waiting_for_instruction") is True
    assert mc.minecraft._delegations.finished_ids == (task_id,) and mc.minecraft._task_suspended is True

    mc.minecraft._on_mcp_recovered()
    assert mc.minecraft._task_suspended is False and mc.minecraft._wake_event.is_set()
    assert any("连接已恢复" in notice for notice in mc.queued_notices())

    mc.minecraft._message_queue.clear()  # 通知被下一步 flush 吸收，旧委派重写 running（账面复活）
    mc.minecraft._delegations.mark_running()
    assert mc.ledger.get(task_id).status == "running"
    await mc.minecraft._handle_report("delivery", "现场评估后继续并完成", scene="")
    assert mc.ledger.get(task_id) is None


