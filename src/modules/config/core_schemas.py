"""基础设施组件 Schema 定义

``config/infra.toml`` 7 段中各组件的 ConfigSchema 定义（TTS / 字幕 / 事件
历史 / Dashboard / SubtitleWidget / DanmakuWidget）。这些类被 ``infra_schemas.py``
的 ``InfraRootConfig`` 引用，不直接对应任何文件根——infra.toml 文件根由
``InfraRootConfig`` 持有。
"""

from typing import Any, Dict, List

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# SubtitleWidget / DanmakuWidget（Dashboard 子段）
# ---------------------------------------------------------------------------


class SubtitleWidgetConfig(BaseConfig):
    """字幕小部件配置"""

    enabled: bool = Field(default=True, title="是否启用", description="是否启用字幕小部件")
    enable_html_page: bool = Field(default=False, title="是否启用 HTML 页面", description="是否启用后端 HTML 页面")
    max_messages: int = Field(
        default=10, ge=1, le=50, title="最大消息数", description="最大字幕显示数量（用于历史记录）"
    )
    auto_hide_after_ms: int = Field(
        default=5000, ge=1000, le=30000, title="自动隐藏时间", description="自动隐藏时间（毫秒）"
    )
    font_size: int = Field(default=32, ge=12, le=72, title="字体大小", description="字体大小")
    font_color: str = Field(default="#ffffff", title="字体颜色", description="字体颜色")
    background_color: str = Field(default="rgba(0,0,0,0.45)", title="背景颜色", description="背景颜色")
    border_color: str = Field(default="#ff8800", title="边框颜色", description="左边边框颜色（橙色）")
    position: str = Field(default="bottom", title="显示位置", description="位置: top, bottom, center")


class DanmakuWidgetConfig(BaseConfig):
    """弹幕小部件配置"""

    enabled: bool = Field(default=True, title="是否启用", description="是否启用弹幕小部件")
    enable_html_page: bool = Field(
        default=False, title="是否启用 HTML 页面", description="是否启用后端 HTML 页面（若为 false，则使用 Vue 页面）"
    )
    max_messages: int = Field(default=30, ge=5, le=100, title="最大消息数", description="最大消息数量")
    show_danmaku: bool = Field(default=True, title="是否显示弹幕", description="显示普通弹幕")
    show_gift: bool = Field(default=True, title="是否显示礼物", description="显示礼物")
    show_super_chat: bool = Field(default=True, title="是否显示 SuperChat", description="显示 SuperChat")
    show_guard: bool = Field(default=True, title="是否显示大航海", description="显示大航海")
    show_enter: bool = Field(default=False, title="是否显示进场", description="显示进入直播间")
    show_reply: bool = Field(default=True, title="是否显示 AI 回复", description="显示 AI 回复")
    min_importance: float = Field(default=0.0, ge=0.0, le=1.0, title="最小重要性", description="最小重要性过滤")


class Game2048WidgetConfig(BaseConfig):
    """2048 棋盘小部件配置"""

    enabled: bool = Field(
        default=True,
        title="是否启用",
        description="是否启用 2048 棋盘小部件（订阅 game.state.changed 推送）",
    )
    enable_html_page: bool = Field(
        default=True,
        title="是否启用 HTML 页面",
        description="是否启用后端 HTML 透明页（/widget/2048，供 OBS 浏览器源 / Warudo 网页道具加载）",
    )


# ---------------------------------------------------------------------------
# Dashboard（含 SubtitleWidget + DanmakuWidget + Game2048Widget 子段）
# ---------------------------------------------------------------------------


class DashboardConfig(BaseConfig):
    """Web Dashboard 配置（``[dashboard]`` 段）"""

    enabled: bool = Field(default=True, title="是否启用", description="是否启用 Dashboard")
    host: str = Field(default="127.0.0.1", title="监听地址", description="Dashboard 监听地址")
    port: int = Field(default=60214, ge=1, le=65535, title="监听端口", description="Dashboard 监听端口")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:60315", "http://127.0.0.1:60315"],
        title="CORS 来源列表",
        description="允许的 CORS 来源列表（Vite 开发服务器端口，由 dashboard/vite.config.ts 配置）",
    )
    max_history_messages: int = Field(
        default=1000,
        title="最大历史消息数",
        description="WebSocket 推送的最大历史消息数",
    )
    websocket_heartbeat: int = Field(
        default=30,
        title="心跳间隔",
        description="WebSocket 心跳间隔（秒）",
    )
    auto_open_browser: bool = Field(default=True, title="是否自动打开浏览器", description="启动时自动打开浏览器")
    dev_mode: bool = Field(
        default=False,
        title="是否开发模式",
        description="开发模式：自动启动 Vite 开发服务器，通常通过 CLI --dev-webui 启用",
    )
    vite_dev_port: int = Field(
        default=60315,
        ge=1,
        le=65535,
        title="Vite 开发端口",
        description="Vite 开发服务器端口（需与 dashboard/vite.config.ts 中的 server.port 保持一致）",
    )
    danmaku_widget: DanmakuWidgetConfig = Field(
        default_factory=DanmakuWidgetConfig,
        title="弹幕小部件",
        description="弹幕小部件配置",
    )
    subtitle_widget: SubtitleWidgetConfig = Field(
        default_factory=SubtitleWidgetConfig,
        title="字幕小部件",
        description="字幕小部件配置",
    )
    game2048_widget: Game2048WidgetConfig = Field(
        default_factory=Game2048WidgetConfig,
        title="2048 棋盘小部件",
        description="2048 棋盘小部件配置",
    )


# ---------------------------------------------------------------------------
# EventHistory
# ---------------------------------------------------------------------------


class EventHistoryConfig(BaseConfig):
    """事件历史记录配置（``[events]`` 段）"""

    history_size: int = Field(
        default=5000,
        ge=100,
        le=50000,
        title="历史缓冲大小",
        description="事件历史内存环形缓冲大小",
    )


# ---------------------------------------------------------------------------
# TTS（4 引擎子段；free-form dict 由 provider schema 补全机制按需校验）
# ---------------------------------------------------------------------------


class TTSConfig(BaseConfig):
    """TTS 基础设施配置（``[tts]`` 段）

    Attributes:
        enabled: TTS 总开关；关闭后即便底层引擎构造完成也不会自动发声。
        provider: 装配时据此选择唯一激活引擎（edge_tts/gptsovits/voicebox/omni_tts）。
        max_queue: 发声播放队列上限；队列满时丢最旧一条保证新鲜度。
        render_timeout_ms: 单次发声（合成+播放）超时（毫秒）；0 表示不限制。
        edge_tts: EdgeTTS 引擎参数（仅 provider=edge_tts 时生效）。
        gptsovits: GPT-SoVITS 引擎参数（仅 provider=gptsovits 时生效）。
        voicebox: Voicebox 引擎参数（仅 provider=voicebox 时生效；需填写 profile_id）。
        omni_tts: OmniTTS 引擎参数（仅 provider=omni_tts 时生效）。
    """

    enabled: bool = Field(
        default=True,
        title="是否启用",
        description="TTS 基础设施开关：开启后主播每句话都会 TTS",
    )
    provider: str = Field(
        default="gptsovits",
        title="TTS 引擎",
        description="装配时据此选择唯一激活引擎（edge_tts/gptsovits/voicebox/omni_tts）",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["edge_tts", "gptsovits", "voicebox", "omni_tts"],
        },
    )
    max_queue: int = Field(
        default=3,
        ge=1,
        le=20,
        title="播放队列上限",
        description="发声播放队列上限，满时丢最旧",
    )
    render_timeout_ms: int = Field(
        default=60000,
        ge=0,
        title="发声超时",
        description="单次发声等待超时（合成+播放，毫秒）；0 表示不限制。"
        "播放时长与语音等长，此值是防引擎卡死的兜底而非正常耗时上限",
    )
    edge_tts: Dict[str, Any] = Field(
        default_factory=dict,
        title="EdgeTTS 参数",
        description="EdgeTTS 引擎参数（仅 provider=edge_tts 时生效）",
    )
    gptsovits: Dict[str, Any] = Field(
        default_factory=dict,
        title="GPT-SoVITS 参数",
        description="GPT-SoVITS 引擎参数（仅 provider=gptsovits 时生效）",
    )
    voicebox: Dict[str, Any] = Field(
        default_factory=dict,
        title="Voicebox 参数",
        description="Voicebox 引擎参数（仅 provider=voicebox 时生效；需填写 profile_id）",
    )
    omni_tts: Dict[str, Any] = Field(
        default_factory=dict,
        title="OmniTTS 参数",
        description="OmniTTS 引擎参数（仅 provider=omni_tts 时生效）",
    )


# ---------------------------------------------------------------------------
# SubtitleInfra（含 tk_gui 子段）
# ---------------------------------------------------------------------------


class SubtitleInfraConfig(BaseConfig):
    """字幕基础设施配置（``[subtitle]`` 段）

    Attributes:
        enabled: 字幕总开关；开启后主播发言自动经 SubtitleService 广播
            到全部启用后端。
        backends: 启用的字幕后端列表（多后端可同时启用，如 tk_gui /
            dashboard）。
        tk_gui: Tk GUI 字幕后端参数（键对应 SubtitleGuiService.ConfigSchema；
            free-form dict，缺失键由多文件加载器的后端 schema 补全机制填充）。
    """

    enabled: bool = Field(
        default=True,
        title="是否启用",
        description="字幕基础设施开关：开启后主播发言自动显示字幕",
    )
    backends: List[str] = Field(
        default_factory=lambda: ["tk_gui"],
        title="字幕后端列表",
        description="启用的字幕后端列表（可多后端同时启用：tk_gui / dashboard）",
        json_schema_extra={
            "x-options": ["tk_gui", "dashboard"],
        },
    )
    tk_gui: Dict[str, Any] = Field(
        default_factory=dict,
        title="Tk GUI 后端参数",
        description="Tk GUI 字幕后端参数（SubtitleGuiService.ConfigSchema 键；缺失键自动补齐）",
    )


# ---------------------------------------------------------------------------
# Agent 守护（心跳 + 巡检 + 自动重建）—— infra.toml [agent_supervisor] 段
# ---------------------------------------------------------------------------


class AgentSupervisorConfig(BaseConfig):
    """Agent 心跳与自动重建（守护）配置

    全部框架侧参数集中于此（默认值的唯一权威处）：BaseAgent 心跳任务与
    AgentManager 巡检循环构造时未显式传参，均从此处默认值解析。
    """

    heartbeat_interval_ms: int = Field(
        default=10000,
        ge=0,
        title="心跳间隔",
        description="Agent 心跳间隔（毫秒）；0 = 关闭心跳任务",
    )
    check_interval_ms: int = Field(
        default=30000,
        ge=0,
        title="巡检间隔",
        description="AgentManager 巡检循环周期（毫秒）；0 = 关闭巡检",
    )
    dead_threshold_ms: int = Field(
        default=60000,
        ge=1000,
        title="判死阈值",
        description="心跳距今超过该阈值判死（毫秒）；需显著大于心跳间隔",
    )
    rebuild_failure_window_ms: int = Field(
        default=300000,
        ge=1000,
        title="重建失败时间窗",
        description="重建失败计数时间窗（毫秒）",
    )
    max_rebuild_failures: int = Field(
        default=3,
        ge=1,
        title="重建失败上限",
        description="时间窗内重建失败达到该次数 → 置 ERRORED 并停止自动重试",
    )
