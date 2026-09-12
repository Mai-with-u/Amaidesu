"""StreamerAgent 配置 Schema（包内单一权威）

定义 ``config/agents.toml`` 的 ``[agents.streamer]`` 段及子段模型。

段树结构（TOML 视角）::

    [agents.streamer]
    rundown_id, planner_max_steps, enable_action_selection, history_limit

    [agents.streamer.persona]
    bot_name, personality, style_constraints, behavior_style, audience_salutation

    [agents.streamer.context]
    enabled, memory_recall_long_term

    [agents.streamer.background]
    enabled, light_tick_ms, cold_timeout_ms, summary_interval_ms, window_event_threshold

    [agents.streamer.background.compressor]
    concurrency, queue_max

    [agents.streamer.batch]
    batch_window_ms, batch_max_size, tick_interval_ms, enable_idle_compensation

    [agents.streamer.force]
    force_data_types, force_importance

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

设计原则：
- 组件包内单一权威：所有字段以嵌套子模型承载；中央树仅引用本类。
- LLM profile 用途名硬编码为 ``planner`` / ``replyer`` / ``summary``，与 model 块
  重构前的现行 LLMManager 解耦；T12 任务接手后再对齐 profile 池。
"""

from __future__ import annotations

from typing import Dict, List

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

    bot_name: str = Field(default="麦麦", description="VTuber 名字（注入 Replyer 表达侧）")
    personality: str = Field(
        default="活泼开朗，有些调皮，喜欢和观众互动",
        description="性格描述（注入 Replyer 表达侧）",
    )
    style_constraints: str = Field(
        default="口语化，使用网络流行语，避免机械式回复，适当使用emoji",
        description="说话风格约束（注入 Replyer 表达侧）",
    )
    behavior_style: str = Field(
        default="积极与观众互动，收到礼物和SC及时致谢，冷场时主动开新话题，遇到争议保持风度不纠缠",
        description="Planner 行动准则（注入 Planner 决策侧）",
    )
    audience_salutation: str = Field(
        default="大家",
        description="对观众的称呼（注入 Replyer 模板 ``$audience_salutation`` 变量）",
    )


class StreamerContextConfig(BaseConfig):
    """[agents.streamer.context] 段

    控制 Planner 的上下文组装路径与长记忆召回强度。
    """

    enabled: bool = Field(
        default=True,
        description="是否启用组装器路径（关闭后 Planner 跳过组装器与记忆召回，直接以直播流窗口文本注入）",
    )
    memory_recall_long_term: int = Field(
        default=3,
        ge=0,
        le=20,
        description="每次决策召回的长记忆条数上限",
    )


class StreamerCompressorConfig(BaseConfig):
    """[agents.streamer.background.compressor] 段

    压缩 worker 参数（并发与队列上限）。
    """

    concurrency: int = Field(
        default=1,
        ge=1,
        le=8,
        description="压缩 worker 并发数（默认 1 保顺序，避免乱序覆盖）",
    )
    queue_max: int = Field(
        default=100,
        ge=1,
        description="压缩队列上限（防止积压耗尽内存）",
    )


class StreamerBackgroundConfig(BaseConfig):
    """[agents.streamer.background] 段

    后台维护任务的轻循环 + 压缩 worker 参数。light_tick_ms / cold_timeout_ms /
    summary_interval_ms / window_event_threshold 与 compressor 子段真正生效
    （修复死配置）。
    """

    enabled: bool = Field(default=True, description="是否启用后台维护任务")
    light_tick_ms: int = Field(
        default=5_000,
        ge=100,
        le=60_000,
        description="轻循环 tick 间隔（毫秒）",
    )
    cold_timeout_ms: int = Field(
        default=60_000,
        ge=0,
        description="房间冷场判定阈值（毫秒）",
    )
    summary_interval_ms: int = Field(
        default=60_000,
        ge=0,
        description="低频 LLM 摘要间隔（毫秒）",
    )
    window_event_threshold: int = Field(
        default=200,
        ge=1,
        description="窗口触发压缩的条数阈值",
    )
    compressor: StreamerCompressorConfig = Field(
        default_factory=StreamerCompressorConfig,
        description="压缩 worker 配置",
    )


class StreamerBatchConfig(BaseConfig):
    """[agents.streamer.batch] 段

    弹幕聚合（含 idle 补偿公式）。
    """

    batch_window_ms: int = Field(default=3_000, ge=0, description="弹幕聚合时间窗口（毫秒）")
    batch_max_size: int = Field(default=20, ge=1, description="单批最多聚合的弹幕条数")
    tick_interval_ms: int = Field(default=300, ge=50, description="后台聚合检查间隔（毫秒）")
    enable_idle_compensation: bool = Field(default=True, description="空窗补偿开关")


class StreamerForceConfig(BaseConfig):
    """[agents.streamer.force] 段

    强制响应触发条件。
    """

    force_data_types: List[str] = Field(
        default_factory=lambda: ["super_chat", "guard", "gift"],
        description="强制响应的数据类型",
    )
    force_importance: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="importance 达到该值则强制响应",
    )


class StreamerProactiveConfig(BaseConfig):
    """[agents.streamer.proactive] 段

    主动发言触发器配置（含 rundown 间隔）。
    """

    enabled: bool = Field(default=True, description="主动发言总开关（流程单/冷场/定时等所有主动发言源）")
    cold_timeout_ms: int = Field(default=45_000, ge=0, description="冷场判定阈值（毫秒）")
    min_interval_ms: int = Field(default=120_000, ge=0, description="两次主动发言最小间隔")
    schedule_interval_ms: int = Field(default=300_000, ge=0, description="定时话题触发间隔（0 = 关闭）")
    schedule_only_cold: bool = Field(default=True, description="定时触发是否仅限冷场")
    max_per_hour: int = Field(default=6, ge=1, description="每小时主动发言次数上限")
    topic_required: bool = Field(default=True, description="话题缺失时跳过触发")
    rundown_speech_interval_ms: int = Field(
        default=3_000,
        ge=1_000,
        description="流程单环节内两次主动发言最小间隔",
    )


class StreamerWordFilterConfig(BaseConfig):
    """[agents.streamer.word_filter] 段

    输出端敏感词净化配置。
    """

    enabled: bool = Field(default=False, description="敏感词净化开关")
    words: List[str] = Field(default_factory=list, description="敏感词列表")
    replacement: str = Field(default="***", description="替换字符")
    case_sensitive: bool = Field(default=False, description="是否大小写敏感")
    drop_on_match: bool = Field(default=False, description="命中时是否整条丢弃")


class StreamerCommandConfig(BaseConfig):
    """[agents.streamer.command] 段

    parse_command 工具的命令前缀与映射。
    """

    prefix: str = Field(default="/", description="命令前缀")
    mappings: Dict[str, str] = Field(
        default_factory=lambda: {
            "chat": "chat",
            "say": "chat",
            "聊天": "chat",
            "attack": "attack",
            "攻击": "attack",
        },
        description="命令映射 {name: action}",
    )


class StreamerThinkingStreamConfig(BaseConfig):
    """[agents.streamer.thinking_stream] 段

    思考流旁路（观察面专用，best-effort 不落库）。
    """

    enabled: bool = Field(
        default=True,
        description="思考流总开关：决策/生成期间的 reasoning 增量经旁路通道推送 WebUI 控制台",
    )
    flush_interval_ms: int = Field(
        default=100,
        ge=20,
        description="思考流合帧推送间隔（毫秒）",
    )
    buffer_max: int = Field(
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
    rundown_id: str = Field(default="", description="流程单 id（空 = 使用内置默认流程单）")
    planner_max_steps: int = Field(
        default=8,
        ge=1,
        description="Planner 单决策窗 ReAct 循环最大步数（超出静默收场，防失控）",
    )
    enable_action_selection: bool = Field(
        default=True,
        description="是否让 LLM 从工具能力中选择动作",
    )
    history_limit: int = Field(
        default=30,
        ge=0,
        description="构建 prompt 时引用的历史消息条数",
    )

    # 子段
    persona: StreamerPersonaConfig = Field(
        default_factory=StreamerPersonaConfig,
        description="人设配置（bot_name/personality/style_constraints/behavior_style/audience_salutation）",
    )
    context: StreamerContextConfig = Field(
        default_factory=StreamerContextConfig,
        description="上下文组装器配置（enabled/memory_recall_long_term）",
    )
    background: StreamerBackgroundConfig = Field(
        default_factory=StreamerBackgroundConfig,
        description="后台维护任务配置（轻循环 + 压缩 worker）",
    )
    batch: StreamerBatchConfig = Field(
        default_factory=StreamerBatchConfig,
        description="弹幕聚合配置",
    )
    force: StreamerForceConfig = Field(
        default_factory=StreamerForceConfig,
        description="强制响应触发配置",
    )
    proactive: StreamerProactiveConfig = Field(
        default_factory=StreamerProactiveConfig,
        description="主动发言配置",
    )
    word_filter: StreamerWordFilterConfig = Field(
        default_factory=StreamerWordFilterConfig,
        description="输出端敏感词净化配置",
    )
    command: StreamerCommandConfig = Field(
        default_factory=StreamerCommandConfig,
        description="parse_command 工具配置",
    )
    thinking_stream: StreamerThinkingStreamConfig = Field(
        default_factory=StreamerThinkingStreamConfig,
        description="思考流旁路配置",
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
]
