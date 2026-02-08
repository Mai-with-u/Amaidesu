"""
输出Provider配置Schema

定义所有输出Provider的Pydantic配置模型。
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from .base import BaseProviderConfig


# 注意：SubtitleProvider, VTSProvider, TTSProvider 已迁移到自管理 Schema 架构
# 配置定义位于：
# - SubtitleProvider: src/domains/output/providers/subtitle/subtitle_provider.py
# - VTSProvider: src/domains/output/providers/vts/vts_provider.py
# - TTSProvider: src/domains/output/providers/tts/tts_provider.py


class StickerProviderConfig(BaseProviderConfig):
    """贴纸输出Provider配置"""

    type: str = "sticker"

    # 表情贴纸配置
    sticker_size: float = Field(default=0.33, ge=0.0, le=1.0, description="贴纸大小")
    sticker_rotation: int = Field(default=90, ge=0, le=360, description="贴纸旋转角度")
    sticker_position_x: float = Field(default=0.0, ge=-1.0, le=1.0, description="贴纸X位置")
    sticker_position_y: float = Field(default=0.0, ge=-1.0, le=1.0, description="贴纸Y位置")

    # 图片处理配置
    image_width: int = Field(default=256, ge=0, le=4096, description="图片宽度")
    image_height: int = Field(default=256, ge=0, le=4096, description="图片高度")

    # 冷却时间和显示时长
    cool_down_seconds: float = Field(default=5.0, ge=0.0, le=300.0, description="冷却时间（秒）")
    display_duration_seconds: float = Field(default=3.0, ge=0.0, le=300.0, description="显示时长（秒）")


class WarudoProviderConfig(BaseProviderConfig):
    """Warudo输出Provider配置"""

    type: str = "warudo"

    # WebSocket配置
    ws_host: str = Field(default="localhost", description="WebSocket主机地址")
    ws_port: int = Field(default=19190, ge=1, le=65535, description="WebSocket端口")


class ObsControlProviderConfig(BaseProviderConfig):
    """OBS控制输出Provider配置"""

    type: str = "obs_control"

    # OBS配置
    host: str = Field(default="localhost", description="OBS WebSocket主机地址")
    port: int = Field(default=4455, ge=1, le=65535, description="OBS WebSocket端口")
    password: Optional[str] = Field(default=None, description="OBS WebSocket密码")
    text_source_name: str = Field(default="text", description="文本源名称")


class GPTSoVITSProviderConfig(BaseProviderConfig):
    """GPT-SoVITS输出Provider配置"""

    type: str = "gptsovits"

    # API配置
    host: str = Field(default="127.0.0.1", description="API主机地址")
    port: int = Field(default=9880, ge=1, le=65535, description="API端口")

    # 参考音频配置
    ref_audio_path: str = Field(default="", description="参考音频路径")
    prompt_text: str = Field(default="", description="提示文本")

    # TTS参数
    text_language: str = Field(default="zh", pattern=r"^(zh|en|ja|auto)$", description="文本语言")
    prompt_language: str = Field(default="zh", pattern=r"^(zh|en|ja)$", description="提示语言")
    top_k: int = Field(default=20, ge=1, le=100, description="Top-K采样")
    top_p: float = Field(default=0.6, ge=0.0, le=1.0, description="Top-P采样")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="温度参数")
    speed_factor: float = Field(default=1.0, ge=0.1, le=3.0, description="语速因子")
    streaming_mode: bool = Field(default=True, description="是否启用流式模式")
    media_type: str = Field(default="wav", pattern=r"^(wav|mp3|ogg)$", description="媒体类型")
    text_split_method: str = Field(default="latency", pattern=r"^(latency|punctuation)$", description="文本分割方法")
    batch_size: int = Field(default=1, ge=1, le=10, description="批处理大小")
    batch_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="批处理阈值")
    repetition_penalty: float = Field(default=1.0, ge=0.5, le=2.0, description="重复惩罚")
    sample_steps: int = Field(default=10, ge=1, le=50, description="采样步数")
    super_sampling: bool = Field(default=True, description="是否启用超采样")

    # 音频输出配置
    output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
    sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")


class OmniTTSProviderConfig(BaseProviderConfig):
    """Omni TTS输出Provider配置"""

    type: str = "omni_tts"

    # GPT-SoVITS API配置
    host: str = Field(default="127.0.0.1", description="API主机地址")
    port: int = Field(default=9880, ge=1, le=65535, description="API端口")

    # 参考音频配置
    ref_audio_path: str = Field(default="", description="参考音频路径")
    prompt_text: str = Field(default="", description="提示文本")

    # TTS参数
    text_language: str = Field(default="zh", pattern=r"^(zh|en|ja|auto)$", description="文本语言")
    prompt_language: str = Field(default="zh", pattern=r"^(zh|en|ja)$", description="提示语言")
    top_k: int = Field(default=20, ge=1, le=100, description="Top-K采样")
    top_p: float = Field(default=0.6, ge=0.0, le=1.0, description="Top-P采样")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="温度参数")
    speed_factor: float = Field(default=1.0, ge=0.1, le=3.0, description="语速因子")
    streaming_mode: bool = Field(default=True, description="是否启用流式模式")

    # 音频输出配置
    output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
    sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")

    # 服务集成配置
    use_text_cleanup: bool = Field(default=True, description="是否使用文本清理")
    use_vts_lip_sync: bool = Field(default=True, description="是否使用VTS口型同步")
    use_subtitle: bool = Field(default=True, description="是否使用字幕")


class AvatarProviderConfig(BaseProviderConfig):
    """虚拟形象输出Provider配置"""

    type: str = "avatar"

    adapter_type: str = Field(
        default="vts",
        pattern=r"^(vts|vrchat|live2d)$",
        description="平台适配器类型"
    )


class RemoteStreamOutputProviderConfig(BaseProviderConfig):
    """远程流输出Provider配置"""

    type: str = "remote_stream"

    # WebSocket配置
    server_mode: bool = Field(default=True, description="服务器模式")
    host: str = Field(default="0.0.0.0", description="主机地址")
    port: int = Field(default=8765, ge=1, le=65535, description="端口")

    # 音频/图像配置
    audio_config: Dict[str, Any] = Field(default_factory=dict, description="音频配置")
    image_config: Dict[str, Any] = Field(default_factory=dict, description="图像配置")


# Provider类型映射（用于工厂模式）
OUTPUT_PROVIDER_CONFIG_MAP = {
    # "subtitle": SubtitleProviderConfig,  # 已迁移到自管理 Schema
    # "vts": VTSProviderConfig,  # 已迁移到自管理 Schema
    # "tts": TTSProviderConfig,  # 已迁移到自管理 Schema
    "sticker": StickerProviderConfig,
    "warudo": WarudoProviderConfig,
    "obs_control": ObsControlProviderConfig,
    "gptsovits": GPTSoVITSProviderConfig,
    "omni_tts": OmniTTSProviderConfig,
    "avatar": AvatarProviderConfig,
    "remote_stream": RemoteStreamOutputProviderConfig,
}


def get_output_provider_config(provider_type: str, config: Dict[str, Any]) -> BaseModel:
    """
    获取输出Provider配置对象

    Args:
        provider_type: Provider类型
        config: 配置字典

    Returns:
        对应的配置对象

    Raises:
        ValueError: 如果provider_type不支持
    """
    config_class = OUTPUT_PROVIDER_CONFIG_MAP.get(provider_type)
    if not config_class:
        raise ValueError(f"不支持的输出Provider类型: {provider_type}")

    return config_class(**config)


__all__ = [
    # Provider配置类（已迁移的自管理 Schema 不再导出）
    # "SubtitleProviderConfig",  # 已迁移到自管理 Schema
    # "VTSProviderConfig",  # 已迁移到自管理 Schema
    # "TTSProviderConfig",  # 已迁移到自管理 Schema
    "StickerProviderConfig",
    "WarudoProviderConfig",
    "ObsControlProviderConfig",
    "GPTSoVITSProviderConfig",
    "OmniTTSProviderConfig",
    "AvatarProviderConfig",
    "RemoteStreamOutputProviderConfig",
    # 工厂函数
    "get_output_provider_config",
    # 类型映射
    "OUTPUT_PROVIDER_CONFIG_MAP",
]
