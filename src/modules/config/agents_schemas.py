"""Agent 配置 Schema 定义

定义 ``config/agents.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [agents]
    enabled = ["streamer", "minecraft", "text_adv"]

    [agents.streamer]  # 主播 Agent
    planner_llm = "llm"
    replyer_llm = "llm_fast"
    proactive_enabled = true

    [agents.minecraft]  # Minecraft 游戏 Agent（自包含所有字段）
    command_llm = "llm"
    max_steps = 50

    [agents.text_adv]  # 文字冒险游戏 Agent（自包含所有字段）
    command_llm = "llm"
    engine_kind = "text_adv"

设计原则：
- 业务 Agent 统一经 ``[agents]`` 段注册启用
- Agent 间无分类层——每个 Agent 是一份顶级子配置，**自己拥有全部字段**
  （无 ``[agents.game]`` 公共段，无 ``engine`` 判别字段）
- 复用 profile 名（planner_llm = "llm"）引用 model.toml 的 LLM profile
- ``enabled`` 列表接受已知 Agent 名（streamer / minecraft / text_adv），
  未知名由 Pydantic 校验拒绝（extra="forbid" + Literal 约束）
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# Agent 类型字面量
# ---------------------------------------------------------------------------


# 顶级 Agent 注册名（与 SUPPORTED_AGENTS 同步；变更时一起改）。
AgentType = Literal[
    "streamer",  # 主播 Agent（Planner+Replyer）
    "minecraft",  # Minecraft 游戏 Agent
    "text_adv",  # 文字冒险游戏 Agent
]


# ---------------------------------------------------------------------------
# [agents.streamer] 配置
# ---------------------------------------------------------------------------


class StreamerAgentConfig(BaseConfig):
    """主播 Agent 配置

    融合 Planner + Replyer 两阶段决策。

    Attributes:
        planner_llm: 决策阶段使用的 LLM profile 名（引用 model.toml）
        replyer_llm: 表达阶段使用的 LLM profile 名
        proactive_enabled: 是否启用主动发言
        proactive_cold_timeout_ms: 冷场判定阈值（毫秒）
        proactive_min_interval_ms: 两次主动发言最小间隔（毫秒）
        proactive_max_per_hour: 每小时主动发言次数上限
        agenda_enabled: 是否启用 Agenda
        agenda_path: Agenda TOML 文件路径
        profanity_enabled: 是否启用敏感词净化（输出端）
        command_prefix: 命令前缀
        command_mappings: 命令映射 {name: action}
    """

    planner_llm: str = Field(
        default="llm",
        description="Planner 使用的 LLM profile 名（ReAct 决策核心：工具编排与意图构思质量敏感，默认 llm 高质量模型）",
    )
    planner_max_steps: int = Field(
        default=8,
        ge=1,
        description="Planner 单决策窗 ReAct 循环最大步数（超出静默收场，防失控）",
    )
    replyer_llm: str = Field(
        default="llm",
        description="Replyer 使用的 LLM profile 名（默认 llm 高质量模型）",
    )

    # 旧字段名兼容
    planner_client: str = Field(default="llm", description="（兼容字段）Planner LLM client")
    replyer_client: str = Field(default="llm", description="（兼容字段）Replyer LLM client")

    # --- 弹幕聚合 ---
    batch_window_ms: int = Field(default=3000, ge=0, description="弹幕聚合时间窗口（毫秒）")
    batch_max_size: int = Field(default=20, ge=1, description="单批最多聚合的消息条数")
    tick_interval_ms: int = Field(default=300, ge=50, description="后台聚合检查间隔（毫秒）")
    enable_idle_compensation: bool = Field(default=True, description="空窗补偿开关")

    # --- 强制触发 ---
    force_data_types: List[str] = Field(
        default_factory=lambda: ["super_chat", "guard", "gift"],
        description="强制响应的数据类型",
    )
    force_importance: float = Field(default=0.8, ge=0.0, le=1.0, description="importance 达到该值则强制响应")

    # --- 人设 ---
    bot_name: str = Field(default="麦麦", description="VTuber 名称")
    history_limit: int = Field(default=30, ge=0, description="构建 prompt 时引用的历史消息条数")
    enable_action_selection: bool = Field(
        default=True,
        description="是否让 LLM 从工具能力中选择动作",
    )

    # --- 房间状态后台预处理（后台双任务：轻循环）---
    room_state_enabled: bool = Field(default=True, description="是否启用房间状态后台预处理")
    room_state_cold_timeout_ms: int = Field(default=60_000, ge=0, description="房间冷场判定阈值（毫秒）")
    room_state_llm_summary_interval_ms: int = Field(default=60_000, ge=0, description="低频 LLM 摘要间隔（毫秒）")
    room_state_summary_client: str = Field(
        default="llm_summary",
        description="房间状态摘要专用 LLM profile",
    )

    # --- 主动发言 ---
    proactive_enabled: bool = Field(default=False, description="主动发言总开关（默认关闭）")
    proactive_cold_timeout_ms: int = Field(default=45_000, ge=0, description="冷场判定阈值（毫秒）")
    proactive_min_interval_ms: int = Field(default=120_000, ge=0, description="两次主动发言最小间隔")
    proactive_schedule_interval_ms: int = Field(default=300_000, ge=0, description="定时话题触发间隔（0=关闭）")
    proactive_schedule_only_cold: bool = Field(default=True, description="定时触发是否仅限冷场")
    proactive_max_per_hour: int = Field(default=6, ge=1, description="每小时主动发言次数上限")
    proactive_topic_required: bool = Field(default=True, description="话题缺失时跳过触发")

    # --- Agenda ---
    agenda_enabled: bool = Field(default=False, description="Agenda 总开关")
    agenda_path: str = Field(default="", description="Agenda TOML 文件路径")
    agenda_expand_client: str = Field(default="llm_agenda", description="AI 扩展用 LLM profile")
    agenda_advance_eval_enabled: bool = Field(default=True, description="Planner 顺带评估开关")
    agenda_scheduler_tick_ms: int = Field(default=1_000, ge=1, description="Agenda 调度循环 tick 间隔（毫秒）")
    agenda_auto_start: bool = Field(default=True, description="setup 时自动加载并启动 Agenda")
    agenda_speech_interval_ms: int = Field(default=3_000, ge=1000, description="Agenda 环节内两次主动发言最小间隔")

    # --- 敏感词净化（输出端）---
    profanity_enabled: bool = Field(default=False, description="敏感词净化开关")
    profanity_words: List[str] = Field(default_factory=list, description="敏感词列表")
    profanity_replacement: str = Field(default="***", description="替换字符")
    profanity_case_sensitive: bool = Field(default=False, description="是否大小写敏感")
    profanity_drop_on_match: bool = Field(default=False, description="命中时是否整条丢弃")

    # --- 命令解析 ---
    command_prefix: str = Field(default="/", description="命令前缀")
    command_mappings: Dict[str, str] = Field(
        default_factory=lambda: {
            "chat": "chat",
            "say": "chat",
            "聊天": "chat",
            "attack": "attack",
            "攻击": "attack",
        },
        description="命令映射 {name: action}",
    )

    # --- 思考流旁路（ADR-008；观察面专用，best-effort 不落库）---
    thinking_stream_enabled: bool = Field(
        default=True,
        description="思考流总开关：决策/生成期间的 reasoning 增量经旁路通道推送 WebUI 控制台",
    )
    thinking_stream_flush_interval_ms: int = Field(
        default=100,
        ge=20,
        description="思考流合帧推送间隔（毫秒）",
    )
    thinking_stream_buffer_max: int = Field(
        default=400,
        ge=10,
        description="思考流环形缓冲上限（条）；超限丢最旧",
    )


# ---------------------------------------------------------------------------
# [agents.minecraft] 配置
# ---------------------------------------------------------------------------


class MinecraftAgentConfig(BaseConfig):
    """Minecraft 游戏 Agent 配置（[agents.minecraft]）

    与运行时 ``MinecraftConfig`` 的字段同步维护；命令驱动 ReAct Agent 的全部
    行为参数集中此处。无任何公共段——它就是自己的顶级子配置。
    """

    command_llm: str = Field(
        default="llm",
        description="Minecraft Agent 决策用的 LLM profile 名",
    )
    max_steps: int = Field(
        default=50,
        ge=1,
        description="单任务 ReAct 循环最大步数（超出挂起上报，防失控）",
    )


# ---------------------------------------------------------------------------
# [agents.text_adv] 配置
# ---------------------------------------------------------------------------


class TextAdvAgentConfig(BaseConfig):
    """文字冒险游戏 Agent 配置（[agents.text_adv]）

    文字冒险 Agent 的全部字段集中此处；不再走 ``[agents.game]`` + ``engine``
    判别字段模式——Agent 本身就是顶级名。
    """

    command_llm: str = Field(
        default="llm",
        description="TextAdv Agent 决策用的 LLM profile 名",
    )
    engine_kind: str = Field(default="text_adv", description="内容引擎标识（默认 text_adv）")
    decision_strategy: str = Field(
        default="first_option",
        description="推进策略（first_option=首选项；llm=LLM 选择——待实现）",
    )
    enable_event_emission: bool = Field(
        default=True,
        description="是否在感知/推进时 emit game.* 事件",
    )


# ---------------------------------------------------------------------------
# [agents] 段聚合
# ---------------------------------------------------------------------------


class AgentsConfig(BaseConfig):
    """[agents] 段聚合

    包含所有业务 Agent 的启用列表与子配置。

    使用 ``extra="forbid"`` 拒绝未知 Agent 子段，避免拼写错误静默通过。
    """

    model_config = ConfigDict(extra="forbid")

    # 启用列表（哪些 Agent 参与运行）——取值必须为已知顶级 Agent 名
    enabled: List[AgentType] = Field(
        default_factory=lambda: ["streamer"],
        description="启用的 Agent 列表",
        json_schema_extra={
            "x-ui-type": "multiselect",
            "x-options": ["streamer", "minecraft", "text_adv"],
        },
    )

    # 各 Agent 的可选子配置
    streamer: Optional[StreamerAgentConfig] = Field(
        default=None,
        description="主播 Agent（Planner+Replyer）配置",
        json_schema_extra={"x-ui-type": "object"},
    )
    minecraft: Optional[MinecraftAgentConfig] = Field(
        default=None,
        description="Minecraft 游戏 Agent 配置",
        json_schema_extra={"x-ui-type": "object"},
    )
    text_adv: Optional[TextAdvAgentConfig] = Field(
        default=None,
        description="文字冒险游戏 Agent 配置",
        json_schema_extra={"x-ui-type": "object"},
    )


# ---------------------------------------------------------------------------
# 顶层根模型（对应 config/agents.toml）
# ---------------------------------------------------------------------------


class AgentsRootConfig(BaseConfig):
    """Agents 配置根类

    对应 ``config/agents.toml`` 文件。
    """

    agents: AgentsConfig = Field(
        default_factory=AgentsConfig,
        description="[agents] 段聚合（启用列表 + 各 Agent 子配置）",
    )


__all__ = [
    # Agent 类型
    "AgentType",
    # 各 Agent ConfigSchema
    "StreamerAgentConfig",
    "MinecraftAgentConfig",
    "TextAdvAgentConfig",
    # 聚合
    "AgentsConfig",
    # 顶层根模型
    "AgentsRootConfig",
]
