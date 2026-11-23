"""Agent 实例化工厂（配置名 → 具体类）

配置名 → 具体类的唯一映射，是启动装配与 Dashboard 动态启停共享的
单一构造路径：所有 Agent 实例化都经 ``instantiate_agent``，组合根只
负责收集基础设施服务并透传，不自行 new Agent 类。

配置名映射：
- streamer   → StreamerAgent
- minecraft  → MinecraftAgent
- text_adv   → TextAdvGameAgent
- game_2048  → Game2048Agent

Agent 间无分类层；每个 Agent 是一等公民。
"""

from __future__ import annotations

import inspect
from typing import Any, Optional

from src.modules.agents.base import BaseAgent
from src.modules.agents.deps import AgentDeps
from src.modules.logging import get_logger
from src.modules.skills import get_skill_library

# Agent 登记表：注册名 → 中文职责描述。
# 组合根注册 Agent 时从这里取描述，spec_provider 恒等于注册名（取值域是
# 封闭枚举："framework" / Agent 名 / "mcp" 预留），不在登记表逐条复述。
AGENT_BINDINGS: dict[str, str] = {
    "streamer": "直播主播决策主体：聚合弹幕 → Planner 决策 → Replyer 表达",
    "minecraft": "游戏 AI 玩家代理（Minecraft / MaiCraftMod）",
    "text_adv": "文字冒险游戏 Agent（视觉小说观察与上报）",
    "game_2048": "2048 游戏 Agent（棋局宿主，主播按键驱动，无自动走子）",
}

# 已实现的 Agent 注册名（从登记表派生，保持既有引用方兼容）
SUPPORTED_AGENTS: tuple[str, ...] = tuple(AGENT_BINDINGS)

_logger = get_logger("AgentFactory")

# StreamerAgent 形参与 AgentDeps 字段同名直传的依赖：构造参数从这份清单编程式派生，
# 不再逐字段手抄 kwargs（deps 字段改名时这里同步改一处即可）。
# 形参名不同的例外单独映射：deps.storage_ledger → 形参 stream_ledger。
_STREAMER_DEPS_FIELDS: tuple[str, ...] = (
    "llm_manager",
    "prompt_manager",
    "event_bus",
    "tool_registry",
    "rundown_repo",
    "rundown_instance_repo",
    "rundown_transition_repo",
    "chat_repo",
    "sessions_repo",
    "topic_repo",
    "viewer_repo",
    "commitments_repo",
    "archive_repo",
    "memory_index_repo",
    "embedding_setup",
    "memory_cue_log_repo",
    "game_player_repo",
    "social_fact_repo",
    "work_docs_repo",
    "talk_card_repo",
    "event_repo",
    "privacy_service",
    "context_assembler_config",
    "speech_config",
    "tts_engine",
    "subtitle_service",
    "session_manager",
    "thinking_sink",
    "task_ledger",
    "stall_auto_cancel_alerts",
)


def _parse_game_config(model_cls: type, config: Optional[dict[str, Any]], label: str) -> Any:
    """解析游戏 Agent 的 Pydantic 配置；解析失败记告警并回落默认配置。"""
    try:
        return model_cls(**config) if config else model_cls()
    except Exception as exc:
        _logger.warning(f"解析 {label} 失败: {exc}; 使用默认配置")
        return model_cls()


def _accepts_init_kwarg(agent_cls: type, param_name: str) -> bool:
    """探测 Agent 构造器是否声明了指定关键字形参（或接收 **kwargs），实现可选基建的平滑注入。"""
    params = inspect.signature(agent_cls.__init__).parameters
    return param_name in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def instantiate_agent(
    name: str,
    config: Optional[dict[str, Any]],
    *,
    deps: AgentDeps,
) -> Optional[BaseAgent]:
    """按名实例化 Agent；未知名字返回 None。

    基础设施依赖经 ``deps``（``AgentDeps`` 值对象）整体传入——依赖清单
    单点定义在该类，本工厂不再自持签名。工厂收到的是**已解析**的值：
    None 字段 = 该基建确实未装配，Agent 按各自降级语义处理（回退合成
    是调用方 ``AgentManager._resolve_deps`` 的职责，不在本层）。

    依赖按各 Agent 实际需要取用（其余字段忽略）：
    - streamer：thinking_sink / speech_config / tts_engine / subtitle_service /
      session_manager / rundown_repo / rundown_instance_repo /
      rundown_transition_repo / chat_repo / sessions_repo / topic_repo /
      viewer_repo / commitments_repo / archive_repo / social_fact_repo / memory_index_repo /
      embedding_setup / memory_cue_log_repo / event_repo /
      context_assembler_config / task_ledger（委派渲染的机器状态查询源）。
      仓储为 None 时对应能力降级（chat_repo 缺失 = 对话历史读取整体短路，
      Planner/Replyer 无历史上下文；承诺/档案仓储缺失 = 账本读写整体关闭；
      索引仓储缺失 = 记忆检索索引不补算），
      组合根必须传入
    - minecraft：thinking_sink / task_tracker / creature_repo（缺失 = 生物名册只在
      内存里，不跨场次）/ work_docs_repo / social_fact_repo（缺失 = 不注入存档公共
      社交规矩）/ game_player_repo（缺失 = 游戏公屏发言人仅按原名显示）/ embedding_setup（可选，
      缺失时本场短期记忆降级为关键词检索）；llm_profile 使用 Agent
      类默认值（``[llm_profiles.minecraft]`` 段）；技能库在工厂内取全局单例注入
    - text_adv / game_2048：工厂只做"名字 → 包内 build"——配置解析后交给
      各包的 ``build(config, deps)``，感知/动作后端选型与装配知识收在包内

    ``thinking_sink`` 透传给 StreamerAgent 与 MinecraftAgent（鸭子类型：
    任何带 ``on_thinking_delta`` 方法的对象）；None 时两 Agent 的思考流
    旁路整体短路，决策循环行为与无旁路完全一致。
    """
    config = config if isinstance(config, dict) else {}

    if name == "streamer":
        # 函数内 import：各 Agent 包携带各自依赖链，仅在实际实例化该 Agent 时加载，
        # 未启用的 Agent 不付导入成本
        from src.agents.streamer.config import StreamerConfig
        from src.agents.streamer.streamer_agent import StreamerAgent

        try:
            cfg_obj = StreamerConfig.from_dict(config) if config else StreamerConfig()
        except Exception as exc:
            _logger.warning(f"解析 StreamerConfig 配置失败: {exc}; 使用默认配置")
            cfg_obj = StreamerConfig()
        _logger.info(
            f"StreamerAgent 配置就绪: "
            f"bot_name={cfg_obj.persona.bot_name!r}, "
            f"behavior_style={'<已注入>' if cfg_obj.persona.behavior_style else '<缺失>'}, "
            f"background.enabled={cfg_obj.background.enabled}, "
            f"background.light_tick_ms={cfg_obj.background.light_tick_ms}"
        )
        streamer_kwargs: dict[str, Any] = {field: getattr(deps, field) for field in _STREAMER_DEPS_FIELDS}
        streamer_kwargs["stream_ledger"] = deps.storage_ledger
        streamer_kwargs["config"] = cfg_obj
        if _accepts_init_kwarg(StreamerAgent, "lead_coordinator"):
            streamer_kwargs["lead_coordinator"] = deps.lead_coordinator
        return StreamerAgent(**streamer_kwargs)

    if name == "minecraft":
        # 函数内 import：同上，minecraft 包（含 mcp 依赖链）仅在实例化时加载
        from src.agents.minecraft import MinecraftAgent
        from src.agents.minecraft.config import MinecraftConfig

        try:
            minecraft_cfg = MinecraftConfig(**config)
        except Exception as exc:
            _logger.warning(f"解析 MinecraftConfig 失败: {exc}; 使用默认配置")
            minecraft_cfg = MinecraftConfig()
        minecraft_kwargs: dict[str, Any] = {
            "config": minecraft_cfg,
            "llm_manager": deps.llm_manager,
            "prompt_manager": deps.prompt_manager,
            "event_bus": deps.event_bus,
            "tool_registry": deps.tool_registry,
            "thinking_sink": deps.thinking_sink,
            "task_tracker": deps.task_tracker,
            "skill_library": get_skill_library(),
            "creature_repo": deps.creature_repo,
            "work_docs_repo": deps.work_docs_repo,
            "social_fact_repo": deps.social_fact_repo,
            "game_player_repo": deps.game_player_repo,
        }
        if _accepts_init_kwarg(MinecraftAgent, "lead_coordinator"):
            minecraft_kwargs["lead_coordinator"] = deps.lead_coordinator
        if _accepts_init_kwarg(MinecraftAgent, "embedding_setup"):
            minecraft_kwargs["embedding_setup"] = deps.embedding_setup
        return MinecraftAgent(**minecraft_kwargs)

    if name == "text_adv":
        # 函数内 import：text_adv 包（感知/动作后端依赖链）仅在实例化时加载；
        # 四后端选型逻辑收在包内 build，工厂只做"名字 → 包内 build"
        from src.agents.text_adv import TextAdvConfig, build as build_text_adv

        return build_text_adv(_parse_game_config(TextAdvConfig, config, "TextAdvConfig"), deps)

    if name == "game_2048":
        # 函数内 import：game_2048 包仅在实例化时加载；装配知识收在包内 build
        from src.agents.game_2048 import Game2048Config, build as build_game_2048

        return build_game_2048(_parse_game_config(Game2048Config, config, "Game2048Config"), deps)

    return None


__all__ = ["AGENT_BINDINGS", "SUPPORTED_AGENTS", "instantiate_agent"]
