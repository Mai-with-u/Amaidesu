"""核心系统配置 Schema 定义（v2.0.0）

定义所有非业务域的系统级配置 Schema，对应 ``config/core.toml`` 文件。

包含：meta, general, persona, context, events, dashboard, mcp, simulator, logging。

> **v2.0.0 变化**：
> - 删除 ``[maicore]`` 段（2.0.0 单进程无 MaiCore WebSocket 连接）
> - ``[context]`` 由会话存储改造为 ContextAssembler 配置（§1.44）
>
> **v2.0.4 变化**：
> - ``[pipelines]`` 正名为 ``[interceptors]``（§1.46.1 管道→事件拦截器迁移收官；
>   阶段嵌套拍平为 ``[interceptors.<name>]``，output 侧 profanity_filter 已归 Replyer）

段树详见 ``.omo/drafts/amaidesu-v2-config-tree.md``（单一事实源）。
"""

from typing import Any

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.config.schemas.logging import LoggingConfig
from src.modules.simulator.config_schema import SimulatorConfigSchema


class MetaConfig(BaseConfig):
    """配置元数据"""

    version: str = Field(
        default="2.0.4",
        description="配置版本号（用于自动迁移检测，权威定义于 multi_file_loader.py）",
    )


class GeneralConfig(BaseConfig):
    """通用配置"""

    platform_id: str = Field(
        default="amaidesu",
        description="Amaidesu 进程标识（Dashboard / 日志 / 模拟器区分用）",
    )


class PersonaConfig(BaseConfig):
    """VTuber 人设配置

    定义 VTuber 的性格和说话风格，被 Planner/Replyer 等 LLM Agent 引用。
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


class ContextAssemblerConfig(BaseConfig):
    """上下文组装器配置（v2.0.0 改造）

    旧版 ``ContextConfig`` 用于会话历史存储（memory/file）。
    新版改造为 ContextAssembler 配置（§1.44）：决定 LLM 输入如何组装。
    会话历史存储职责下放给 memory 后端，ContextAssembler 只负责"如何取"。

    Attributes:
        enabled: 是否启用上下文组装（关闭后 Agent 直接接收裸消息）
        memory_recall_viewers: 每次组装召回的观众画像条数
        memory_recall_long_term: 每次组装召回的长记忆条数
        cache_ttl_ms: 组装快照缓存 TTL（毫秒，0 表示不缓存）
    """

    enabled: bool = Field(
        default=True,
        description="是否启用上下文组装（关闭后 Agent 直接接收裸消息）",
    )
    memory_recall_viewers: int = Field(
        default=3,
        ge=0,
        le=20,
        description="每次组装召回的观众画像条数上限",
    )
    memory_recall_long_term: int = Field(
        default=3,
        ge=0,
        le=20,
        description="每次组装召回的长记忆条数上限",
    )
    cache_ttl_ms: int = Field(
        default=1000,
        ge=0,
        description="组装快照缓存 TTL（毫秒，0 表示每次重新组装）",
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


class EventHistoryConfig(BaseConfig):
    """事件历史记录配置"""

    history_size: int = Field(
        default=5000,
        ge=100,
        le=50000,
        description="事件历史内存环形缓冲大小",
    )
    persist: bool = Field(
        default=True,
        description="是否将事件历史持久化到 data/events/",
    )


class CoreConfig(BaseConfig):
    """核心系统配置根类

    聚合所有非业务域的系统级配置，对应 ``config/core.toml`` 文件。

    v2.0.0 段树：
    - ``[meta]``        — 配置元数据（CONFIG_VERSION 权威）
    - ``[general]``     — 通用配置
    - ``[persona]``     — VTuber 人设
    - ``[context]``     — ContextAssembler 配置（替代旧会话存储）
    - ``[events]``      — EventBus 事件历史
    - ``[dashboard]``   — Web Dashboard
    - ``[simulator]``   — 模拟直播间
    - ``[logging]``     — 日志
    - ``[interceptors]`` — 事件拦截器配置（动态键；2.0.4 由 ``[pipelines]`` 正名）

    v2.0.0 删除：
    - ``[maicore]``（旧 MaiCore WebSocket 连接，单进程下不再需要）
    """

    meta: MetaConfig = Field(default_factory=MetaConfig, description="配置元数据")
    general: GeneralConfig = Field(default_factory=GeneralConfig, description="通用配置")
    persona: PersonaConfig = Field(default_factory=PersonaConfig, description="VTuber 人设配置")
    context: ContextAssemblerConfig = Field(
        default_factory=ContextAssemblerConfig,
        description="上下文组装器配置（v2.0.0 替代旧会话存储）",
    )
    events: EventHistoryConfig = Field(default_factory=EventHistoryConfig, description="事件历史记录配置")
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig, description="Dashboard 配置")
    simulator: SimulatorConfigSchema = Field(
        default_factory=SimulatorConfigSchema, description="模拟直播间配置（数据文件位于 data/simulator/）"
    )
    logging: LoggingConfig = Field(default_factory=LoggingConfig, description="日志配置")
    interceptors: dict[str, Any] = Field(
        default_factory=lambda: {
            "rate_limit": {
                "enabled": True,
                "global_rate_limit": 100,
                "user_rate_limit": 10,
                "window_size": 60,
            },
            "similar_filter": {
                "enabled": True,
                "similarity_threshold": 0.85,
                "time_window": 5.0,
                "min_text_length": 3,
                "cross_user_filter": True,
            },
        },
        description="事件拦截器配置（动态键，如 rate_limit / similar_filter；2.0.4 由 [pipelines] 正名）",
    )
