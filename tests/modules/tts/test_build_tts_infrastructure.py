"""build_tts_infrastructure 装配入口：enabled 门控、四个引擎构造、未知引擎回退、构造失败与契约不符的 fail-soft"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.tts import TTSProvider, assembly, build_tts_infrastructure
from src.modules.tts.backends.edge.provider import EdgeTTSProvider
from src.modules.tts.backends.gptsovits.provider import GPTSoVITSProvider
from src.modules.tts.backends.omni.provider import OmniTTSProvider
from src.modules.tts.backends.voicebox.provider import VoiceboxProvider


@pytest.mark.parametrize(
    "config",
    [
        {"enabled": False, "provider": "edge_tts"},
        {"provider": "edge_tts"},  # 缺 enabled 视为未启用
        {},
        None,
        "not a dict",
        [1, 2, 3],
    ],
)
def test_disabled_or_malformed_config_returns_none(config) -> None:
    assert build_tts_infrastructure(config) is None  # type: ignore[arg-type]


def test_known_providers_construct_with_sub_config() -> None:
    edge = build_tts_infrastructure(
        {"enabled": True, "provider": "edge_tts", "edge_tts": {"voice": "zh-CN-YunxiNeural"}}
    )
    assert isinstance(edge, EdgeTTSProvider) and edge.typed_config.voice == "zh-CN-YunxiNeural"

    gsv = build_tts_infrastructure(
        {"enabled": True, "provider": "gptsovits", "gptsovits": {"host": "10.0.0.1", "port": 9881}}
    )
    assert isinstance(gsv, GPTSoVITSProvider) and (gsv.host, gsv.port) == ("10.0.0.1", 9881)

    vb = build_tts_infrastructure({"enabled": True, "provider": "voicebox", "voicebox": {"profile_id": "abc-123"}})
    assert isinstance(vb, VoiceboxProvider) and vb.profile_id == "abc-123"

    llm = MagicMock()
    omni = build_tts_infrastructure(
        {"enabled": True, "provider": "omni_tts", "omni_tts": {"voice": "Chelsie"}}, llm_service=llm
    )
    assert isinstance(omni, OmniTTSProvider) and omni._llm is llm and omni.voice == "Chelsie"
    assert all(isinstance(p, TTSProvider) for p in (edge, gsv, vb, omni))
    assert assembly._KNOWN_PROVIDERS == frozenset({"edge_tts", "gptsovits", "voicebox", "omni_tts"})


def test_sub_config_missing_or_not_a_dict_uses_schema_defaults_and_bus_is_injected() -> None:
    for config in (
        {"enabled": True, "provider": "edge_tts"},
        {"enabled": True, "provider": "edge_tts", "edge_tts": "oops"},
    ):
        engine = build_tts_infrastructure(config)
        assert isinstance(engine, EdgeTTSProvider) and engine.typed_config.voice == "zh-CN-XiaoxiaoNeural"

    bus = EventBus()
    engine = build_tts_infrastructure({"enabled": True, "provider": "edge_tts", "edge_tts": {}}, event_bus=bus)
    assert engine is not None and engine.event_bus is bus


def test_unknown_provider_falls_back_to_edge_tts_with_error_log(loguru_capture) -> None:
    engine = build_tts_infrastructure({"enabled": True, "provider": "definitely_not_a_real_engine"})
    assert isinstance(engine, EdgeTTSProvider)
    assert any(r["level"] == "ERROR" and "未知" in r["message"] for r in loguru_capture.records)


@pytest.mark.parametrize(
    ("factory", "log_keywords"),
    [
        (MagicMock(side_effect=RuntimeError("simulated failure")), ("构造", "失败")),
        # 工厂误返回缺方法的对象时，装配边界 fail-fast，避免脏对象注入 StreamerAgent 下游
        (lambda config, event_bus=None, **kwargs: object(), ("TTSProvider", "不满足")),
    ],
    ids=["factory-raises", "factory-returns-non-conforming"],
)
def test_factory_failure_or_contract_violation_fails_soft(monkeypatch, loguru_capture, factory, log_keywords) -> None:
    monkeypatch.setitem(assembly._PROVIDER_FACTORIES, "edge_tts", factory)
    assert build_tts_infrastructure({"enabled": True, "provider": "edge_tts"}) is None
    assert any(r["level"] == "ERROR" and all(k in r["message"] for k in log_keywords) for r in loguru_capture.records)


def test_omni_without_llm_service_fails_soft(loguru_capture) -> None:
    config = {"enabled": True, "provider": "omni_tts", "omni_tts": {"voice": "Chelsie"}}
    assert build_tts_infrastructure(config) is None
    assert any(r["level"] == "ERROR" and "构造" in r["message"] for r in loguru_capture.records)
