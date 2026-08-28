"""Amaidesu 应用程序主入口（Wave 6 重写）

v2 架构组合根（参见 .omo/drafts/amaidesu-v2-architecture.md）：
- LLMManager：统一 LLM 客户端池
- ContextService：会话历史（替代旧 ContextService 路径）
- EventBus：事件分发；启动时挂载事件拦截器（§1.46.1）
- CollectorManager：管理 src/modules/collectors/ 下所有 Input Domain 组件
- AgentManager：管理 src/agents/ 下所有 Agent（包括主播 StreamerAgent）
- ToolRegistry：管理 src/modules/tools/ 下所有 Output Domain 组件
- DashboardServer：WebUI（仅作为 observer，不参与决策/执行数据流）
- MCPServerService：外部 MCP 协议适配
- LogStreamer + EventHistoryRecorder：日志 + 事件历史

Wave 6 重写变更：
- 删除旧 InputCollectorManager / DeciderManager / OutputHandlerManager 引用
- 删除旧 stage 装饰器导入（src.stages.input.collectors/pipelines/deciders/output.manager/output.pipelines）
- Agent / Tool 注册走 config [agents]/[tools] 段 + StreamerAgent 自身
- 事件拦截器（rate_limit / similar_filter）通过 EventBus.add_interceptor 注册
- 关闭顺序：CollectorManager.stop_all → AgentManager.stop_all → EventRecorder.stop → EventBus.cleanup → LLMManager.cleanup → ContextService.cleanup
"""

from __future__ import annotations

import webbrowser
import argparse
import asyncio
import contextlib
import os
import signal
import sys
from typing import Any, Dict, Optional, Tuple

from loguru import logger as loguru_logger

from src.modules.agents.manager import AgentManager
from src.modules.collectors.manager import CollectorManager
from src.modules.config.service import ConfigService
from src.modules.context import ContextService, ContextServiceConfig
from src.modules.dashboard.server import DashboardServer
from src.modules.events import (
    EventBus,
    list_registered_events,
    register_core_events,
)
from src.modules.events.event_recorder import EventHistoryRecorder
from src.modules.events.interceptors import (
    RateLimitInterceptor,
    SimilarFilterInterceptor,
)
from src.modules.llm.manager import LLMManager
from src.modules.logging import get_logger
from src.modules.prompts import get_prompt_manager
from src.modules.storage.sqlite_store import SQLiteStore

logger = get_logger("Main")
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# 命令行与日志
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Amaidesu 应用程序")
    parser.add_argument("--debug", action="store_true", help="启用 DEBUG 级别日志输出")
    parser.add_argument(
        "--filter",
        nargs="+",
        metavar="MODULE_NAME",
        help="仅显示指定模块的 INFO/DEBUG 级别日志 (WARNING 及以上级别总是显示)",
    )
    parser.add_argument(
        "--dev-webui",
        action="store_true",
        help="启用 WebUI 开发模式：自动启动 Vite 开发服务器（HMR 热更新），浏览器将打开 http://localhost:60315",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="dry-run 模式：仅验证配置加载与组件构造（不订阅事件、不启动 LLM 调用）",
    )
    return parser.parse_args()


def setup_logging_early(args: argparse.Namespace) -> None:
    """早期日志配置，在导入阶段参与者之前调用。

    使用默认的INFO级别，避免DEBUG日志过早输出。
    完整的日志配置会在load_config后再次调用。
    """
    from src.modules.logging import configure_from_config

    default_config = {"level": "INFO", "console_level": "INFO"}

    if args.debug:
        default_config["level"] = "DEBUG"
        default_config["console_level"] = "DEBUG"

    configure_from_config(default_config)


def setup_logging(args: argparse.Namespace, logging_config: Optional[Dict[str, Any]] = None) -> None:
    """根据命令行参数和配置文件配置日志。

    Args:
        args: 命令行参数
        logging_config: 日志配置字典（从 ConfigService.get_section("logging") 获取）
    """
    from src.modules.logging import configure_from_config

    final_config = {}

    if logging_config:
        final_config.update(logging_config)

    if args.debug:
        final_config["level"] = "DEBUG"
        final_config["console_level"] = "DEBUG"

    if args.filter:
        filtered_modules = set(args.filter)

        def filter_logic(record: Dict[str, Any]) -> bool:
            if record["level"].no >= loguru_logger.level("WARNING").no:
                return True
            module_name = record["extra"].get("module")
            return bool(module_name and module_name in filtered_modules)

        final_config["filter"] = filter_logic

    configure_from_config(final_config)

    if args.debug:
        logger.info(f"已启用 DEBUG 日志级别{f'，并激活模块过滤器: {list(args.filter)}' if args.filter else '。'}")
    elif args.filter:
        logger.info(f"日志过滤器已激活: {list(args.filter)} (INFO 级别)")

    logger.info("启动 Amaidesu 应用程序...")


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def load_config() -> Tuple[ConfigService, Dict[str, Any], bool]:
    """加载配置，失败时直接退出进程。返回 (config_service, config, was_created)。"""
    config_service = ConfigService(base_dir=_BASE_DIR)
    try:
        config, was_created = config_service.initialize()
        return config_service, config, was_created
    except Exception as e:
        logger.critical(f"配置文件初始化失败: {e}", exc_info=True)
        logger.critical("请检查 config/ 目录下的配置文件格式，或删除 config/ 目录重新生成。")
        sys.exit(1)


def validate_config(config: Dict[str, Any]) -> None:
    """验证 v2 配置完整性，缺失必要配置时给出明确错误提示。

    v2 配置按 7 文件树划分（参见 multi_file_loader._CONFIG_FILES）：
    core / model / agents / tools / memory / storage / background。
    本函数只做"存在性 + 顶层类型"轻量检查；详细字段验证由各 ConfigSchema
    在组件构造阶段自动完成（fail-fast 由 Pydantic 保证）。
    """
    if not isinstance(config, dict):
        logger.critical("配置根对象不是 dict（schema 漂移？）")
        return

    # core.toml 段（meta / general / context / events / dashboard / logging / interceptors）
    if "general" not in config or not isinstance(config["general"], dict):
        logger.critical("缺少 [general] 配置段（core.toml）")

    # model.toml 段（顶层无聚合键，llm_providers/llm/llm_fast 等散落，不强制）
    # 不强制报错：model.toml 缺失时 LLM 调用会自然降级为 warning。

    # agents.toml 段
    agents_cfg = config.get("agents")
    if not agents_cfg:
        logger.warning("未检测到 [agents] 配置，Agent 功能将被禁用")
    elif not isinstance(agents_cfg, dict):
        logger.warning("[agents] 配置类型异常（期望 dict），Agent 功能将被禁用")

    # tools.toml 段
    tools_cfg = config.get("tools")
    if not tools_cfg:
        logger.warning("未检测到 [tools] 配置，Tool 功能将被禁用")
    elif not isinstance(tools_cfg, dict):
        logger.warning("[tools] 配置类型异常（期望 dict），Tool 功能将被禁用")

    # collectors 子段位于 tools/agents 等聚合下，由各组件 Schema 自行校验
    # 不再顶层检查（旧 [collectors] 已迁入 agents/agents_collectors）

    # memory.toml 段
    memory_cfg = config.get("memory")
    if not memory_cfg:
        logger.debug("未检测到 [memory] 配置，使用 SimpleMemory 默认值")
    elif not isinstance(memory_cfg, dict):
        logger.warning("[memory] 配置类型异常（期望 dict），Memory 功能将退化")

    # storage.toml 段
    storage_cfg = config.get("storage")
    if not storage_cfg:
        logger.debug("未检测到 [storage] 配置，存储功能将仅 in-memory")
    elif not isinstance(storage_cfg, dict):
        logger.warning("[storage] 配置类型异常（期望 dict），存储功能将退化")

    # background.toml 段
    background_cfg = config.get("background")
    if not background_cfg:
        logger.debug("未检测到 [background] 配置，后台任务采用默认 tick")
    elif not isinstance(background_cfg, dict):
        logger.warning("[background] 配置类型异常（期望 dict），后台任务采用默认 tick")

    logger.info("配置验证通过（v2 7-file tree 存在性 + 类型检查）")


def exit_if_config_created(was_created: bool) -> None:
    """若配置文件为新创建，提示用户并退出。"""
    if was_created:
        box = "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        logger.warning(box)
        logger.warning("!! 配置文件已在 config/ 目录下自动生成。                    !!")
        logger.warning("!! 请编辑 config/ 目录下的 .toml 文件，填写必要配置。       !!")
        logger.warning("!! 修改完成后，请重新运行程序。                           !!")
        logger.warning(box)
        sys.exit(0)

    logger.info("所有必要的配置文件已存在。继续正常启动...")


# ---------------------------------------------------------------------------
# 事件拦截器注册
# ---------------------------------------------------------------------------


def register_event_interceptors(event_bus: EventBus, config: Dict[str, Any]) -> None:
    """注册输入域事件拦截器（§1.46.1）。

    - rate_limit：从旧 input pipeline 迁移（防刷屏/防突发）
    - similar_filter：从旧 input pipeline 迁移（相似文本合并）
    - 拦截器作用于 ``room.message.*`` 事件（v2 语义域）
    - 拦截器返回 ``None`` 即丢弃该事件
    """
    interceptors_config = config.get("interceptors", {}) if isinstance(config, dict) else {}
    rate_limit_cfg = interceptors_config.get("rate_limit", {}) if isinstance(interceptors_config, dict) else {}
    if rate_limit_cfg.get("enabled", True):
        event_bus.add_interceptor(
            RateLimitInterceptor(
                global_rate_limit=rate_limit_cfg.get("global_rate_limit", 100),
                user_rate_limit=rate_limit_cfg.get("user_rate_limit", 10),
                window_size=rate_limit_cfg.get("window_size", 60),
            )
        )
        logger.info("RateLimitInterceptor 已注册（[interceptors.rate_limit]）")

    similar_cfg = interceptors_config.get("similar_filter", {}) if isinstance(interceptors_config, dict) else {}
    if similar_cfg.get("enabled", True):
        event_bus.add_interceptor(
            SimilarFilterInterceptor(
                similarity_threshold=similar_cfg.get("similarity_threshold", 0.85),
                time_window=similar_cfg.get("time_window", 5.0),
                min_text_length=similar_cfg.get("min_text_length", 3),
                cross_user_filter=similar_cfg.get("cross_user_filter", True),
            )
        )
        logger.info("SimilarFilterInterceptor 已注册（[interceptors.similar_filter]）")


# ---------------------------------------------------------------------------
# 核心组件构造（v2 组合根）
# ---------------------------------------------------------------------------


async def create_app_components(
    config: Dict[str, Any],
    config_service: ConfigService,
    dev_webui: bool = False,
) -> Tuple[
    ContextService,
    EventBus,
    LLMManager,
    Optional["DashboardServer"],
    Optional["EventHistoryRecorder"],
    Optional["CollectorManager"],
    Optional["AgentManager"],
    "SQLiteStore",
]:
    """v2 组合根：构造并连接所有核心组件。

    创建顺序（依赖关系）：
    1. LLMManager（统一 LLM 客户端池）
    2. ContextService（会话历史 / 多会话隔离）
    2b. 存储与记忆（SQLiteStore + SimpleMemory，§1.50 组合根接线）
    3. EventBus + 事件拦截器（§1.46.1）
    4. CollectorManager（采集器：bilibili/console/mock/screen）
    5. AgentManager（Agent 子系统：StreamerAgent 等）
    6. ToolRegistry（bind_core_tools/bind_pending_tools 显式接线 + Agent 自注册，audit_tools 审计）
    7. DashboardServer（WebUI observer）

    Returns:
        (context_service, event_bus, llm_service, dashboard_server,
         event_recorder, collector_manager, agent_manager, sqlite_store)
    """
    # --- 1. LLM 服务 ---
    logger.info("初始化 LLM 服务...")
    llm_service = LLMManager()
    await llm_service.setup(config)
    logger.info("已创建 LLM 服务实例")

    # --- 2. ContextService ---
    logger.info("初始化上下文服务...")
    context_config = config.get("context", {}) if isinstance(config, dict) else {}
    context_service_config = ContextServiceConfig(**context_config)
    context_service = ContextService(config=context_service_config)
    await context_service.initialize()
    logger.info("已创建上下文服务实例")

    # --- 2b. 存储与记忆（§1.50：SQLiteStore + SimpleMemory 组合根接线）---
    from src.modules.memory.bootstrap import build_memory_stack

    logger.info("初始化存储与记忆（SQLiteStore + SimpleMemory）...")
    sqlite_store, memory = await build_memory_stack(config)
    logger.info(f"存储与记忆已就绪（db={sqlite_store.db_path}）")

    # --- 3. EventBus + 拦截器 ---
    logger.info("初始化事件总线...")
    event_bus = EventBus()
    register_event_interceptors(event_bus, config)
    logger.info("事件总线已初始化，事件拦截器已挂载")

    # --- 3b. 事件历史（系统级）---
    event_recorder = await _start_event_recorder(event_bus, config)
    logger.info("事件历史记录器已启动")

    # --- 4. CollectorManager ---
    # v2：采集器配置位于 tools.toml 的 [tools.perception.config]（旧 [collectors] 段已迁移）
    collector_manager: Optional["CollectorManager"] = None
    tools_perception = (config.get("tools") or {}).get("perception", {}) if isinstance(config, dict) else {}
    collectors_config = tools_perception.get("config", {}) if isinstance(tools_perception, dict) else {}
    if collectors_config:
        logger.info("初始化 CollectorManager（src/modules/collectors/）...")
        from src.modules.collectors.manager import CollectorManager

        collector_manager = CollectorManager()
        await _register_collectors_from_config(collector_manager, collectors_config, config_service, event_bus)
        await collector_manager.start_all()
        logger.info(f"CollectorManager 已启动（{len(collector_manager)} 个 Collector）")

    # --- 5. AgentManager + StreamerAgent ---
    agent_manager: Optional["AgentManager"] = None
    agents_config = config.get("agents", {}) if isinstance(config, dict) else {}
    if agents_config:
        logger.info("初始化 AgentManager（src/agents/）...")
        from src.modules.agents.manager import AgentManager
        from src.modules.memory.bootstrap import bind_memory_tools
        from src.modules.tools import ToolRegistry
        from src.modules.tools.bootstrap import bind_core_tools
        from src.modules.tools.decorator import bind_pending_tools

        tool_registry = ToolRegistry()
        agent_manager = AgentManager(tool_registry=tool_registry, memory=memory)
        await _register_agents_from_config(
            agent_manager,
            agents_config,
            config_service,
            llm_service,
            event_bus,
            context_service,
            tool_registry,
            memory,
        )

        # --- 6b. 核心工具包（output/* 的 L2 Provider） + L1 @tool pending 刷入 ---
        # 在 agent_manager.start_all() 之前完成 → StreamerAgent._on_start()
        # 调用 _register_tools() 时 registry 已就绪，可与 L2/L1 工具同台。
        # 配置切片取法与 step 5 的 tools.perception.config 一致：先取
        # [tools.output] 子段（ToolPackMeta），再取其 .config 字典作为
        # bind_core_tools 入参；缺失则降级为 {}（多数包将走 schema 默认）。
        tools_output_pack = (config.get("tools") or {}).get("output", {}) if isinstance(config, dict) else {}
        output_tools_config = tools_output_pack.get("config", {}) if isinstance(tools_output_pack, dict) else {}
        if not isinstance(output_tools_config, dict):
            output_tools_config = {}
        core_report = bind_core_tools(tool_registry, output_tools_config)
        core_succeeded = sum(1 for c in core_report.values() if c > 0)
        core_failed = [name for name, count in core_report.items() if count == 0]
        logger.info(
            f"核心工具包已绑定: 成功 {core_succeeded}/{len(core_report)}"
            f"，合计新增 {sum(core_report.values())} 个工具" + (f"，失败包: {core_failed}" if core_failed else "")
        )
        pending_count = bind_pending_tools(tool_registry)
        if pending_count > 0:
            logger.info(f"@tool pending 已刷入 {pending_count} 个工具")
        else:
            logger.debug("@tool pending 表为空（L1 装饰器路径今日无产出）")

        # --- 6c. 记忆检索工具（§1.51 第二路：LLM 主动 query_memory）---
        memory_tool_count = bind_memory_tools(tool_registry, memory)
        logger.info(f"query_memory 记忆检索工具已注册（新增 {memory_tool_count} 个）")

        await agent_manager.start_all()
        logger.info(f"AgentManager 已启动（{len(agent_manager)} 个 Agent）")

    # --- 6. ToolRegistry 工具审计：所有 Agent 声明的工具是否都已注册实现 ---
    if agent_manager is not None and agent_manager._tool_registry is not None:
        registry = agent_manager._tool_registry
        logger.info(f"ToolRegistry 就绪（{len(registry)} 个工具已注册）")
        missing = agent_manager.audit_tools(registry)
        if missing:
            logger.warning(f"审计发现 {len(missing)} 个已声明但缺失实现的工具: {missing}")
        else:
            logger.info("工具审计通过：所有 Agent 声明的工具均已在 ToolRegistry 中找到实现")

    # --- 7. DashboardServer ---
    dashboard_server: Optional["DashboardServer"] = None
    log_streamer = await _start_log_streamer()
    dashboard_config = config.get("dashboard", {}) if isinstance(config, dict) else {}
    if dashboard_config.get("enabled", True):
        dashboard_server = await _start_dashboard(
            dashboard_config,
            dev_webui,
            event_bus,
            context_service,
            config_service,
            collector_manager,
            agent_manager,
            llm_service,
            log_streamer,
        )

    # --- 8. 组件装配完成 ---
    return (
        context_service,
        event_bus,
        llm_service,
        dashboard_server,
        event_recorder,
        collector_manager,
        agent_manager,
        sqlite_store,
    )


# ---------------------------------------------------------------------------
# 子启动器（保持 create_app_components 函数短小）
# ---------------------------------------------------------------------------


async def _start_event_recorder(event_bus: EventBus, config: Dict[str, Any]):
    """启动事件历史记录器（系统级，与 Dashboard 解耦）。"""
    from src.modules.config.core_schemas import EventHistoryConfig
    from src.modules.events.event_history import EventHistoryService
    from src.modules.events.event_recorder import EventHistoryRecorder

    events_config = config.get("events", {}) if isinstance(config, dict) else {}
    typed_events_config = EventHistoryConfig(**events_config)
    try:
        service = EventHistoryService(
            max_events=typed_events_config.history_size,
            persist=typed_events_config.persist,
        )
        recorder = EventHistoryRecorder(event_bus=event_bus, event_history=service)
        await recorder.start()
        logger.info(
            f"事件历史记录器已启动（size={typed_events_config.history_size}, persist={typed_events_config.persist}）"
        )
        return recorder
    except Exception as e:
        logger.warning(f"事件历史记录器启动失败: {e}")
        return None


async def _start_log_streamer():
    """启动 LogStreamer（用于 Dashboard 抓取实时日志）。"""
    from src.modules.logging.log_streamer import LogStreamer

    streamer = LogStreamer(min_level="DEBUG", persist=True)
    await streamer.start()
    return streamer


async def _register_collectors_from_config(manager, config_section, config_service, event_bus=None):
    """根据 [tools.perception.config] 段注册 Collector 实例到 CollectorManager。

    v2 段结构（tools.toml）：
        enabled = ["bili_danmaku", "mock_danmaku", ...]
        bili_danmaku = { ... }
        mock_danmaku = { ... }
    """
    from src.modules.collectors.factory import instantiate_collector

    enabled_list = config_section.get("enabled", []) or []
    for collector_name in enabled_list:
        sub_cfg = config_section.get(collector_name, {})
        if not isinstance(sub_cfg, dict):
            sub_cfg = {}
        instance = instantiate_collector(collector_name, sub_cfg, event_bus=event_bus)
        if instance is None:
            logger.warning(f"Collector '{collector_name}' 未找到 Collector 类，跳过")
            continue
        manager.register(instance, description=sub_cfg.get("description", ""))


async def _register_agents_from_config(
    manager,
    config_section,
    config_service,
    llm_service,
    event_bus,
    context_service,
    tool_registry=None,
    memory=None,
):
    """根据 [agents] 段注册 Agent 实例到 AgentManager。

    [agents] 段结构（v2）：
        enabled = ["streamer", "game"]
        streamer = { planner_llm = "llm_fast", replyer_llm = "llm", ... }
        game = { ... }

    memory 为 §1.50 记忆后端（SimpleMemory），仅 streamer Agent 消费。
    """
    enabled_list = config_section.get("enabled", []) or []
    for agent_name in enabled_list:
        sub_cfg = config_section.get(agent_name, {})
        if not isinstance(sub_cfg, dict):
            sub_cfg = {}
        if agent_name == "streamer":
            from src.agents.streamer.streamer_agent import (
                StreamerAgent,
                StreamerAgentConfig,
            )

            try:
                cfg_obj = StreamerAgentConfig(**sub_cfg) if sub_cfg else StreamerAgentConfig()
            except Exception as e:
                logger.warning(f"解析 StreamerAgent 配置失败: {e}; 使用默认配置")
                cfg_obj = StreamerAgentConfig()

            # v2.0.6 B2 修复：从 config_service 拉取 [persona] 段，构造 StreamerAgent
            # 时透传为 persona_provider。优先级链：persona dict（来自 core.toml）
            # > StreamerAgentConfig.bot_name > _DEFAULT_*。缺段时退化为空 dict，
            # 由下游 Replyer 走 _DEFAULT_* 兜底，避免装配失败阻断冷启动。
            persona_provider_dict = {}
            if config_service is not None:
                try:
                    persona_provider_dict = dict(config_service.get_section("persona", default={}) or {})
                except Exception as exc:
                    logger.warning(f"读取 [persona] 配置段失败，回退为空 dict: {exc}")
                    persona_provider_dict = {}
            if persona_provider_dict:
                logger.info(
                    f"StreamerAgent 已注入 persona: bot_name={persona_provider_dict.get('bot_name', '<缺>')!r}, "
                    f"behavior_style={'<已注入>' if persona_provider_dict.get('behavior_style') else '<缺失>'}"
                )
            else:
                logger.warning(
                    "[persona] 配置段为空，StreamerAgent.persona_provider 将传空 dict；"
                    "Replyer/Planner 走 _DEFAULT_* 兜底（请检查 config/core.toml）"
                )

            agent = StreamerAgent(
                config=cfg_obj,
                llm_manager=llm_service,
                prompt_manager=get_prompt_manager(),
                context_service=context_service,
                event_bus=event_bus,
                tool_registry=tool_registry,
                memory=memory,
                persona_provider=persona_provider_dict,
            )
            manager.register(
                agent,
                spec_provider="builtin",
                description="直播主播决策主体：聚合弹幕 → Planner 决策 → Replyer 表达",
            )
            continue
        if agent_name == "game":
            try:
                from src.agents.game.text_adv import (
                    TextAdvGameAgent,
                    TextAdvGameConfig,
                )
                from src.modules.tools.content_engine import StubContentEngine

                game_cfg_dict = sub_cfg if isinstance(sub_cfg, dict) else {}
                engine_name = str(game_cfg_dict.get("engine", "text_adv") or "text_adv")
                if engine_name != "text_adv":
                    logger.warning(f"game Agent 引擎 '{engine_name}' 尚未实现（仅 text_adv），跳过")
                    continue

                try:
                    text_adv_cfg = TextAdvGameConfig(**{k: v for k, v in game_cfg_dict.items() if k != "engine"})
                except Exception as e:
                    logger.warning(f"解析 TextAdvGameConfig 失败: {e}; 使用默认配置")
                    text_adv_cfg = TextAdvGameConfig()

                text_adv_agent = TextAdvGameAgent(
                    config=text_adv_cfg,
                    content_engine=StubContentEngine(engine_kind="text_adv"),
                    llm_manager=llm_service,
                    prompt_manager=get_prompt_manager(),
                    event_bus=event_bus,
                )
                manager.register(
                    text_adv_agent,
                    spec_provider="game",
                    description="游戏 AI 玩家代理（text_adv 文字冒险引擎）",
                )
                logger.info(f"TextAdvGameAgent 已注册 (engine={engine_name})")
            except Exception as e:
                logger.warning(f"game Agent 注册失败: {e}")
            continue
        if agent_name == "custom":
            logger.info("custom Agent 由用户自定义注册，当前跳过（占位）")
            continue
        logger.warning(f"未知的 Agent 类型: {agent_name}（升级 hook 应已过滤 maibot 等）")


async def _start_dashboard(
    dashboard_config: Dict[str, Any],
    dev_webui: bool,
    event_bus: EventBus,
    context_service: ContextService,
    config_service: ConfigService,
    collector_manager: Optional["CollectorManager"] = None,
    agent_manager: Optional["AgentManager"] = None,
    llm_service=None,
    log_streamer=None,
):
    """启动 DashboardServer（仅作为 WebUI observer，不参与决策数据流）。"""
    try:
        from src.modules.config.core_schemas import DashboardConfig
        from src.modules.dashboard.server import DashboardServer

        dashboard_config = dict(dashboard_config)
        dashboard_config["dev_mode"] = dev_webui
        typed_dashboard_config = DashboardConfig(**dashboard_config)

        dashboard_server = DashboardServer(
            event_bus=event_bus,
            context_service=context_service,
            config_service=config_service,
            dashboard_config=typed_dashboard_config,
            collector_manager=collector_manager,
            agent_manager=agent_manager,
            tool_registry=(agent_manager._tool_registry if agent_manager else None),
            llm_manager=llm_service,
            prompt_manager=get_prompt_manager(),
            log_streamer=log_streamer,
        )
        await dashboard_server.start()
        logger.info(f"Dashboard 已启动: http://{typed_dashboard_config.host}:{typed_dashboard_config.port}")
        if typed_dashboard_config.auto_open_browser:
            if typed_dashboard_config.dev_mode:
                dashboard_url = f"http://localhost:{typed_dashboard_config.vite_dev_port}"
            else:
                dashboard_url = f"http://{typed_dashboard_config.host}:{typed_dashboard_config.port}"
            webbrowser.open(dashboard_url)
            logger.info(f"已自动打开浏览器: {dashboard_url}")
        return dashboard_server
    except ImportError as e:
        logger.warning(f"Dashboard 模块导入失败（可能缺少依赖）: {e}")
        logger.warning("Dashboard 功能将被禁用。请运行: uv add fastapi 'uvicorn[standard]'")
        return None
    except Exception as e:
        logger.error(f"Dashboard 启动失败: {e}")
        logger.warning("Dashboard 功能将被禁用")
        return None


# ---------------------------------------------------------------------------
# 信号与关闭
# ---------------------------------------------------------------------------


def setup_signal_handlers(stop_event: asyncio.Event) -> Tuple[Optional[Any], Optional[Any]]:
    """注册退出信号处理，返回原始处理器以便恢复。"""
    shutdown_initiated = False

    def handler(signum=None, frame=None):
        nonlocal shutdown_initiated
        if shutdown_initiated:
            logger.warning("已经在关闭中，忽略重复信号")
            return
        shutdown_initiated = True
        logger.info("收到退出信号，开始关闭...")
        stop_event.set()

    original_sigint = signal.signal(signal.SIGINT, handler)
    try:
        original_sigterm = signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError):
        original_sigterm = None

    with contextlib.suppress(Exception):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, handler)
    return original_sigint, original_sigterm


def restore_signal_handlers(original_sigint: Optional[Any], original_sigterm: Optional[Any]) -> None:
    """恢复原始信号处理器。"""
    try:
        signal.signal(signal.SIGINT, original_sigint)
        if original_sigterm is not None:
            signal.signal(signal.SIGTERM, original_sigterm)
        logger.debug("信号处理器已恢复")
    except Exception as e:
        logger.debug(f"恢复信号处理器时出错: {e}")


async def run_shutdown(
    context_service: ContextService,
    event_bus: EventBus,
    llm_service: LLMManager,
    dashboard_server: Optional["DashboardServer"],
    event_recorder: Optional["EventHistoryRecorder"],
    collector_manager: Optional["CollectorManager"],
    agent_manager: Optional["AgentManager"],
    *,
    sqlite_store: Optional["SQLiteStore"] = None,
) -> None:
    """v2 关闭顺序（依赖关系反向）：

    1. 停止 CollectorManager（数据生产者，停止 emit 事件）
    2. 停止 AgentManager（Agent 主循环，后台任务退出）
    3. 停止 Dashboard（WebSocket 连接关闭）
    4. 停止 EventHistoryRecorder（必须在 EventBus.cleanup 之前 off）
    5. EventBus.cleanup（清除所有 listener）
    6. LLMManager.cleanup
    7. ContextService.cleanup
    8. SQLiteStore.close（存储落盘收尾，最后关闭）
    """
    _saw_cancelled = False

    async def safe_log(coro, name: str):
        nonlocal _saw_cancelled
        try:
            return await coro
        except (Exception, asyncio.CancelledError) as e:
            _saw_cancelled = _saw_cancelled or isinstance(e, asyncio.CancelledError)
            logger.error(f"{name} 失败: {e}")

    if collector_manager is not None:
        logger.info("正在停止 CollectorManager...")
        await safe_log(collector_manager.stop_all(), "CollectorManager.stop_all")
        await safe_log(collector_manager.cleanup_all(), "CollectorManager.cleanup_all")
        logger.info("CollectorManager 已停止")

    if agent_manager is not None:
        logger.info("正在停止 AgentManager...")
        await safe_log(agent_manager.stop_all(), "AgentManager.stop_all")
        await safe_log(agent_manager.cleanup_all(), "AgentManager.cleanup_all")
        logger.info("AgentManager 已停止")

    if dashboard_server is not None:
        logger.info("正在停止 Dashboard...")
        try:
            await dashboard_server.stop()
            await dashboard_server.cleanup()
        except (Exception, asyncio.CancelledError) as e:
            _saw_cancelled = _saw_cancelled or isinstance(e, asyncio.CancelledError)
            logger.error(f"Dashboard 停止失败: {e}")
        logger.info("Dashboard 已停止")

    if event_recorder is not None:
        logger.info("正在停止事件历史记录器...")
        await safe_log(event_recorder.stop(), "EventHistoryRecorder")
        if event_recorder.event_history is not None:
            try:
                event_recorder.event_history.cleanup()
            except Exception as e:
                logger.debug(f"event_history cleanup: {e}")
        logger.info("事件历史记录器已停止")

    logger.info("等待待处理事件完成并清理 EventBus...")
    if event_bus is not None:
        await safe_log(event_bus.cleanup(), "EventBus.cleanup")
        logger.info("EventBus 已清理")

    logger.info("正在清理 LLMManager...")
    if llm_service is not None:
        await safe_log(llm_service.cleanup(), "LLMManager.cleanup")
    logger.info("核心服务已关闭")

    logger.info("正在清理 ContextService...")
    if context_service is not None:
        await safe_log(context_service.cleanup(), "ContextService.cleanup")

    if sqlite_store is not None:
        logger.info("正在关闭 SQLiteStore...")
        await safe_log(sqlite_store.close(), "SQLiteStore.close")

    logger.info("Amaidesu 应用程序已关闭。")

    if _saw_cancelled:
        raise asyncio.CancelledError()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def main() -> None:
    """v2 应用程序主入口。"""
    args = parse_args()
    setup_logging_early(args)

    config_service, config, was_created = load_config()
    logging_config = config_service.get_section("logging", default={})
    setup_logging(args, logging_config)
    validate_config(config)
    exit_if_config_created(was_created)

    # 注册所有 @register_event 装饰器触发的 Payload 模块（必须在 EventBus 构造前）
    register_core_events()
    logger.info(f"核心事件注册完成，共 {len(list_registered_events())} 个事件")

    (
        context_service,
        event_bus,
        llm_service,
        dashboard_server,
        event_recorder,
        collector_manager,
        agent_manager,
        sqlite_store,
    ) = await create_app_components(config, config_service, dev_webui=args.dev_webui)

    if args.dry:
        logger.info("--dry 模式：仅验证组合根构造，组件已构造但不进入主循环")
        logger.info("（此模式用于快速检查 wiring 是否完整，关闭后退出）")
        await run_shutdown(
            context_service,
            event_bus,
            llm_service,
            dashboard_server,
            event_recorder,
            collector_manager,
            agent_manager,
            sqlite_store=sqlite_store,
        )
        return

    stop_event = asyncio.Event()
    orig_sigint, orig_sigterm = setup_signal_handlers(stop_event)

    logger.info("应用程序正在运行。按 Ctrl+C 退出。")

    try:
        await stop_event.wait()
        logger.info("收到关闭信号，开始执行清理...")
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，开始清理...")

    restore_signal_handlers(orig_sigint, orig_sigterm)
    await run_shutdown(
        context_service,
        event_bus,
        llm_service,
        dashboard_server,
        event_recorder,
        collector_manager,
        agent_manager,
        sqlite_store=sqlite_store,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，强制退出。")
