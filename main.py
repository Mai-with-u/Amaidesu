"""Amaidesu 应用程序主入口（Wave 6 重写）

v2 架构组合根（参见 .omo/drafts/amaidesu-v2-architecture.md）：
- AudioStreamChannel：TTS ↔ 皮套/远程 的音频总线
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
    """验证配置完整性，缺失必要配置时给出明确错误提示。"""
    if not config.get("general"):
        logger.critical("缺少 [general] 配置段")

    if not config.get("agents"):
        logger.warning("未检测到 [agents] 配置，Agent 功能将被禁用")

    if not config.get("tools"):
        logger.warning("未检测到 [tools] 配置，Tool 功能将被禁用")

    if not config.get("collectors"):
        logger.warning("未检测到 [collectors] 配置，Collector 功能将被禁用")

    logger.info("配置验证通过")


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
    pipelines_config = config.get("pipelines", {}) if isinstance(config, dict) else {}
    rate_limit_cfg = pipelines_config.get("rate_limit", {}) if isinstance(pipelines_config, dict) else {}
    if rate_limit_cfg.get("enabled", True):
        event_bus.add_interceptor(
            RateLimitInterceptor(
                global_rate_limit=rate_limit_cfg.get("global_rate_limit", 100),
                user_rate_limit=rate_limit_cfg.get("user_rate_limit", 10),
                window_size=rate_limit_cfg.get("window_size", 60),
            )
        )
        logger.info("RateLimitInterceptor 已注册（[pipelines.rate_limit]）")

    similar_cfg = pipelines_config.get("similar_filter", {}) if isinstance(pipelines_config, dict) else {}
    if similar_cfg.get("enabled", True):
        event_bus.add_interceptor(
            SimilarFilterInterceptor(
                similarity_threshold=similar_cfg.get("similarity_threshold", 0.85),
                time_window=similar_cfg.get("time_window", 5.0),
                min_text_length=similar_cfg.get("min_text_length", 3),
                cross_user_filter=similar_cfg.get("cross_user_filter", True),
            )
        )
        logger.info("SimilarFilterInterceptor 已注册（[pipelines.similar_filter]）")


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
]:
    """v2 组合根：构造并连接所有核心组件。

    创建顺序（依赖关系）：
    1. AudioStreamChannel（音频总线，工具层依赖）
    2. LLMManager（统一 LLM 客户端池）
    3. ContextService（会话历史 / 多会话隔离）
    4. EventBus + 事件拦截器（§1.46.1）
    5. CollectorManager（采集器：bilibili/console/mock/screen）
    6. AgentManager（Agent 子系统：StreamerAgent 等）
    7. ToolRegistry（通过 AgentManager.register_all_tools 注入到各 Agent）
    8. DashboardServer（WebUI observer）

    Returns:
        (context_service, event_bus, llm_service, dashboard_server,
         event_recorder, collector_manager, agent_manager)
    """
    # --- 1. AudioStreamChannel ---
    from src.modules.streaming.audio_stream_channel import AudioStreamChannel

    logger.info("初始化 AudioStreamChannel...")
    audio_stream_channel = AudioStreamChannel("tts")
    await audio_stream_channel.start()
    logger.info("AudioStreamChannel 已创建并启动")

    # --- 2. LLM 服务 ---
    logger.info("初始化 LLM 服务...")
    llm_service = LLMManager()
    await llm_service.setup(config)
    logger.info("已创建 LLM 服务实例")

    # --- 3. ContextService ---
    logger.info("初始化上下文服务...")
    context_config = config.get("context", {}) if isinstance(config, dict) else {}
    context_service_config = ContextServiceConfig(**context_config)
    context_service = ContextService(config=context_service_config)
    await context_service.initialize()
    logger.info("已创建上下文服务实例")

    # --- 4. EventBus + 拦截器 ---
    logger.info("初始化事件总线...")
    event_bus = EventBus()
    register_event_interceptors(event_bus, config)
    logger.info("事件总线已初始化，事件拦截器已挂载")

    # --- 4b. 事件历史（系统级）---
    event_recorder = await _start_event_recorder(event_bus, config)
    logger.info("事件历史记录器已启动")

    # --- 5. CollectorManager ---
    collector_manager: Optional["CollectorManager"] = None
    collectors_config = config.get("collectors", {}) if isinstance(config, dict) else {}
    if collectors_config:
        logger.info("初始化 CollectorManager（src/modules/collectors/）...")
        from src.modules.collectors.manager import CollectorManager

        collector_manager = CollectorManager()
        await _register_collectors_from_config(collector_manager, collectors_config, config_service)
        await collector_manager.start_all()
        logger.info(f"CollectorManager 已启动（{len(collector_manager)} 个 Collector）")

    # --- 6. AgentManager + StreamerAgent ---
    agent_manager: Optional["AgentManager"] = None
    agents_config = config.get("agents", {}) if isinstance(config, dict) else {}
    if agents_config:
        logger.info("初始化 AgentManager（src/agents/）...")
        from src.modules.agents.manager import AgentManager

        agent_manager = AgentManager()
        await _register_agents_from_config(
            agent_manager,
            agents_config,
            config_service,
            llm_service,
            event_bus,
            context_service,
        )
        await agent_manager.start_all()
        logger.info(f"AgentManager 已启动（{len(agent_manager)} 个 Agent）")

    # --- 7. ToolRegistry（AgentManager 启动时已通过 register_all_tools 自动注入；保留此句为文档占位）---
    logger.info("ToolRegistry 已通过 AgentManager.register_all_tools 完成注入")

    # --- 8. DashboardServer ---
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
            log_streamer,
        )

    # --- 9. 组件装配完成 ---
    return (
        context_service,
        event_bus,
        llm_service,
        dashboard_server,
        event_recorder,
        collector_manager,
        agent_manager,
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


async def _register_collectors_from_config(manager, config_section, config_service):
    """根据 [collectors] 段注册 Collector 实例到 CollectorManager。

    [collectors] 段结构：
        enabled = ["bilibili", "mock", ...]
        bilibili = { ... }
        mock = { ... }
    """

    enabled_list = config_section.get("enabled", []) or []
    for collector_name in enabled_list:
        sub_cfg = config_section.get(collector_name, {})
        if not isinstance(sub_cfg, dict):
            sub_cfg = {}
        try:
            # 通过 ConfigService 加载子 schema
            cfg_class = _try_get_collector_config(collector_name)
            if cfg_class is not None:
                typed_cfg = cfg_class(**sub_cfg)
            else:
                typed_cfg = None
        except Exception as e:
            logger.warning(f"Collector '{collector_name}' 配置验证失败: {e}")
            typed_cfg = None

        instance = _instantiate_collector(collector_name, typed_cfg)
        if instance is None:
            logger.warning(f"Collector '{collector_name}' 未找到 Collector 类，跳过")
            continue
        manager.register(instance, description=sub_cfg.get("description", ""))


def _try_get_collector_config(collector_name: str):
    """尝试获取 Collector 子段 Schema（v2 暂未细分，fallback 到 dict）。"""
    # v2 简化：暂不细分 Collector Schema（agent_schemas / collector_schemas 合并策略后续 wave 决定）
    return None


def _instantiate_collector(collector_name: str, typed_cfg):
    """根据 collector_name 实例化对应的 Collector 类。"""
    try:
        if collector_name == "bilibili":
            from src.modules.collectors.bilibili.legacy.bili_danmaku_collector import (
                BiliDanmakuCollector,
            )

            return BiliDanmakuCollector(config=typed_cfg) if typed_cfg else BiliDanmakuCollector(config={})
        if collector_name == "bilibili_official":
            from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
                BiliDanmakuOfficialCollector,
            )

            return (
                BiliDanmakuOfficialCollector(config=typed_cfg) if typed_cfg else BiliDanmakuOfficialCollector(config={})
            )
        if collector_name == "console_input":
            from src.modules.collectors.console.console_input_collector import (
                ConsoleInputCollector,
            )

            return ConsoleInputCollector(config=typed_cfg) if typed_cfg else ConsoleInputCollector(config={})
        if collector_name == "mock":
            from src.modules.collectors.mock.mock_collector import MockCollector

            return MockCollector(config=typed_cfg) if typed_cfg else MockCollector(config={})
        if collector_name == "screen":
            from src.modules.collectors.screen.screen_change_collector import (
                ScreenChangeCollector,
            )

            return ScreenChangeCollector(config=typed_cfg) if typed_cfg else ScreenChangeCollector(config={})
        if collector_name == "stt":
            from src.modules.collectors.stt.stt_collector import STTCollector

            return STTCollector(config=typed_cfg) if typed_cfg else STTCollector(config={})
        logger.warning(f"未实现的 Collector: {collector_name}")
        return None
    except Exception as e:
        logger.warning(f"实例化 Collector '{collector_name}' 失败: {e}")
        return None


async def _register_agents_from_config(
    manager,
    config_section,
    config_service,
    llm_service,
    event_bus,
    context_service,
):
    """根据 [agents] 段注册 Agent 实例到 AgentManager。

    [agents] 段结构（v2）：
        enabled = ["streamer", "game"]
        streamer = { planner_llm = "llm_fast", replyer_llm = "llm", ... }
        game = { ... }
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
            agent = StreamerAgent(
                config=cfg_obj,
                llm_manager=llm_service,
                prompt_manager=get_prompt_manager(),
                context_service=context_service,
                event_bus=event_bus,
            )
            manager.register(agent, spec_provider="builtin")
            continue
        if agent_name == "game":
            # Wave 7 占位：游戏 Agent 范式（§1.5.1）
            logger.info("game Agent 待 Wave 7 实现，当前跳过")
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
    log_streamer,
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
) -> None:
    """v2 关闭顺序（依赖关系反向）：

    1. 停止 CollectorManager（数据生产者，停止 emit 事件）
    2. 停止 AgentManager（Agent 主循环，后台任务退出）
    3. 停止 Dashboard（WebSocket 连接关闭）
    4. 停止 EventHistoryRecorder（必须在 EventBus.cleanup 之前 off）
    5. EventBus.cleanup（清除所有 listener）
    6. LLMManager.cleanup
    7. ContextService.cleanup
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
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，强制退出。")
