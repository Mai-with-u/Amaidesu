"""StreamerAgent 配置 Schema（包内单一权威）

定义 ``config/agents.toml`` 的 ``[agents.streamer]`` 段及子段模型。

段树结构（TOML 视角）::

    [agents.streamer]
    (
        rundown_id,
        planner_max_steps,
        planner_recent_intents_max,
        planner_recent_relays_max,
    )
    speech_gate_window, speech_gate_ratio

    [agents.streamer.persona]
    bot_name, personality, style_constraints, behavior_style, audience_salutation

    [agents.streamer.context]
    enabled, memory_recall_long_term

    [agents.streamer.background]
    enabled, light_tick_ms, cold_timeout_ms, summary_interval_ms

    [agents.streamer.background.compressor]
    concurrency, queue_max

    [agents.streamer.batch]
    batch_window_ms, batch_max_size, tick_interval_ms, enable_idle_compensation

    [agents.streamer.force]
    force_message_types

    [agents.streamer.proactive]
    (
        enabled,
        cold_timeout_ms,
        min_interval_ms,
        schedule_interval_ms,
    )
    (
        schedule_only_cold,
        max_per_hour,
        topic_required,
        rundown_speech_interval_ms,
    )
    rundown_ready_interval_ms

    [agents.streamer.word_filter]
    enabled, words, replacement, case_sensitive, drop_on_match

    [agents.streamer.command]
    prefix, mappings

    [agents.streamer.thinking_stream]
    enabled, flush_interval_ms, buffer_max

设计原则：
- 组件包内单一权威：所有字段以嵌套子模型承载；中央树仅引用本类。
- LLM profile 用途名硬编码为 ``planner`` / ``replyer`` / ``summary``，与 model 块
  重构前的现行 LLMManager 解耦；T12 任务接手后再对齐 profile 池。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.context import ContextBudgetConfig


# ---------------------------------------------------------------------------
# 子段模型
# ---------------------------------------------------------------------------


class StreamerPersonaConfig(BaseConfig):
    """[agents.streamer.persona] 段

    主播人设与对观众的称呼。bot_name/personality/style_constraints 注入 Replyer
    表达侧，behavior_style 注入 Planner 决策侧，audience_salutation 用于称呼观众
    （Replyer 模板 ``$audience_salutation`` 变量）。
    """

    bot_name: str = Field(title="VTuber 名字", default="麦麦", description="VTuber 名字（注入 Replyer 表达侧）")
    personality: str = Field(
        title="性格描述",
        default="活泼开朗，有些调皮，喜欢和观众互动",
        description="性格描述（注入 Replyer 表达侧）",
    )
    style_constraints: str = Field(
        title="说话风格约束",
        default="口语化，使用网络流行语，避免机械式回复",
        description="说话风格约束（注入 Replyer 表达侧）",
    )
    behavior_style: str = Field(
        title="行动准则",
        default="积极与观众互动，收到礼物和SC及时致谢，冷场时主动开新话题，遇到争议保持风度不纠缠",
        description="Planner 行动准则（注入 Planner 决策侧）",
    )
    audience_salutation: str = Field(
        title="观众称呼",
        default="大家",
        description="对观众的称呼（注入 Replyer 模板 ``$audience_salutation`` 变量）",
    )


class StreamerContextConfig(BaseConfig, ContextBudgetConfig):
    """[agents.streamer.context] 段

    控制 Planner 的上下文组装路径开关，以及本场对话历史（时间线）的预算：
    预算 = min(模型窗口 × window_ratio, max_tokens)，越过提前整理线时后台把最老
    一段写成前情提要，压回到回落线。人物画像行为参数（门槛/长度/注入上限/
    提取开关等）集中在 [memory] 段（storage.toml）——画像写入（后台循环）与
    注入（Planner）共享同一组策略值，不拆两处。
    """

    enabled: bool = Field(
        title="是否启用组装器路径",
        default=True,
        description="是否启用组装器路径（关闭后 Planner 跳过组装器与画像注入，直接以直播流窗口文本注入）",
    )


class StreamerCompressorConfig(BaseConfig):
    """[agents.streamer.background.compressor] 段

    压缩 worker 参数（并发与队列上限）。
    """

    concurrency: int = Field(
        title="压缩并发数",
        default=1,
        ge=1,
        le=8,
        description="后台压缩的同时工作数（默认 1 保顺序，避免乱序覆盖）",
        json_schema_extra={"x-ui-advanced": True},
    )
    queue_max: int = Field(
        title="压缩队列上限",
        default=100,
        ge=1,
        description="待压缩内容的排队上限（防止积压耗尽内存）",
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerBackgroundConfig(BaseConfig):
    """[agents.streamer.background] 段

    后台维护任务的轻循环 + 压缩 worker 参数。
    """

    enabled: bool = Field(title="是否启用后台维护", default=True, description="是否启用后台维护任务")
    light_tick_ms: int = Field(
        title="轻循环间隔",
        default=5_000,
        ge=100,
        le=60_000,
        description="后台维护循环的巡检间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    cold_timeout_ms: int = Field(
        title="冷场判定阈值",
        default=60_000,
        ge=0,
        description="房间冷场判定阈值（毫秒）",
    )
    summary_interval_ms: int = Field(
        title="摘要间隔",
        default=60_000,
        ge=0,
        description="后台定期做 LLM 摘要的间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    compressor: StreamerCompressorConfig = Field(
        title="压缩 worker 配置",
        default_factory=StreamerCompressorConfig,
        description="压缩 worker 配置",
    )


class StreamerBatchConfig(BaseConfig):
    """[agents.streamer.batch] 段

    弹幕聚合（含 idle 补偿公式）。
    """

    batch_window_ms: int = Field(title="聚合窗口", default=3_000, ge=0, description="弹幕聚合时间窗口（毫秒）")
    batch_max_size: int = Field(title="单批条数上限", default=20, ge=1, description="单批最多聚合的弹幕条数")
    tick_interval_ms: int = Field(
        title="聚合检查间隔",
        default=300,
        ge=50,
        description="后台检查聚合窗口到期的间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    enable_idle_compensation: bool = Field(title="是否启用空窗补偿", default=True, description="空窗补偿开关")


class StreamerForceConfig(BaseConfig):
    """[agents.streamer.force] 段

    强制响应触发条件。
    """

    force_message_types: List[str] = Field(
        title="强制响应消息类型",
        default_factory=lambda: ["super_chat", "guard", "gift"],
        description="强制响应的消息类型（与 TimingGate 构造参数一致）",
    )


class StreamerProactiveConfig(BaseConfig):
    """[agents.streamer.proactive] 段

    主动发言触发器配置（含 rundown 间隔）。
    """

    enabled: bool = Field(
        title="是否启用主动发言", default=True, description="主动发言总开关（流程单/冷场/定时等所有主动发言源）"
    )
    cold_timeout_ms: int = Field(title="冷场判定阈值", default=45_000, ge=0, description="冷场判定阈值（毫秒）")
    min_interval_ms: int = Field(title="发言最小间隔", default=120_000, ge=0, description="两次主动发言最小间隔")
    schedule_interval_ms: int = Field(
        title="定时话题间隔", default=300_000, ge=0, description="定时话题触发间隔（0 = 关闭）"
    )
    schedule_only_cold: bool = Field(title="定时是否仅限冷场", default=True, description="定时触发是否仅限冷场")
    max_per_hour: int = Field(title="每小时发言上限", default=6, ge=1, description="每小时主动发言次数上限")
    topic_required: bool = Field(title="是否必须有话题", default=True, description="话题缺失时跳过触发")
    rundown_speech_interval_ms: int = Field(
        title="流程单发言间隔",
        default=3_000,
        ge=1_000,
        description="流程单环节内两次主动发言最小间隔",
    )
    rundown_ready_interval_ms: int = Field(
        title="环节内持续产出间隔",
        default=30_000,
        ge=5_000,
        description="环节供能期间给主播开口机会的节奏；只供机会不保证发言，质量由发言自检与重合度闸把关",
    )


class StreamerWordFilterConfig(BaseConfig):
    """[agents.streamer.word_filter] 段

    输出端敏感词净化配置。
    """

    enabled: bool = Field(title="是否启用敏感词净化", default=False, description="敏感词净化开关")
    words: List[str] = Field(title="敏感词名单", default_factory=list, description="敏感词列表")
    replacement: str = Field(title="替换字符", default="***", description="替换字符")
    case_sensitive: bool = Field(title="是否区分大小写", default=False, description="是否大小写敏感")
    drop_on_match: bool = Field(title="命中是否整条丢弃", default=False, description="命中时是否整条丢弃")


class CommandToolMapping(BaseConfig):
    """``[[agents.streamer.command.tool_mappings]]`` 元素：观众命令 → 一次固定的工具调用

    遥控型游戏（按键、选项）没有可委派的后台任务，观众命令直接变成一次工具
    调用（如 ``/up`` → ``game_2048_press(key=up)``）。参数由配置写死，观众
    的命令参数不透传——命令面只认白名单里的固定动作。
    """

    command: str = Field(title="命令名", default="", description="命令名（不含前缀，大小写不敏感）")
    tool: str = Field(title="工具全名", default="", description="要调用的工具全名（如 game_2048_press）")
    arguments: Dict[str, Any] = Field(
        title="调用参数",
        default_factory=dict,
        description="固定的调用参数（观众不能改）",
    )


class StreamerCommandConfig(BaseConfig):
    """[agents.streamer.command] 段

    观众弹幕命令接线（最小接线：玩法待扩展）。命令解析是代码直连的
    内部件，不是 LLM 工具；本段只承载解析前缀、白名单映射与安全闸参数。
    两张映射表合起来即天然白名单：表里没有的命令一律静默丢弃。
    ``mappings`` 给委派型游戏（命令 → 自然语言指令，经 framework_delegate），
    ``tool_mappings`` 给遥控型游戏（命令 → 固定工具调用）；同名以后者为准。
    """

    enabled: bool = Field(
        title="是否启用命令接线",
        default=True,
        description="命令接线开关（接线已完成，默认开放机制；实际可用性由 mappings 白名单决定）",
    )
    prefix: str = Field(title="命令前缀", default="/", description="命令前缀")
    mappings: Dict[str, str] = Field(
        title="命令映射表",
        default_factory=dict,
        description="命令名 → 委派语义目标（给游戏 Agent 的自然语言指令，作为 framework_delegate 的 instruction）",
    )
    tool_mappings: List[CommandToolMapping] = Field(
        title="命令工具映射表",
        default_factory=list,
        description=(
            "命令名 → 固定的工具调用（遥控型游戏用，如 /up → game_2048_press(key=up)）；与 mappings 同名时以本表为准"
        ),
    )
    target_agent: Optional[str] = Field(
        title="委派目标 Agent",
        default=None,
        description=(
            "委派目标 Agent 注册名；留空 = 由框架委派原语解析为当前唯一启用的游戏 Agent"
            "（换游戏时只需改 agents.enabled，不必回来改这里）"
        ),
    )
    rate_window_ms: int = Field(
        title="限频时间窗",
        default=60_000,
        ge=1,
        description="命令限频的统计时间窗（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    rate_max: int = Field(
        title="限频条数上限",
        default=3,
        ge=1,
        description="同一用户在一个时间窗内允许发出的命令条数上限",
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerThinkingStreamConfig(BaseConfig):
    """[agents.streamer.thinking_stream] 段

    思考流旁路（观察面专用，best-effort 不落库）。
    """

    enabled: bool = Field(
        title="是否启用思考流",
        default=True,
        description="思考流总开关：决策/生成期间的 reasoning 增量经旁路通道推送 WebUI 控制台",
    )
    flush_interval_ms: int = Field(
        title="推送间隔",
        default=100,
        ge=20,
        description="思考流合并推送的间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    buffer_max: int = Field(
        title="缓冲上限",
        default=400,
        ge=10,
        description="思考流环形缓冲上限（条）；超限丢最旧",
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerLiveFrameConfig(BaseConfig):
    """[agents.streamer.live_frame] 段

    决策时看一眼直播画面（画面来自 collectors.toml 的 ``live_frame`` 采集器）。
    只在 planner 档位的主模型能看图时生效；每个决策窗的第一次请求尾部必挂
    最新一帧（查询式取自图像暂存的 latest 槽位，静止画面重复看是设计内行为）；
    同窗后续 ReAct 步不重复挂。
    """

    enabled: bool = Field(
        title="是否看直播画面",
        default=False,
        description="决策时把最新的直播画面挂在请求尾部（需同时启用 live_frame 采集器、主模型支持视觉）",
    )
    stale_warn_ms: int = Field(
        title="画面过时告警阈值",
        default=60_000,
        ge=0,
        description="挂帧时最新画面帧龄超过它就记一条告警（毫秒，限频 5 分钟一条）；用于暴露 OBS 画面断流",
    )


# ---------------------------------------------------------------------------
# 顶层 Streamer 配置
# ---------------------------------------------------------------------------


class StreamerMemoryConfig(BaseConfig):
    """[agents.streamer.memory] —— 主播自己的记忆：场次回顾 + 观众画像

    场次回顾在收播时写（启动时补写上一场缺失的），开播后注入最近几场；
    观众画像在收播时为本场最活跃、互动量达门槛的几位常客更新印象。
    两样都用 summary 档位的模型，失败只降级不阻断直播主链路。
    """

    recap_enabled: bool = Field(
        title="是否启用场次回顾",
        default=True,
        description="收播时写本场回顾、开播后注入上场回顾（关闭后主播跨场次不记得自己做过什么）",
    )
    recap_max_length: int = Field(
        title="回顾长度上限",
        default=500,
        ge=100,
        le=2000,
        description="单场回顾文本长度上限（字符；提示词约束 LLM 篇幅，超出截断）",
    )
    recap_inject_count: int = Field(
        title="开播注入最近几场",
        default=1,
        ge=0,
        le=5,
        description="开播后注入最近几场的回顾（0 = 不注入，只留工具按需查）",
    )
    roster_enabled: bool = Field(
        title="是否启用观众画像",
        default=True,
        description="收播时为本场活跃常客更新印象（关闭后收播不再更新，已有印象仍注入）",
    )
    roster_min_interactions: int = Field(
        title="建档门槛",
        default=3,
        ge=1,
        le=100,
        description="累计互动量（viewers.interaction_count）达到多少才建档；不够格不建、自然不注入",
    )
    roster_batch_max: int = Field(
        title="每场更新人数上限",
        default=5,
        ge=1,
        le=20,
        description="每场收播最多为几位常客更新印象（按本场弹幕数取最活跃的）",
    )
    profile_max_length: int = Field(
        title="印象长度上限",
        default=400,
        ge=100,
        le=1000,
        description="单人印象文本长度上限（字符；提示词约束 LLM 篇幅，超出截断）",
    )
    notebook_max_chars: int = Field(
        title="备忘录容量上限",
        default=2000,
        ge=100,
        le=20000,
        description="本场备忘录（工作笔记）的字符上限，超出截断；备忘录每轮全量注入决策与表达两层，"
        "覆盖写的结论集量级天然小，默认值按注入成本与结论体量折中",
        json_schema_extra={"x-ui-advanced": True},
    )


class StreamerConfig(BaseConfig):
    """主播 Agent 完整配置（[agents.streamer] 段及子段）

    组件包内单一权威：所有子段都集中在本类下，中央树仅引用本类（agents_schemas
    不再持有镜像定义）。
    """

    # 根段：流程单与决策循环控制
    rundown_id: str = Field(title="流程单 ID", default="", description="流程单 id（空 = 使用内置默认流程单）")
    planner_max_steps: int = Field(
        title="决策最大步数",
        default=8,
        ge=1,
        description="Planner 单决策窗 ReAct 循环最大步数（超出静默收场，防失控）",
        json_schema_extra={"x-ui-advanced": True},
    )
    planner_recent_intents_max: int = Field(
        title="回看最近几轮打算",
        default=60,
        ge=0,
        description="Planner 上下文列出最近几轮自己说出口的话题与指引（防止连着几轮讲同一件事；"
        "60 轮约覆盖 15-20 分钟，按 18 秒一轮口径；0 = 不列）",
    )
    planner_recent_relays_max: int = Field(
        title="回看最近几次补充要求",
        default=4,
        ge=0,
        description="Planner 上下文列出最近几次给手上游戏活补充过的要求（防止重复补同样的话；0 = 不列）",
    )
    reference_tools: List[str] = Field(
        title="每轮自动参考的只读工具",
        default_factory=list,
        description="决策前自动调用的只读工具全名（无参数），结果放进参考段的【现场速览】；"
        "省掉“先查一步再开口”的请求。按所接游戏配置，空 = 不调用",
        json_schema_extra={"x-ui-advanced": True},
    )
    speech_gate_window: int = Field(
        title="发言闸比对窗口条数",
        default=10,
        ge=0,
        description="reply 生成台词后，与最近这么多条已说发言做重合度比对（0 = 关闭该检查）",
        json_schema_extra={"x-ui-advanced": True},
    )
    speech_gate_ratio: float = Field(
        title="发言闸判重阈值",
        default=0.5,
        gt=0.0,
        le=1.0,
        description="台词对最近发言的字符重合度达到该值即打回（场次 31 真实发言回放定标："
        "正常发言上限约 0.42，复读对落在 0.53）",
        json_schema_extra={"x-ui-advanced": True},
    )

    # 子段
    persona: StreamerPersonaConfig = Field(
        title="人设配置",
        default_factory=StreamerPersonaConfig,
        description="人设配置（bot_name/personality/style_constraints/behavior_style/audience_salutation）",
    )
    context: StreamerContextConfig = Field(
        title="上下文组装器配置",
        default_factory=StreamerContextConfig,
        description="上下文组装器配置（enabled + 对话历史预算）",
    )
    memory: StreamerMemoryConfig = Field(
        title="主播记忆",
        default_factory=StreamerMemoryConfig,
        description="主播自己的记忆：场次回顾（收播写、开播注入）与观众画像（收播更新观众印象）",
    )
    background: StreamerBackgroundConfig = Field(
        title="后台维护配置",
        default_factory=StreamerBackgroundConfig,
        description="后台维护任务配置（轻循环 + 压缩 worker）",
    )
    batch: StreamerBatchConfig = Field(
        title="弹幕聚合配置",
        default_factory=StreamerBatchConfig,
        description="弹幕聚合配置",
    )
    force: StreamerForceConfig = Field(
        title="强制响应配置",
        default_factory=StreamerForceConfig,
        description="强制响应触发配置",
    )
    proactive: StreamerProactiveConfig = Field(
        title="主动发言配置",
        default_factory=StreamerProactiveConfig,
        description="主动发言配置",
    )
    word_filter: StreamerWordFilterConfig = Field(
        title="敏感词净化配置",
        default_factory=StreamerWordFilterConfig,
        description="输出端敏感词净化配置",
    )
    command: StreamerCommandConfig = Field(
        title="命令接线配置",
        default_factory=StreamerCommandConfig,
        description="观众命令接线配置（代码直连，非 LLM 工具）",
    )
    thinking_stream: StreamerThinkingStreamConfig = Field(
        title="思考流配置",
        default_factory=StreamerThinkingStreamConfig,
        description="思考流旁路配置",
    )
    live_frame: StreamerLiveFrameConfig = Field(
        title="直播画面",
        default_factory=StreamerLiveFrameConfig,
        description="决策时看一眼直播画面（默认关闭）",
    )


__all__ = [
    "StreamerConfig",
    "StreamerPersonaConfig",
    "StreamerContextConfig",
    "StreamerMemoryConfig",
    "StreamerBackgroundConfig",
    "StreamerCompressorConfig",
    "StreamerBatchConfig",
    "StreamerForceConfig",
    "StreamerProactiveConfig",
    "StreamerWordFilterConfig",
    "StreamerCommandConfig",
    "StreamerThinkingStreamConfig",
    "StreamerLiveFrameConfig",
]
