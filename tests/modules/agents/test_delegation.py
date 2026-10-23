"""委派 / 递话原语（framework_delegate / framework_prompt / framework_task_status）

受理失败与任务失败分开（受理失败不登记）；目标留空解析为唯一游戏 Agent；递话受理不记账；
最后用真实 minecraft Agent 验证入队带任务号、批次状态写回、硬取消与断连恢复续跑。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.agents.base import BaseAgent
from src.modules.agents.control import build_agent_control_provider
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import AgentPromptedPayload, LiveEndedPayload, TaskChangedPayload
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


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
    await asyncio.sleep(0.03)
    # accepted→running 属安静组（发起方已持回执，不唤醒）；终态必达
    assert [p.status for p in received if p.task_id == task_id] == ["succeeded"]
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
    await asyncio.sleep(0.03)
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
    await asyncio.sleep(0.03)

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


# 在途拒受理：目标名下有未终态委派单即拒（队列深度 = 1），framework_prompt 不受此限


async def test_delegate_rejected_while_target_has_in_flight_delegation(rig: _Rig) -> None:
    first = (
        await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})
    ).structured_content["task_id"]

    # 不判语义相同：指令不同的新单同样拒，理由带在途任务号 + 指令原文 + 受理时长
    res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "再去挖个矿"})
    assert res.success is False
    assert first in res.error_message and "盖一座房子" in res.error_message and "秒" in res.error_message
    assert len(rig.ledger) == 1  # 拒单不登记

    # 拒绝理由带已知进展（快照里除指令外的字段有就带）
    rig.ledger.update(first, "running", summary="开始", snapshot={"instruction": "盖一座房子", "goals": "地基已挖好"})
    res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "换个活"})
    assert res.success is False and "地基已挖好" in res.error_message

    # 在途不拦递话：补充要求的合法通道照常
    res = await rig.call("framework_prompt", {"agent": "agent_b", "content": "屋顶用石砖"})
    assert res.success and len(rig.ledger) == 1

    # 终态后放行新单
    _agent(rig, "agent_b").finish(rig.ledger)
    res = await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "再来一件"})
    assert res.success and res.structured_content["accepted"] is True


async def test_in_flight_gate_is_per_target(rig: _Rig) -> None:
    """门只看目标名下：agent_b 在途不挡别人的单。"""
    await rig.call("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"})
    res = await rig.call("framework_delegate", {"agent": "agent_a", "instruction": "闲着照常接"}, source="streamer")
    assert res.success and res.structured_content["executor"] == "agent_a"


# 真实 minecraft Agent：入队带任务号、批次状态写回、硬取消、断连恢复续跑


class _McRig(_Rig):
    def __init__(self) -> None:
        bus = EventBus(enable_stats=False)
        registry = ToolRegistry(event_bus=bus)
        ledger = TaskLedger(event_bus=bus)
        llm = MagicMock()
        llm.chat_messages = AsyncMock(side_effect=[])
        self.minecraft = MinecraftAgent(
            MinecraftConfig(mcp=McpServerConfig(enabled=False)),
            llm_manager=llm,
            event_bus=bus,
            tool_registry=registry,
            task_tracker=TaskTracker(registry, ledger, poll_interval_ms=20),
        )
        # 游戏连上了：maicraft 有工具才接委派（没连上时直接拒收，另有测试）。这里的工具只用来过这道门，
        # 没有连接，宿主不会经它读 MaiCraft。
        self.minecraft._mcp_provider = SimpleNamespace(list_tools=lambda: ["maicraft_observe"])
        super().__init__(self.minecraft)
        # 复用上面 _Rig 构造出的 bus/registry/ledger 会与 Agent 持有的不一致，这里改为沿用 Agent 的那一套
        self.bus, self.registry, self.ledger = bus, registry, ledger
        registry.register_provider(build_agent_control_provider(self.manager, ledger))

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
                self.minecraft._delegated_batch_ids.append(tid)
        self.minecraft._mark_delegated_running()

    def queued_notices(self) -> List[str]:
        return [content for tid, content in self.minecraft._message_queue if not tid]


@pytest.fixture
def mc() -> _McRig:
    return _McRig()


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
    assert mc.minecraft._delegated_batch_ids == [] and mc.minecraft._delegated_finished_ids == []
    assert any(task_id in notice and "已被取消" in notice for notice in mc.queued_notices())  # 停手通知已入队

    mc.minecraft._finish_delegated("succeeded", summary="迟到的收尾")  # 追踪清单已清空，账面不被污染
    assert mc.ledger.get(task_id) is None
    assert mc.minecraft.cancel_task(task_id, source="operator") is False  # 已终态再取消（REST 映射 404）


async def test_minecraft_cancel_clears_suspended_zombie(mc: _McRig) -> None:
    task_id = await mc.delegate()
    mc.absorb_into_batch()
    await mc.minecraft._suspend_with_report("迷宫尽头卡住，需要决策")
    assert mc.ledger.get(task_id).status == "waiting_for_decision"

    assert mc.minecraft.cancel_task(task_id, source="operator") is True
    assert mc.ledger.get(task_id) is None and mc.minecraft._delegated_finished_ids == []


async def test_escalation_waits_for_decision_and_mcp_recovery_resumes(mc: _McRig) -> None:
    """escalation = 受阻上报待定夺：账面 waiting_for_decision（非 failed 终态），追踪保留；MCP 恢复后解锁续跑直至交付。"""
    task_id = await mc.delegate()
    mc.absorb_into_batch()
    await mc.minecraft._handle_report("escalation", "连接失败受阻", scene="")

    record = mc.ledger.get(task_id)
    assert record is not None and record.status == "waiting_for_decision"
    assert record.snapshot.get("waiting_for_instruction") is True
    assert mc.minecraft._delegated_finished_ids == [task_id] and mc.minecraft._task_suspended is True

    mc.minecraft._on_mcp_recovered()
    assert mc.minecraft._task_suspended is False and mc.minecraft._wake_event.is_set()
    assert any("连接已恢复" in notice for notice in mc.queued_notices())

    mc.minecraft._message_queue.clear()  # 通知被下一步 flush 吸收，旧委派重写 running（账面复活）
    mc.minecraft._mark_delegated_running()
    assert mc.ledger.get(task_id).status == "running"
    await mc.minecraft._handle_report("delivery", "现场评估后继续并完成", scene="")
    assert mc.ledger.get(task_id) is None


async def test_live_ended_settles_suspended_delegations(mc: _McRig) -> None:
    """下播强制结算：waiting_for_decision 悬置账随收工置 cancelled（下播自动收尾）。"""
    changed: list[TaskChangedPayload] = []

    async def _capture(name: str, payload: TaskChangedPayload, source: str) -> None:
        changed.append(payload)

    mc.bus.on(CoreEvents.TASK_CHANGED, _capture, model_class=TaskChangedPayload)

    task_id = await mc.delegate()
    mc.absorb_into_batch()
    await mc.minecraft._handle_report("escalation", "迷宫尽头卡住，需要决策", scene="")
    record = mc.ledger.get(task_id)
    assert record is not None and record.status == "waiting_for_decision"

    await mc.minecraft._on_live_ended(
        CoreEvents.LIVE_ENDED,
        LiveEndedPayload(live_session_id=1, reason="手动下播"),
        source="LiveSessionManager",
    )

    assert mc.ledger.get(task_id) is None, "悬置条目已强制结算移除"
    assert mc.minecraft._delegated_finished_ids == [] and mc.minecraft._delegated_batch_ids == []
    await asyncio.sleep(0.05)  # task.changed 经后台任务调度广播，让一拍再断言
    assert changed[-1].status == "cancelled"
    assert "下播自动收尾" in changed[-1].summary


# ---------------------------------------------------------------------------
# 遥控型 Agent 在场：不接委派的 Agent 不构成留空目标的歧义
# ---------------------------------------------------------------------------


class _RemoteStubAgent(BaseAgent):
    """遥控型游戏 Agent 桩：不覆写接收入口（默认拒收委派与递话），只靠工具被驱动。"""

    name = "remote_game"
    description = "遥控型游戏桩"

    def list_tools(self):
        return []


async def test_empty_target_ignores_agents_without_receive_entry(rig: _Rig) -> None:
    """留空目标只在覆写了接收入口的名册里解析：同时启用一个遥控型 Agent 不算第二个候选。"""
    rig.manager.register(_RemoteStubAgent())
    provider = rig.registry._providers[0]  # noqa: SLF001 - 取已注册的 framework provider 读名册

    res = await rig.call("framework_delegate", {"agent": "", "instruction": "去挖矿"})

    assert res.success is True and res.structured_content["executor"] == "agent_b"
    assert _agent(rig, "agent_b").received[0][1] == "去挖矿"
    enum = provider._delegate_spec().parameters_schema["properties"]["agent"].get("enum", [])  # noqa: SLF001
    assert "remote_game" not in enum and "agent_b" in enum


async def test_empty_target_with_no_receivers_fails_clearly() -> None:
    """名册里没有能接收委派的 Agent：留空目标受理失败，错误信息指向"直接用它自己的操作工具"。"""
    no_receiver = _Rig(_StubAgent("agent_a"), _RemoteStubAgent())
    res = await no_receiver.call("framework_delegate", {"agent": "", "instruction": "按一下"})

    assert res.success is False
    assert "没有能接收的 Agent" in res.error_message and "操作工具" in res.error_message
    assert len(no_receiver.ledger) == 0


async def test_framework_guide_lists_delegable_agents_or_stays_silent(rig: _Rig) -> None:
    """framework 工具面说明：有能接收委派的 Agent 才给（读者自己不算），否则空串。"""
    provider = rig.registry._providers[0]  # noqa: SLF001 - 取已注册的 framework provider

    guide = provider.guide_for("agent_a")
    assert "agent_b=stub agent_b" in guide and "agent_a=" not in guide
    assert "framework_delegate" in guide and "framework_prompt" in guide and "framework_task_status" in guide
    # framework 工具面未声明名单 → 默认仅主播可见：主播读到的说明列出全部能接收委派的 Agent
    streamer_guide = provider.guide_for("streamer")
    assert "agent_a=stub agent_a" in streamer_guide and "agent_b=stub agent_b" in streamer_guide
    assert rig.registry.guides(for_agent="streamer") == [("framework", streamer_guide)]
    assert rig.registry.guides(for_agent="agent_a") == []

    silent = _Rig(_StubAgent("solo"), _RemoteStubAgent())
    assert silent.registry._providers[0].guide_for("solo") == ""  # noqa: SLF001
