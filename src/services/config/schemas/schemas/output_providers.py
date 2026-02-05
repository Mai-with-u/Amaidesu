"""
输出Provider配置Schema

定义所有输出Provider的Pydantic配置模型。
"""

from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field, field_validator

from .base import BaseProviderConfig


class SubtitleProviderConfig(BaseProviderConfig):
    """字幕输出Provider配置"""

    type: str = "subtitle"

    # GUI配置
    window_width: int = Field(default=800, ge=100, le=3840, description="字幕窗口宽度")
    window_height: int = Field(default=100, ge=50, le=2160, description="字幕窗口高度")
    window_offset_y: int = Field(default=100, ge=0, le=2160, description="字幕窗口距离底部的偏移")
    font_family: str = Field(default="Microsoft YaHei UI", description="字体名称")
    font_size: int = Field(default=28, ge=10, le=100, description="字体大小")
    font_weight: str = Field(default="bold", description="字体粗细")
    text_color: str = Field(default="white", pattern=r"^[a-zA-Z#]+$", description="文字颜色")

    # 描边配置
    outline_enabled: bool = Field(default=True, description="是否启用描边")
    outline_color: str = Field(default="black", pattern=r"^[a-zA-Z#]+$", description="描边颜色")
    outline_width: int = Field(default=2, ge=0, le=10, description="描边宽度")

    # 背景配置
    background_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$", description="背景颜色")

    # 行为配置
    fade_delay_seconds: int = Field(default=5, ge=0, le=300, description="淡出延迟（秒）")
    auto_hide: bool = Field(default=True, description="是否自动隐藏")
    window_alpha: float = Field(default=0.95, ge=0.0, le=1.0, description="窗口透明度")
    always_on_top: bool = Field(default=False, description="是否置顶")

    # OBS集成配置
    obs_friendly_mode: bool = Field(default=True, description="OBS友好模式")
    window_title: str = Field(default="Amaidesu-Subtitle-OBS", description="窗口标题")
    use_chroma_key: bool = Field(default=False, description="是否使用色度键")
    chroma_key_color: str = Field(default="#00FF00", pattern=r"^#[0-9A-Fa-f]{6}$", description="色度键颜色")

    # 窗口显示配置
    always_show_window: bool = Field(default=True, description="是否始终显示窗口")
    show_in_taskbar: bool = Field(default=True, description="是否在任务栏显示")
    window_minimizable: bool = Field(default=True, description="窗口是否可最小化")
    show_waiting_text: bool = Field(default=False, description="是否显示等待文本")


class VTSProviderConfig(BaseProviderConfig):
    """VTS输出Provider配置"""

    type: str = "vts"

    # VTS连接配置
    vts_host: str = Field(default="localhost", description="VTS WebSocket主机地址")
    vts_port: int = Field(default=8001, ge=1, le=65535, description="VTS WebSocket端口")

    # LLM智能匹配配置
    llm_matching_enabled: bool = Field(default=False, description="是否启用LLM智能热键匹配")
    llm_api_key: Optional[str] = Field(default=None, description="LLM API密钥")
    llm_base_url: Optional[str] = Field(default=None, description="LLM API地址")
    llm_model: str = Field(default="deepseek-chat", description="LLM模型名称")
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0, description="LLM温度参数")
    llm_max_tokens: int = Field(default=100, ge=1, le=4096, description="LLM最大token数")
    llm_prompt_prefix: Optional[str] = Field(
        default=None,
        description="LLM提示词前缀"
    )

    # 情感热键映射
    emotion_hotkey_mapping: Dict[str, List[str]] = Field(
        default={
            "happy": ["微笑", "笑", "开心", "高兴", "愉快", "喜悦"],
            "surprised": ["惊讶", "吃惊", "震惊", "意外"],
            "sad": ["难过", "伤心", "悲伤", "沮丧", "失落"],
            "angry": ["生气", "愤怒", "不满", "恼火"],
            "shy": ["害羞", "脸红", "羞涩", "不好意思"],
            "wink": ["眨眼", "wink", "眨眨眼"],
        },
        description="情感热键映射"
    )

    # 口型同步配置
    lip_sync_enabled: bool = Field(default=True, description="是否启用口型同步")
    volume_threshold: float = Field(default=0.01, ge=0.0, le=1.0, description="音量阈值")
    smoothing_factor: float = Field(default=0.3, ge=0.0, le=1.0, description="平滑因子")
    vowel_detection_sensitivity: float = Field(default=0.5, ge=0.0, le=2.0, description="元音检测敏感度")
    sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")

    @field_validator("llm_api_key")
    @classmethod
    def validate_llm_api_key(cls, v: Optional[str], info) -> Optional[str]:
        """验证LLM API密钥"""
        if info.data.get("llm_matching_enabled") and not v:
            raise ValueError("启用LLM匹配时必须提供API密钥")
        return v


class TTSProviderConfig(BaseProviderConfig):
    """TTS输出Provider配置"""

    type: str = "tts"

    # 引擎选择
    engine: str = Field(default="edge", pattern=r"^(edge|omni)$", description="TTS引擎类型")

    # Edge TTS配置
    voice: str = Field(default="zh-CN-XiaoxiaoNeural", description="Edge TTS语音")
    output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")

    # Omni TTS配置
    omni_config: Dict[str, Any] = Field(default_factory=dict, description="Omni TTS配置")


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

    type: str = "remote_stream_output"

    # 注意：remote_stream输出provider使用input provider的实现
    # 这里定义输出相关的配置
    stream_url: Optional[str] = Field(default=None, description="流媒体URL")
    stream_key: Optional[str] = Field(default=None, description="流媒体密钥")


# Provider类型映射（用于工厂模式）
OUTPUT_PROVIDER_CONFIG_MAP = {
    "subtitle": SubtitleProviderConfig,
    "vts": VTSProviderConfig,
    "tts": TTSProviderConfig,
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
    # Provider配置类
    "SubtitleProviderConfig",
    "VTSProviderConfig",
    "TTSProviderConfig",
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
