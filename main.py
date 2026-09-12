"""Amaidesu 应用程序主入口。

架构组合根：
- LLMManager：统一 LLM 客户端池
- ContextService：会话历史/多会话隔离
- EventBus：事件分发；启动时挂载事件拦截器
- CollectorManager：管理 src/modules/collectors/ 下所有 Input Domain 组件
- AgentManager：管理 src/agents/ 下所有 Agent（包括主播 StreamerAgent）
- ToolRegistry：管理 src/modules/tools/ 下所有 Output Domain 组件
- DashboardServer：WebUI（仅作为 observer，不参与决策/执行数据流）
- LogStreamer + EventHistoryRecorder：日志 + 事件历史

关闭顺序：CollectorManager.stop_all → SimulatorService.stop → AgentManager.stop_all → EventRecorder.stop → StorageLedger.stop → EventBus.cleanup → LLMManager.cleanup → ContextService.cleanup
"""

from __future__ import annotations

import webbrowser
import argparse
import asyncio
import contextlib
import os
import signal
import sys
import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger as loguru_logger

from src.agents.text_adv import TextAdvGameAgent, TextAdvGameConfig
from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
from src.modules.agents.manager import AgentManager
from src.modules.collectors.factory import instantiate_collector
from src.modules.collectors.manager import CollectorManager
from src.modules.config.core_schemas import ContextAssemblerConfig, DashboardConfig, EventHistoryConfig
from src.modules.config.service import ConfigService
from src.modules.context import ContextService
from src.modules.dashboard.server import DashboardServer
from src.modules.dashboard.stream_preview import StreamPreviewHub
from src.modules.events import (
    EventBus,
    list_registered_events,
    register_core_events,
)
from src.modules.events.event_history import EventHistoryService
from src.modules.events.event_recorder import EventHistoryRecorder
from src.modules.events.interceptors import (
    RateLimitInterceptor,
    SessionStampInterceptor,
    SimilarFilterInterceptor,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import CoreShutdownPayload, CoreStartupPayload
from src.modules.llm.manager import LLMManager
from src.modules.llm.request_history_manager import get_global_request_history_manager
from src.modules.logging import configure_from_config, get_logger
from src.modules.logging.log_streamer import LogStreamer
from src.modules.memory.bootstrap import bind_memory_tools, build_memory_stack
from src.modules.prompts import get_prompt_manager
from src.modules.session import LiveSessionManager
from src.modules.simulator import SimulatorService
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.subtitle import build_subtitle_infrastructure
from src.modules.subtitle.backends import DashboardBackend
from src.modules.tts import build_tts_infrastructure
from src.modules.storage.storage_ledger import StorageLedger
from src.modules.tools import TaskLedger, TaskTracker, ToolHealthMonitor, ToolRegistry
from src.modules.tools.tasks import resolve_tasks_config
from src.modules.tools.bootstrap import bind_core_tools
from src.agents.text_adv.content_engine import StubContentEngine
from src.modules.vision.look_at_screen import LookAtScreenProvider
from src.modules.vision.pil_capture import PillowImageGrabCapture

logger = get_logger("Main")
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# live 场次的 ContextService 会话键——与 StreamerAgent 内
# ``_record_streamer_speech_history`` / ``_read_history`` 一致使用 "live" 字面量；
# 这是 L1 对话窗口的逻辑键（内存），与存储层 live_sessions 场次主键无关。
_LIVE_SESSION_ID = "live"


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
    """验证配置完整性，缺失必要配置时给出明确错误提示。

    配置按 7 文件树划分：
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

    # collectors 子段位于 tools/agents 等聚合下，由各组件 Schema 自行校验，
    # 不做顶层检查

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


def register_event_interceptors(event_bus: EventBus, config: Dict[str, Any], session_manager: Any = None) -> None:
    """注册输入域事件拦截器。

    - session_stamp：场次归属盖章（业务事件统一注入当前场次主键；框架级必挂）
    - rate_limit：防刷屏/防突发
    - similar_filter：相似文本合并
    - 拦截器返回 ``None`` 即丢弃该事件
    """
    if session_manager is not None:
        event_bus.add_interceptor(SessionStampInterceptor(session_manager))
        logger.info("SessionStampInterceptor 已注册（场次归属单点注入）")

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
# 核心组件构造（组合根）
# ---------------------------------------------------------------------------


async def create_app_components(
    config: Dict[str, Any],
    config_service: ConfigService,
    dev_webui: bool = False,
    *,
    simulator_auto_start: bool = True,
    storage_ledger_auto_start: bool = True,
    session_manager_auto_start: bool = True,
) -> Tuple[
    ContextService,
    EventBus,
    LLMManager,
    Optional["DashboardServer"],
    Optional["EventHistoryRecorder"],
    Optional["CollectorManager"],
    Optional["AgentManager"],
    Optional["SimulatorService"],
    "SQLiteStore",
    Optional["StorageLedger"],
    "LiveSessionManager",
    Optional[ToolRegistry],
    Optional[ToolHealthMonitor],
]:
    """组合根：构造并连接所有核心组件。

    按依赖关系构造：LLM/ContextService 为基座，再装存储与记忆、EventBus 与
    拦截器与场次管理，然后 EventHistoryRecorder + StorageLedger（溯源链收口），
    Collector/Simulator/Agent 按各自配置开关装配，ToolRegistry 在 Agent 启动前
    完成 L2/L1 接线与审计，最后挂 DashboardServer 作为 WebUI observer。

    Args:
        config: 完整配置字典
        config_service: 配置服务实例
        dev_webui: 是否启用 WebUI 开发模式
        simulator_auto_start: 是否允许 SimulatorService 自动启动主循环。
            ``--dry`` 模式传 False，避免产生 LLM 调用。
        storage_ledger_auto_start: 是否启动 StorageLedger 订阅。
            ``--dry`` 模式传 False，避免组合根冒烟时事件被处理（写入数据库）。
            关闭链（``run_shutdown``）无论如何都会 ``stop()`` 一次，幂等安全。
        session_manager_auto_start: 是否启动 LiveSessionManager（残留场次收口）。
            ``--dry`` 模式传 False，避免冒烟写库。

    Returns:
        (context_service, event_bus, llm_service, dashboard_server,
         event_recorder, collector_manager, agent_manager,
         simulator_service, sqlite_store, storage_ledger, session_manager,
         tool_registry, health_monitor)
    """
    # --- 存储与记忆（SQLiteStore + SimpleMemory）---
    # 必须先于 LLMManager 构造：LLMManager 需要注入 store 做 llm_usage 落库
    logger.info("初始化存储与记忆（SQLiteStore + SimpleMemory）...")
    sqlite_store, memory = await build_memory_stack(config)
    logger.info(f"存储与记忆已就绪（db={sqlite_store.db_path}）")

    # --- LLM 服务 ---
    logger.info("初始化 LLM 服务...")
    llm_service = LLMManager(sqlite_store=sqlite_store)
    await llm_service.setup(config)
    # 请求历史落库目标注入（全局单例可能已被惰性创建，须显式 attach）
    get_global_request_history_manager().attach_store(sqlite_store)
    logger.info("已创建 LLM 服务实例")

    # --- ContextService（L1 对话配对窗口，内置默认配置）---
    logger.info("初始化上下文服务...")
    context_service = ContextService()
    await context_service.initialize()
    logger.info("已创建上下文服务实例")

    # --- [context] 组装器配置（Schema 权威：core_schemas.ContextAssemblerConfig）---
    # 控制 Planner 的组装路径开关与长记忆召回条数；CoreService 构造走内置默认。
    context_assembler_config = ContextAssemblerConfig.from_dict(
        config.get("context", {}) if isinstance(config, dict) else {}
    )

    # --- 启动期不进行任何上下文重新写入（每次启动 = 干净测试环境）---
    # 跨场次对话记忆由 SimpleMemory / 摘要机制承载，不在组合根做 live_chat 重新写入。
    # 无显式场次期间消息仅在内存流转，落库路径依据 0 值跳过。

    # --- EventBus + 场次管理 + 拦截器 ---
    logger.info("初始化事件总线...")
    event_bus = EventBus()

    # LiveSessionManager：场次唯一事实源（开启/结束/删除/归属解析）。
    # 启动不自动开新场次；无显式场次期间 ``resolve_pk()`` 返回 None，
    # 下游 StorageLedger 据此跳过落库。
    session_manager = LiveSessionManager(sqlite_store, event_bus)
    if session_manager_auto_start:
        await session_manager.start()

    register_event_interceptors(event_bus, config, session_manager=session_manager)
    logger.info("事件总线已初始化，事件拦截器已挂载")

    # --- 事件历史（系统级）---
    event_recorder = await _start_event_recorder(event_bus, config, sqlite_store)
    logger.info("事件历史记录器已启动")

    # --- StorageLedger（订阅 room.message.# 落业务表）---
    storage_ledger: Optional[StorageLedger] = await _start_storage_ledger(
        event_bus,
        sqlite_store,
        auto_start=storage_ledger_auto_start,
        session_manager=session_manager,
    )

    # --- CollectorManager ---
    # 采集器配置位于 tools.toml 的 [tools.perception.config]
    collector_manager: Optional["CollectorManager"] = None
    tools_perception = (config.get("tools") or {}).get("perception", {}) if isinstance(config, dict) else {}
    collectors_config = tools_perception.get("config", {}) if isinstance(tools_perception, dict) else {}
    if collectors_config:
        logger.info("初始化 CollectorManager（src/modules/collectors/）...")
        collector_manager = CollectorManager()
        await _register_collectors_from_config(
            collector_manager,
            collectors_config,
            config_service,
            event_bus,
            llm_service=llm_service,
        )
        await collector_manager.start_all()
        logger.info(f"CollectorManager 已启动（{len(collector_manager)} 个 Collector）")

    # --- SimulatorService（开发基础设施）---
    # 默认 enabled=false 生产零装配；enabled=true 时装配并自动启动（除非 --dry）。
    # 装配需 SQLiteStore（人设/礼物/世界窗口）+ LLMManager（generate 模式）+ EventBus + ConfigService。
    simulator_service: Optional["SimulatorService"] = None
    simulator_cfg = config.get("simulator", {}) if isinstance(config, dict) else {}
    if isinstance(simulator_cfg, dict) and simulator_cfg.get("enabled", False):
        logger.info("初始化 SimulatorService（src/modules/simulator/）...")
        simulator_service = SimulatorService(
            event_bus=event_bus,
            sqlite_store=sqlite_store,
            services_by_type={type(llm_service): llm_service},
            session_manager=session_manager,
        )
        await simulator_service.setup(
            config_service,
            auto_start=simulator_auto_start,
        )
        if simulator_service.is_running:
            logger.info("SimulatorService 已启动（[simulator].enabled=true）")
        else:
            logger.info("SimulatorService 已装配（enabled=true 但未启动，见 auto_start）")
    else:
        logger.debug("[simulator].enabled=false，零装配 SimulatorService")

    # --- 核心配置预读：TTS 基础设施段 [tts] ---
    #  AgentManager 注册 StreamerAgent 时需要 speech_config（max_queue /
    #  render_timeout_ms / enabled），TTS 引擎装配由 build_tts_infrastructure
    #  按 [tts].provider 选择；两处都消费同一段，提前预读避免重复取值。
    tts_section = config.get("tts", {}) if isinstance(config, dict) else {}
    if not isinstance(tts_section, dict):
        tts_section = {}
    tts_enabled = bool(tts_section.get("enabled", False))
    tts_provider = str(tts_section.get("provider", "edge_tts") or "edge_tts")
    if tts_enabled:
        logger.info(f"[tts] 基础设施启用：provider='{tts_provider}'（引擎将直接注入 StreamerAgent）")
    else:
        logger.info("[tts] 基础设施关闭：TTS 引擎不构造")

    # --- 核心配置预读：字幕基础设施段 [subtitle] ---
    #  AgentManager 注册 StreamerAgent 时需要 subtitle_service（注入后
    #  编排层驱动字幕显示）；配置源为 core.toml [subtitle]。
    subtitle_section = config.get("subtitle", {}) if isinstance(config, dict) else {}
    if not isinstance(subtitle_section, dict):
        subtitle_section = {}
    logger.info(
        f"[subtitle] 基础设施段预读完毕（keys={list(subtitle_section.keys()) if subtitle_section else '<空，使用默认>'}）"
    )

    # 字幕基础设施实例——提到 agents_config 分支之外构建，Dashboard
    # 也消费同一引用（widget 字幕由 DashboardBackend 桥接），保证
    # 两处指向同一可变对象。
    subtitle_service = build_subtitle_infrastructure(subtitle_section)

    # --- AgentManager + StreamerAgent ---
    agent_manager: Optional["AgentManager"] = None
    thinking_hub: Optional[StreamPreviewHub] = None
    agents_config = config.get("agents", {}) if isinstance(config, dict) else {}
    if agents_config:
        logger.info("初始化 AgentManager（src/agents/）...")
        # 挂载 EventBus：工具调用完成后广播 tool.result.<name>（Dashboard 溯源消费）；
        # 熔断器连续失败阈值由 [tools.health].failure_threshold 控制（<=0 关闭熔断）
        tools_health_cfg = (config.get("tools") or {}).get("health", {}) if isinstance(config, dict) else {}
        if not isinstance(tools_health_cfg, dict):
            tools_health_cfg = {}
        # 注意不可用 `or 3` 兜底：会把用户显式配置的 0（关闭熔断）吞成 3
        failure_threshold = int(tools_health_cfg.get("failure_threshold", 3))
        tool_registry = ToolRegistry(event_bus=event_bus, failure_threshold=failure_threshold)
        agent_manager = AgentManager(tool_registry=tool_registry, memory=memory)

        # TTS 引擎实例（基础设施，不经 ToolRegistry）：按 [tts] 段装配；
        # 失败 / 关闭时返回 None，StreamerAgent 走 TTS 关闭路径。
        tts_engine = build_tts_infrastructure(tts_section, event_bus=event_bus)

        # 思考流旁路 hub（ADR-008；观察面专用）：Agent 装配先于 dashboard 启动，
        # ws 通道延迟绑定（dashboard 就绪后 attach_ws）；未绑定期间 delta 丢弃。
        thinking_hub = StreamPreviewHub(
            flush_interval_ms=int(
                (agents_config.get("streamer") or {}).get("thinking_stream_flush_interval_ms", 100) or 100
            ),
            buffer_max=int((agents_config.get("streamer") or {}).get("thinking_stream_buffer_max", 400) or 400),
        )

        # --- 通用任务基建（[tools.tasks] 兜底读取：新键 → 旧键 → 默认；ADR-013）---
        # 记录表挂事件总线（task.changed 广播）；跟踪循环与 Agent 生命周期同步启停。
        _tools_cfg_tmp = (config.get("tools") or {}) if isinstance(config, dict) else {}
        task_ledger = TaskLedger(event_bus=event_bus)
        tasks_poll_ms, tasks_wait_ms = resolve_tasks_config(
            _tools_cfg_tmp if isinstance(_tools_cfg_tmp, dict) else None,
            agents_config,
        )
        task_tracker = TaskTracker(
            tool_registry,
            task_ledger,
            poll_interval_ms=tasks_poll_ms,
            wait_timeout_ms=tasks_wait_ms,
        )
        task_tracker.start()
        logger.info(f"任务跟踪循环已启动（poll_interval_ms={tasks_poll_ms}, wait_timeout_ms={tasks_wait_ms}）")

        await _register_agents_from_config(
            agent_manager,
            agents_config,
            config_service,
            llm_service,
            event_bus,
            context_service,
            tool_registry,
            memory,
            task_tracker,
            context_assembler_config=context_assembler_config,
            tts_section=tts_section,
            tts_engine=tts_engine,
            subtitle_service=subtitle_service,
            session_manager=session_manager,
            thinking_sink=thinking_hub,
        )

        # --- 核心域工具（avatar/studio 域开关，L2 Provider）---
        # 在 agent_manager.start_all() 之前完成 → StreamerAgent._on_start()
        # 调用 _register_tools() 时 registry 已就绪，可与 L2 工具同台。
        # 配置：bind_core_tools 读取 [tools] 段的域开关（avatar.vts / studio.obs 等），
        # 每个域段 enabled=true 才装配该提供者。TTS/字幕装配由核心 [tts]/[subtitle]
        # 段驱动（见各自 build 入口），不在本段。
        tools_cfg = (config.get("tools") or {}) if isinstance(config, dict) else {}
        tools_section = tools_cfg if isinstance(tools_cfg, dict) else {}

        core_report = bind_core_tools(
            tool_registry,
            tools_section,
        )
        core_succeeded = sum(1 for c in core_report.values() if c > 0)
        core_failed = [name for name, count in core_report.items() if count == 0]
        logger.info(
            f"核心工具包已绑定: 成功 {core_succeeded}/{len(core_report)}"
            f"，合计新增 {sum(core_report.values())} 个工具" + (f"，失败包: {core_failed}" if core_failed else "")
        )

        # --- 记忆检索工具（LLM 主动 query_memory；[tools.memory] 域开关）---
        memory_cfg = tools_section.get("memory", {}) if isinstance(tools_section, dict) else {}
        if isinstance(memory_cfg, dict) and memory_cfg.get("enabled", False):
            memory_tool_count = bind_memory_tools(tool_registry, memory)
            logger.info(f"query_memory 记忆检索工具已注册（新增 {memory_tool_count} 个）")

        # --- 视觉基础模块工具 look_at_screen（L2 DI：组合根注入 Pillow 截图后端）---
        # bootstrap 明文不接管 DI 工具（见 bootstrap.py 注释），由组合根按 [tools.vision] 开关装配
        vision_cfg = tools_section.get("vision", {}) if isinstance(tools_section, dict) else {}
        if isinstance(vision_cfg, dict) and vision_cfg.get("enabled", False):
            vision_config = vision_cfg.get("config", {}) if isinstance(vision_cfg.get("config"), dict) else {}
            tool_registry.register_provider(
                LookAtScreenProvider(
                    config=vision_config,
                    screen_capture=PillowImageGrabCapture(),
                )
            )
            logger.info("look_at_screen 已注册（Pillow 截图后端）")

        # --- 通用 MCP 外部工具源（[tools.mcp] 段驱动；可选能力，失败不阻断启动）---
        # 必须在 start_all() 之前装配：任何启动阶段查询工具清单的消费方
        # 需看到已注册的 MCP 工具。配置示例：
        #   [tools.mcp]  enabled = true
        #   [tools.mcp.config.servers.my_server]  url = "http://127.0.0.1:8766/mcp"
        mcp_pack = tools_section.get("mcp", {}) if isinstance(tools_section, dict) else {}
        if isinstance(mcp_pack, dict) and mcp_pack.get("enabled", False):
            mcp_cfg = mcp_pack.get("config", {}) if isinstance(mcp_pack.get("config"), dict) else {}
            try:
                # 延迟 import：fastmcp 为可选重型依赖（避免启动强制加载）
                from src.modules.mcp import bind_mcp_tools

                mcp_report = await bind_mcp_tools(tool_registry, mcp_cfg)
                ok_count = sum(1 for s in mcp_report.values() if s["ok"])
                server_count = len(mcp_report)
                logger.info(f"MCP 外部工具源装配完成: 成功 {ok_count}/{server_count} 个 server")
                for sname, sinfo in mcp_report.items():
                    if not sinfo["ok"]:
                        logger.warning(f"MCP server '{sname}' 装配失败: {sinfo['error']}")
            except Exception as exc:  # noqa: BLE001 - MCP 装配失败不阻断启动
                logger.warning(f"MCP 外部工具源装配异常（跳过）: {type(exc).__name__}: {exc}")
        elif isinstance(mcp_pack, dict) and "servers" in (mcp_pack.get("config") or {}):
            logger.warning("[tools.mcp].enabled=false 但配置了 servers —— MCP 工具未装配")

        agents_enabled = ((config.get("agents") or {}).get("enabled") or []) if isinstance(config, dict) else []
        if "text_adv" in agents_enabled and "vision_look_at_screen" not in tool_registry:
            logger.warning(
                "文字冒险 Agent 已启用但 vision_look_at_screen 未注册"
                "（[tools.vision].enabled=false？）——感知将走空快照降级路径"
            )

        # --- 工具级停用（[tools].disabled_tools 列表；在全部装配完成后应用）---
        # 停用的工具保留在注册表中（工具页可见全集），但对 LLM 不可见且调用被拒绝
        disabled_raw = tools_section.get("disabled_tools") if isinstance(tools_section, dict) else []
        disabled_names = [n for n in disabled_raw if isinstance(n, str)] if isinstance(disabled_raw, list) else []
        if disabled_names:
            applied = tool_registry.apply_disabled(disabled_names)
            logger.info(f"已按 [tools].disabled_tools 停用 {applied} 个工具（重启前配置为唯一事实源）")

        # --- 工具熔断器探活（[tools.health]；装配期启动 monitor，shutdown 关闭）---
        # monitor 必须先于 agents 启动：agents 已经在跑的情况下熔断工具的恢复不能
        # 滞后；started=True 后在 [agents] 任何使用 registry 的调用都能看到探活结果。
        health_monitor: Optional[ToolHealthMonitor] = None
        if bool(tools_health_cfg.get("enabled", True)):
            probe_interval_ms = int(tools_health_cfg.get("probe_interval_ms", 30000) or 30000)
            health_monitor = ToolHealthMonitor(
                tool_registry,
                event_bus,
                probe_interval_ms=probe_interval_ms,
            )
            health_monitor.start()
            logger.info(
                f"ToolHealthMonitor 已启动（probe_interval_ms={probe_interval_ms}, "
                f"failure_threshold={failure_threshold}）"
            )
        else:
            logger.info("[tools.health].enabled=false：仅保留 ToolRegistry 熔断判定，跳过探活循环")

        # --- 框架 Agent 控制与委派原语（provider=framework；含 delegate/task_status）---
        if tool_registry is not None:
            from src.modules.agents.control import build_agent_control_provider

            framework_provider = build_agent_control_provider(agent_manager, task_ledger)
            framework_count = tool_registry.register_provider(framework_provider)
            logger.info(f"framework 工具已注册（控制+委派，新增 {framework_count} 个）")

        await agent_manager.start_all()
        logger.info(f"AgentManager 已启动（{len(agent_manager)} 个 Agent）")

    # --- ToolRegistry 工具审计：所有 Agent 声明的工具是否都已注册实现 ---
    if agent_manager is not None and agent_manager._tool_registry is not None:
        registry = agent_manager._tool_registry
        logger.info(f"ToolRegistry 就绪（{len(registry)} 个工具已注册）")
        missing = agent_manager.audit_tools(registry)
        if missing:
            logger.warning(f"审计发现 {len(missing)} 个已声明但缺失实现的工具: {missing}")
        else:
            logger.info("工具审计通过：所有 Agent 声明的工具均已在 ToolRegistry 中找到实现")

    # --- DashboardServer ---
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
            simulator_service,
            event_recorder,
            session_manager,
        )

    # Dashboard 字幕后端注册：StreamerAgent 与 Dashboard 共享同一
    # subtitle_service 引用，新 Backend 注册后 show/clear 自动广播到 widget。
    # 仅当 [subtitle].backends 显式包含 "dashboard" 时注册——纯 tk_gui
    # 配置不应拉起 Dashboard 广播链路。
    if dashboard_server is not None and dashboard_server.widget_service is not None:
        subtitle_backends = (
            subtitle_section.get("backends", ["tk_gui"]) if isinstance(subtitle_section, dict) else ["tk_gui"]
        )
        if not isinstance(subtitle_backends, list):
            subtitle_backends = ["tk_gui"]
        if "dashboard" in subtitle_backends:
            subtitle_service.register_backend(DashboardBackend(dashboard_server.widget_service))
            logger.info("DashboardBackend 已注册到 subtitle_service")

    # 思考流旁路 hub 绑定 WS 通道（Agent 装配先于 dashboard 启动，见构造处）
    if thinking_hub is not None and dashboard_server is not None and dashboard_server.ws_handler is not None:
        thinking_hub.attach_ws(dashboard_server.ws_handler)
        logger.info("StreamPreviewHub 已绑定 dashboard WS 通道")

    # --- 组件装配完成 ---
    return (
        context_service,
        event_bus,
        llm_service,
        dashboard_server,
        event_recorder,
        collector_manager,
        agent_manager,
        simulator_service,
        sqlite_store,
        storage_ledger,
        session_manager,
        tool_registry if agents_config else None,
        health_monitor if agents_config else None,
        task_tracker if agents_config else None,
    )


# ---------------------------------------------------------------------------
# 子启动器（保持 create_app_components 函数短小）
# ---------------------------------------------------------------------------


async def _start_event_recorder(event_bus: EventBus, config: Dict[str, Any], sqlite_store: "SQLiteStore"):
    """启动事件历史记录器（系统级，与 Dashboard 解耦）。"""
    events_config = config.get("events", {}) if isinstance(config, dict) else {}
    typed_events_config = EventHistoryConfig(**events_config)
    try:
        service = EventHistoryService(
            max_events=typed_events_config.history_size,
            persist=typed_events_config.persist,
            sqlite_store=sqlite_store,
        )
        recorder = EventHistoryRecorder(event_bus=event_bus, event_history=service)
        await recorder.start()
        if typed_events_config.persist:
            # 启动重新写入：从 event_history 表载入当日事件（dashboard 重启不丢当日历史）
            await service.backfill_today_from_store()
        logger.info(
            f"事件历史记录器已启动（size={typed_events_config.history_size}, persist={typed_events_config.persist}）"
        )
        return recorder
    except Exception as e:
        logger.warning(f"事件历史记录器启动失败: {e}")
        return None


async def _start_storage_ledger(
    event_bus: EventBus,
    sqlite_store: SQLiteStore,
    *,
    auto_start: bool = True,
    session_manager: Optional["LiveSessionManager"] = None,
) -> Optional[StorageLedger]:
    """构造 StorageLedger 并按 ``auto_start`` 决定是否订阅。

    ``--dry`` 模式传 ``auto_start=False`` 仅构造不订阅，避免组合根冒烟
    时落无关测试数据；``run_shutdown`` 仍然 stop 一次（leader 幂等）。

    ``session_manager`` 注入后，主播发言（``streamer.speech``）与观众明细
    按其解析的当前场次归属落 ``live_chat`` 等业务表；不注入则按无场次降级
    跳过（与既有降级风格一致）。
    """
    try:
        ledger = StorageLedger(
            event_bus=event_bus,
            sqlite_store=sqlite_store,
            session_manager=session_manager,
        )
        if auto_start:
            await ledger.start()
        logger.info(
            f"StorageLedger 已构造（auto_start={auto_start}；场次归属经 LiveSessionManager 解析；"
            "订阅 room.message.# → 业务表 + streamer.speech → live_chat.assistant）"
        )
        return ledger
    except Exception as exc:
        logger.warning(f"StorageLedger 构造/启动失败: {exc}")
        return None


async def _start_log_streamer():
    """启动 LogStreamer（用于 Dashboard 抓取实时日志）。"""
    streamer = LogStreamer(min_level="DEBUG", persist=True)
    await streamer.start()
    return streamer


async def _register_collectors_from_config(
    manager,
    config_section,
    config_service,
    event_bus=None,
    llm_service=None,
):
    """根据 [tools.perception.config] 段注册 Collector 实例到 CollectorManager。

    段结构（tools.toml）：
        enabled = ["bili_danmaku", "console_input", ...]
        bili_danmaku = { ... }
        console_input = { ... }

    新增可选 ``llm_service`` 参数，透传给需要 VLM 的采集器（仅
    ``screen``/``read_pingmu``）。其余 collector 不消费 LLMManager，参数被忽略。
    """
    enabled_list = config_section.get("enabled", []) or []
    for collector_name in enabled_list:
        sub_cfg = config_section.get(collector_name, {})
        if not isinstance(sub_cfg, dict):
            sub_cfg = {}
        instance = instantiate_collector(
            collector_name,
            sub_cfg,
            event_bus=event_bus,
            llm_manager=llm_service,
        )
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
    task_tracker=None,
    *,
    context_assembler_config: Optional[Any] = None,
    tts_section: Optional[Dict[str, Any]] = None,
    tts_engine: Optional[Any] = None,
    subtitle_service: Optional[Any] = None,
    session_manager: Optional[Any] = None,
    thinking_sink: Optional[Any] = None,
):
    """根据 [agents] 段注册 Agent 实例到 AgentManager。

    [agents] 段结构（扁平：每个 Agent 一份顶级子配置）：
        enabled = ["streamer", "minecraft", "text_adv"]
        streamer = { planner_llm = "llm_fast", replyer_llm = "llm", ... }
        minecraft = { command_llm = "llm", max_steps = 50 }
        text_adv = { command_llm = "llm", engine_kind = "text_adv" }

    memory 为 SimpleMemory 记忆后端，仅 streamer Agent 消费。

    tts_section 为核心 ``[tts]`` 段，仅 streamer Agent 在构造时消费
    ``speech_config``（enabled / max_queue / render_timeout_ms）。
    tts_engine 为对应 Provider 实例（由 build_tts_infrastructure 构造），
    streamer Agent 直接持有并由编排队列调用其 ``handle_speech``。

    subtitle_service 为字幕基础设施实例（由 build_subtitle_infrastructure
    构造）；streamer Agent 在编排路径上调用其 ``show`` / ``clear``，
    与 TTS 关闭正交——字幕关闭不影响业务事件与 TTS 入队。
    """
    enabled_list = config_section.get("enabled", []) or []
    for agent_name in enabled_list:
        sub_cfg = config_section.get(agent_name, {})
        if not isinstance(sub_cfg, dict):
            sub_cfg = {}
        if agent_name == "streamer":
            try:
                cfg_obj = StreamerAgentConfig(**sub_cfg) if sub_cfg else StreamerAgentConfig()
            except Exception as e:
                logger.warning(f"解析 StreamerAgent 配置失败: {e}; 使用默认配置")
                cfg_obj = StreamerAgentConfig()

            # 从 config_service 拉取 [persona] 段，构造 StreamerAgent
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

            tts = tts_section if isinstance(tts_section, dict) else {}
            # render_timeout_ms 兜底与 Schema 默认一致（防引擎卡死的上限语义）
            speech_cfg = {
                "enabled": bool(tts.get("enabled", False)),
                "max_queue": int(tts.get("max_queue", 3) or 3),
                "render_timeout_ms": int(tts.get("render_timeout_ms", 60000) or 0),
            }

            agent = StreamerAgent(
                config=cfg_obj,
                llm_manager=llm_service,
                prompt_manager=get_prompt_manager(),
                context_service=context_service,
                event_bus=event_bus,
                tool_registry=tool_registry,
                memory=memory,
                persona_provider=persona_provider_dict,
                context_assembler_config=context_assembler_config,
                speech_config=speech_cfg,
                tts_engine=tts_engine,
                subtitle_service=subtitle_service,
                session_manager=session_manager,
                thinking_sink=thinking_sink,
            )
            manager.register(
                agent,
                spec_provider="builtin",
                description="直播主播决策主体：聚合弹幕 → Planner 决策 → Replyer 表达",
            )
            continue
        if agent_name == "minecraft":
            try:
                from src.agents.minecraft import MinecraftAgent
                from src.agents.minecraft.config import MinecraftConfig

                mc_section = sub_cfg if isinstance(sub_cfg, dict) else {}
                try:
                    minecraft_cfg = MinecraftConfig(**mc_section)
                except Exception as e:
                    logger.warning(f"解析 MinecraftConfig 失败: {e}; 使用默认配置")
                    minecraft_cfg = MinecraftConfig()
                minecraft_agent = MinecraftAgent(
                    config=minecraft_cfg,
                    llm_manager=llm_service,
                    llm_profile=str(mc_section.get("command_llm", "llm") or "llm"),
                    prompt_manager=get_prompt_manager(),
                    event_bus=event_bus,
                    tool_registry=tool_registry,
                    live_session_id=_LIVE_SESSION_ID,
                    thinking_sink=thinking_sink,
                    task_tracker=task_tracker,
                )
                manager.register(
                    minecraft_agent,
                    spec_provider="minecraft",
                    description="游戏 AI 玩家代理（Minecraft / MaiCraftMod）",
                )
                logger.info("MinecraftAgent 已注册")
            except Exception as e:
                logger.warning(f"minecraft Agent 注册失败: {e}")
            continue
        if agent_name == "text_adv":
            try:
                text_adv_section = sub_cfg if isinstance(sub_cfg, dict) else {}
                try:
                    text_adv_cfg = TextAdvGameConfig(**text_adv_section)
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
                    spec_provider="text_adv",
                    description="游戏 AI 玩家代理（text_adv 文字冒险引擎）",
                )
                logger.info("TextAdvGameAgent 已注册")
            except Exception as e:
                logger.warning(f"text_adv Agent 注册失败: {e}")
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
    simulator_service: Optional["SimulatorService"] = None,
    event_recorder: Optional["EventHistoryRecorder"] = None,
    session_manager: Optional["LiveSessionManager"] = None,
):
    """启动 DashboardServer（仅作为 WebUI observer，不参与决策数据流）。"""
    try:
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
            simulator_service=simulator_service,
            session_manager=session_manager,
            # 事件历史服务所有权在 EventHistoryRecorder，这里共享引用供
            # REST（/events、/traces）与 WS（events.history 推送）读取
            event_history=(event_recorder.event_history if event_recorder else None),
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
    simulator_service: Optional["SimulatorService"],
    *,
    sqlite_store: Optional["SQLiteStore"] = None,
    storage_ledger: Optional["StorageLedger"] = None,
    session_manager: Optional["LiveSessionManager"] = None,
    tool_registry: Optional[ToolRegistry] = None,
    health_monitor: Optional[ToolHealthMonitor] = None,
    task_tracker: Optional[TaskTracker] = None,
) -> None:
    """按依赖关系反向关闭：先停数据生产者 CollectorManager，再停 SimulatorService 与 AgentManager，然后 Dashboard 与事件历史/StorageLedger（必须在 EventBus.cleanup 之前 off，否则 listener 解绑失败），再依次停 ToolHealthMonitor、关闭 MCP stdio 子进程（修停机泄漏），最后 EventBus/ContextService/LLMManager 清理，SQLiteStore.close 收尾落盘。"""
    _saw_cancelled = False

    async def safe_log(coro, name: str):
        nonlocal _saw_cancelled
        try:
            return await coro
        except (Exception, asyncio.CancelledError) as e:
            _saw_cancelled = _saw_cancelled or isinstance(e, asyncio.CancelledError)
            logger.error(f"{name} 失败: {e}")

    # 关闭起点广播：趁 EventHistoryRecorder 还在订阅，先落 core.shutdown
    if event_bus is not None:
        with contextlib.suppress(Exception):
            await event_bus.emit(
                CoreEvents.CORE_SHUTDOWN,
                CoreShutdownPayload(event=CoreEvents.CORE_SHUTDOWN, message="Amaidesu 开始关闭"),
                source="Main",
            )

    if collector_manager is not None:
        logger.info("正在停止 CollectorManager...")
        await safe_log(collector_manager.stop_all(), "CollectorManager.stop_all")
        await safe_log(collector_manager.cleanup_all(), "CollectorManager.cleanup_all")
        logger.info("CollectorManager 已停止")

    if simulator_service is not None:
        logger.info("正在停止 SimulatorService...")
        await safe_log(simulator_service.stop(), "SimulatorService.stop")
        await safe_log(simulator_service.cleanup(), "SimulatorService.cleanup")
        logger.info("SimulatorService 已停止")

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

    if storage_ledger is not None:
        logger.info("正在停止 StorageLedger...")
        await safe_log(storage_ledger.stop(), "StorageLedger")
        logger.info("StorageLedger 已停止")

    # 场次结账：进行中的显式场次在进程退出时收口（live.ended 事件 + 结束时间；
    # 空场次整行丢弃）。无显式场次时 close_session 返回 False 静默跳过。
    if session_manager is not None:
        await safe_log(
            session_manager.close_session(reason="进程退出"),
            "live_sessions 场次结账",
        )
        await safe_log(session_manager.stop(), "LiveSessionManager.stop")

    # 工具熔断器探活：先停 monitor 循环（不再触发新的 recover_tool），
    # 再关闭 MCP stdio 子进程（修停机泄漏——``close_mcp_providers`` 此前
    # 仅导出未被调用，MCP server 子进程会随 Python 进程一起被强杀）。
    if task_tracker is not None:
        await safe_log(task_tracker.stop(), "TaskTracker.stop")

    if health_monitor is not None:
        logger.info("正在停止 ToolHealthMonitor...")
        await safe_log(health_monitor.stop(), "ToolHealthMonitor.stop")
        logger.info("ToolHealthMonitor 已停止")

    if tool_registry is not None:
        logger.info("正在关闭 MCP Provider（修停机泄漏）...")
        # 函数体内 import：fastmcp 为可选重型依赖（避免停机路径强制加载）
        from src.modules.mcp import close_mcp_providers

        await safe_log(close_mcp_providers(tool_registry), "close_mcp_providers")

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
    """应用程序主入口。"""
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
        simulator_service,
        sqlite_store,
        storage_ledger,
        session_manager,
        tool_registry,
        health_monitor,
        task_tracker,
    ) = await create_app_components(
        config,
        config_service,
        dev_webui=args.dev_webui,
        simulator_auto_start=not args.dry,
        storage_ledger_auto_start=not args.dry,
        session_manager_auto_start=not args.dry,
    )

    # 启动完成广播：此时 EventHistoryRecorder 已就绪，core.startup 会进事件历史
    await event_bus.emit(
        CoreEvents.CORE_STARTUP,
        CoreStartupPayload(event=CoreEvents.CORE_STARTUP, message="Amaidesu 组件装配完成"),
        source="Main",
    )

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
            simulator_service,
            sqlite_store=sqlite_store,
            storage_ledger=storage_ledger,
            session_manager=session_manager,
            tool_registry=tool_registry,
            health_monitor=health_monitor,
            task_tracker=task_tracker,
        )
        return

    stop_event = asyncio.Event()
    orig_sigint, orig_sigterm = setup_signal_handlers(stop_event)

    # 事件循环心跳探测：sleep(1) 的唤醒间隔异常拉长，说明循环被同步
    # 调用冻结（如阻塞式 HTTP/控制台写入），输入丢行类问题由此定位
    async def event_loop_heartbeat() -> None:
        last = time.perf_counter()
        while True:
            await asyncio.sleep(1)
            now = time.perf_counter()
            gap = now - last
            last = now
            if gap > 2.5:
                logger.warning(f"事件循环心跳间隔 {gap:.1f}s（存在同步阻塞调用冻结循环）")

    heartbeat_task = asyncio.create_task(event_loop_heartbeat(), name="event-loop-heartbeat")

    logger.info("应用程序正在运行。按 Ctrl+C 退出。")

    try:
        await stop_event.wait()
        logger.info("收到关闭信号，开始执行清理...")
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，开始清理...")
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

    restore_signal_handlers(orig_sigint, orig_sigterm)
    await run_shutdown(
        context_service,
        event_bus,
        llm_service,
        dashboard_server,
        event_recorder,
        collector_manager,
        agent_manager,
        simulator_service,
        sqlite_store=sqlite_store,
        storage_ledger=storage_ledger,
        session_manager=session_manager,
        tool_registry=tool_registry,
        health_monitor=health_monitor,
        task_tracker=task_tracker,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，强制退出。")
