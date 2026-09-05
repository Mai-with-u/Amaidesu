"""build_tts_infrastructure 装配入口测试。

覆盖契约：
- ``enabled=True`` + 已知 provider → 构造对应引擎实例（属性正确）
- ``enabled=True`` + 未知 provider → 记 ERROR 日志 + 回退到 edge_tts
- ``enabled=False`` → 返回 None
- ``tts_config=None`` / 非 dict → 返回 None
- 子配置构造异常 → fail-soft 返回 None + ERROR 日志
- 子配置缺失 / 形态非 dict → 走 Schema 默认（用空 dict 构造）
- event_bus 注入 → 引擎持有引用
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger as _loguru_logger

from src.modules.tts import build_tts_infrastructure


# ---------------------------------------------------------------------------
# loguru 日志捕获
# ---------------------------------------------------------------------------


class _LoguruCapture:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self._sink_id: Optional[int] = None

    def __enter__(self) -> "_LoguruCapture":
        def _sink(message) -> None:
            r = message.record
            self.records.append(
                {"level": r["level"].name, "message": r["message"], "module": r["name"]}
            )

        self._sink_id = _loguru_logger.add(_sink, level="DEBUG")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._sink_id is not None:
            _loguru_logger.remove(self._sink_id)


@pytest.fixture
def loguru_capture():
    cap = _LoguruCapture()
    with cap:
        yield cap


# ---------------------------------------------------------------------------
# enabled 门控
# ---------------------------------------------------------------------------


class TestEnabledGate:
    def test_disabled_returns_none(self):
        """``enabled=False`` → 返回 None。"""
        result = build_tts_infrastructure({"enabled": False, "provider": "edge_tts"})
        assert result is None

    def test_missing_enabled_returns_none(self):
        """缺 enabled 字段 → 返回 None（默认 disabled）。"""
        result = build_tts_infrastructure({"provider": "edge_tts"})
        assert result is None

    def test_empty_config_returns_none(self):
        """空 dict → 返回 None。"""
        assert build_tts_infrastructure({}) is None

    def test_none_config_returns_none(self):
        """``None`` 入参 → 返回 None。"""
        assert build_tts_infrastructure(None) is None  # type: ignore[arg-type]

    def test_non_dict_config_returns_none(self):
        """非 dict 入参（字符串 / 列表）→ 返回 None（fail-soft）。"""
        assert build_tts_infrastructure("not a dict") is None  # type: ignore[arg-type]
        assert build_tts_infrastructure([1, 2, 3]) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 已知 provider 装配
# ---------------------------------------------------------------------------


class TestKnownProviders:
    def test_edge_tts_constructs_provider(self):
        """provider=edge_tts + 子配置 → EdgeTTSProvider 实例。"""
        from src.modules.tts.edge_tts_tool import EdgeTTSProvider

        result = build_tts_infrastructure(
            {
                "enabled": True,
                "provider": "edge_tts",
                "edge_tts": {"voice": "zh-CN-YunxiNeural"},
            }
        )
        assert isinstance(result, EdgeTTSProvider)
        # 子配置正确透传（typed_config 字段）
        assert result.typed_config.voice == "zh-CN-YunxiNeural"

    def test_gptsovits_constructs_provider(self):
        """provider=gptsovits + 子配置 → GPTSoVITSProvider 实例。"""
        from src.modules.tts.gptsovits_tool import GPTSoVITSProvider

        result = build_tts_infrastructure(
            {
                "enabled": True,
                "provider": "gptsovits",
                "gptsovits": {"host": "10.0.0.1", "port": 9881},
            }
        )
        assert isinstance(result, GPTSoVITSProvider)
        assert result.host == "10.0.0.1"
        assert result.port == 9881

    def test_voicebox_constructs_provider(self):
        """provider=voicebox + 子配置 → VoiceboxProvider 实例。"""
        from src.modules.tts.voicebox_tool import VoiceboxProvider

        result = build_tts_infrastructure(
            {
                "enabled": True,
                "provider": "voicebox",
                "voicebox": {"profile_id": "abc-123"},
            }
        )
        assert isinstance(result, VoiceboxProvider)
        assert result.profile_id == "abc-123"

    def test_omni_tts_constructs_provider(self):
        """provider=omni_tts + 子配置 → OmniTTSProvider 实例。"""
        from src.modules.tts.omni_tts_tool import OmniTTSProvider

        result = build_tts_infrastructure(
            {
                "enabled": True,
                "provider": "omni_tts",
                "omni_tts": {"host": "192.168.0.1", "port": 9999},
            }
        )
        assert isinstance(result, OmniTTSProvider)
        assert result.host == "192.168.0.1"
        assert result.port == 9999

    def test_event_bus_passed_through_to_engine(self):
        """event_bus 注入 → 引擎持有该引用。"""
        from src.modules.events.event_bus import EventBus

        bus = EventBus()
        result = build_tts_infrastructure(
            {"enabled": True, "provider": "edge_tts", "edge_tts": {}},
            event_bus=bus,
        )
        assert result is not None
        assert result.event_bus is bus

    def test_missing_sub_config_uses_schema_defaults(self):
        """子配置缺失 → 工厂用空 dict 构造，引擎走 Schema 默认值。"""
        from src.modules.tts.edge_tts_tool import EdgeTTSProvider

        result = build_tts_infrastructure(
            {"enabled": True, "provider": "edge_tts"}
        )
        assert isinstance(result, EdgeTTSProvider)
        # 缺省 voice
        assert result.typed_config.voice == "zh-CN-XiaoxiaoNeural"

    def test_sub_config_not_dict_falls_back_to_empty(self):
        """子配置形态非 dict（字符串 / 数字）→ 当空 dict 处理。"""
        from src.modules.tts.edge_tts_tool import EdgeTTSProvider

        result = build_tts_infrastructure(
            {"enabled": True, "provider": "edge_tts", "edge_tts": "oops"}
        )
        assert isinstance(result, EdgeTTSProvider)


# ---------------------------------------------------------------------------
# 未知 provider：ERROR + 回退
# ---------------------------------------------------------------------------


class TestUnknownProviderFallback:
    def test_unknown_provider_falls_back_to_edge_tts(self, loguru_capture):
        """未知 provider 名 → ERROR 日志 + 回退到 edge_tts（仍可装配）。"""
        from src.modules.tts.edge_tts_tool import EdgeTTSProvider

        result = build_tts_infrastructure(
            {"enabled": True, "provider": "definitely_not_a_real_engine"}
        )
        assert isinstance(result, EdgeTTSProvider)

        # ERROR 日志记录未知 provider + 回退信息
        error_records = [
            r
            for r in loguru_capture.records
            if r["level"] == "ERROR" and "未知" in r["message"]
        ]
        assert error_records, "未知 provider 应记录 ERROR 日志"


# ---------------------------------------------------------------------------
# 工厂构造异常：fail-soft
# ---------------------------------------------------------------------------


class TestConstructionFailure:
    def test_factory_exception_returns_none_with_error_log(self, loguru_capture):
        """引擎工厂抛异常 → fail-soft 返回 None + ERROR 日志。"""
        from src.modules.tts import assembly

        # Mock 装配表中的工厂函数（assembly 内部按引用持有）抛 RuntimeError
        broken_factory = MagicMock(side_effect=RuntimeError("simulated failure"))
        original = assembly._PROVIDER_FACTORIES["edge_tts"]
        assembly._PROVIDER_FACTORIES["edge_tts"] = broken_factory
        try:
            result = build_tts_infrastructure(
                {"enabled": True, "provider": "edge_tts"}
            )
        finally:
            assembly._PROVIDER_FACTORIES["edge_tts"] = original

        assert result is None

        # ERROR 日志记录失败原因
        error_records = [
            r
            for r in loguru_capture.records
            if r["level"] == "ERROR" and "构造" in r["message"] and "失败" in r["message"]
        ]
        assert error_records, "工厂异常应记录 ERROR 日志"


# ---------------------------------------------------------------------------
# 已知 provider 集合完整性
# ---------------------------------------------------------------------------


def test_known_providers_set_complete():
    """``_KNOWN_PROVIDERS`` 包含全部四个引擎名（与设计契约一致）。"""
    from src.modules.tts.assembly import _KNOWN_PROVIDERS

    assert _KNOWN_PROVIDERS == frozenset({"edge_tts", "gptsovits", "voicebox", "omni_tts"})
