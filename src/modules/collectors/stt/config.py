"""STTCollector 配置 Schema。"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.config.schemas.base import BaseConfig


class IflytekAsrConfig(BaseModel):
    """讯飞 ASR 配置

    凭证字段默认空串（=未配置）：采集器配置段必须可默认构造，
    物化机制按注册表为文件缺的段补默认值；未配置凭证的 STT
    在运行期连接失败暴露，而非加载期拒绝整个配置文件。
    """

    appid: str = Field("", title="讯飞应用 ID", description="讯飞开放平台的应用 ID")
    api_key: str = Field("", title="讯飞 API Key", description="讯飞开放平台的 API Key")
    api_secret: str = Field("", title="讯飞 API Secret", description="讯飞开放平台的 API Secret")
    host: str = Field(
        default="iat.xf-yun.com",
        title="服务主机",
        description="讯飞服务主机地址",
        json_schema_extra={"x-ui-advanced": True},
    )
    path: str = Field(
        default="/v1", title="服务路径", description="讯飞服务路径", json_schema_extra={"x-ui-advanced": True}
    )
    language: str = Field(
        default="zh_cn",
        title="识别语言",
        description="识别语言（zh_cn = 中文）",
        json_schema_extra={"x-ui-advanced": True},
    )
    domain: str = Field(
        default="iat",
        title="识别领域",
        description="识别领域（iat: 流式识别；slm: 大模型识别），与采集器读取键一致",
        json_schema_extra={"x-ui-advanced": True},
    )
    accent: str = Field(
        default="mandarin",
        title="口音",
        description="识别口音（mandarin = 普通话）",
        json_schema_extra={"x-ui-advanced": True},
    )
    ptt: int = Field(
        default=1,
        title="标点符号",
        description="是否添加标点（1 = 添加，0 = 不添加）",
        json_schema_extra={"x-ui-advanced": True},
    )
    rlang: str = Field(
        default="zh-cn", title="修正语言", description="动态修正语言", json_schema_extra={"x-ui-advanced": True}
    )
    vinfo: int = Field(
        default=1,
        title="时间信息",
        description="是否返回拼音时间信息",
        json_schema_extra={"x-ui-advanced": True},
    )
    dwa: str = Field(
        default="wpgs",
        title="动态修正",
        description="动态修正方式（wpgs = 逐字渐进）",
        json_schema_extra={"x-ui-advanced": True},
    )


class VadConfig(BaseModel):
    """VAD (语音活动检测) 配置"""

    enable: bool = Field(default=True, title="启用 VAD", description="是否启用语音活动检测")
    vad_threshold: float = Field(
        default=0.5, title="人声判定阈值", description="判定为人声的概率阈值（0-1，越小越灵敏）"
    )
    silence_seconds: float = Field(default=1.0, title="静音判定时长", description="静音持续多久算说完一句（秒）")


class AudioConfig(BaseModel):
    """音频配置"""

    sample_rate: int = Field(default=16000, title="采样率", description="麦克风采样率（8000 或 16000）")
    channels: int = Field(
        default=1, title="声道数", description="录音声道数", json_schema_extra={"x-ui-advanced": True}
    )
    dtype: str = Field(
        default="int16",
        title="数据类型",
        description="音频数据类型（int16 / float32）",
        json_schema_extra={"x-ui-advanced": True},
    )
    stt_input_device_name: Optional[str] = Field(
        default=None, title="输入设备名称", description="使用的麦克风设备名称（留空 = 系统默认设备）"
    )
    use_remote_stream: bool = Field(
        default=False,
        title="远程音频流",
        description="改用远程推送的音频而不是本机麦克风",
        json_schema_extra={"x-ui-advanced": True},
    )


class MessageConfig(BaseModel):
    """消息配置"""

    user_id: str = Field(default="stt_user", title="用户 ID", description="语音消息展示用的用户 ID")
    user_nickname: str = Field(default="语音", title="用户昵称", description="语音消息展示用的昵称")
    user_cardname: str = Field(
        default="", title="用户名片", description="语音消息展示用的名片", json_schema_extra={"x-ui-advanced": True}
    )
    enable_group_info: bool = Field(
        default=False, title="启用群组信息", description="是否附带群组信息", json_schema_extra={"x-ui-advanced": True}
    )
    group_id: str = Field(
        default="stt_group",
        title="群组 ID",
        description="语音消息归属的群组 ID",
        json_schema_extra={"x-ui-advanced": True},
    )
    group_name: str = Field(
        default="stt_default",
        title="群组名称",
        description="语音消息归属的群组名称",
        json_schema_extra={"x-ui-advanced": True},
    )
    content_format: List[str] = Field(
        default=["text"],
        title="内容格式",
        description="发出的消息内容格式",
        json_schema_extra={"x-ui-advanced": True},
    )
    accept_format: List[str] = Field(
        default=["text", "vts_command"],
        title="接受格式",
        description="接受的消息内容格式",
        json_schema_extra={"x-ui-advanced": True},
    )
    enable_template_info: bool = Field(
        default=False, title="启用模板项", description="是否启用消息模板", json_schema_extra={"x-ui-advanced": True}
    )
    template_items: Dict[str, Any] = Field(
        default_factory=dict, title="模板项", description="消息模板内容", json_schema_extra={"x-ui-advanced": True}
    )
    main_prompt_key: str = Field(
        default="reasoning_prompt_main",
        title="主提示词键",
        description="使用的主提示词键名",
        json_schema_extra={"x-ui-advanced": True},
    )
    context_tags: Optional[List[str]] = Field(
        default=None, title="上下文标签", description="消息附带的上下文标签", json_schema_extra={"x-ui-advanced": True}
    )
    additional_config: Dict[str, Any] = Field(
        default_factory=dict,
        title="额外配置",
        description="扩展的附加配置项",
        json_schema_extra={"x-ui-advanced": True},
    )


class STTInputConfig(BaseConfig):
    """STT 输入组件配置"""

    iflytek_asr: IflytekAsrConfig = Field(
        default_factory=IflytekAsrConfig, title="讯飞语音识别", description="讯飞 ASR 服务配置"
    )
    vad: VadConfig = Field(default_factory=VadConfig, title="人声检测", description="说话起止检测配置")
    audio: AudioConfig = Field(default_factory=AudioConfig, title="音频采集", description="麦克风音频采集配置")
    message_config: MessageConfig = Field(
        default_factory=MessageConfig, title="消息设置", description="识别文本作为消息发出时的身份与格式配置"
    )
