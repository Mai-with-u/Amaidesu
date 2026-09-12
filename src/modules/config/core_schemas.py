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


# ---------------------------------------------------------------------------
# Dashboard（含 SubtitleWidget + DanmakuWidget 子段）
# ---------------------------------------------------------------------------


class DashboardConfig(BaseConfig):
    """Web Dashboard 配置（``[dashboard]`` 段）"""

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


# ---------------------------------------------------------------------------
# EventHistory
# ---------------------------------------------------------------------------


class EventHistoryConfig(BaseConfig):
    """事件历史记录配置（``[events]`` 段）"""

    history_size: int = Field(
        default=5000,
        ge=100,
        le=50000,
        description="事件历史内存环形缓冲大小",
    )
    persist: bool = Field(
        default=False,
        description=(
            "是否将事件历史持久化到 SQLite event_history 表。"
            "事件日志定位为运行周期观察窗，默认仅内存、重启即清，"
            "避免上一轮运行的事件经回灌混入新一轮调试视野"
        ),
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
        description="TTS 基础设施开关：开启后主播每句话都会 TTS",
    )
    provider: str = Field(
        default="gptsovits",
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
        description="发声播放队列上限，满时丢最旧",
    )
    render_timeout_ms: int = Field(
        default=60000,
        ge=0,
        description="单次发声等待超时（合成+播放，毫秒）；0 表示不限制。"
        "播放时长与语音等长，此值是防引擎卡死的兜底而非正常耗时上限",
    )
    edge_tts: Dict[str, Any] = Field(
        default_factory=dict,
        description="EdgeTTS 引擎参数（仅 provider=edge_tts 时生效）",
    )
    gptsovits: Dict[str, Any] = Field(
        default_factory=dict,
        description="GPT-SoVITS 引擎参数（仅 provider=gptsovits 时生效）",
    )
    voicebox: Dict[str, Any] = Field(
        default_factory=dict,
        description="Voicebox 引擎参数（仅 provider=voicebox 时生效；需填写 profile_id）",
    )
    omni_tts: Dict[str, Any] = Field(
        default_factory=dict,
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
        description="字幕基础设施开关：开启后主播发言自动显示字幕",
    )
    backends: List[str] = Field(
        default_factory=lambda: ["tk_gui"],
        description="启用的字幕后端列表（可多后端同时启用：tk_gui / dashboard）",
        json_schema_extra={
            "x-ui-type": "multi-select",
            "x-options": ["tk_gui", "dashboard"],
        },
    )
    tk_gui: Dict[str, Any] = Field(
        default_factory=dict,
        description="Tk GUI 字幕后端参数（SubtitleGuiService.ConfigSchema 键；缺失键自动补齐）",
    )
