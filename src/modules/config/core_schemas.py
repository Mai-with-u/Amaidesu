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

    version: str = Field(default="0.4.0", description="配置版本号（用于自动迁移检测）")


class GeneralConfig(BaseConfig):
    """通用配置"""

    platform_id: str = Field(default="amaidesu", description="Amaidesu 在 MaiCore 中注册的平台标识符")


class PersonaConfig(BaseConfig):
    """VTuber 人设配置

    定义 VTuber 的性格和说话风格，
    被 LLM Decider 等使用 LLM 的组件引用。
    """

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

    host: str = Field(default="127.0.0.1", description="MaiCore WebSocket 服务器地址")
    port: int = Field(default=8000, description="MaiCore WebSocket 服务器端口")
    token: str = Field(default="", description="认证 Token（如果 MaiCore 需要认证）")


class ContextConfig(BaseConfig):
    """上下文管理配置"""

    storage_type: str = Field(
        default="memory",
        description="存储类型: memory 或 file",
        json_schema_extra={"x-ui-type": "select", "x-options": ["memory", "file"]},
    )
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


class SubtitleWidgetConfig(BaseConfig):
    """字幕小部件配置"""

    enabled: bool = Field(default=True, description="是否启用字幕小部件")
    enable_html_page: bool = Field(default=False, description="是否启用后端 HTML 页面")
    max_messages: int = Field(default=10, ge=1, le=50, description="最大字幕显示数量（用于历史记录）")
    auto_hide_after_ms: int = Field(default=5000, ge=1000, le=30000, description="自动隐藏时间（毫秒）")
    font_size: int = Field(default=32, ge=12, le=72, description="字体大小")
    font_color: str = Field(default="#ffffff", description="字体颜色")
    background_color: str = Field(default="rgba(0,0,0,0.45)", description="背景颜色")
    border_color: str = Field(default="#ff8800", description="左边边框颜色（橙色）")
    position: str = Field(default="bottom", description="位置: top, bottom, center")


class DanmakuWidgetConfig(BaseConfig):
    """弹幕小部件配置"""

    enabled: bool = Field(default=True, description="是否启用弹幕小部件")
    enable_html_page: bool = Field(default=False, description="是否启用后端 HTML 页面（若为 false，则使用 Vue 页面）")
    max_messages: int = Field(default=30, ge=5, le=100, description="最大消息数量")
    show_danmaku: bool = Field(default=True, description="显示普通弹幕")
    show_gift: bool = Field(default=True, description="显示礼物")
    show_super_chat: bool = Field(default=True, description="显示 SuperChat")
    show_guard: bool = Field(default=True, description="显示大航海")
    show_enter: bool = Field(default=False, description="显示进入直播间")
    show_reply: bool = Field(default=True, description="显示 AI 回复")
    min_importance: float = Field(default=0.0, ge=0.0, le=1.0, description="最小重要性过滤")


class DashboardConfig(BaseConfig):
    """Web Dashboard 配置"""

    enabled: bool = Field(default=True, description="是否启用 Dashboard")
    host: str = Field(default="127.0.0.1", description="Dashboard 监听地址")
    port: int = Field(default=60214, description="Dashboard 监听端口")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:60315", "http://127.0.0.1:60315"],
        description="允许的 CORS 来源列表（Vite 开发服务器端口，由 dashboard/vite.config.ts 配置）",
    )
    max_history_messages: int = Field(
        default=1000,
        description="WebSocket 推送的最大历史消息数",
    )
    websocket_heartbeat: int = Field(
        default=30,
        description="WebSocket 心跳间隔（秒）",
    )
    auto_open_browser: bool = Field(default=True, description="启动时自动打开浏览器")
    dev_mode: bool = Field(
        default=False,
        description="开发模式：自动启动 Vite 开发服务器，通常通过 CLI --dev-webui 启用",
    )
    vite_dev_port: int = Field(
        default=60315,
        description="Vite 开发服务器端口（需与 dashboard/vite.config.ts 中的 server.port 保持一致）",
    )
    danmaku_widget: DanmakuWidgetConfig = Field(
        default_factory=DanmakuWidgetConfig,
        description="弹幕小部件配置",
    )
    subtitle_widget: SubtitleWidgetConfig = Field(
        default_factory=SubtitleWidgetConfig,
        description="字幕小部件配置",
    )


class CoreConfig(BaseConfig):
    """核心系统配置根类

    聚合所有非组件的系统级配置。
    对应 config/core.toml 文件。
    """

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
