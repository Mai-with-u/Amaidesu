"""
STTInputProvider 配置 Schema

定义了语音转文字 Provider 的嵌套配置结构。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.config.schemas.base import BaseProviderConfig


class IflytekAsrConfig(BaseModel):
    """讯飞 ASR 配置"""

    appid: str = Field(..., description="讯飞应用 ID")
    api_key: str = Field(..., description="讯飞 API Key")
    api_secret: str = Field(..., description="讯飞 API Secret")
    host: str = Field(default="iat-api.xfyun.cn", description="讯飞 API 主机")
    path: str = Field(default="/v2/iat", description="讯飞 API 路径")
    language: str = Field(default="zh_cn", description="语言类型")
    domain: str = Field(default="iat", description="领域")
    accent: str = Field(default="mandarin", description="口音")
    ptt: int = Field(default=1, description="标点符号")
    rlang: str = Field(default="zh-cn", description="动态修正语言")
    vinfo: int = Field(default=1, description="返回时间信息")
    dwa: str = Field(default="wpgs", description="动态修正")


class VadConfig(BaseModel):
    """VAD (语音活动检测) 配置"""

    enable: bool = Field(default=True, description="是否启用 VAD")
    vad_threshold: float = Field(default=0.5, description="VAD 阈值 (0-1)")
    silence_seconds: float = Field(default=1.0, description="静音持续时间阈值 (秒)")


class AudioConfig(BaseModel):
    """音频配置"""

    sample_rate: int = Field(default=16000, description="采样率 (8000 或 16000)")
    channels: int = Field(default=1, description="声道数")
    dtype: str = Field(default="int16", description="数据类型")
    stt_input_device_name: Optional[str] = Field(default=None, description="音频输入设备名称")
    use_remote_stream: bool = Field(default=False, description="是否使用远程音频流")


class MessageConfig(BaseModel):
    """消息配置"""

    user_id: str = Field(default="stt_user", description="用户 ID")
    user_nickname: str = Field(default="语音", description="用户昵称")
    user_cardname: str = Field(default="", description="用户名片")
    enable_group_info: bool = Field(default=False, description="是否启用群组信息")
    group_id: str = Field(default="stt_group", description="群组 ID")
    group_name: str = Field(default="stt_default", description="群组名称")
    content_format: List[str] = Field(default=["text"], description="内容格式")
    accept_format: List[str] = Field(default=["text", "vts_command"], description="接受格式")
    enable_template_info: bool = Field(default=False, description="是否启用模板信息")
    template_items: Dict[str, Any] = Field(default_factory=dict, description="模板项")
    main_prompt_key: str = Field(default="reasoning_prompt_main", description="主提示词键")
    context_tags: Optional[List[str]] = Field(default=None, description="上下文标签")
    additional_config: Dict[str, Any] = Field(default_factory=dict, description="额外配置")


class STTInputProviderConfig(BaseProviderConfig):
    """STT 输入 Provider 配置"""

    type: str = "stt"

    # 嵌套配置结构（与旧插件一致）
    iflytek_asr: IflytekAsrConfig = Field(..., description="讯飞 ASR 配置")
    vad: VadConfig = Field(default_factory=VadConfig, description="VAD 配置")
    audio: AudioConfig = Field(default_factory=AudioConfig, description="音频配置")
    message_config: MessageConfig = Field(default_factory=MessageConfig, description="消息配置")
