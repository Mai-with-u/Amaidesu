"""MinecraftAgent 动态主从（自主领航模式）全链路测试。

覆盖：
- 能力声明（supports_autonomous_lead）、工厂自动注入 lead_coordinator 与 can_take_lead 三要素校验；
- enter_lead / yield_lead 生命周期（幂等、交接简报与开局资料、收尾进度摘要、取消在跑目标、live.ended 复位）；
- 主控态自主循环语义（不发 undelivered、有待办继续推进、无待办有界等待、防空转保护）；
- 直接主导游戏公屏（普通玩家闲聊立即唤醒并用 chat 回应）；
- 非强杀观众建议 receive_suggestion（不取消在跑目标、来源原话去重）；
- 只读台词回执 receive_spoken_echo（严格零唤醒、有界去重截断、下步拼接）；
- 工具面与提示词切换（framework_commentary 可见、minecraft_report 隐藏、主播侧 minecraft_chat_send 业务拒绝与 guide_for 切换）；
- 运行态保护（MCP 断连与 LLM 连续失败/致命故障平滑降级回主播主控，重连恢复不乱自启）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.lead_mode import (
    LEAD_MODE_PROMPT_SECTION,
    SpokenEchoBuffer,
    format_lead_handover_brief,
    format_suggestion_message,
)
from src.agents.minecraft.tools import STREAMER_GUIDE, STREAMER_LEAD_GUIDE
from src.modules.agents import (
    AgentManager,
    BaseAgent,
    CommentaryAck,
    CommentaryRequest,
    LeadCoordinator,
    LeadHandoverContext,
    build_agent_control_provider,
)
from src.modules.agents.deps import AgentDeps
from src.modules.agents.factory import instantiate_agent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentLeadChangedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.events.payloads.live import LiveEndedPayload
from src.modules.llm.payload import Response
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger

from .minecraft_v1_fakes import (
    FakeMaicraft,
    ScriptLlm,
    _call,
    _resp,
    _texts,
    _wait_until,
    assemble_minecraft_agent,
    make_minecraft_agent,
    offline_config,
)


class _StreamerStub(BaseAgent):
    """最小主播桩（仅用于在 AgentManager 中占位，不反向依赖 streamer 包）。"""

    name = "streamer"
    description = "测试主播"

    def __init__(self) -> None:
        super().__init__(heartbeat_interval_ms=0)

    def list_tools(self) -> Iterable[ToolSpec]:
        return []


class _McpDisconnectableProvider(FakeMaicraft):
    """支持模拟 MCP 断连与重连恢复的 FakeMaicraft 变体（零私有 patch）。"""

    def __init__(self) -> None:
        super().__init__()
        self.tools_exposed = True
        self.on_connection_changed: Optional[Any] = None

    def list_tools(self) -> List[ToolSpec]:
        if not self.tools_exposed:
            return []
        return super().list_tools()


def _make_lead_env(
    *,
    server: FakeMaicraft,
    script: Any,
    fake_clock: Any,
    live: bool = True,
    mcp_client: Optional[Any] = None,
    lead_idle_wakeup_s: float = 30.0,
    lead_max_empty_turns: int = 2,
    lead_llm_failure_threshold: int = 3,
    lead_llm_retry_delay_s: float = 0.0,
    lead_llm_cooldown_s: float = 60.0,
) -> Dict[str, Any]:
    """同步构造测试环境句柄（由协程内启动 agent 与 manager）。"""
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus, clock=fake_clock.now_ms)
    manager = AgentManager()
    manager.register(_StreamerStub())
    coordinator = LeadCoordinator(manager, event_bus=bus, clock=fake_clock.now_ms)
    return {
        "bus": bus,
        "registry": registry,
        "ledger": ledger,
        "manager": manager,
        "coordinator": coordinator,
        "server": server,
        "script": script,
        "live": live,
        "mcp_client": mcp_client or SimpleNamespace(connected=True),
        "clock": fake_clock,
        "lead_idle_wakeup_s": lead_idle_wakeup_s,
        "lead_max_empty_turns": lead_max_empty_turns,
        "lead_llm_failure_threshold": lead_llm_failure_threshold,
        "lead_llm_retry_delay_s": lead_llm_retry_delay_s,
        "lead_llm_cooldown_s": lead_llm_cooldown_s,
    }


async def _start_lead_env(env: Dict[str, Any]) -> tuple[MinecraftAgent, ScriptLlm, Any]:
    """启动完整的动态主从联调环境：AgentManager + LeadCoordinator + AgentControlProvider + MinecraftAgent。"""
    clock = env["clock"]
    agent, llm, _ = await make_minecraft_agent(
        env["server"],
        env["script"],
        registry=env["registry"],
        bus=env["bus"],
        live=env["live"],
        mcp_client=env["mcp_client"],
        clock=lambda: clock.now_ms() / 1000.0,
        lead_coordinator=env["coordinator"],
        lead_idle_wakeup_s=env["lead_idle_wakeup_s"],
        lead_max_empty_turns=env["lead_max_empty_turns"],
        lead_llm_failure_threshold=env["lead_llm_failure_threshold"],
        lead_llm_retry_delay_s=env["lead_llm_retry_delay_s"],
        lead_llm_cooldown_s=env["lead_llm_cooldown_s"],
        config=offline_config(events_wait_ms=1000),
    )
    env["manager"].register(agent)
    control_provider = build_agent_control_provider(
        env["manager"],
        env["ledger"],
        event_bus=env["bus"],
        clock=clock.now_ms,
        lead_coordinator=env["coordinator"],
    )
    env["registry"].register_provider(control_provider)
    env["control_provider"] = control_provider
    return agent, llm, control_provider


async def _stop_lead_env(env: Dict[str, Any], agent: MinecraftAgent) -> None:
    await agent.stop()
    await env["bus"].cleanup()


async def _settle_loop(bus: EventBus, turns: int = 6) -> None:
    """排空事件总线并让出数轮事件循环，替代真实 sleep 等待负向断言。"""
    await bus.wait_until_drained()
    for _ in range(turns):
        await asyncio.sleep(0)


# ======================================================================
# 1. 纯函数与缓冲区单元测试
# ======================================================================


def test_spoken_echo_buffer_bounds_dedupe_and_drain() -> None:
    """只读回执缓冲：忽略空白、去重、截断、有界滚动，取出后即清空。"""
    buf = SpokenEchoBuffer(maxlen=3, max_chars=16)
    assert buf.push("   ") is False
    assert buf.push("刚挖到第一块铁矿") is True
    assert buf.push("刚挖到第一块铁矿") is False  # 同文去重
    assert buf.push("前面好像有个大洞穴可以去探一探看看里面有什么宝贝") is True
    assert buf.push("观众说小心背后") is True
    assert buf.push("马上回基地放箱子") is True

    # 超出 maxlen=3，最早一条被滚出
    snap = buf.snapshot()
    assert len(snap) == 3
    assert all("第一块铁矿" not in item.text for item in snap)
    assert len(snap[0].text) <= 16 and snap[0].text.endswith("…")

    notice = buf.drain_notice()
    assert "[直播间刚说过]" in notice
    assert "观众说小心背后" in notice
    assert "马上回基地放箱子" in notice
    assert len(buf) == 0
    assert buf.drain_notice() == ""


def test_handover_brief_and_suggestion_formatting() -> None:
    """交权简报与观众建议格式包含全部关键信息并正确去重。"""
    ctx = LeadHandoverContext(
        segment_id="seg-play",
        segment_title="矿洞探险",
        segment_goal="收集铁矿并升级全套铁装备",
        expected_ms=1_500_000,
        save_name="机械动力二周目",
        memory_brief="答应观众今晚做出铁镐并去东边矿洞",
        operator_notes=["注意别碰同伴小明的红石机器", "注意别碰同伴小明的红石机器"],
        recent_speech=["大家久等了，马上进矿洞！"],
    )
    brief = format_lead_handover_brief(ctx)
    assert "[主控交接简报]" in brief
    assert "矿洞探险（预计时长：约 25 分钟）" in brief
    assert "收集铁矿并升级全套铁装备" in brief
    assert "机械动力二周目" in brief
    assert "答应观众今晚做出铁镐" in brief
    assert brief.count("注意别碰同伴小明的红石机器") == 1
    assert "大家久等了，马上进矿洞！" in brief

    sug = format_suggestion_message(
        "顺路采点煤炭",
        source="streamer",
        source_dialogue=["顺路采点煤炭", "观众A：火把快没了", "观众A：火把快没了", "观众B：采点煤"],
    )
    assert "[直播间建议]" in sug
    assert "建议内容：顺路采点煤炭" in sug
    assert sug.count("顺路采点煤炭") == 1
    assert sug.count("观众A：火把快没了") == 1
    assert "观众B：采点煤" in sug


# ======================================================================
# 2. 能力声明、工厂注入与 can_take_lead 校验
# ======================================================================


async def test_supports_autonomous_lead_factory_injection_and_can_take_lead(fake_clock: Any) -> None:
    """声明自主领航能力、工厂自动注入 lead_coordinator，且 can_take_lead 严格校验三项前置条件。"""
    assert MinecraftAgent.supports_autonomous_lead is True

    mgr = AgentManager()
    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)
    deps = AgentDeps(lead_coordinator=coord)
    built = instantiate_agent("minecraft", {"mcp": {"enabled": False}}, deps=deps)
    assert isinstance(built, MinecraftAgent)
    assert built.supports_autonomous_lead is True

    # 离线未开播、无 MCP、无 LLM → 不可接管
    assert built.can_take_lead() is False

    # 开播但未连 MCP → False
    agent_no_mcp, _, _ = await make_minecraft_agent(server=None, live=True)
    try:
        assert agent_no_mcp.can_take_lead() is False
    finally:
        await agent_no_mcp.stop()

    # 连了 MCP 但未开播 → False
    agent_not_live, _, _ = await make_minecraft_agent(FakeMaicraft(), live=False)
    try:
        assert agent_not_live.can_take_lead() is False
    finally:
        await agent_not_live.stop()

    # 连了 MCP 且开播但无 LLM → False
    agent_no_llm, _, _ = await make_minecraft_agent(FakeMaicraft(), live=True, llm=None)
    try:
        assert agent_no_llm.can_take_lead() is False
    finally:
        await agent_no_llm.stop()

    # 三项全部就绪 → True；MCP 断开立即变 False
    mcp_client = SimpleNamespace(connected=True)
    agent_ok, _, _ = await make_minecraft_agent(FakeMaicraft(), live=True, mcp_client=mcp_client)
    try:
        assert agent_ok.can_take_lead() is True
        mcp_client.connected = False
        assert agent_ok.can_take_lead() is False
    finally:
        await agent_ok.stop()


# ======================================================================
# 3. 交权进入、工具面与提示词切换、解说调用、收权退出与 live.ended 复位
# ======================================================================


async def test_enter_lead_commentary_and_yield_lead_full_lifecycle(fake_clock: Any) -> None:
    """完整主控生命周期：交接简报 + 开局资料 + 动态工具面 + 解说提交 + 收权取消目标与摘要。"""
    server = FakeMaicraft()
    server.enter_world("save-survival", "生存周目")
    commentaries: List[CommentaryRequest] = []

    step_counter = [0]

    def script(messages: List[Dict[str, Any]]) -> Response:
        step_counter[0] += 1
        if step_counter[0] == 1:
            return _resp(
                "先立好待办、开干并给直播间报个开场进展",
                calls=[
                    _call(
                        "minecraft_todo",
                        {
                            "action": "write",
                            "todos": [
                                {"content": "砍树做工具", "status": "done"},
                                {"content": "收集原木盖小屋", "status": "in_progress"},
                            ],
                        },
                        "c1",
                    ),
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "gather", "purpose": "砍橡木原木"}},
                        "c2",
                    ),
                    _call(
                        "framework_commentary",
                        {
                            "summary": "刚进林地开始砍橡木，准备先凑够材料盖小屋",
                            "mood": "excited",
                            "urgency": "normal",
                        },
                        "c3",
                    ),
                ],
            )
        return _resp("后台目标正在砍树，静默等待完成")

    env = _make_lead_env(server=server, script=script, fake_clock=fake_clock)
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]

    async def _on_commentary(req: CommentaryRequest) -> CommentaryAck:
        commentaries.append(req)
        return CommentaryAck(accepted=True, spoken=True, reason="spoken")

    coord.bind_commentary_handler(_on_commentary)

    try:
        ctx = LeadHandoverContext(
            segment_id="seg-1",
            segment_title="林地建家",
            segment_goal="搭一座能遮风挡雨的小木屋",
            expected_ms=1_200_000,
            save_name="生存周目",
            memory_brief="上次约定盖在河边空地",
            operator_notes=["注意不要动东边仓库"],
            recent_speech=["咱们先去林子里弄点木头！"],
            reason="rundown_play",
        )
        chosen = await coord.request_game_lead(ctx, target="minecraft")
        assert chosen == "minecraft"
        assert agent.in_lead_mode is True
        # 幂等 enter_lead
        assert await agent.enter_lead(ctx) is True

        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)

        # 首轮系统提示词包含自主领航段，用户消息包含 [主控交接简报] 与 [开局资料]
        first_messages = llm.seen[0]
        assert LEAD_MODE_PROMPT_SECTION.strip()[:20] in str(first_messages[0]["content"])
        joined_first = _texts(first_messages)
        assert "[主控交接简报]" in joined_first
        assert "林地建家" in joined_first
        assert "搭一座能遮风挡雨的小木屋" in joined_first
        assert "上次约定盖在河边空地" in joined_first
        assert "注意不要动东边仓库" in joined_first
        assert "咱们先去林子里弄点木头！" in joined_first
        assert "[开局资料]" in joined_first

        # 首轮工具列表：纳入 framework_commentary，隐藏 minecraft_report
        tool_names = {t["name"] for t in llm.tools_given[0]}
        assert "framework_commentary" in tool_names
        assert "minecraft_report" not in tool_names

        # framework_commentary 经 LeadCoordinator 成功送达主播解说处理器
        assert len(commentaries) == 1
        assert commentaries[0].game == "minecraft"
        assert "砍橡木" in commentaries[0].summary

        # 在跑目标存在且 glance 显示自主游玩中
        assert len(agent.tracked_goal_ids) == 1
        goal_id = agent.tracked_goal_ids[0]
        glance_res = await env["registry"].invoke(
            ToolInvocation(tool_name="minecraft_glance", arguments={}, source="streamer", invoking_agent="streamer")
        )
        assert glance_res.success is True
        assert glance_res.structured_content["work"]["state"] == "自主游玩中"

        # 兜底校验：主控态下强行直调 minecraft_report 被拒绝且不发 game.report
        report_events: List[GamePayload] = []

        async def _capture_report(_n: str, p: GamePayload, _s: str) -> None:
            report_events.append(p)

        env["bus"].on(CoreEvents.GAME_REPORT, _capture_report, model_class=GamePayload)
        report_res = await env["registry"].invoke(
            ToolInvocation(
                tool_name="minecraft_report",
                arguments={"kind": "delivery", "content": "误调交付"},
                source="minecraft-react",
                invoking_agent="minecraft",
            )
        )
        await env["bus"].wait_until_drained()
        assert report_res.structured_content["success"] is False
        assert "framework_commentary" in report_res.structured_content["error"]
        assert report_events == []

        # 收权退出：取消在跑目标、返回客观收尾摘要、清空临时状态、恢复工具面
        summary = await coord.reclaim_streamer_lead(reason="segment_ended")
        await env["bus"].wait_until_drained()
        assert agent.in_lead_mode is False
        assert server.goals[goal_id]["result"]["status"] == "cancelled"
        assert agent.tracked_goal_ids == []
        assert "砍树做工具" in summary
        assert "收集原木盖小屋" in summary
        assert "砍橡木原木" in summary
        assert agent.observation()["task_finished"] is True
        assert agent.queued_messages == []
        assert await agent.yield_lead(reason="repeat") == ""

        # 退出主控态后：minecraft_report 恢复可见，framework_commentary 隐藏
        restored_tools = {s.full_name for s in env["registry"].list_tools(for_agent="minecraft")}
        assert "minecraft_report" in restored_tools
        assert "framework_commentary" not in restored_tools
    finally:
        await _stop_lead_env(env, agent)


async def test_live_ended_cleanly_yields_lead_and_cancels_goals(fake_clock: Any) -> None:
    """live.ended 到达时若仍处于游戏主控态，自动干净收手、取消在跑目标并切回主播态。"""
    server = FakeMaicraft()
    fired = [False]

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not fired[0]:
            fired[0] = True
            return _resp(
                "开挖", calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "挖石头"}})]
            )
        return _resp("等挖完")

    env = _make_lead_env(server=server, script=script, fake_clock=fake_clock)
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="采矿"), target="minecraft") == "minecraft"
        )
        await _wait_until(lambda: len(agent.tracked_goal_ids) == 1 and not agent.batch_active)
        goal_id = agent.tracked_goal_ids[0]
        coord.publish_spoken_echo("观众说加油挖矿", source="streamer")
        assert agent.observation()["spoken_echo_count"] == 1

        await env["bus"].emit(CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=99), source="test")
        await env["bus"].wait_until_drained()

        assert agent.in_lead_mode is False
        assert coord.mode == "streamer"
        assert server.goals[goal_id]["result"]["status"] == "cancelled"
        assert agent.tracked_goal_ids == []
        assert agent.observation()["spoken_echo_count"] == 0
    finally:
        await _stop_lead_env(env, agent)


async def test_stop_while_in_lead_mode_notifies_coordinator_and_cancels_goals(fake_clock: Any) -> None:
    """游戏主控态下停止/禁用 MinecraftAgent 时，主动通知协调器切回主播主控并取消在跑目标。"""
    server = FakeMaicraft()
    fired = [False]

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not fired[0]:
            fired[0] = True
            return _resp(
                "去挖铁矿", calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "挖铁矿"}})]
            )
        return _resp("等待挖矿完成")

    env = _make_lead_env(server=server, script=script, fake_clock=fake_clock)
    agent, _llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    lead_events: List[AgentLeadChangedPayload] = []
    coord.bind_lead_changed_listener(lambda payload: lead_events.append(payload))

    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="采矿"), target="minecraft") == "minecraft"
        )
        await _wait_until(lambda: len(agent.tracked_goal_ids) == 1 and not agent.batch_active)
        goal_id = agent.tracked_goal_ids[0]

        # 模拟 Dashboard 停用/热重启停止 Agent：必须主动通知协调器切回主播主控，不能卡死在 mode="game"
        await agent.stop()
        await env["bus"].wait_until_drained()

        assert agent.in_lead_mode is False
        assert coord.mode == "streamer"
        assert coord.active_lead_agent is None
        assert server.goals[goal_id]["result"]["status"] == "cancelled"
        assert lead_events[-1].mode == "streamer"
        assert lead_events[-1].lead_agent == "streamer"
        assert lead_events[-1].previous_lead == "minecraft"
        assert lead_events[-1].reason == "agent_stopped"
    finally:
        await _stop_lead_env(env, agent)


# ======================================================================
# 4. 自主循环语义：不发 undelivered、待办推进、有界等待与防空转保护
# ======================================================================


async def test_lead_loop_continues_open_todos_and_guards_against_spin(fake_clock: Any) -> None:
    """主控态有未完成待办时自动提醒推进，连续无行动达上限后触发防空转保护挂起，且绝不发 undelivered。"""
    server = FakeMaicraft()
    calls_made = [0]

    def script(messages: List[Dict[str, Any]]) -> Response:
        calls_made[0] += 1
        if calls_made[0] == 1:
            return _resp(
                "先建一条待办",
                calls=[
                    _call(
                        "minecraft_todo",
                        {"action": "write", "todos": [{"content": "找羊剪羊毛", "status": "pending"}]},
                    )
                ],
            )
        # 后续故意连续不调用工具，验证先提醒推进再触发防空转保护挂起
        return _resp("先看看再说")

    env = _make_lead_env(
        server=server,
        script=script,
        fake_clock=fake_clock,
        lead_max_empty_turns=2,
        lead_idle_wakeup_s=0.0,
    )
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    reports: List[GamePayload] = []

    async def _capture_report(_n: str, p: GamePayload, _s: str) -> None:
        reports.append(p)

    env["bus"].on(CoreEvents.GAME_REPORT, _capture_report, model_class=GamePayload)

    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="牧场"), target="minecraft") == "minecraft"
        )
        # 第 1 步写 todo；第 2、3 步无 tool_calls 触发 2 次 [自主推进] 提醒；第 4 步再次无行动命中防空转上限挂起
        await _wait_until(lambda: agent.observation()["lead_idle_waiting"] and not agent.batch_active)
        await env["bus"].wait_until_drained()

        assert len(llm.seen) == 4
        assert "[自主推进]" in _texts(llm.seen[2])
        assert "[自主推进]" in _texts(llm.seen[3])
        assert reports == []  # 绝不发 undelivered / escalation
        assert agent.in_lead_mode is True
        assert agent.observation()["task_finished"] is False
    finally:
        await _stop_lead_env(env, agent)


async def test_lead_loop_bounded_idle_wakeup_then_halts_until_external_event(fake_clock: Any) -> None:
    """无待办自然结束后进入有界休眠（按假时钟唤醒一次），连续空转达上限后彻底挂起纯等外部事件。"""
    server = FakeMaicraft()
    env = _make_lead_env(
        server=server,
        script=lambda _messages: _resp("眼下先观望一下"),
        fake_clock=fake_clock,
        lead_idle_wakeup_s=30.0,
        lead_max_empty_turns=2,
    )
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    reports: List[GamePayload] = []

    async def _capture_report(_n: str, p: GamePayload, _s: str) -> None:
        reports.append(p)

    env["bus"].on(CoreEvents.GAME_REPORT, _capture_report, model_class=GamePayload)

    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="自由探索"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)
        assert agent.observation()["lead_idle_waiting"] is True
        assert reports == []

        # 假时钟只前进 10 秒（未满 30 秒）：不提前唤醒
        fake_clock.tick(10_000)
        server.body_event("sky_changed", "微风拂过")  # 触发事件页刷新（不直接唤醒）
        await _settle_loop(env["bus"])
        assert len(llm.seen) == 1

        # 假时钟前进 25 秒（累计 35 秒 >= 30 秒）：有界休眠到点唤醒一次
        fake_clock.tick(25_000)
        server.body_event("sky_changed", "云朵飘过")
        await _wait_until(lambda: len(llm.seen) == 2 and not agent.batch_active)
        assert "短暂休整结束" in _texts(llm.seen[1])

        # 第二次仍无行动（达到 lead_max_empty_turns=2）：即使再前进 120 秒也不再自唤醒，严格零空转
        fake_clock.tick(120_000)
        server.body_event("sky_changed", "天色渐晚")
        await _settle_loop(env["bus"])
        assert len(llm.seen) == 2
        assert reports == []
    finally:
        await _stop_lead_env(env, agent)


# ======================================================================
# 5. 直接主导游戏公屏 & 非强杀观众建议 & 零唤醒台词回执
# ======================================================================


async def test_lead_mode_directly_handles_player_chat_even_casual_banter(fake_clock: Any) -> None:
    """主控态下任何玩家公屏消息都立即唤醒游戏循环，由游戏侧直接用 chat 回应同伴。"""
    server = FakeMaicraft()
    replied = [False]

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = _texts(messages)
        if "今天建得不错呀" in text and not replied[0]:
            replied[0] = True
            return _resp(
                "回同伴一句",
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "chat", "parameters": {"message": "谢谢夸奖，一会来参观"}}},
                    )
                ],
            )
        return _resp("先看着")

    env = _make_lead_env(
        server=server,
        script=script,
        fake_clock=fake_clock,
        lead_idle_wakeup_s=0.0,
    )
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="联机日常"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)

        # 发送一条不含喊停/点名关键词的普通日常闲聊公屏
        await env["bus"].emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(game="minecraft", kind="player", sender="同伴甲", content="今天建得不错呀"),
            source="test",
        )
        await env["bus"].wait_until_drained()

        await _wait_until(lambda: replied[0] and not agent.batch_active)
        assert "[公屏] 玩家 同伴甲：今天建得不错呀" in _texts(llm.seen[1])
        chat_calls = [args for args in server.calls_to("execute") if (args.get("goal") or {}).get("ability") == "chat"]
        assert len(chat_calls) == 1
        assert chat_calls[0]["goal"]["parameters"]["message"] == "谢谢夸奖，一会来参观"
    finally:
        await _stop_lead_env(env, agent)


async def test_receive_suggestion_is_non_destructive_and_preserves_running_goal(fake_clock: Any) -> None:
    """观众建议经 receive_suggestion 入队唤醒但不取消在跑目标、不建委派账本。"""
    server = FakeMaicraft()
    started_goal = [False]

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not started_goal[0]:
            started_goal[0] = True
            return _resp(
                "先去砍树", calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})]
            )
        return _resp("收到建议，砍完树再去")

    env = _make_lead_env(server=server, script=script, fake_clock=fake_clock)
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    try:
        # 非主控空闲态拒收建议
        assert await agent.receive_suggestion("空闲时试试") is False

        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="砍树"), target="minecraft") == "minecraft"
        )
        await _wait_until(lambda: len(agent.tracked_goal_ids) == 1 and not agent.batch_active)
        goal_id = agent.tracked_goal_ids[0]
        calls_before = len(llm.seen)

        # 通过框架坐标发送观众建议（带重复来源原话）
        result = await coord.send_suggestion(
            "观众想看去河边钓鱼",
            source="streamer",
            source_dialogue=["观众想看去河边钓鱼", "水友1：去钓鱼吧", "水友1：去钓鱼吧"],
        )
        assert result == {"accepted": True, "target": "minecraft", "reason": ""}

        await _wait_until(lambda: len(llm.seen) > calls_before and not agent.batch_active)
        # 在跑目标完好无损，绝不被强杀
        assert server.goals[goal_id]["state"] == "running"
        assert goal_id in agent.tracked_goal_ids
        assert len(env["ledger"]) == 0

        latest_text = _texts(llm.seen[-1])
        assert "[直播间建议]" in latest_text
        assert "建议内容：观众想看去河边钓鱼" in latest_text
        assert latest_text.count("水友1：去钓鱼吧") == 1
    finally:
        await _stop_lead_env(env, agent)


async def test_spoken_echo_zero_wakeup_and_attached_on_next_reasoning_step(fake_clock: Any) -> None:
    """receive_spoken_echo 严格零唤醒，仅在下一次正常推理步里极简拼入 [直播间刚说过]。"""
    server = FakeMaicraft()
    env = _make_lead_env(
        server=server,
        script=lambda _messages: _resp("明白了"),
        fake_clock=fake_clock,
        lead_idle_wakeup_s=0.0,
    )
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="日常"), target="minecraft") == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)

        # 挂起态连续推 6 条台词回执（含重复），绝不触发任何 LLM 推理
        for idx in range(6):
            coord.publish_spoken_echo(f"主播刚聊到的第 {idx} 个话题", source="streamer")
        coord.publish_spoken_echo("主播刚聊到的第 5 个话题", source="streamer")  # 去重
        await _settle_loop(env["bus"])

        assert len(llm.seen) == 1
        assert agent.observation()["spoken_echo_count"] == 5

        # 直到收到真正外部事件（观众建议）唤醒下一次推理步，才把未读回执一并带上并清空缓冲
        await coord.send_suggestion("回头看看背包")
        await _wait_until(lambda: len(llm.seen) == 2 and not agent.batch_active)
        second_text = _texts(llm.seen[1])
        assert "[直播间刚说过]" in second_text
        assert "主播刚聊到的第 0 个话题" not in second_text  # 超出 5 条上限已滚出
        assert "主播刚聊到的第 5 个话题" in second_text
        assert agent.observation()["spoken_echo_count"] == 0

        # 再次唤醒时不重复注入已经消费过的回执
        await coord.send_suggestion("再看一眼天色")
        await _wait_until(lambda: len(llm.seen) == 3 and not agent.batch_active)
        assert "[直播间刚说过]" not in str(llm.seen[2][-1].get("content", ""))
    finally:
        await _stop_lead_env(env, agent)


# ======================================================================
# 6. 主播侧 minecraft_chat_send 业务拒绝与 guide_for 动态切换
# ======================================================================


async def test_streamer_chat_send_blocked_and_guide_switches_in_lead_mode(fake_clock: Any) -> None:
    """主控态下禁用主播侧 minecraft_chat_send（返回 business_fail_result 不熔断）并切换 guide_for；收权后 100% 恢复。"""
    server = FakeMaicraft()
    env = _make_lead_env(
        server=server,
        script=lambda _messages: _resp("待机"),
        fake_clock=fake_clock,
        lead_idle_wakeup_s=0.0,
    )
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    registry: ToolRegistry = env["registry"]
    mc_provider = next(p for p in registry.providers() if p.name == "minecraft")

    try:
        # 1. 非主控态：保持原 STREAMER_GUIDE 且 minecraft_chat_send 正常发送
        assert mc_provider.guide_for("streamer") == STREAMER_GUIDE
        assert mc_provider.guide_for("minecraft") == ""
        ok_send = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_chat_send",
                arguments={"text": "大家好我来了"},
                source="planner-react",
                invoking_agent="streamer",
            )
        )
        assert ok_send.success is True
        assert ok_send.structured_content["success"] is True

        # 2. 进入游戏主控态：guide_for("streamer") 切为只读主控说明；minecraft_chat_send 返回业务拒绝且不熔断
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="联机"), target="minecraft") == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)

        lead_guide = mc_provider.guide_for("streamer")
        assert lead_guide == STREAMER_LEAD_GUIDE
        assert "minecraft_glance" in lead_guide
        assert "minecraft_get_work_log" in lead_guide
        assert "framework_suggest" in lead_guide
        assert "framework_delegate" not in lead_guide
        assert "minecraft_chat_send" not in lead_guide

        calls_before = len(server.calls_to("execute"))
        for _ in range(4):
            blocked = await registry.invoke(
                ToolInvocation(
                    tool_name="minecraft_chat_send",
                    arguments={"text": "主播别乱承诺"},
                    source="planner-react",
                    invoking_agent="streamer",
                )
            )
            assert blocked.success is False
            assert blocked.failure_kind == "business"
            assert "主控态" in (blocked.error_message or "")
        assert registry.is_tripped("minecraft_chat_send") is False
        assert len(server.calls_to("execute")) == calls_before  # 未触碰底层 chat 能力

        # 3. 收权回主播主控：guide_for 与 minecraft_chat_send 100% 恢复原样
        await coord.reclaim_streamer_lead(reason="back_to_talk")
        assert mc_provider.guide_for("streamer") == STREAMER_GUIDE
        ok_after = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_chat_send",
                arguments={"text": "我回来了"},
                source="planner-react",
                invoking_agent="streamer",
            )
        )
        assert ok_after.success is True
        assert ok_after.structured_content["success"] is True
    finally:
        await _stop_lead_env(env, agent)


# ======================================================================
# 7. 运行态保护：MCP 断连降级、重连不乱自启、LLM 连续失败与致命故障降级
# ======================================================================


async def test_mcp_disconnect_degrades_to_streamer_and_reconnect_stays_safe(fake_clock: Any) -> None:
    """主控态期间 MCP 断连主动降级回主播主控；重连恢复后保持安全待命，不自行抢回主控也不乱启循环。"""
    server = _McpDisconnectableProvider()
    mcp_client = SimpleNamespace(connected=True)
    env = _make_lead_env(
        server=server,
        script=lambda _messages: _resp("正在看周围"),
        fake_clock=fake_clock,
        mcp_client=mcp_client,
        lead_idle_wakeup_s=0.0,
    )
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    lead_events: List[AgentLeadChangedPayload] = []
    coord.bind_lead_changed_listener(lambda p: lead_events.append(p))

    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="探险"), target="minecraft") == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)
        assert coord.mode == "game"

        # 模拟 MCP 突然断连（客户端断开 + 工具摘除 + 触发连接事件页/状态变化）
        mcp_client.connected = False
        server.tools_exposed = False
        server.body_event("sky_changed", "断连前最后一次轮询")
        await _wait_until(lambda: coord.mode == "streamer" and not agent.in_lead_mode)

        assert agent.can_take_lead() is False
        assert lead_events[-1].mode == "streamer"
        assert lead_events[-1].reason == "mcp_disconnected"

        # 模拟 MCP 重连恢复：can_take_lead 恢复 True，但不自行切回主控、不自启 LLM 循环
        mcp_client.connected = True
        server.tools_exposed = True
        env["registry"].refresh_provider_tools(server)
        await _settle_loop(env["bus"])

        assert agent.can_take_lead() is True
        assert agent.in_lead_mode is False
        assert coord.mode == "streamer"
        assert len(llm.seen) == 1  # 未乱自启
    finally:
        await _stop_lead_env(env, agent)


@pytest.mark.parametrize(
    ("responses", "expected_reason", "expected_calls", "recovers_after_cooldown"),
    [
        (
            [
                Response(success=False, error="timeout-1"),
                Response(success=False, error="timeout-2"),
                Response(success=False, error="timeout-3"),
            ],
            "llm_unavailable",
            3,
            True,
        ),
        (
            [Response(success=False, error="余额不足", fault_kind="balance")],
            "llm_fatal:balance",
            1,
            False,
        ),
    ],
)
async def test_llm_failure_in_lead_mode_triggers_graceful_fallback(
    fake_clock: Any,
    responses: List[Response],
    expected_reason: str,
    expected_calls: int,
    recovers_after_cooldown: bool,
) -> None:
    """主控态下 LLM 连续失败达阈值或致命故障时，主动通知协调器平滑降级回主播主控。"""
    server = FakeMaicraft()
    idx = [0]

    def script(_messages: List[Dict[str, Any]]) -> Response:
        cur = responses[min(idx[0], len(responses) - 1)]
        idx[0] += 1
        return cur

    env = _make_lead_env(
        server=server,
        script=script,
        fake_clock=fake_clock,
        lead_llm_failure_threshold=3,
        lead_llm_retry_delay_s=0.0,
        lead_llm_cooldown_s=60.0,
    )
    agent, llm, _ = await _start_lead_env(env)
    coord: LeadCoordinator = env["coordinator"]
    lead_events: List[AgentLeadChangedPayload] = []
    coord.bind_lead_changed_listener(lambda p: lead_events.append(p))

    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="建造"), target="minecraft") == "minecraft"
        )
        await _wait_until(lambda: coord.mode == "streamer" and not agent.in_lead_mode and not agent.batch_active)

        assert len(llm.seen) == expected_calls
        assert lead_events[-1].mode == "streamer"
        assert lead_events[-1].reason == expected_reason
        assert agent.can_take_lead() is False

        # 冷却期内仍不可接管；推进假时钟超过冷却期后，仅瞬时失败允许重新评估，致命故障维持 False
        fake_clock.tick(30_000)
        assert agent.can_take_lead() is False
        fake_clock.tick(35_000)
        assert agent.can_take_lead() is recovers_after_cooldown
    finally:
        await _stop_lead_env(env, agent)


async def test_mcp_reconnect_notifies_ready_listener_without_self_grabbing_lead(fake_clock: Any) -> None:
    """直播中 MCP 重连恢复时，MinecraftAgent 仅发送 mcp_reconnected 就绪通知，不自行抢回主控权。"""
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(_StreamerStub())
    coord = LeadCoordinator(manager, event_bus=bus, clock=fake_clock.now_ms)

    ready_events: List[tuple[str, str]] = []
    coord.bind_ready_listener(lambda name, reason: ready_events.append((name, reason)))

    class _ReconnectableMcpClient:
        def __init__(self) -> None:
            self.connected = False

        async def close(self) -> None:
            self.connected = False

    class _ReconnectableMcpProvider(_McpDisconnectableProvider):
        def __init__(self, client: _ReconnectableMcpClient) -> None:
            super().__init__()
            self._client = client
            self.tools_exposed = False
            self.switch_config: Dict[str, Any] = {}

        async def setup(self) -> int:
            return len(self.list_tools())

        def start_recovery(self) -> None:
            return None

    fake_client = _ReconnectableMcpClient()
    fake_provider = _ReconnectableMcpProvider(fake_client)

    agent = assemble_minecraft_agent(
        config=MinecraftConfig(mcp=McpServerConfig(enabled=True, url="http://127.0.0.1:8766/mcp"), events_wait_ms=1000),
        llm=ScriptLlm(lambda _messages: _resp("待机")),
        bus=bus,
        registry=registry,
        live=True,
        clock=lambda: fake_clock.now_ms() / 1000.0,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        mcp_client_factory=lambda _name, _cfg: fake_client,
        mcp_provider_factory=lambda **_kwargs: fake_provider,
    )
    manager.register(agent)
    await agent.start()

    try:
        assert agent.can_take_lead() is False
        assert fake_provider.on_connection_changed is not None

        # 模拟 MCP 重连成功并触发连接恢复回调
        fake_client.connected = True
        fake_provider.tools_exposed = True
        fake_provider.on_connection_changed(True)
        await _wait_until(lambda: len(ready_events) == 1)

        assert ready_events == [("minecraft", "mcp_reconnected")]
        assert agent.can_take_lead() is True
        # 未自行抢权：没有主播监听器裁决交权时，协调器仍维持 streamer 态
        assert coord.mode == "streamer"
        assert agent.in_lead_mode is False
    finally:
        await agent.stop()
        await bus.cleanup()
