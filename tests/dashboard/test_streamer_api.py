"""Streamer 测试台 Dashboard API 测试套件

覆盖：
1. ``GET /api/v1/streamer/status`` — agent 缺席降级 / 正常返回统计与配置摘要。
2. ``POST /api/v1/streamer/test-decision`` — agent 缺席、入参互斥校验、
   门面成功（plan+speech 回填）、门面失败（error 如实回传）、门面异常兜底。
3. ``POST /api/v1/streamer/trigger-proactive`` — 置位成功、proactive_enabled=false
   时的提示文案、异常兜底。

注：使用 Fake Agent（duck-typed 实现 debug_test_decision / trigger_external_proactive /
    get_statistics），不触发真实 LLM。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient


_CORE_TOML = """\
[meta]
version = "2.0.3"
"""


# ---------------------------------------------------------------------------
# Fake Agent（鸭子类型：实现 dashboard 关心的门面即可）
# ---------------------------------------------------------------------------


class FakeStreamerAgent:
    """最小可注入 Agent 替身。"""

    def __init__(
        self,
        *,
        debug_result: Optional[Dict[str, Any]] = None,
        debug_exception: Optional[Exception] = None,
        trigger_exception: Optional[Exception] = None,
    ) -> None:
        self._debug_result = debug_result
        self._debug_exception = debug_exception
        self._trigger_exception = trigger_exception
        self.debug_calls: list[Dict[str, Any]] = []
        self.trigger_calls: list[Optional[str]] = []

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "total_messages": 10,
            "total_batches": 3,
            "total_replies": 2,
            "total_no_action": 1,
            "total_proactive": 0,
            "planner_failures": 0,
            "replyer_failures": 0,
        }

    async def debug_test_decision(
        self,
        *,
        batch: Optional[list] = None,
        forced: bool = False,
        proactive: bool = False,
    ) -> Dict[str, Any]:
        self.debug_calls.append({"batch": batch, "forced": forced, "proactive": proactive})
        if self._debug_exception is not None:
            raise self._debug_exception
        assert self._debug_result is not None, "FakeStreamerAgent 未注入 debug_result"
        return self._debug_result

    def trigger_external_proactive(self, topic_hint: Optional[str] = None) -> None:
        self.trigger_calls.append(topic_hint)
        if self._trigger_exception is not None:
            raise self._trigger_exception


class _FakeAgentManager:
    """仅暴露 get_agent_by_name 的最小 manager 替身。"""

    def __init__(self, agent: Optional[FakeStreamerAgent]) -> None:
        self._agent = agent

    def get_agent_by_name(self, name: str) -> Optional[FakeStreamerAgent]:
        if name == "streamer":
            return self._agent
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_config(config_dir: Path, *, proactive_enabled: bool = False) -> None:
    """写入测试用 agents.toml / core.toml（main_config["agents"]["streamer"] 来源）。"""
    (config_dir / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    agents_toml = f"""\
[agents]
enabled = ["streamer"]

[agents.streamer]
proactive_enabled = {str(proactive_enabled).lower()}
planner_llm = "llm_fast"
replyer_llm = "llm"
"""
    (config_dir / "agents.toml").write_text(agents_toml, encoding="utf-8")


def _make_client(
    config_dir: Path,
    agent: Optional[FakeStreamerAgent],
) -> TestClient:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()

    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        agent_manager=_FakeAgentManager(agent),  # type: ignore[arg-type]
    )
    set_dashboard_server(server)
    return TestClient(create_app())


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    return cfg


#: 门面成功返回的典型决策视图（plan=回复 + 有发言）
_SUCCESS_DEBUG_RESULT: Dict[str, Any] = {
    "success": True,
    "trigger_reason": "dashboard:debug_test",
    "proactive": False,
    "plan": {
        "should_reply": True,
        "target": "debug_测试观众",
        "topic_summary": "打招呼",
        "reply_guidance": "友好回应",
        "confidence": 0.87,
    },
    "speech": "你好呀，欢迎来到直播间！",
    "emotion": "happy",
    "utterance_id": "utt_1700000000000_1",
    "error": None,
    "elapsed_ms": 1234,
}

#: 门面失败返回（Planner 拒绝）
_REJECTED_DEBUG_RESULT: Dict[str, Any] = {
    "success": True,
    "trigger_reason": "dashboard:debug_test",
    "proactive": False,
    "plan": {
        "should_reply": False,
        "target": None,
        "topic_summary": "闲聊",
        "reply_guidance": "",
        "confidence": 0.2,
    },
    "speech": None,
    "emotion": None,
    "utterance_id": None,
    "error": None,
    "elapsed_ms": 800,
}


# ---------------------------------------------------------------------------
# GET /api/v1/streamer/status
# ---------------------------------------------------------------------------


def test_status_unavailable_when_no_streamer_agent(config_dir: Path) -> None:
    """AgentManager 中无 streamer → available=false，不抛 404。"""
    _write_config(config_dir)
    client = _make_client(config_dir, agent=None)

    resp = client.get("/api/v1/streamer/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "主播 Agent 未启用" in body["message"]
    assert body["statistics"] == {}


def test_status_returns_statistics_and_config(config_dir: Path) -> None:
    """正常路径：statistics 来自 get_statistics，config 来自 main_config。"""
    _write_config(config_dir, proactive_enabled=True)
    agent = FakeStreamerAgent()
    client = _make_client(config_dir, agent=agent)

    resp = client.get("/api/v1/streamer/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["statistics"]["total_replies"] == 2
    assert body["config"]["proactive_enabled"] is True
    assert body["config"]["planner_llm"] == "llm_fast"
    assert body["config"]["replyer_llm"] == "llm"


# ---------------------------------------------------------------------------
# POST /api/v1/streamer/test-decision
# ---------------------------------------------------------------------------


def test_test_decision_unavailable_when_no_streamer_agent(config_dir: Path) -> None:
    """Agent 不在 → success=false，不抛 HTTPException。"""
    _write_config(config_dir)
    client = _make_client(config_dir, agent=None)

    resp = client.post(
        "/api/v1/streamer/test-decision",
        json={"batch": [{"nickname": "观众", "text": "你好"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "主播 Agent 未启用" in body["message"]


def test_test_decision_rejects_proactive_with_batch(config_dir: Path) -> None:
    """proactive 与 batch 互斥 → success=false，门面不被调用。"""
    _write_config(config_dir)
    agent = FakeStreamerAgent(debug_result=_SUCCESS_DEBUG_RESULT)
    client = _make_client(config_dir, agent=agent)

    resp = client.post(
        "/api/v1/streamer/test-decision",
        json={"batch": [{"nickname": "观众", "text": "你好"}], "proactive": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "proactive" in body["message"]
    assert agent.debug_calls == []


def test_test_decision_rejects_empty_batch(config_dir: Path) -> None:
    """非 proactive 模式下 batch 为空 → success=false，门面不被调用。"""
    _write_config(config_dir)
    agent = FakeStreamerAgent(debug_result=_SUCCESS_DEBUG_RESULT)
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/streamer/test-decision", json={"batch": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "弹幕批次为空" in body["message"]
    assert agent.debug_calls == []


def test_test_decision_success_returns_plan_and_speech(config_dir: Path) -> None:
    """成功路径：plan/speech/emotion/utterance_id/elapsed_ms 全量回填。"""
    _write_config(config_dir)
    agent = FakeStreamerAgent(debug_result=_SUCCESS_DEBUG_RESULT)
    client = _make_client(config_dir, agent=agent)

    resp = client.post(
        "/api/v1/streamer/test-decision",
        json={"batch": [{"nickname": "测试观众", "text": "主播好"}], "forced": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["plan"]["should_reply"] is True
    assert body["plan"]["confidence"] == 0.87
    assert body["speech"] == "你好呀，欢迎来到直播间！"
    assert body["emotion"] == "happy"
    assert body["utterance_id"].startswith("utt_")
    assert body["elapsed_ms"] == 1234
    # 门面收到正确的入参形态
    assert agent.debug_calls == [
        {"batch": [{"nickname": "测试观众", "text": "主播好"}], "forced": True, "proactive": False}
    ]


def test_test_decision_planner_rejected_returns_plan_without_speech(config_dir: Path) -> None:
    """Planner 拒绝（should_reply=false）→ success=true + plan 如实回传 + speech=None。

    "没回复"本身是有效测试结果，不算 API 失败。
    """
    _write_config(config_dir)
    agent = FakeStreamerAgent(debug_result=_REJECTED_DEBUG_RESULT)
    client = _make_client(config_dir, agent=agent)

    resp = client.post(
        "/api/v1/streamer/test-decision",
        json={"batch": [{"nickname": "路人", "text": "……"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["plan"]["should_reply"] is False
    assert body["speech"] is None


def test_test_decision_proactive_mode_passes_none_batch(config_dir: Path) -> None:
    """proactive 模式：batch 转为 None 透传门面。"""
    _write_config(config_dir)
    agent = FakeStreamerAgent(
        debug_result={
            "success": True,
            "trigger_reason": "proactive:dashboard_debug",
            "proactive": True,
            "plan": None,
            "speech": "主动打个招呼",
            "emotion": None,
            "utterance_id": "utt_1700000000000_2",
            "error": None,
            "elapsed_ms": 2000,
        }
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/streamer/test-decision", json={"proactive": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["speech"] == "主动打个招呼"
    assert agent.debug_calls == [{"batch": None, "forced": False, "proactive": True}]


def test_test_decision_facade_error_wrapped(config_dir: Path) -> None:
    """门面返回 success=false（入参校验失败）→ error 透传，success=false。"""
    _write_config(config_dir)
    agent = FakeStreamerAgent(
        debug_result={"success": False, "error": "proactive 模式不接受弹幕批次"}
    )
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/streamer/test-decision", json={"proactive": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "proactive 模式不接受弹幕批次" in (body["error"] or "")


def test_test_decision_facade_exception_does_not_500(config_dir: Path) -> None:
    """门面抛未预期异常 → API 层兜底为 success=false（不返 500）。"""
    _write_config(config_dir)
    agent = FakeStreamerAgent(debug_exception=RuntimeError("决策内爆"))
    client = _make_client(config_dir, agent=agent)

    resp = client.post(
        "/api/v1/streamer/test-decision",
        json={"batch": [{"nickname": "观众", "text": "你好"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "调用失败" in body["message"]


# ---------------------------------------------------------------------------
# POST /api/v1/streamer/trigger-proactive
# ---------------------------------------------------------------------------


def test_trigger_proactive_calls_facade(config_dir: Path) -> None:
    """正常置位：topic_hint 透传，返回等待限流判定文案。"""
    _write_config(config_dir, proactive_enabled=True)
    agent = FakeStreamerAgent()
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/streamer/trigger-proactive", json={"topic_hint": "聊聊天气"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "flush tick" in body["message"]
    assert agent.trigger_calls == ["聊聊天气"]


def test_trigger_proactive_warns_when_disabled(config_dir: Path) -> None:
    """proactive_enabled=false → 置位成功但提示真实链路会静默丢弃。"""
    _write_config(config_dir, proactive_enabled=False)
    agent = FakeStreamerAgent()
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/streamer/trigger-proactive", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "静默丢弃" in body["message"]


def test_trigger_proactive_unavailable_when_no_streamer_agent(config_dir: Path) -> None:
    """Agent 不在 → success=false。"""
    _write_config(config_dir)
    client = _make_client(config_dir, agent=None)

    resp = client.post("/api/v1/streamer/trigger-proactive", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "主播 Agent 未启用" in body["message"]


def test_trigger_proactive_exception_does_not_500(config_dir: Path) -> None:
    """门面抛异常 → success=false，不 500。"""
    _write_config(config_dir, proactive_enabled=True)
    agent = FakeStreamerAgent(trigger_exception=RuntimeError("置位失败"))
    client = _make_client(config_dir, agent=agent)

    resp = client.post("/api/v1/streamer/trigger-proactive", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "触发失败" in body["message"]
