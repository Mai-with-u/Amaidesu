"""TTSProvider 协议契约测试。

覆盖：

- 四个 TTS 引擎 Provider 都能通过 ``isinstance(p, TTSProvider)`` 校验
  （结构契约的"形"满足——装配层 ``build_tts_infrastructure`` 正是用这条
  校验做 fail-fast 兜底）；
- 缺方法 / 缺类级成员的类不满足契约（负例，证明 ``@runtime_checkable``
  生效——只检查成员存在性，不校验签名）；
- ``None`` / ``dict`` / 裸 ``object`` 同样不满足契约；
- 协议从 ``src.modules.tts`` 与 ``src.modules.tts.protocol`` 都能 import
  到同一对象（包对外暴露路径一致性）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from loguru import logger as _loguru_logger

from src.modules.tts import TTSProvider, build_tts_infrastructure
from src.modules.tts.edge_tts_tool import EdgeTTSProvider
from src.modules.tts.gptsovits_tool import GPTSoVITSProvider
from src.modules.tts.omni_tts_tool import OmniTTSProvider
from src.modules.tts.protocol import TTSProvider as TTSProviderDirect
from src.modules.tts.voicebox_tool import VoiceboxProvider


# ---------------------------------------------------------------------------
# loguru 日志捕获（与 test_build_tts_infrastructure 风格保持一致）
# ---------------------------------------------------------------------------


class _LoguruCapture:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self._sink_id: Optional[int] = None

    def __enter__(self) -> "_LoguruCapture":
        def _sink(message) -> None:
            r = message.record
            self.records.append({"level": r["level"].name, "message": r["message"], "module": r["name"]})

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
# 正例：四个 Provider 都满足契约
# ---------------------------------------------------------------------------


class TestProvidersConform:
    def test_edge_tts_satisfies_protocol(self):
        """``EdgeTTSProvider`` 在最小配置下满足 ``TTSProvider`` 契约。"""
        provider = EdgeTTSProvider(config={})
        assert isinstance(provider, TTSProvider)

    def test_gptsovits_satisfies_protocol(self):
        """``GPTSoVITSProvider`` 在最小配置下满足 ``TTSProvider`` 契约。"""
        provider = GPTSoVITSProvider(config={})
        assert isinstance(provider, TTSProvider)

    def test_voicebox_satisfies_protocol(self):
        """``VoiceboxProvider`` 在最小配置下满足 ``TTSProvider`` 契约。"""
        provider = VoiceboxProvider(config={})
        assert isinstance(provider, TTSProvider)

    def test_omni_tts_satisfies_protocol(self):
        """``OmniTTSProvider`` 在最小配置下满足 ``TTSProvider`` 契约。"""
        provider = OmniTTSProvider(config={})
        assert isinstance(provider, TTSProvider)

    def test_protocol_import_path_consistency(self):
        """``src.modules.tts`` 与 ``src.modules.tts.protocol`` 暴露的是同一对象。

        包级 re-export 与协议模块 import 路径必须一致，避免外部代码误以为
        存在两个不同的协议类型。
        """
        assert TTSProvider is TTSProviderDirect

    def test_provider_class_attribute_schema_present(self):
        """每个 Provider 都持有 ``ConfigSchema`` 嵌套类（协议成员）。"""
        assert hasattr(EdgeTTSProvider, "ConfigSchema")
        assert hasattr(GPTSoVITSProvider, "ConfigSchema")
        assert hasattr(VoiceboxProvider, "ConfigSchema")
        assert hasattr(OmniTTSProvider, "ConfigSchema")

    def test_provider_class_attribute_name_present(self):
        """每个 Provider 都有 ``PROVIDER_NAME`` 与 ``name`` 属性（协议成员）。"""
        for cls in (EdgeTTSProvider, GPTSoVITSProvider, VoiceboxProvider, OmniTTSProvider):
            assert hasattr(cls, "PROVIDER_NAME")
            instance = cls(config={})
            # name 是 @property，访问时返回字符串
            assert isinstance(instance.name, str)
            assert instance.name == cls.PROVIDER_NAME


# ---------------------------------------------------------------------------
# 负例：缺方法 / 非引擎对象被协议拒绝
# ---------------------------------------------------------------------------


class TestProtocolRejects:
    def test_class_missing_handle_speech_rejected(self):
        """缺 ``handle_speech`` 方法的类 → ``isinstance`` 返回 False。

        证明 ``@runtime_checkable`` 真的在按成员存在性校验，缺核心业务
        方法时不会误判为满足契约。
        """

        # 故意没实现 handle_speech，但补齐其他成员以隔离单点失败
        class NotATTS:
            PROVIDER_NAME = "fake"

            @property
            def name(self) -> str:
                return "fake"

            async def setup(self) -> None:
                return None

            async def cleanup(self) -> None:
                return None

            class ConfigSchema:
                pass

            def get_stats(self) -> Dict[str, Any]:
                return {}

        assert not isinstance(NotATTS(), TTSProvider)

    def test_plain_object_rejected(self):
        """裸 ``object`` 不满足契约。"""
        assert not isinstance(object(), TTSProvider)

    def test_dict_rejected(self):
        """``dict`` 不是引擎实例。"""
        assert not isinstance({}, TTSProvider)

    def test_none_rejected(self):
        """``None`` 不是引擎实例。"""
        assert not isinstance(None, TTSProvider)


# ---------------------------------------------------------------------------
# 装配层 fail-fast：工厂返回不满足契约的对象 → 返回 None + ERROR 日志
# ---------------------------------------------------------------------------


class TestAssemblyFailFast:
    def test_factory_returning_non_conforming_object_yields_none(self, loguru_capture):
        """工厂返回缺方法的对象 → ``build_tts_infrastructure`` 返回 ``None`` + ERROR 日志。

        模拟"工厂误改返回 None / dict"等场景——装配边界 fail-fast 兜底，
        避免脏对象被注入 ``StreamerAgent`` 下游。
        """
        from src.modules.tts import assembly

        # 构造一个结构不完整的对象（缺 handle_speech 等）
        class BrokenEngine:
            PROVIDER_NAME = "broken"

        def broken_factory(config, event_bus=None):
            return BrokenEngine()

        original = assembly._PROVIDER_FACTORIES["edge_tts"]
        assembly._PROVIDER_FACTORIES["edge_tts"] = broken_factory
        try:
            result = build_tts_infrastructure({"enabled": True, "provider": "edge_tts"})
        finally:
            assembly._PROVIDER_FACTORIES["edge_tts"] = original

        assert result is None

        # ERROR 日志记录"不满足契约"信息
        error_records = [
            r
            for r in loguru_capture.records
            if r["level"] == "ERROR" and "TTSProvider" in r["message"] and "不满足" in r["message"]
        ]
        assert error_records, f"fail-fast 路径应记录 ERROR 日志，实际: {[r['message'] for r in loguru_capture.records]}"
