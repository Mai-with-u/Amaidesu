"""StreamerAgent 配置 Schema（包内单一权威）

定义 ``config/agents.toml`` 的 ``[agents.streamer]`` 段及子段模型。

段树结构（TOML 视角）::

    [agents.streamer]
    rundown_id, planner_max_steps, planner_recent_intents_max, planner_recent_relays_max

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
    schedule_only_cold, max_per_hour, topic_required, rundown_speech_interval_ms

    [agents.streamer.word_filter]
    enabled, words, replacement, case_sensitive, drop_on_match

    [agents.streamer.command]
    prefix, mappings

    [agents.streamer.thinking_stream]
    enabled, flush_interval_ms, buffer_max

    [agents.streamer.narrative]
    game_ttl_ms, game_max_items, body_ttl_ms, body_max_items, chat_ttl_ms, chat_max_items

设计原则：
- 组件包内单一权威：所有字段以嵌套子模型承载；中央树仅引用本类。
- LLM profile 用途名硬编码为 ``planner`` / ``replyer`` / ``summary``，与 model 块
  重构前的现行 LLMManager 解耦；T12 任务接手后再对齐 profile 池。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


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
        default="口语化，使用网络流行语，避免机械式回复，适当使用emoji",
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


class StreamerContextConfig(BaseConfig):
    """[agents.streamer.context] 段

    控制 Planner 的上下文组装路径开关。人物画像行为参数（门槛/长度/
    注入上限/提取开关等）集中在 [memory] 段（storage.toml）——画像写入
    （后台循环）与注入（Planner）共享同一组策略值，不拆两处。
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
        description="压缩 worker 并发数（默认 1 保顺序，避免乱序覆盖）",
    )
    queue_max: int = Field(
        title="压缩队列上限",
        default=100,
        ge=1,
        description="压缩队列上限（防止积压耗尽内存）",
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
        description="轻循环 tick 间隔（毫秒）",
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
        description="低频 LLM 摘要间隔（毫秒）",
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
    tick_interval_ms: int = Field(title="聚合检查间隔", default=300, ge=50, description="后台聚合检查间隔（毫秒）")
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


class StreamerWordFilterConfig(BaseConfig):
    """[agents.streamer.word_filter] 段

    输出端敏感词净化配置。
    """

    enabled: bool = Field(title="是否启用敏感词净化", default=False, description="敏感词净化开关")
    words: List[str] = Field(title="敏感词名单", default_factory=list, description="敏感词列表")
    replacement: str = Field(title="替换字符", default="***", description="替换字符")
    case_sensitive: bool = Field(title="是否区分大小写", default=False, description="是否大小写敏感")
    drop_on_match: bool = Field(title="命中是否整条丢弃", default=False, description="命中时是否整条丢弃")


class StreamerCommandConfig(BaseConfig):
    """[agents.streamer.command] 段

    观众弹幕命令接线（最小接线：玩法待扩展）。命令解析是代码直连的
    内部件，不是 LLM 工具；本段只承载解析前缀、白名单映射与安全闸参数。
    mappings 即天然白名单：映射表里没有的命令一律静默丢弃。
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
    target_agent: Optional[str] = Field(
        title="委派目标 Agent",
        default=None,
        description=(
            "委派目标 Agent 注册名；留空 = 由框架委派原语解析为当前唯一启用的游戏 Agent"
            "（换游戏时只需改 agents.enabled，不必回来改这里）"
        ),
    )
    rate_window_ms: int = Field(title="限频时间窗", default=60_000, ge=1, description="限频时间窗（毫秒）")
    rate_max: int = Field(title="限频条数上限", default=3, ge=1, description="同一用户在时间窗内允许的命令条数上限")


class StreamerNarrativeConfig(BaseConfig):
    """[agents.streamer.narrative] 段

    游戏叙事 / 身体近况 / 游戏聊天三个缓冲的回放上限。每个决策窗把缓冲回放给
    Planner；已经交给过决策窗、又超过保留期的旧条目不再回放，避免主播把讲过的
    遭遇当新闻反复播。没交出过的条目不受保留期影响，只受条数上限约束。
    """

    game_ttl_ms: int = Field(
        title="游戏叙事保留期",
        default=900_000,
        ge=0,
        description="游戏进展与上报交给过决策窗后，自到达起保留多久（毫秒）；更早的进展要靠查询工具",
    )
    game_max_items: int = Field(title="游戏叙事条数上限", default=20, ge=1, description="游戏叙事最多保留条数")
    body_ttl_ms: int = Field(
        title="身体近况保留期",
        default=300_000,
        ge=0,
        description="被袭击/死亡/重生等遭遇交给过决策窗后，自到达起保留多久（毫秒）",
    )
    body_max_items: int = Field(title="身体近况条数上限", default=10, ge=1, description="身体近况最多保留条数")
    chat_ttl_ms: int = Field(
        title="游戏聊天保留期",
        default=300_000,
        ge=0,
        description="游戏里玩家与系统消息交给过决策窗后，自到达起保留多久（毫秒）",
    )
    chat_max_items: int = Field(title="游戏聊天条数上限", default=10, ge=1, description="游戏聊天最多保留条数")


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
        description="思考流合帧推送间隔（毫秒）",
    )
    buffer_max: int = Field(
        title="缓冲上限",
        default=400,
        ge=10,
        description="思考流环形缓冲上限（条）；超限丢最旧",
    )


# ---------------------------------------------------------------------------
# 顶层 Streamer 配置
# ---------------------------------------------------------------------------


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
    )
    planner_recent_intents_max: int = Field(
        title="回看最近几轮打算",
        default=6,
        ge=0,
        description="Planner 上下文列出最近几轮自己说出口的话题与指引（防止连着几轮讲同一件事；0 = 不列）",
    )
    planner_recent_relays_max: int = Field(
        title="回看最近几次补充要求",
        default=4,
        ge=0,
        description="Planner 上下文列出最近几次给手上游戏活补充过的要求（防止重复补同样的话；0 = 不列）",
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
        description="上下文组装器配置（enabled/memory_recall_long_term）",
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
    narrative: StreamerNarrativeConfig = Field(
        title="叙事回放配置",
        default_factory=StreamerNarrativeConfig,
        description="游戏叙事/身体近况/游戏聊天回放给 Planner 的保留期与条数上限",
    )


__all__ = [
    "StreamerConfig",
    "StreamerPersonaConfig",
    "StreamerContextConfig",
    "StreamerBackgroundConfig",
    "StreamerCompressorConfig",
    "StreamerBatchConfig",
    "StreamerForceConfig",
    "StreamerProactiveConfig",
    "StreamerWordFilterConfig",
    "StreamerCommandConfig",
    "StreamerThinkingStreamConfig",
    "StreamerNarrativeConfig",
]
