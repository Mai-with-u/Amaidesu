"""
测试 STTInputProviderConfig 配置

运行: uv run pytest tests/domains/input/providers/stt/test_stt_config.py -v
"""

import pytest
from pydantic_core import ValidationError
from src.domains.input.providers.stt.config import (
    STTInputProviderConfig,
    IflytekAsrConfig,
    VadConfig,
    AudioConfig,
    MessageConfig,
)


# =============================================================================
# IflytekAsrConfig 测试
# =============================================================================


def test_iflytek_asr_config_creation():
    """测试讯飞 ASR 配置创建"""
    config = IflytekAsrConfig(
        host="custom.host.com",
        path="/v2/iat",
        appid="test_appid",
        api_secret="test_secret",
        api_key="test_key",
    )

    assert config.host == "custom.host.com"
    assert config.path == "/v2/iat"
    assert config.appid == "test_appid"


def test_iflytek_asr_config_defaults():
    """测试讯飞 ASR 配置默认值"""
    config = IflytekAsrConfig(
        appid="test_appid", api_secret="test_secret", api_key="test_key"
    )

    assert config.host == "iat-api.xfyun.cn"
    assert config.path == "/v2/iat"
    assert config.language == "zh_cn"
    assert config.domain == "iat"
    assert config.accent == "mandarin"


def test_iflytek_asr_config_custom_values():
    """测试自定义讯飞 ASR 配置"""
    config = IflytekAsrConfig(
        host="custom.host.com",
        appid="test_appid",
        api_secret="test_secret",
        api_key="test_key",
        language="en_us",
    )

    assert config.host == "custom.host.com"
    assert config.language == "en_us"


def test_iflytek_asr_config_validation():
    """测试讯飞 ASR 配置验证"""
    # 缺少必需字段应该失败
    with pytest.raises(ValidationError):
        IflytekAsrConfig(appid="test")  # 缺少 api_key 和 api_secret


# =============================================================================
# VadConfig 测试
# =============================================================================


def test_vad_config_creation():
    """测试 VAD 配置创建"""
    config = VadConfig()
    assert config.enable is True
    assert config.vad_threshold == 0.5
    assert config.silence_seconds == 1.0


def test_vad_config_custom_values():
    """测试自定义 VAD 配置"""
    config = VadConfig(enable=False, vad_threshold=0.8, silence_seconds=2.0)
    assert config.enable is False
    assert config.vad_threshold == 0.8
    assert config.silence_seconds == 2.0


def test_vad_config_threshold_range():
    """测试 VAD 阈值范围"""
    # 测试不同阈值
    config1 = VadConfig(vad_threshold=0.0)
    assert config1.vad_threshold == 0.0

    config2 = VadConfig(vad_threshold=1.0)
    assert config2.vad_threshold == 1.0


# =============================================================================
# AudioConfig 测试
# =============================================================================


def test_audio_config_creation():
    """测试音频配置创建"""
    config = AudioConfig()
    assert config.sample_rate == 16000
    assert config.channels == 1
    assert config.dtype == "int16"


def test_audio_config_custom_values():
    """测试自定义音频配置"""
    config = AudioConfig(sample_rate=8000, channels=2, dtype="float32")
    assert config.sample_rate == 8000
    assert config.channels == 2
    assert config.dtype == "float32"


def test_audio_config_device_name():
    """测试音频设备名称"""
    config = AudioConfig(stt_input_device_name="麦克风 (Realtek)")
    assert config.stt_input_device_name == "麦克风 (Realtek)"


def test_audio_config_remote_stream():
    """测试远程音频流配置"""
    config = AudioConfig(use_remote_stream=True)
    assert config.use_remote_stream is True


# =============================================================================
# MessageConfig 测试
# =============================================================================


def test_message_config_creation():
    """测试消息配置创建"""
    config = MessageConfig()
    assert config.user_id == "stt_user"
    assert config.user_nickname == "语音"


def test_message_config_custom_values():
    """测试自定义消息配置"""
    config = MessageConfig(user_id="custom_user", user_nickname="自定义语音")
    assert config.user_id == "custom_user"
    assert config.user_nickname == "自定义语音"


# =============================================================================
# STTInputProviderConfig 测试
# =============================================================================


def test_stt_input_provider_config_creation():
    """测试 STT Provider 配置创建"""
    config = STTInputProviderConfig(
        iflytek_asr={
            "appid": "test_appid",
            "api_secret": "test_secret",
            "api_key": "test_key",
        }
    )

    assert isinstance(config.iflytek_asr, IflytekAsrConfig)
    assert isinstance(config.vad, VadConfig)
    assert isinstance(config.audio, AudioConfig)
    assert isinstance(config.message_config, MessageConfig)


def test_stt_input_provider_config_validation():
    """测试 STT Provider 配置验证"""
    # 缺少 iflytek_asr 应该失败
    with pytest.raises(ValidationError):
        STTInputProviderConfig()


# =============================================================================
# 序列化测试
# =============================================================================


def test_iflytek_asr_config_serialization():
    """测试讯飞 ASR 配置序列化"""
    config = IflytekAsrConfig(
        appid="test_appid",
        api_secret="test_secret",
        api_key="test_key",
        host="custom.host.com",
    )

    data = config.model_dump()

    assert data["appid"] == "test_appid"
    assert data["host"] == "custom.host.com"


def test_stt_input_provider_config_serialization():
    """测试 STT Provider 配置序列化"""
    config = STTInputProviderConfig(
        iflytek_asr={
            "appid": "test_appid",
            "api_secret": "test_secret",
            "api_key": "test_key",
        }
    )

    data = config.model_dump()

    assert "iflytek_asr" in data
    assert "vad" in data
    assert "audio" in data
    assert "message_config" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
