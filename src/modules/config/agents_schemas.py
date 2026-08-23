"""Agent 配置 Schema 定义（v2.0.0）

定义 ``config/agents.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [agents]
    enabled = ["streamer", "game.minecraft"]

    [agents.streamer]
    planner_llm = "llm"
    replyer_llm = "llm_fast"
    reply_probability = 0.7
    budget = {max_rounds = 3, timeout_ms = 5000, finish_action = "say"}

    [agents.game]
    enabled = true
    engine = "minecraft"
    command_llm = "llm"

设计原则：
- 业务 Agent 替代旧决策/输出组件注册（[deciders]/[handlers] → [agents.*]）
- 复用 profile 名（planner_llm = "llm"）引用 model.toml 的 LLM profile
- ``budget`` 用嵌套 dict 而非独立 BaseConfig（保持简洁，字段少）
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# Agent 类型字面量
# ---------------------------------------------------------------------------


AgentType = Literal[
    "streamer",  # 主播 Agent（Planner+Replyer，旧 Amaidesu Decider  替代）
    "game",  # 游戏 Agent（占位，具体游戏子类型用 game.<name>）
    "custom",  # 自定义 Agent
]


# ---------------------------------------------------------------------------
# [agents.streamer] 配置（替代旧 [deciders.amaidesu] 等）
# ---------------------------------------------------------------------------


class StreamerAgentConfig(BaseConfig):
    """主播 Agent 配置

    替代旧版 ``[deciders.amaidesu]`` 段。融合 Planner + Replyer 两阶段决策。

    Attributes:
        planner_llm: 决策阶段使用的 LLM profile 名（引用 model.toml）
        replyer_llm: 表达阶段使用的 LLM profile 名
        reply_probability: 回复概率（0.0-1.0，越低越沉默）
        budget: Planner 决策预算（轮数/超时/收尾动作）
        room_state_enabled: 是否启用房间状态摘要（背景信息）
        room_state_cold_timeout_ms: 房间状态"冷启动"超时（无新弹幕多久触发摘要）
        room_state_llm_summary_interval_ms: 摘要刷新间隔
    """

    planner_llm: str = Field(
        default="llm_fast",
        description="决策阶段使用的 LLM profile 名（引用 model.toml 中的 [llm]/[llm_fast]/...）",
    )
    replyer_llm: str = Field(
        default="llm",
        description="表达阶段使用的 LLM profile 名",
    )
    reply_probability: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="回复概率（0.0-1.0）。越低越沉默，越高越话多",
    )
    budget: Dict[str, Any] = Field(
        default_factory=lambda: {
            "max_rounds": 3,
            "timeout_ms": 5000,
            "finish_action": "say",
        },
        description="Planner 决策预算（最大轮数/单次超时/收尾动作）",
    )
    room_state_enabled: bool = Field(
        default=True,
        description="是否启用房间状态摘要（用作决策背景信息）",
    )
    room_state_cold_timeout_ms: int = Field(
        default=60000,
        ge=0,
        description="房间状态冷启动超时（无新弹幕多久触发摘要刷新）",
    )
    room_state_llm_summary_interval_ms: int = Field(
        default=60000,
        ge=0,
        description="摘要 LLM 刷新间隔（毫秒）",
    )


# ---------------------------------------------------------------------------
# [agents.game] 配置（占位 — W3 由 game Agent 框架补全）
# ---------------------------------------------------------------------------


class GameAgentConfig(BaseConfig):
    """游戏 Agent 配置（占位）

    替代旧版游戏相关配置段。``engine`` 标识具体游戏实现
    （如 "minecraft" / "stardew" 等），W3 由具体游戏 Agent 实现补全字段。
    """

    enabled: bool = Field(default=True, description="是否启用游戏 Agent")
    engine: str = Field(
        default="minecraft",
        description="游戏引擎标识（与 [agents.game.<engine>] 子段对应）",
    )
    command_llm: str = Field(
        default="llm",
        description="游戏 Agent 决策用的 LLM profile 名",
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

    # 启用列表（哪些 Agent 参与运行）
    enabled: List[AgentType] = Field(
        default_factory=lambda: ["streamer"],
        description="启用的 Agent 列表",
        json_schema_extra={
            "x-ui-type": "multiselect",
            "x-options": ["streamer", "game", "custom"],
        },
    )

    # 各 Agent 的可选子配置
    streamer: Optional[StreamerAgentConfig] = Field(
        default=None,
        description="主播 Agent（Planner+Replyer）配置",
        json_schema_extra={"x-ui-type": "object"},
    )
    game: Optional[GameAgentConfig] = Field(
        default=None,
        description="游戏 Agent 通用配置（具体游戏子段由 W3 补全）",
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
    "GameAgentConfig",
    # 聚合
    "AgentsConfig",
    # 顶层根模型
    "AgentsRootConfig",
]
