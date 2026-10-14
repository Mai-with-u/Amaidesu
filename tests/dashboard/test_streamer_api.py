"""Streamer 测试台 Dashboard API：状态、决策测试、主动发言触发与总开关

Agent 用鸭子类型替身（只实现 dashboard 关心的门面），不触发真实 LLM。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.modules.config.multi_file_loader import generate_default_configs, update_config_values
from src.modules.config.service import ConfigService


class FakeStreamerAgent:
    def __init__(
        self,
        *,
        debug_result: Optional[Dict[str, Any]] = None,
        debug_exception: Optional[Exception] = None,
        trigger_exception: Optional[Exception] = None,
        proactive_enabled: bool = True,
    ) -> None:
        self._debug_result = debug_result
        self._debug_exception = debug_exception
        self._trigger_exception = trigger_exception
        self.debug_calls: list[Dict[str, Any]] = []
        self.trigger_calls: list[Optional[str]] = []
        self.proactive_enabled = proactive_enabled
        self.proactive_toggle_calls: list[bool] = []

    def is_proactive_enabled(self) -> bool:
        return self.proactive_enabled

    def set_proactive_enabled(self, enabled: bool) -> None:
        self.proactive_toggle_calls.append(enabled)
        self.proactive_enabled = enabled

    def get_statistics(self) -> Dict[str, Any]:
        return {"total_messages": 10, "total_batches": 3, "total_replies": 2, "total_no_action": 1}

    async def debug_test_decision(
        self, *, batch: Optional[list] = None, forced: bool = False, proactive: bool = False
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
    def __init__(self, agent: Optional[FakeStreamerAgent]) -> None:
        self._agent = agent

    def get_agent_by_name(self, name: str) -> Optional[FakeStreamerAgent]:
        return self._agent if name == "streamer" else None


@pytest.fixture
def streamer_client(mount, tmp_path: Path):
    """streamer_client(agent, proactive_enabled=False)：七文件基线 + 统一管线铺设 proactive 开关。"""

    def _make(agent: Optional[FakeStreamerAgent], proactive_enabled: bool = False):
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        generate_default_configs(config_dir)
        update_config_values(config_dir, "agents.toml", {"agents.streamer.proactive.enabled": proactive_enabled})
        service = ConfigService(base_dir=str(tmp_path))
        service.initialize()
        return mount(config_service=service, agent_manager=_FakeAgentManager(agent))

    return _make


_DECISION = {
    "success": True,
    "trigger_reason": "dashboard:debug_test",
    "proactive": False,
    "plan": {"should_reply": True, "target": "debug_测试观众", "topic_summary": "打招呼", "confidence": 0.87},
    "speech": "你好呀，欢迎来到直播间！",
    "emotion": "happy",
    "utterance_id": "utt_1700000000000_1",
    "error": None,
    "elapsed_ms": 1234,
}
_BATCH = [{"nickname": "测试观众", "text": "主播好"}]


def test_status_reports_availability_statistics_and_config(streamer_client) -> None:
    body = streamer_client(None).get("/api/v1/streamer/status").json()
    assert body["available"] is False and "主播 Agent 未启用" in body["message"] and body["statistics"] == {}

    body = streamer_client(FakeStreamerAgent(), proactive_enabled=True).get("/api/v1/streamer/status").json()
    assert body["available"] is True and body["statistics"]["total_replies"] == 2
    assert body["config"]["proactive_enabled"] is True


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"batch": _BATCH, "proactive": True}, "proactive"),  # 互斥
        ({"batch": []}, "弹幕批次为空"),
    ],
)
def test_test_decision_rejects_invalid_input_before_calling_agent(streamer_client, payload, message) -> None:
    agent = FakeStreamerAgent(debug_result=_DECISION)
    body = streamer_client(agent).post("/api/v1/streamer/test-decision", json=payload).json()
    assert body["success"] is False and message in body["message"]
    assert agent.debug_calls == []


def test_test_decision_unavailable_without_agent(streamer_client) -> None:
    resp = streamer_client(None).post("/api/v1/streamer/test-decision", json={"batch": _BATCH})
    assert resp.status_code == 200 and resp.json()["success"] is False
    assert "主播 Agent 未启用" in resp.json()["message"]


def test_test_decision_success_and_forwarded_arguments(streamer_client) -> None:
    agent = FakeStreamerAgent(debug_result=_DECISION)
    body = streamer_client(agent).post("/api/v1/streamer/test-decision", json={"batch": _BATCH, "forced": True}).json()

    assert body["success"] is True and body["plan"]["should_reply"] is True and body["plan"]["confidence"] == 0.87
    assert (body["speech"], body["emotion"], body["elapsed_ms"]) == ("你好呀，欢迎来到直播间！", "happy", 1234)
    assert body["utterance_id"].startswith("utt_")
    assert agent.debug_calls == [{"batch": _BATCH, "forced": True, "proactive": False}]


def test_test_decision_proactive_mode_passes_none_batch(streamer_client) -> None:
    agent = FakeStreamerAgent(debug_result={**_DECISION, "proactive": True, "plan": None, "speech": "主动打个招呼"})
    body = streamer_client(agent).post("/api/v1/streamer/test-decision", json={"proactive": True}).json()
    assert (body["success"], body["speech"]) == (True, "主动打个招呼")
    assert agent.debug_calls == [{"batch": None, "forced": False, "proactive": True}]


def test_test_decision_planner_rejection_is_a_valid_result(streamer_client) -> None:
    """ "没回复"本身是有效测试结果，不算 API 失败。"""
    rejected = {**_DECISION, "plan": {**_DECISION["plan"], "should_reply": False}, "speech": None, "emotion": None}
    client = streamer_client(FakeStreamerAgent(debug_result=rejected))
    body = client.post("/api/v1/streamer/test-decision", json={"batch": _BATCH}).json()
    assert (body["success"], body["plan"]["should_reply"], body["speech"]) == (True, False, None)


@pytest.mark.parametrize(
    ("agent", "key", "text"),
    [
        (
            FakeStreamerAgent(debug_result={"success": False, "error": "proactive 模式不接受弹幕批次"}),
            "error",
            "不接受弹幕批次",
        ),
        (FakeStreamerAgent(debug_exception=RuntimeError("决策内爆")), "message", "调用失败"),
    ],
    ids=["facade_error", "facade_exception"],
)
def test_test_decision_failures_never_500(streamer_client, agent, key: str, text: str) -> None:
    resp = streamer_client(agent).post("/api/v1/streamer/test-decision", json={"batch": _BATCH})
    assert resp.status_code == 200 and resp.json()["success"] is False and text in (resp.json()[key] or "")


@pytest.mark.parametrize(
    ("agent", "enabled", "success", "message"),
    [
        (FakeStreamerAgent(), True, True, "决策周期"),
        (FakeStreamerAgent(proactive_enabled=False), False, True, "不会开口"),  # 总开关关闭时的提示
        (None, False, False, "主播 Agent 未启用"),
        (FakeStreamerAgent(trigger_exception=RuntimeError("置位失败")), True, False, "触发失败"),
    ],
    ids=["registered", "switch_off", "no_agent", "facade_exception"],
)
def test_trigger_proactive(streamer_client, agent, enabled: bool, success: bool, message: str) -> None:
    resp = streamer_client(agent, proactive_enabled=enabled).post(
        "/api/v1/streamer/trigger-proactive", json={"topic_hint": "聊聊天气"}
    )
    assert resp.status_code == 200 and resp.json()["success"] is success and message in resp.json()["message"]
    if agent is not None and success:
        assert agent.trigger_calls == ["聊聊天气"]


def test_proactive_toggle_updates_runtime_and_config(streamer_client, tmp_path: Path) -> None:
    """总开关闭合：Agent 运行时立即生效 + agents.toml 落盘保持。"""
    agent = FakeStreamerAgent(proactive_enabled=False)
    body = streamer_client(agent).post("/api/v1/streamer/proactive-toggle", json={"enabled": True}).json()

    assert body["enabled"] is True and agent.proactive_toggle_calls == [True]
    saved = tomllib.loads((tmp_path / "config" / "agents.toml").read_text(encoding="utf-8-sig"))
    assert saved["agents"]["streamer"]["proactive"]["enabled"] is True
