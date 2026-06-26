"""Amaidesu 应用程序主入口。"""

import webbrowser
import argparse
import asyncio
import contextlib
import os
import signal
import sys
from typing import Any, Dict, Optional, Tuple

from loguru import logger as loguru_logger
from src.modules.dashboard.server import DashboardServer
from src.modules.events import (
    EventBus,
    list_registered_events,
    register_core_events,
)
from src.modules.logging import get_logger
from src.modules.config.service import ConfigService
from src.modules.llm.manager import LLMManager
from src.modules.context import ContextService, ContextServiceConfig
from src.modules.prompts import get_prompt_manager
from src.modules.mcp import MCPServerConfig, MCPServerService

from src.stages.decision import DeciderManager
from src.stages.input.pipelines.manager import InputPipelineManager
from src.stages.input.manager import InputCollectorManager
from src.stages.output import OutputHandlerManager

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
    return parser.parse_args()


def setup_logging_early(args: argparse.Namespace) -> None:
    """早期日志配置，在导入阶段参与者之前调用。

    使用默认的INFO级别，避免DEBUG日志过早输出。
    完整的日志配置会在load_config后再次调用。
    """
    from src.modules.logging import configure_from_config

    # 使用默认INFO级别配置
    default_config = {"level": "INFO", "console_level": "INFO"}

    # 如果--debug参数，使用DEBUG级别
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

    # 构建配置字典，应用 CLI 覆盖
    final_config = {}

    if logging_config:
        final_config.update(logging_config)

    # CLI --debug 覆盖配置级别
    if args.debug:
        final_config["level"] = "DEBUG"
        final_config["console_level"] = "DEBUG"

    # CLI --filter 参数处理（模块过滤）
    if args.filter:
        filtered_modules = set(args.filter)

        def filter_logic(record: Dict[str, Any]) -> bool:
            """只显示指定模块的日志，WARNING 及以上级别总是显示"""
            if record["level"].no >= loguru_logger.level("WARNING").no:
                return True
            module_name = record["extra"].get("module")
            return bool(module_name and module_name in filtered_modules)

        final_config["filter"] = filter_logic

    configure_from_config(final_config)

    # 输出启动信息
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
    """
    验证配置完整性，缺失必要配置时给出明确错误提示

    Args:
        config: 配置字典

    Raises:
        SystemExit: 如果配置验证失败
    """
    errors = []

    # 检查核心配置段
    if not config.get("general"):
        errors.append("缺少 [general] 配置段")

    if not config.get("collectors"):
        logger.warning("未检测到 [collectors] 配置，输入Collector功能将被禁用")

    if not config.get("deciders"):
        logger.warning("未检测到 [deciders] 配置，决策Decider功能将被禁用")

    if not config.get("handlers"):
        logger.warning("未检测到 [handlers] 配置，输出Handler功能将被禁用")

    # 如果有严重错误，退出程序
    if errors:
        logger.critical("配置验证失败，发现以下问题：")
        for error in errors:
            logger.critical(f"  - {error}")
        logger.critical("请检查 config.toml 文件并添加缺失的配置段。")
        sys.exit(1)

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
# 管道与核心组件
# ---------------------------------------------------------------------------


async def load_pipeline_manager(config: Dict[str, Any]) -> Optional[InputPipelineManager]:
    """加载输入管道管理器（Input 阶段：消息预处理）。"""
    pipeline_config = config.get("pipelines", {})
    if not pipeline_config:
        logger.info("配置中未启用管道功能")
        return None

    pipeline_load_dir = os.path.join(_BASE_DIR, "src", "stages", "input", "pipelines")
    logger.info(f"准备加载管道 (从目录: {pipeline_load_dir})...")

    try:
        manager = InputPipelineManager()

        # 加载管道（Input 阶段：消息预处理）
        await manager.load_pipelines(pipeline_load_dir, pipeline_config)
        pipeline_count = len(manager._pipelines)

        if pipeline_count > 0:
            logger.info(f"管道加载完成，共 {pipeline_count} 个管道。")
            return manager
        else:
            logger.warning("未找到任何有效的管道，管道功能将被禁用。")
            return None
    except Exception as e:
        logger.error(f"加载管道时出错: {e}", exc_info=True)
        logger.warning("由于加载失败，管道处理功能将被禁用")
        return None


async def create_app_components(
    config: Dict[str, Any],
    input_pipeline_manager: Optional[InputPipelineManager],
    config_service: ConfigService,
) -> Tuple[
    ContextService,
    EventBus,
    Optional[OutputHandlerManager],
    Optional[InputCollectorManager],
    LLMManager,
    Optional[DeciderManager],
    Optional["DashboardServer"],
    Optional["MCPServerService"],
]:
    """创建并连接核心组件。

    创建顺序：
    1. AudioStreamChannel
    2. LLMManager
    3. ContextService（新增，在 EventBus 之前）
    4. EventBus
    5. InputCollectorManager
    6. DeciderManager
    7. OutputHandlerManager
    8. DashboardServer
    9. MCPServerService

    返回顺序（8个）：
    1. ContextService
    2. EventBus
    3. OutputHandlerManager
    4. InputCollectorManager
    5. LLMManager
    6. DeciderManager
    7. DashboardServer
    8. MCPServerService
    """
    output_config = config.get("handlers", {})
    decision_config = config.get("deciders", {})
    input_config = config.get("collectors", {})

    # 这些导入必须在创建任何 Manager 之前完成，以确保 @collector/@decider/@handler 装饰器
    # 已经将类注册到对应的 _COLLECTORS/_DECIDERS/_HANDLERS 字典中
    import src.stages.input.collectors  # noqa: F401 — 触发 @collector 注册
    import src.stages.decision.deciders  # noqa: F401 — 触发 @decider 注册
    import src.stages.output.handlers  # noqa: F401 — 触发 @handler 注册

    if output_config:
        logger.info("检测到输出Handler配置，将启用输出协调器")
    else:
        logger.info("未检测到输出Handler配置，输出协调器功能将被禁用")

    # 创建 AudioStreamChannel
    from src.modules.streaming.audio_stream_channel import AudioStreamChannel

    logger.info("初始化 AudioStreamChannel...")
    audio_stream_channel = AudioStreamChannel("tts")
    await audio_stream_channel.start()
    logger.info("AudioStreamChannel 已创建并启动")

    # LLM 服务
    logger.info("初始化 LLM 服务...")
    llm_service = LLMManager()
    await llm_service.setup(config)
    logger.info("已创建 LLM 服务实例")

    # 上下文服务（在 EventBus 之前创建）
    logger.info("初始化上下文服务...")
    context_config = config.get("context", {})
    context_service_config = ContextServiceConfig(**context_config)
    context_service = ContextService(config=context_service_config)
    await context_service.initialize()
    logger.info("已创建上下文服务实例")

    # 事件总线
    logger.info("初始化事件总线和数据流协调器...")
    event_bus = EventBus()

    # 输入Collector管理器 (Input 阶段)
    input_manager: Optional[InputCollectorManager] = None
    if input_config:
        logger.info("初始化输入Collector管理器（Input 阶段）...")
        try:
            input_manager = InputCollectorManager(
                event_bus=event_bus,
                pipeline_manager=input_pipeline_manager,
            )
            if input_pipeline_manager:
                logger.info("已注入 InputPipelineManager 到 InputCollectorManager")

            await input_manager.setup(input_config, config_service=config_service)
            await input_manager.start()
        except Exception as e:
            logger.error(f"设置输入Collector管理器失败: {e}", exc_info=True)
            logger.warning("输入Collector功能不可用，继续启动其他服务")
            input_manager = None
    else:
        logger.info("未检测到输入配置，输入Collector功能将被禁用")

    # InputCollectorManager 直接订阅事件，无需协调器
    # input_coordinator 已被移除

    # 决策阶段 (Decision 阶段)
    decision_manager: Optional[DeciderManager] = None

    # 初始化 prompt_manager（供 decision 和 output 阶段使用）
    prompt_manager = get_prompt_manager()

    if decision_config:
        logger.info("初始化决策阶段组件（Decision 阶段）...")
        try:
            decision_manager = DeciderManager(event_bus, llm_service, config_service, context_service, prompt_manager)

            await decision_manager.setup(decision_config=decision_config)
            await decision_manager.start()
            active_decider = decision_manager.get_current_decider_name()
            logger.info(f"DeciderManager 已设置并启动（Decider: {active_decider}）")
        except Exception as e:
            logger.error(f"设置决策域组件失败: {e}", exc_info=True)
            logger.warning("决策域功能不可用，继续启动其他服务")
            decision_manager = None
    else:
        logger.info("未检测到决策配置，决策域功能将被禁用")

    # 输出Handler管理器 (Output 阶段)
    logger.info("初始化输出Handler管理器...")
    output_manager: Optional[OutputHandlerManager] = (
        OutputHandlerManager(event_bus, prompt_manager=prompt_manager) if output_config else None
    )
    if output_manager:
        try:
            await output_manager.setup(
                output_config,
                config_service=config_service,
                root_config=config,
                audio_stream_channel=audio_stream_channel,
                prompt_manager=prompt_manager,
            )
            await output_manager.start()
            logger.info("输出Handler管理器已设置（Output 阶段）")
        except Exception as e:
            logger.error(f"设置输出Handler管理器失败: {e}", exc_info=True)
            logger.warning("输出Handler管理器功能不可用，继续启动其他服务")
            output_manager = None

    # ========================================
    # 初始化 Dashboard Server
    # ========================================
    dashboard_server: Optional["DashboardServer"] = None
    dashboard_config = config.get("dashboard", {})

    # 提前创建 LogStreamer，以便捕获应用启动过程中的日志
    from src.modules.logging.log_streamer import LogStreamer

    log_streamer = LogStreamer(min_level="DEBUG")
    await log_streamer.start()  # 立即启动捕获日志

    if dashboard_config.get("enabled", True):
        try:
            from src.modules.dashboard.config import DashboardConfig
            from src.modules.dashboard.server import DashboardServer

            typed_dashboard_config = DashboardConfig(**dashboard_config)

            dashboard_server = DashboardServer(
                event_bus=event_bus,
                input_manager=input_manager,
                decision_manager=decision_manager,
                output_manager=output_manager,
                context_service=context_service,
                config_service=config_service,
                dashboard_config=typed_dashboard_config,
                log_streamer=log_streamer,
            )
            await dashboard_server.start()
            logger.info(f"Dashboard 已启动: http://{typed_dashboard_config.host}:{typed_dashboard_config.port}")

            # 自动打开浏览器
            if typed_dashboard_config.auto_open_browser:
                dashboard_url = f"http://{typed_dashboard_config.host}:{typed_dashboard_config.port}"
                webbrowser.open(dashboard_url)
                logger.info(f"已自动打开浏览器: {dashboard_url}")
        except ImportError as e:
            logger.warning(f"Dashboard 模块导入失败（可能缺少依赖）: {e}")
            logger.warning("Dashboard 功能将被禁用。请运行: uv add fastapi 'uvicorn[standard]'")
        except Exception as e:
            logger.error(f"Dashboard 启动失败: {e}")
            logger.warning("Dashboard 功能将被禁用")

    # ========================================
    # 初始化 MCP 服务
    # ========================================
    mcp_service: Optional["MCPServerService"] = None
    mcp_config = config.get("mcp", {})
    if mcp_config.get("enabled", False):
        try:
            mcp_service = MCPServerService(MCPServerConfig(**mcp_config))
            await mcp_service.setup(mcp_config)
            logger.info("已创建 MCP 服务实例")
        except Exception as e:
            logger.error(f"MCP 服务初始化失败: {e}")
            logger.warning("MCP 服务功能将被禁用")
            mcp_service = None
    else:
        logger.info("MCP 服务未启用")

    return (
        context_service,
        event_bus,
        output_manager,
        input_manager,
        llm_service,
        decision_manager,
        dashboard_server,
        mcp_service,
    )


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
    context_service: "ContextService",
    output_manager: Optional[OutputHandlerManager],
    input_manager: Optional[InputCollectorManager],
    llm_service: LLMManager,
    event_bus: EventBus,
    decision_manager: Optional[DeciderManager],
    dashboard_server: Optional["DashboardServer"] = None,
    mcp_service: Optional["MCPServerService"] = None,
) -> None:
    """按顺序执行关闭与清理。

    关闭顺序（关键）：
    1. 先停止数据生产者（InputCollectorManager）
    2. 组件取消订阅（InputCollectorManager、DeciderManager、OutputHandlerManager）- 在 EventBus.cleanup 之前
       2.1 清理输入域协调器（取消订阅 data.raw）
       2.2 清理 DeciderManager（取消订阅 data.message 和 decision.intent）
       2.3 清理 OutputHandlerManager（取消订阅 decision.intent）
       2.4 清理 OutputHandler（必须在 EventBus.cleanup 之前，因为会调用 event_bus.off()）
    3. 清理 MCP 服务
    4. 清理 Dashboard（停止 WebSocket 连接和服务器）
    5. 等待待处理事件完成（EventBus.cleanup）- 清除所有监听器
    6. 清理基础设施（LLM等）

    关键原则：
    - 所有订阅者的 cleanup() 必须在 EventBus.cleanup() 之前执行
    - 否则 cleanup() 中的 event_bus.off() 会因为监听器已被清除而失败
    - OutputHandler 的 cleanup() 也会调用 event_bus.off()，因此必须在步骤 2.4 中清理
    """
    if input_manager:
        logger.info("正在停止输入Collector（数据生产者）...")
        try:
            await input_manager.cleanup()
            logger.info("输入Collector已停止并清理")
        except Exception as e:
            logger.error(f"停止输入Collector时出错: {e}")

    if decision_manager:
        logger.info("正在清理 DeciderManager（取消订阅）...")
        try:
            await decision_manager.cleanup()
            logger.info("DeciderManager 清理完成")
        except Exception as e:
            logger.error(f"清理 DeciderManager 时出错: {e}")

    if output_manager:
        logger.info("正在清理 OutputHandler（必须在 EventBus.cleanup 之前）...")
        try:
            await output_manager.stop()
            await output_manager.cleanup()
            logger.info("OutputHandler 已清理")
        except Exception as e:
            logger.error(f"清理 OutputHandler 失败: {e}")

    # 3. 清理 MCP 服务
    if mcp_service:
        logger.info("正在清理 MCP 服务...")
        try:
            await mcp_service.cleanup()
            logger.info("MCP 服务已清理")
        except Exception as e:
            logger.error(f"MCP 服务清理失败: {e}")

    # 4. 清理 Dashboard（停止 WebSocket 连接和服务器）
    if dashboard_server:
        logger.info("正在停止 Dashboard...")
        try:
            await dashboard_server.stop()
            await dashboard_server.cleanup()
            logger.info("Dashboard 已停止")
        except Exception as e:
            logger.error(f"停止 Dashboard 失败: {e}")

    # 4. 等待待处理事件完成并清除所有监听器（EventBus 清理）
    logger.info("等待待处理事件完成...")
    if event_bus:
        try:
            await event_bus.cleanup()
            logger.info("EventBus 清理完成")
        except Exception as e:
            logger.error(f"EventBus 清理失败: {e}")

    # 5. 清理基础设施（LLM等）
    logger.info("正在清理核心服务...")
    try:
        if llm_service:
            await llm_service.cleanup()
            logger.info("LLM 服务已清理")
        logger.info("核心服务关闭完成")
    except Exception as e:
        logger.error(f"核心服务关闭时出错: {e}")

    # 6. 清理上下文服务
    logger.info("正在清理上下文服务...")
    try:
        if context_service:
            await context_service.cleanup()
            logger.info("上下文服务已清理")
    except Exception as e:
        logger.error(f"清理上下文服务失败: {e}")

    logger.info("Amaidesu 应用程序已关闭。")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def main() -> None:
    """应用程序主入口点。"""
    # 1. 解析命令行参数
    args = parse_args()

    # 2. 早期日志配置（使用默认INFO级别）
    #    这必须在导入阶段参与者之前完成，避免过早的DEBUG日志输出
    setup_logging_early(args)

    # 3. 加载配置文件
    config_service, config, was_created = load_config()

    # 4. 获取完整的日志配置并重新配置日志（应用完整配置）
    logging_config = config_service.get_section("logging", default={})
    setup_logging(args, logging_config)

    validate_config(config)
    exit_if_config_created(was_created)

    input_pipeline_manager = await load_pipeline_manager(config)

    # 注册所有核心事件（通过 @register_event 装饰器触发 Payload 模块导入）
    # 必须在 create_app_components() 之前调用，否则 EventBus 校验时找不到 Payload 类型
    register_core_events()
    logger.info(f"核心事件注册完成，共 {len(list_registered_events())} 个事件")

    (
        context_service,
        event_bus,
        output_manager,
        input_manager,
        llm_service,
        decision_manager,
        dashboard_server,
        mcp_service,
    ) = await create_app_components(config, input_pipeline_manager, config_service)

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
        output_manager,
        input_manager,
        llm_service,
        event_bus,
        decision_manager,
        dashboard_server,
        mcp_service,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，强制退出。")
