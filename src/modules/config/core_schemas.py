"""核心系统配置 Schema 定义

定义所有非组件的系统级配置 Schema。
这些配置拆分到 config/core.toml 文件中。

包含：meta, general, persona, maicore, context, dashboard,
event_bus, logging, mcp, pipelines。
"""

from typing import Any

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.config.schemas.logging import LoggingConfig


class MetaConfig(BaseConfig):
    """配置元数据"""

    type: str = Field(default="meta", description="配置类型标识")
    version: str = Field(default="0.4.0", description="配置版本号（用于自动迁移检测）")


class GeneralConfig(BaseConfig):
    """通用配置"""

    type: str = Field(default="general", description="配置类型标识")
    platform_id: str = Field(default="amaidesu", description="Amaidesu 在 MaiCore 中注册的平台标识符")


class PersonaConfig(BaseConfig):
    """VTuber 人设配置

    定义 VTuber 的性格和说话风格，
    被 LLM Decider 等使用 LLM 的组件引用。
    """

    type: str = Field(default="persona", description="配置类型标识")
    bot_name: str = Field(default="麦麦", description="VTuber 名字")
    personality: str = Field(
        default="活泼开朗，有些调皮，喜欢和观众互动",
        description="性格描述（50字以内）",
    )
    style_constraints: str = Field(
        default="口语化，使用网络流行语，避免机械式回复，适当使用emoji",
        description="说话风格约束（指导 LLM 生成回复的风格）",
    )
    user_name: str = Field(default="大家", description="对观众的称呼")
    max_response_length: int = Field(default=50, description="回复长度限制（字数）")
    emotion_intensity: int = Field(
        default=7,
        description="情感表达强度 (1-10, 1=平淡, 10=丰富)",
    )


class MaiCoreConfig(BaseConfig):
    """MaiCore 连接配置"""

    type: str = Field(default="maicore", description="配置类型标识")
    host: str = Field(default="127.0.0.1", description="MaiCore WebSocket 服务器地址")
    port: int = Field(default=8000, description="MaiCore WebSocket 服务器端口")
    token: str = Field(default="", description="认证 Token（如果 MaiCore 需要认证）")


class ContextConfig(BaseConfig):
    """上下文管理配置"""

    type: str = Field(default="context", description="配置类型标识")
    storage_type: str = Field(default="memory", description="存储类型: memory 或 file")
    max_messages_per_session: int = Field(
        default=50,
        description="每个会话保留的最大消息数",
    )
    max_sessions: int = Field(default=100, description="最大会话数")
    session_timeout_seconds: int = Field(
        default=3600,
        description="会话超时时间（秒）",
    )
    enable_persistence: bool = Field(
        default=False,
        description="启用持久化（暂未实现）",
    )


class DashboardConfig(BaseConfig):
    """Web Dashboard 配置

    注意：完整配置（含 danmaku_widget / subtitle_widget 等）由
    `src.modules.dashboard.config.DashboardConfig` 管理。
    此处仅保留顶层基础字段，供 core.toml [dashboard] 段使用。
    """

    type: str = Field(default="dashboard", description="配置类型标识")
    enabled: bool = Field(default=True, description="是否启用 Dashboard")
    host: str = Field(default="127.0.0.1", description="Dashboard 监听地址")
    port: int = Field(default=60214, description="Dashboard 监听端口")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"],
        description="允许的 CORS 来源列表",
    )
    max_history_messages: int = Field(
        default=1000,
        description="WebSocket 推送的最大历史消息数",
    )
    websocket_heartbeat: int = Field(
        default=30,
        description="WebSocket 心跳间隔（秒）",
    )


class CoreConfig(BaseConfig):
    """核心系统配置根类

    聚合所有非组件的系统级配置。
    对应 config/core.toml 文件。
    """

    type: str = Field(default="core", description="配置类型标识")
    meta: MetaConfig = Field(default_factory=MetaConfig, description="配置元数据")
    general: GeneralConfig = Field(default_factory=GeneralConfig, description="通用配置")
    persona: PersonaConfig = Field(default_factory=PersonaConfig, description="VTuber 人设配置")
    maicore: MaiCoreConfig = Field(default_factory=MaiCoreConfig, description="MaiCore 连接配置")
    context: ContextConfig = Field(default_factory=ContextConfig, description="上下文管理配置")
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig, description="Dashboard 配置")
    logging: LoggingConfig = Field(default_factory=LoggingConfig, description="日志配置")
    pipelines: dict[str, Any] = Field(
        default_factory=lambda: {
            "input": {
                "rate_limit": {
                    "priority": 100,
                    "enabled": True,
                    "global_rate_limit": 100,
                    "user_rate_limit": 10,
                    "window_size": 60,
                },
                "similar_filter": {
                    "priority": 500,
                    "enabled": True,
                    "similarity_threshold": 0.85,
                    "time_window": 5.0,
                    "min_text_length": 3,
                    "cross_user_filter": True,
                },
            },
            "output": {
                "profanity_filter": {
                    "enabled": True,
                    "priority": 100,
                    "words": ["测试脏话", "示例敏感词"],
                    "replacement": "***",
                    "case_sensitive": False,
                    "drop_on_match": False,
                },
            },
        },
        description="管道配置（动态键，如 rate_limit, similar_filter 等）",
    )
