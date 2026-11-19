"""Dashboard 动态主从控制面 REST API（GET / POST /api/v1/agents/lead）测试。"""

from __future__ import annotations

from typing import Iterable, List

import pytest

from src.modules.agents import (
    AgentManager,
    BaseAgent,
    LeadCoordinator,
    LeadHandoverContext,
)
from src.modules.events import EventBus, register_core_events
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentLeadChangedPayload
from src.modules.tools.models import ToolSpec


class _StreamerStub(BaseAgent):
    name = "streamer"
    description = "主播桩"

    def __init__(self) -> None:
        super().__init__(heartbeat_interval_ms=0)

    def list_tools(self) -> Iterable[ToolSpec]:
        return []


class _LeadGameStub(BaseAgent):
    name = "adventure_game"
    description = "自主游戏桩"
    supports_autonomous_lead = True

    def __init__(self, *, ready: bool = True, accept_lead: bool = True) -> None:
        super().__init__(heartbeat_interval_ms=0)
        self.ready = ready
        self.accept_lead = accept_lead
        self.entered: List[LeadHandoverContext] = []

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def can_take_lead(self) -> bool:
        return self.ready

    async def enter_lead(self, context: LeadHandoverContext) -> bool:
        if not self.accept_lead:
            return False
        self.entered.append(context)
        return True

    async def yield_lead(self, *, reason: str = "") -> str:
        return f"收尾:{reason}"


class _NonLeadGameStub(BaseAgent):
    name = "puzzle_game"
    description = "非自主游戏桩"

    def __init__(self) -> None:
        super().__init__(heartbeat_interval_ms=0)

    def list_tools(self) -> Iterable[ToolSpec]:
        return []


async def test_lead_endpoints_return_503_when_coordinator_not_injected(api, initialized_config) -> None:
    client = await api(config_service=initialized_config)
    assert (await client.get("/api/v1/agents/lead")).status_code == 503
    assert (await client.post("/api/v1/agents/lead", json={"target": "auto"})).status_code == 503


async def test_get_and_post_lead_state_roundtrip(api, initialized_config, fake_clock) -> None:
    register_core_events()
    bus = EventBus(enable_stats=False)
    mgr = AgentManager()
    streamer = _StreamerStub()
    game = _LeadGameStub(ready=True)
    non_lead = _NonLeadGameStub()
    mgr.register(streamer)
    mgr.register(game)
    mgr.register(non_lead)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, event_bus=bus, clock=fake_clock.now_ms)
    coord.bind_handover_builder(lambda target: LeadHandoverContext(segment_title=f"切给 {target}"))

    emitted: List[AgentLeadChangedPayload] = []

    async def _capture(name: str, payload: AgentLeadChangedPayload, source: str) -> None:
        del name, source
        emitted.append(payload)

    bus.on(CoreEvents.AGENT_LEAD_CHANGED, _capture, model_class=AgentLeadChangedPayload)
    client = await api(config_service=initialized_config, agent_manager=mgr, lead_coordinator=coord)

    # 1. 初始快照
    resp = await client.get("/api/v1/agents/lead")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "streamer"
    assert body["lead_agent"] == "streamer"
    assert body["override"] == "auto"
    assert body["enabled"] is True
    assert body["candidates"] == ["adventure_game"]
    assert body["ready_candidates"] == ["adventure_game"]
    assert body["since_ms"] == fake_clock.now_ms()

    # 2. 手动切到自主游戏
    fake_clock.tick(2_000)
    sw = await client.post("/api/v1/agents/lead", json={"target": "adventure_game", "reason": "webui_switch"})
    await bus.wait_until_drained()
    assert sw.status_code == 200
    sw_body = sw.json()
    assert (sw_body["mode"], sw_body["lead_agent"], sw_body["override"], sw_body["reason"]) == (
        "game",
        "adventure_game",
        "adventure_game",
        "webui_switch",
    )
    assert len(game.entered) == 1 and game.entered[0].segment_title == "切给 adventure_game"
    assert len(emitted) == 1 and emitted[0].lead_agent == "adventure_game"

    # 3. 切回 streamer 立即收权
    fake_clock.tick(3_000)
    back = await client.post("/api/v1/agents/lead", json={"target": "streamer", "reason": "back_to_host"})
    await bus.wait_until_drained()
    assert back.status_code == 200
    back_body = back.json()
    assert (back_body["mode"], back_body["lead_agent"], back_body["override"]) == ("streamer", "streamer", "streamer")
    assert emitted[-1].handover_summary == "收尾:back_to_host"

    # 4. 恢复 auto
    auto = await client.post("/api/v1/agents/lead", json={"target": "auto"})
    assert auto.status_code == 200
    assert auto.json()["override"] == "auto"
    assert auto.json()["reason"] == "manual_override"

    await mgr.stop_all()
    await bus.cleanup()


@pytest.mark.parametrize("bad_target", ["", "   ", "unknown_game", "puzzle_game"])
async def test_post_lead_invalid_target_returns_400(api, initialized_config, bad_target: str) -> None:
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    mgr.register(_LeadGameStub())
    mgr.register(_NonLeadGameStub())
    await mgr.start_all()

    coord = LeadCoordinator(mgr)
    client = await api(config_service=initialized_config, agent_manager=mgr, lead_coordinator=coord)

    resp = await client.post("/api/v1/agents/lead", json={"target": bad_target})
    assert resp.status_code == 400
    assert "无效的主控切换目标" in resp.json()["detail"]

    await mgr.stop_all()


async def test_post_lead_unready_or_refused_returns_409(api, initialized_config) -> None:
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _LeadGameStub(ready=False)
    mgr.register(game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr)
    client = await api(config_service=initialized_config, agent_manager=mgr, lead_coordinator=coord)

    resp_unready = await client.post("/api/v1/agents/lead", json={"target": "adventure_game"})
    assert resp_unready.status_code == 409
    assert "未就绪" in resp_unready.json()["detail"]

    game.ready = True
    game.accept_lead = False
    resp_refused = await client.post("/api/v1/agents/lead", json={"target": "adventure_game"})
    assert resp_refused.status_code == 409
    assert "拒绝接管" in resp_refused.json()["detail"]

    await mgr.stop_all()
