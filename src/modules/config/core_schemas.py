"""基础设施组件 Schema 定义

``config/infra.toml`` 7 段中各组件的 ConfigSchema 定义（TTS / 字幕 / 事件
历史 / Dashboard / SubtitleWidget / DanmakuWidget）。这些类被 ``infra_schemas.py``
的 ``InfraRootConfig`` 引用，不直接对应任何文件根——infra.toml 文件根由
``InfraRootConfig`` 持有。
"""

from typing import List

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
        description="允许的跨域来源列表（Vite 开发服务器端口，由 dashboard/vite.config.ts 配置）",
        json_schema_extra={"x-ui-advanced": True},
    )
    max_history_messages: int = Field(
        default=1000,
        title="最大历史消息数",
        description="打开面板时最多回放多少条历史消息",
        json_schema_extra={"x-ui-advanced": True},
    )
    websocket_heartbeat: int = Field(
        default=30,
        title="心跳间隔",
        description="面板连接保活心跳间隔（秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    auto_open_browser: bool = Field(default=True, title="是否自动打开浏览器", description="启动时自动打开浏览器")
    dev_mode: bool = Field(
        default=False,
        title="是否开发模式",
        description="开发模式：自动启动 Vite 开发服务器，通常通过 CLI --dev-webui 启用",
        json_schema_extra={"x-ui-advanced": True},
    )
    vite_dev_port: int = Field(
        default=60315,
        ge=1,
        le=65535,
        title="Vite 开发端口",
        description="Vite 开发服务器端口（需与 dashboard/vite.config.ts 中的 server.port 保持一致）",
        json_schema_extra={"x-ui-advanced": True},
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
        description="事件历史在内存里保留多少条（超出丢最旧）",
        json_schema_extra={"x-ui-advanced": True},
    )


# ---------------------------------------------------------------------------
# TTS（4 引擎子段 typed 化：字段全集在文件/WebUI 可发现，加载期校验；
# 引擎 Provider 经 ConfigSchema 别名引用本处定义，单一事实源在 config 侧）
# ---------------------------------------------------------------------------


class EdgeTTSParams(BaseConfig):
    """Edge TTS 引擎参数（``[tts.edge_tts]`` 段）"""

    voice: str = Field(default="zh-CN-XiaoxiaoNeural", title="语音", description="Edge TTS 语音（微软官方预设音色名）")


class GPTSoVITSParams(BaseConfig):
    """GPT-SoVITS 引擎参数（``[tts.gptsovits]`` 段；对接 api_v2）"""

    host: str = Field(default="127.0.0.1", title="API 主机地址", description="API 主机地址")
    port: int = Field(default=9880, ge=1, le=65535, title="API 端口", description="API 端口")
    ref_audio_path: str = Field(
        default="",
        title="参考音频路径",
        description="参考音频路径（由 GPT-SoVITS 服务端读取，建议绝对路径）",
    )
    prompt_text: str = Field(default="", title="提示文本", description="提示文本")
    text_language: str = Field(default="zh", pattern=r"^(zh|en|ja|auto)$", title="文本语言", description="文本语言")
    prompt_language: str = Field(default="zh", pattern=r"^(zh|en|ja)$", title="提示语言", description="提示语言")
    top_k: int = Field(
        default=20,
        ge=1,
        le=100,
        title="Top-K 采样",
        description="Top-K 采样",
        json_schema_extra={"x-ui-advanced": True},
    )
    top_p: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        title="Top-P 采样",
        description="Top-P 采样",
        json_schema_extra={"x-ui-advanced": True},
    )
    temperature: float = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        title="温度参数",
        description=(
            "温度参数（GPT 语义 token 采样）：低温度会诱发 token 复读（音频拉长数倍并伴随异常音），保持服务端默认 1.0"
        ),
        json_schema_extra={"x-ui-advanced": True},
    )
    speed_factor: float = Field(default=1.0, ge=0.1, le=3.0, title="语速因子", description="语速因子")
    streaming_mode: int = Field(
        default=1,
        ge=0,
        le=3,
        title="流式档位",
        description=(
            "流式档位：0=整段合成完一次性返回；1=按句流式（质量最好，首包=首句合成完）；"
            "2=token级流式（质量中等，首包更低）；3=token级定长chunk（响应最快，质量略降）"
        ),
        json_schema_extra={"x-ui-advanced": True},
    )
    media_type: str = Field(
        default="wav",
        pattern=r"^(wav|ogg)$",
        title="媒体类型",
        description="媒体类型（api_v2 支持 wav/ogg/aac/raw；流式 wav 为首块 WAV 头 + 裸 PCM）",
        json_schema_extra={"x-ui-advanced": True},
    )
    text_split_method: str = Field(
        default="cut5",
        pattern=r"^(cut0|cut1|cut2|cut3|cut4|cut5)$",
        title="文本分割方法",
        description="文本分割方法",
        json_schema_extra={"x-ui-advanced": True},
    )
    batch_size: int = Field(
        default=1,
        ge=1,
        le=10,
        title="批处理大小",
        description="批处理大小",
        json_schema_extra={"x-ui-advanced": True},
    )
    batch_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        title="批处理阈值",
        description="批处理阈值",
        json_schema_extra={"x-ui-advanced": True},
    )
    repetition_penalty: float = Field(
        default=1.35,
        ge=0.5,
        le=2.0,
        title="重复惩罚",
        description=(
            "重复惩罚（GPT 语义 token 防复读）：1.0 等于关闭，实测在 v5 系"
            "底模上约 40% 句子诱发 token 循环（音频拉长伴杂音）；保持服务端默认 1.35"
        ),
        json_schema_extra={"x-ui-advanced": True},
    )
    sample_steps: int = Field(
        default=10,
        ge=1,
        le=50,
        title="采样步数",
        description="采样步数",
        json_schema_extra={"x-ui-advanced": True},
    )
    super_sampling: bool = Field(
        default=True,
        title="超采样",
        description="是否启用超采样",
        json_schema_extra={"x-ui-advanced": True},
    )
    sample_rate: int = Field(
        default=32000,
        ge=8000,
        le=48000,
        title="采样率",
        description="采样率",
        json_schema_extra={"x-ui-advanced": True},
    )


class VoiceboxParams(BaseConfig):
    """Voicebox 引擎参数（``[tts.voicebox]`` 段）"""

    host: str = Field(default="127.0.0.1", title="API 主机地址", description="Voicebox API 主机地址")
    port: int = Field(default=17493, ge=1, le=65535, title="API 端口", description="Voicebox API 端口")
    profile_id: str = Field(
        default="",
        title="音色 Profile ID",
        description="音色 profile ID（在 Voicebox 中克隆音色后获得；留空时合成会报错提示）",
    )
    language: str = Field(
        default="zh",
        title="语言代码",
        description="语言代码",
        json_schema_extra={"x-ui-advanced": True},
    )
    sample_rate: int = Field(
        default=24000,
        ge=8000,
        le=48000,
        title="输出采样率",
        description="输出采样率",
        json_schema_extra={"x-ui-advanced": True},
    )
    generation_timeout_ms: int = Field(
        default=120000,
        ge=5000,
        le=300000,
        title="生成超时",
        description="生成超时（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )


class OmniTTSParams(BaseConfig):
    """全模态大模型 TTS 引擎参数（``[tts.omni_tts]`` 段；OpenAI 兼容线协议）"""

    llm_profile: str = Field(
        default="tts",
        title="LLM 用途档位",
        description="LLM 用途 profile（model.toml [llm_profiles] 键，须指向带语音输出模态的模型）",
    )
    voice: str = Field(
        default="",
        title="音色",
        description="音色（厂商词表，如 DashScope 的 Chelsie；必填，留空合成会报错提示）",
    )
    audio_format: str = Field(
        default="wav",
        title="音频格式",
        description="音频封装格式（wav/pcm16 等，随端点支持）",
        json_schema_extra={"x-ui-advanced": True},
    )
    sample_rate: int = Field(
        default=24000,
        ge=8000,
        le=48000,
        title="采样率",
        description="输出采样率（播放与时长计算用）",
        json_schema_extra={"x-ui-advanced": True},
    )
    style_prompt: str = Field(
        default="",
        title="语音风格提示",
        description="语音风格提示（系统提示词静态注入；不承载台词内容）",
    )


class TTSConfig(BaseConfig):
    """TTS 基础设施配置（``[tts]`` 段）

    Attributes:
        enabled: TTS 总开关；关闭后即便底层引擎构造完成也不会自动发声。
        provider: 装配时据此选择唯一激活引擎（edge_tts/gptsovits/voicebox/omni_tts）。
        max_queue: 发声播放队列上限；队列满时丢最旧一条保证新鲜度。
        render_timeout_ms: 单次发声（合成+播放）超时（毫秒）；0 表示不限制。
        wait_playback: 主播发言等上一句播完再放行（发言节奏 = 嘴速）。
        wait_playback_timeout_ms: 等待播放队列排空的上限（毫秒）；0 不限制。
        edge_tts: EdgeTTS 引擎参数（仅 provider=edge_tts 时生效）。
        gptsovits: GPT-SoVITS 引擎参数（仅 provider=gptsovits 时生效）。
        voicebox: Voicebox 引擎参数（仅 provider=voicebox 时生效；需填写 profile_id）。
        omni_tts: 全模态大模型 TTS 引擎参数（OpenAI 兼容线协议，经 LLMManager
            语音 profile 调用带语音输出模态的模型；仅 provider=omni_tts 时生效）。
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
        json_schema_extra={"x-ui-advanced": True},
    )
    render_timeout_ms: int = Field(
        default=60000,
        ge=0,
        title="发声超时",
        description="单次发声等待超时（合成+播放，毫秒）；0 表示不限制。"
        "播放时长与语音等长，此值是防引擎卡死的兜底而非正常耗时上限",
        json_schema_extra={"x-ui-advanced": True},
    )
    wait_playback: bool = Field(
        default=True,
        title="发言等播放",
        description="主播发言等待上一句播完再放行（发言节奏跟随语音播放，"
        "消除思考快过嘴速的错位）；关闭后退回入队即返回的 fire-and-forget",
    )
    wait_playback_timeout_ms: int = Field(
        default=60000,
        ge=0,
        title="等播超时",
        description="发言等待播放队列排空的上限（毫秒）；超时放行发言不丢话，"
        "0 表示不限制。量级覆盖当前句剩余播放加队列积压排空的最坏时间",
        json_schema_extra={"x-ui-advanced": True},
    )
    edge_tts: EdgeTTSParams = Field(
        default_factory=EdgeTTSParams,
        title="EdgeTTS 参数",
        description="EdgeTTS 引擎参数（仅 provider=edge_tts 时生效）",
    )
    gptsovits: GPTSoVITSParams = Field(
        default_factory=GPTSoVITSParams,
        title="GPT-SoVITS 参数",
        description="GPT-SoVITS 引擎参数（仅 provider=gptsovits 时生效）",
    )
    voicebox: VoiceboxParams = Field(
        default_factory=VoiceboxParams,
        title="Voicebox 参数",
        description="Voicebox 引擎参数（仅 provider=voicebox 时生效；需填写 profile_id）",
    )
    omni_tts: OmniTTSParams = Field(
        default_factory=OmniTTSParams,
        title="全模态大模型 TTS 参数",
        description="全模态大模型 TTS 引擎参数（OpenAI 兼容线协议；仅 provider=omni_tts 时生效）",
    )


# ---------------------------------------------------------------------------
# SubtitleInfra（含 tk_gui 子段）
# ---------------------------------------------------------------------------


class SubtitleGuiParams(BaseConfig):
    """Tk GUI 字幕后端参数（``[subtitle.tk_gui]`` 段）"""

    window_width: int = Field(default=800, ge=100, le=3840, title="窗口宽度", description="字幕窗口宽度")
    window_height: int = Field(default=100, ge=50, le=2160, title="窗口高度", description="字幕窗口高度")
    window_offset_y: int = Field(default=100, ge=0, le=2160, title="底部偏移", description="字幕窗口距离底部的偏移")
    font_family: str = Field(default="Microsoft YaHei UI", title="字体名称", description="字体名称")
    font_size: int = Field(default=28, ge=10, le=100, title="字体大小", description="字体大小")
    font_weight: str = Field(default="bold", title="字体粗细", description="字体粗细")
    text_color: str = Field(default="white", pattern=r"^[a-zA-Z#]+$", title="文字颜色", description="文字颜色")
    outline_enabled: bool = Field(default=True, title="启用描边", description="是否启用描边")
    outline_color: str = Field(default="black", pattern=r"^[a-zA-Z#]+$", title="描边颜色", description="描边颜色")
    outline_width: int = Field(default=2, ge=0, le=10, title="描边宽度", description="描边宽度")
    background_color: str = Field(
        default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$", title="背景颜色", description="背景颜色"
    )
    fade_delay_ms: int = Field(default=5000, ge=0, le=300000, title="淡出延迟", description="淡出延迟（毫秒）")
    auto_hide: bool = Field(default=True, title="自动隐藏", description="是否自动隐藏")
    window_alpha: float = Field(default=0.95, ge=0.0, le=1.0, title="窗口透明度", description="窗口透明度")
    always_on_top: bool = Field(default=False, title="窗口置顶", description="是否置顶")
    obs_friendly_mode: bool = Field(default=True, title="OBS 友好模式", description="OBS 友好模式")
    window_title: str = Field(default="Amaidesu-Subtitle-OBS", title="窗口标题", description="窗口标题")
    use_chroma_key: bool = Field(default=False, title="使用色度键", description="是否使用色度键")
    chroma_key_color: str = Field(
        default="#00FF00", pattern=r"^#[0-9A-Fa-f]{6}$", title="色度键颜色", description="色度键颜色"
    )
    always_show_window: bool = Field(default=True, title="始终显示窗口", description="是否始终显示窗口")
    show_in_taskbar: bool = Field(default=True, title="任务栏显示", description="是否在任务栏显示")
    window_minimizable: bool = Field(default=True, title="可最小化", description="窗口是否可最小化")
    show_waiting_text: bool = Field(default=False, title="显示等待文字", description="是否显示等待文字")


class SubtitleInfraConfig(BaseConfig):
    """字幕基础设施配置（``[subtitle]`` 段）

    Attributes:
        enabled: 字幕总开关；开启后主播发言自动经 SubtitleService 广播
            到全部启用后端。
        backends: 启用的字幕后端列表（多后端可同时启用，如 tk_gui /
            dashboard）。
        tk_gui: Tk GUI 字幕后端参数（键由 SubtitleGuiParams 声明；
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
    tk_gui: SubtitleGuiParams = Field(
        default_factory=SubtitleGuiParams,
        title="Tk GUI 后端参数",
        description="Tk GUI 字幕后端参数（仅 backends 含 tk_gui 时生效）",
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
        json_schema_extra={"x-ui-advanced": True},
    )
    check_interval_ms: int = Field(
        default=30000,
        ge=0,
        title="巡检间隔",
        description="多久检查一次各 Agent 是否存活（毫秒）；0 = 关闭巡检",
        json_schema_extra={"x-ui-advanced": True},
    )
    dead_threshold_ms: int = Field(
        default=60000,
        ge=1000,
        title="判死阈值",
        description="心跳多久没来判定 Agent 已死（毫秒）；需显著大于心跳间隔",
        json_schema_extra={"x-ui-advanced": True},
    )
    rebuild_failure_window_ms: int = Field(
        default=300000,
        ge=1000,
        title="重建失败时间窗",
        description="统计重建失败的滚动时间窗（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    max_rebuild_failures: int = Field(
        default=3,
        ge=1,
        title="重建失败上限",
        description="时间窗内重建失败达到该次数后停止自动重试（标记为出错）",
        json_schema_extra={"x-ui-advanced": True},
    )


# ---------------------------------------------------------------------------
# Diagnostics（本地诊断记录落盘：LLM 正文 / 事件流 / TTS 逐句记录）
# ---------------------------------------------------------------------------


class DiagnosticsConfig(BaseConfig):
    """本地诊断记录配置（``[diagnostics]`` 段）

    落盘根固定为 ``logs/`` 平铺子目录（``logs/<kind>/...``），本段只管
    保留期与总开关。诊断正文是场次回看的事件侧事实源，默认长期保留；
    统计事实永远在 SQLite，删正文不伤账。
    """

    enabled: bool = Field(
        default=True,
        title="启用诊断落盘",
        description="关闭后 LLM 请求正文 / 事件流 / TTS 合成记录不再写文件（统计落库不受影响）",
    )
    retention_days: int = Field(
        default=0,
        ge=0,
        title="保留天数",
        description=(
            "诊断正文文件保留天数，超期天目录整体删除；默认 0 = 永不清理，"
            "仅显式设置正整数才滚动删除（删除后对应场次的回看降级为仅消息明细）"
        ),
        json_schema_extra={"x-ui-advanced": True},
    )
