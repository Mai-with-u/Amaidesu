"""LeadCoordinator 观测事件发射测试（agent.lead.commentary / agent.lead.suggestion）。

覆盖两条提交通道的每条退出路径都发观测事件：
- submit_commentary：受理成功、not_in_lead、no_commentary_handler、handler_error；
- send_suggestion：受理成功、refused、not_in_game_lead、target_not_leading、
  agent_not_found、投递异常，以及 content 为空不发事件。
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from src.modules.agents import (
    AgentManager,
    BaseAgent,
    CommentaryAck,
    CommentaryRequest,
    LeadCoordinator,
    LeadHandoverContext,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentLeadCommentaryPayload, AgentLeadSuggestionPayload
from src.modules.tools.models import ToolSpec


class _CaptureBus:
    """捕获 emit 调用的桩事件总线（替代真实 EventBus）。"""

    def __init__(self) -> None:
        self.events: List[tuple[str, Any]] = []

    async def emit(self, event_name: str, payload: Any, **kwargs: Any) -> None:
        self.events.append((event_name, payload))


class _StreamerStub(BaseAgent):
    """主播 Agent 最小桩（不支持自主领航）。"""

    name = "streamer"
    description = "测试主播"

    def __init__(self) -> None:
        super().__init__(heartbeat_interval_ms=0)

    def list_tools(self) -> Iterable[ToolSpec]:
        return []


class _GameStub(BaseAgent):
    """可控测试游戏 Agent（支持自主领航，建议受理结果可配）。"""

    name = "block_game"
    description = "测试游戏"
    supports_autonomous_lead = True

    def __init__(self, name: str = "block_game", *, accept_suggestion: bool = True) -> None:
        self.__class__ = type(f"_GameStub_{name}", (type(self),), {"name": name, "description": f"游戏 {name}"})
        super().__init__(heartbeat_interval_ms=0)
        self.accept_suggestion = accept_suggestion
        self.suggestions: List[tuple[str, str, Optional[List[str]]]] = []

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def can_take_lead(self) -> bool:
        return True

    async def enter_lead(self, context: LeadHandoverContext) -> bool:
        return True

    async def receive_suggestion(
        self, content: str, *, source: str = "", source_dialogue: Optional[List[str]] = None
    ) -> bool:
        self.suggestions.append((content, source, source_dialogue))
        return self.accept_suggestion


def _of_type(bus: _CaptureBus, event_name: str) -> List[Any]:
    return [payload for name, payload in bus.events if name == event_name]


async def test_submit_commentary_emits_event_on_every_exit_path() -> None:
    """解说提交的每条退出路径都发一条 agent.lead.commentary（成功与每种失败都算事实）。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _GameStub()
    mgr.register(game)
    await mgr.start_all()

    bus = _CaptureBus()
    coord = LeadCoordinator(mgr, event_bus=bus)
    req = CommentaryRequest(game="block_game", summary="挖到第一颗钻石", urgency="high", about_player="Steve")

    # 非主控态提交 → not_in_lead
    ack = await coord.submit_commentary("block_game", req)
    assert ack.reason == "not_in_lead"
    # 未绑定处理器 → no_commentary_handler
    await coord.request_game_lead(LeadHandoverContext(), target="block_game")
    ack = await coord.submit_commentary("block_game", req)
    assert ack.reason == "no_commentary_handler"

    async def _boom(r: CommentaryRequest) -> CommentaryAck:
        raise RuntimeError("handler exploded")

    coord.bind_commentary_handler(_boom)
    ack = await coord.submit_commentary("block_game", req)
    assert ack.reason == "handler_error: RuntimeError"

    async def _ok(r: CommentaryRequest) -> CommentaryAck:
        return CommentaryAck(accepted=True, spoken=True)

    coord.bind_commentary_handler(_ok)
    ack = await coord.submit_commentary("block_game", req)
    assert ack.accepted is True

    events = _of_type(bus, CoreEvents.AGENT_LEAD_COMMENTARY)
    assert len(events) == 4
    assert [e.reason for e in events] == ["not_in_lead", "no_commentary_handler", "handler_error: RuntimeError", ""]
    assert [e.accepted for e in events] == [False, False, False, True]
    success = events[-1]
    assert isinstance(success, AgentLeadCommentaryPayload)
    assert (success.game, success.summary, success.urgency, success.about_player, success.spoken) == (
        "block_game",
        "挖到第一颗钻石",
        "high",
        "Steve",
        True,
    )
    await mgr.stop_all()


async def test_send_suggestion_emits_event_on_every_exit_path_and_skips_empty() -> None:
    """content 非空的每条退出路径都发 agent.lead.suggestion（含拒收）；content 为空不发。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _GameStub(accept_suggestion=False)
    mgr.register(game)
    await mgr.start_all()

    bus = _CaptureBus()
    coord = LeadCoordinator(mgr, event_bus=bus)

    # content 为空：不发事件
    result = await coord.send_suggestion("   ")
    assert result["reason"] == "empty_content"
    assert _of_type(bus, CoreEvents.AGENT_LEAD_SUGGESTION) == []

    # 非游戏主控态 → not_in_game_lead
    result = await coord.send_suggestion("去搭个桥")
    assert result["reason"] == "not_in_game_lead"

    # 进入主控态：指定错误目标 → target_not_leading
    await coord.request_game_lead(LeadHandoverContext(), target="block_game")
    result = await coord.send_suggestion("试试", target="other_game")
    assert result["reason"] == "target_not_leading"

    # 主控方拒收 → refused（accepted=False 也发事件）
    result = await coord.send_suggestion("观众想看搭个小桥")
    assert result["accepted"] is False and result["reason"] == "refused"

    events = _of_type(bus, CoreEvents.AGENT_LEAD_SUGGESTION)
    assert len(events) == 3
    assert [e.reason for e in events] == ["not_in_game_lead", "target_not_leading", "refused"]
    assert all(e.accepted is False for e in events)
    refused = events[-1]
    assert isinstance(refused, AgentLeadSuggestionPayload)
    assert (refused.content, refused.source, refused.target, refused.lead_agent) == (
        "观众想看搭个小桥",
        "streamer",
        "block_game",
        "block_game",
    )
    await mgr.stop_all()


async def test_send_suggestion_emits_accepted_event_with_operator_source() -> None:
    """受理成功也发事件，source 透传发起方标识（如运营通道的 operator）。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _GameStub()
    mgr.register(game)
    await mgr.start_all()

    bus = _CaptureBus()
    coord = LeadCoordinator(mgr, event_bus=bus)
    await coord.request_game_lead(LeadHandoverContext(), target="block_game")

    result = await coord.send_suggestion("去看看村庄", source="operator")
    assert result["accepted"] is True and result["reason"] == ""

    events = _of_type(bus, CoreEvents.AGENT_LEAD_SUGGESTION)
    assert len(events) == 1
    payload = events[0]
    assert isinstance(payload, AgentLeadSuggestionPayload)
    assert (payload.accepted, payload.source, payload.target, payload.lead_agent, payload.reason) == (
        True,
        "operator",
        "block_game",
        "block_game",
        "",
    )
    await mgr.stop_all()


async def test_emission_skipped_when_event_bus_absent() -> None:
    """未注入事件总线时观测发射静默跳过，业务回执不受影响。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _GameStub()
    mgr.register(game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, event_bus=None)

    async def _ok(r: CommentaryRequest) -> CommentaryAck:
        return CommentaryAck(accepted=True, spoken=True)

    coord.bind_commentary_handler(_ok)
    await coord.request_game_lead(LeadHandoverContext(), target="block_game")

    ack = await coord.submit_commentary("block_game", CommentaryRequest(game="block_game", summary="进度正常"))
    result = await coord.send_suggestion("继续推进")
    assert ack.accepted is True and result["accepted"] is True
    await mgr.stop_all()
