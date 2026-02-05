"""决策Provider的Pydantic Schema定义

定义所有决策Provider的配置Schema类，用于配置验证和类型提示。
注意：这些Schema不包含`enabled`字段，该字段由管理器统一管理。
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal


class MaiCoreDecisionProviderConfig(BaseModel):
    """MaiCore决策Provider配置

    通过WebSocket与MaiCore通信进行决策。

    Attributes:
        type: Provider类型标识，必须为"maicore"
        host: MaiCore WebSocket服务器主机地址
        port: MaiCore WebSocket服务器端口
        platform: 平台标识符
        http_host: HTTP服务器主机（可选）
        http_port: HTTP服务器端口（可选）
        http_callback_path: HTTP回调路径，默认"/callback"
        connect_timeout: 连接超时时间（秒）
        reconnect_interval: 重连间隔时间（秒）
    """

    type: Literal["maicore"] = "maicore"
    host: str = Field(default="localhost", description="MaiCore WebSocket服务器主机地址")
    port: int = Field(default=8000, description="MaiCore WebSocket服务器端口", ge=1, le=65535)
    platform: str = Field(default="amaidesu", description="平台标识符")
    http_host: Optional[str] = Field(default=None, description="HTTP服务器主机（可选）")
    http_port: Optional[int] = Field(default=None, description="HTTP服务器端口", ge=1, le=65535)
    http_callback_path: str = Field(default="/callback", description="HTTP回调路径")
    connect_timeout: float = Field(default=10.0, description="连接超时时间（秒）", gt=0)
    reconnect_interval: float = Field(default=5.0, description="重连间隔时间（秒）", gt=0)

    class Config:
        extra = "ignore"  # 允许额外字段，便于扩展


class LocalLLMDecisionProviderConfig(BaseModel):
    """本地LLM决策Provider配置

    使用LLM Service统一接口进行决策。

    Attributes:
        type: Provider类型标识，必须为"local_llm"
        backend: 使用的LLM后端名称（llm, llm_fast, vlm）
        prompt_template: Prompt模板，使用{text}占位符
        fallback_mode: 降级模式（"simple", "echo", "error"）
    """

    type: Literal["local_llm"] = "local_llm"
    backend: Literal["llm", "llm_fast", "vlm"] = Field(
        default="llm", description="使用的LLM后端名称"
    )
    prompt_template: str = Field(
        default="You are a helpful AI assistant. Please respond to the user's message.\n\nUser: {text}\n\nAssistant:",
        description="Prompt模板，使用{text}占位符"
    )
    fallback_mode: Literal["simple", "echo", "error"] = Field(
        default="simple", description="降级模式"
    )

    @field_validator("prompt_template")
    @classmethod
    def validate_prompt_template(cls, v: str) -> str:
        """验证prompt_template包含{text}占位符"""
        if "{text}" not in v:
            # 尝试自动修复
            v = v.replace("{}", "{text}")
        return v

    class Config:
        extra = "ignore"


class RuleEngineDecisionProviderConfig(BaseModel):
    """规则引擎决策Provider配置

    使用本地规则进行决策，无需外部API。

    Attributes:
        type: Provider类型标识，必须为"rule_engine"
        rules_file: 规则文件路径（支持JSON和YAML格式）
        default_response: 默认响应文本
        case_sensitive: 是否区分大小写
        match_mode: 匹配模式（"any"或"all"）
    """

    type: Literal["rule_engine"] = "rule_engine"
    rules_file: str = Field(
        default="config/rules.json", description="规则文件路径（JSON或YAML格式）"
    )
    default_response: str = Field(
        default="我不理解你的意思", description="默认响应文本"
    )
    case_sensitive: bool = Field(default=False, description="是否区分大小写")
    match_mode: Literal["any", "all"] = Field(
        default="any", description="匹配模式"
    )

    class Config:
        extra = "ignore"


class EmotionJudgeDecisionProviderConfig(BaseModel):
    """情感判断决策Provider配置

    使用LLM分析文本情感并触发热键。

    Attributes:
        type: Provider类型标识，必须为"emotion_judge"
        base_url: LLM API基础URL
        api_key: LLM API密钥
        model: 模型配置（包含name、system_prompt、max_tokens、temperature等）
        cool_down_seconds: 冷却时间（秒）
    """

    type: Literal["emotion_judge"] = "emotion_judge"
    base_url: str = Field(
        default="https://api.siliconflow.cn/v1/", description="LLM API基础URL"
    )
    api_key: str = Field(default="", description="LLM API密钥")
    model: dict = Field(
        default_factory=lambda: {
            "name": "Qwen/Qwen2.5-7B-Instruct",
            "system_prompt": "你是一个主播的助手，根据主播的文本内容，判断主播的情感状态，确定触发哪一个Live2D热键以帮助主播更好地表达情感。只输出热键名称，不要包含其他任何文字或解释。",
            "max_tokens": 10,
            "temperature": 0.3
        },
        description="模型配置"
    )
    cool_down_seconds: int = Field(
        default=10, description="冷却时间（秒）", ge=0
    )

    class Config:
        extra = "ignore"


class MockDecisionProviderConfig(BaseModel):
    """模拟决策Provider配置

    用于测试的模拟Provider，返回预设响应。

    Attributes:
        type: Provider类型标识，必须为"mock"
        default_response: 默认响应文本
    """

    type: Literal["mock"] = "mock"
    default_response: str = Field(
        default="这是模拟的回复", description="默认响应文本"
    )

    class Config:
        extra = "ignore"


# 导出所有Schema
__all__ = [
    "MaiCoreDecisionProviderConfig",
    "LocalLLMDecisionProviderConfig",
    "RuleEngineDecisionProviderConfig",
    "EmotionJudgeDecisionProviderConfig",
    "MockDecisionProviderConfig",
]
