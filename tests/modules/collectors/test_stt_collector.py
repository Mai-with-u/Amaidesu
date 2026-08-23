"""
stt 采集器注册 + 基本 import 测试（Wave 5）

覆盖：
- STTCollector 继承 BaseCollector
- 元数据正确
- 配置 Schema 嵌套结构（iflytek_asr/vad/audio/message_config）正确加载
"""

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.stt import STTCollector


def test_stt_inherits_base_collector() -> None:
    """STTCollector 继承 BaseCollector"""
    assert issubclass(STTCollector, BaseCollector)


def test_stt_metadata() -> None:
    """元数据正确"""
    assert STTCollector.name == "stt"
    assert "STT" in STTCollector.description or "语音" in STTCollector.description


def test_stt_config_schema_loads_nested() -> None:
    """配置 Schema 嵌套结构正确加载（来自 iflytek_asr/vad/audio/message_config）"""
    cfg = STTCollector.ConfigSchema.from_dict(
        {
            "iflytek_asr": {
                "appid": "test_appid",
                "api_key": "test_key",
                "api_secret": "test_secret",
            },
            "vad": {
                "enable": True,
                "vad_threshold": 0.5,
                "silence_seconds": 1.0,
            },
            "audio": {
                "sample_rate": 16000,
                "channels": 1,
                "dtype": "int16",
            },
            "message_config": {
                "user_id": "stt_user",
                "user_nickname": "语音",
            },
        }
    )
    assert cfg.iflytek_asr.appid == "test_appid"
    assert cfg.vad.enable is True
    assert cfg.audio.sample_rate == 16000
    assert cfg.message_config.user_nickname == "语音"
