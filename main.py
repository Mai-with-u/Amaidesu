"""Amaidesu 应用程序主入口。"""


import asyncio
import signal
import sys
import os
import argparse
import contextlib
from typing import Any, Callable, Dict, Optional, Tuple

from src.core.amaidesu_core import AmaidesuCore
from src.core.config_service import ConfigService
from src.core.context_manager import ContextManager
from src.core.event_bus import EventBus
from src.core.events import register_core_events
from src.core.flow_coordinator import FlowCoordinator
from src.core.llm_service import LLMService
from src.core.pipeline_manager import PipelineManager
from src.utils.logger import get_logger
from src.layers.input.input_layer import InputLayer
from src.layers.input.input_provider_manager import InputProviderManager
from src.layers.decision.decision_manager import DecisionManager

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


def setup_logging(args: argparse.Namespace) -> None:
    """根据命令行参数配置日志。"""
    base_level = "DEBUG" if args.debug else "INFO"
    log_format = "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <2}</level> | <cyan>{line: <4}</cyan> | <cyan>{extra[module]}</cyan> | <level>{message}</level>"

    logger.remove()

    module_filter_func: Optional[Callable] = None
    if args.filter:
        filtered_modules = set(args.filter)

        def filter_logic(record: Dict[str, Any]) -> bool:
            if record["level"].no >= logger.level("WARNING").no:
                return True
            module_name = record["extra"].get("module")
            return bool(module_name and module_name in filtered_modules)

        module_filter_func = filter_logic
        logger.add(sys.stderr, level="INFO", format=log_format, colorize=True, filter=None)
        logger.info(f"日志过滤器已激活，将主要显示来自模块 {list(filtered_modules)} 的日志")
        logger.remove()

    logger.add(sys.stderr, level=base_level, colorize=True, format=log_format, filter=module_filter_func)

    if args.debug:
        logger.info(f"已启用 DEBUG 日志级别{f'，并激活模块过滤器: {list(args.filter)}' if args.filter else '。'}")
    elif args.filter:
        logger.info(f"日志过滤器已激活: {list(args.filter)} (INFO 级别)")

    logger.info("启动 Amaidesu 应用程序...")


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def load_config() -> Tuple[ConfigService, Dict[str, Any], bool, bool, bool]:
    """加载配置，失败时直接退出进程。返回 (config_service, config, main_copied, plugin_copied, pipeline_copied)。"""
    config_service = ConfigService(base_dir=_BASE_DIR)
    try:
        config, main_copied, plugin_copied, pipeline_copied = config_service.initialize()
        return config_service, config, main_copied, plugin_copied, pipeline_copied
    except (IOError, FileNotFoundError) as e:
        logger.critical(f"配置文件初始化失败: {e}")
        logger.critical("请检查错误信息并确保配置文件或模板存在且可访问。")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"加载配置时发生未知严重错误: {e}", exc_info=True)
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

    # 检查输入层配置
    if not config.get("input"):
        logger.warning("未检测到 [input] 配置，输入层将被禁用")

    # 检查决策层配置
    if not config.get("decision"):
        logger.warning("未检测到 [decision] 配置，决策层将被禁用")

    # 检查渲染层配置
    if not config.get("rendering"):
        logger.warning("未检测到 [rendering] 配置，渲染层将被禁用")

    # 如果有严重错误，退出程序
    if errors:
        logger.critical("配置验证失败，发现以下问题：")
        for error in errors:
            logger.critical(f"  - {error}")
        logger.critical("请检查 config.toml 文件并添加缺失的配置段。")
        sys.exit(1)

    logger.info("配置验证通过")


def exit_if_config_copied(main_cfg_copied: bool, plugin_cfg_copied: bool, pipeline_cfg_copied: bool) -> None:
    """若配置文件为新创建，提示用户并退出。"""
    box = "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"

    if main_cfg_copied:
        logger.warning(box)
        logger.warning("!! 主配置文件 config.toml 已根据模板创建。                 !!")
        logger.warning("!! 请检查根目录下的 config.toml 文件，并根据需要进行修改。   !!")
        logger.warning("!! 修改完成后，请重新运行程序。                           !!")
        logger.warning(box)
        sys.exit(0)

    if plugin_cfg_copied or pipeline_cfg_copied:
        logger.warning(box)
        if plugin_cfg_copied:
            logger.warning("!! 已根据模板创建了部分插件的 config.toml 文件。          !!")
            logger.warning("!! 请检查 src/layers/ 下各插件目录中的 config.toml 文件， !!")
        if pipeline_cfg_copied:
            logger.warning("!! 已根据模板创建了部分管道的 config.toml 文件。          !!")
            logger.warning("!! 请检查 src/pipelines/ 下各管道目录中的 config.toml 文件，!!")
        logger.warning("!! 特别是 API 密钥、房间号、设备名称等需要您修改的配置。   !!")
        logger.warning("!! 修改完成后，请重新运行程序。                           !!")
        logger.warning(box)
        sys.exit(0)

    logger.info("所有必要的配置文件已存在或已处理。继续正常启动...")


# ---------------------------------------------------------------------------
# 管道与核心组件
# ---------------------------------------------------------------------------


async def load_pipeline_manager(config: Dict[str, Any]) -> Optional[PipelineManager]:
    """加载管道管理器，包括 MessagePipeline（旧架构）和 TextPipeline（新架构）。"""
    pipeline_config = config.get("pipelines", {})
    if not pipeline_config:
        logger.info("配置中未启用管道功能")
        return None

    pipeline_load_dir = os.path.join(_BASE_DIR, "src", "pipelines")
    logger.info(f"准备加载管道 (从目录: {pipeline_load_dir})...")

    try:
        manager = PipelineManager()

        # 加载 MessagePipeline（inbound/outbound，旧架构）
        await manager.load_pipelines(pipeline_load_dir, pipeline_config)
        inbound = len(manager._inbound_pipelines)
        outbound = len(manager._outbound_pipelines)

        # 加载 TextPipeline（Layer 1-2 输入层文本预处理，新架构）
        await manager.load_text_pipelines(pipeline_load_dir, pipeline_config)
        text_pipeline_count = len(manager._text_pipelines)

        total = inbound + outbound + text_pipeline_count

        if total > 0:
            logger.info(
                f"管道加载完成，共 {total} 个管道 "
                f"(入站: {inbound}, 出站: {outbound}, TextPipeline: {text_pipeline_count})。"
            )
        else:
            logger.warning("未找到任何有效的管道，管道功能将被禁用。")
            return None
        return manager
    except Exception as e:
        logger.error(f"加载管道时出错: {e}", exc_info=True)
        logger.warning("由于加载失败，管道处理功能将被禁用")
        return None


async def create_app_components(
    config: Dict[str, Any],
    pipeline_manager: Optional[PipelineManager],
    config_service: ConfigService,
) -> Tuple[
    AmaidesuCore,
    Optional[FlowCoordinator],
    InputLayer,
    LLMService,
    Optional[DecisionManager],
    Optional[InputProviderManager],
]:
    """创建并连接核心组件（事件总线、输入层、决策层、协调器、核心、插件）。"""
    general_config = config.get("general", {})
    rendering_config = config.get("rendering", {})
    decision_config = config.get("decision", {})
    input_config = config.get("input", {})
    platform_id = general_config.get("platform_id", "amaidesu")

    if rendering_config:
        logger.info("检测到渲染配置，将启用数据流协调器")
    else:
        logger.info("未检测到渲染配置，数据流协调器功能将被禁用")

    # 上下文管理器
    context_manager_config = config.get("context_manager", {})
    context_manager = ContextManager(context_manager_config)
    logger.info("已创建上下文管理器实例")

    # LLM 服务
    logger.info("初始化 LLM 服务...")
    llm_service = LLMService()
    await llm_service.setup(config)
    logger.info("已创建 LLM 服务实例")

    # 事件总线
    logger.info("初始化事件总线、数据流协调器和 AmaidesuCore...")
    event_bus = EventBus()

    register_core_events()
    logger.info("核心事件已注册到 EventRegistry")

    # 输入Provider管理器 (Layer 1)
    input_provider_manager: Optional[InputProviderManager] = None
    if input_config:
        logger.info("初始化输入Provider管理器（Layer 1 数据流）...")
        try:
            input_provider_manager = InputProviderManager(event_bus)
            providers = await input_provider_manager.load_from_config(input_config)
            await input_provider_manager.start_all_providers(providers)
            logger.info(f"InputProviderManager 已设置（已启动 {len(input_provider_manager._providers)} 个Provider）")
        except Exception as e:
            logger.error(f"设置输入Provider管理器失败: {e}", exc_info=True)
            logger.warning("输入Provider功能不可用，继续启动其他服务")
            input_provider_manager = None
    else:
        logger.info("未检测到输入配置，输入Provider功能将被禁用")

    # 输入层 (Layer 1-2)
    logger.info("初始化输入层组件（Layer 1-2 数据流）...")
    input_layer = InputLayer(event_bus, pipeline_manager=pipeline_manager)
    await input_layer.setup()
    logger.info("InputLayer 已设置（Layer 1-2）")

    # 决策层 (Layer 3)
    decision_manager: Optional[DecisionManager] = None
    if decision_config:
        logger.info("初始化决策层组件（Layer 3 数据流）...")
        try:
            decision_manager = DecisionManager(event_bus, llm_service)

            # 设置决策 Provider（通过 ProviderRegistry 自动创建）
            provider_name = decision_config.get("provider", "maicore")
            provider_config = decision_config.get(provider_name, {})
            await decision_manager.setup(provider_name, provider_config)
            logger.info(f"DecisionManager 已设置（Provider: {provider_name}）")
        except Exception as e:
            logger.error(f"设置决策层组件失败: {e}", exc_info=True)
            logger.warning("决策层功能不可用，继续启动其他服务")
            decision_manager = None
    else:
        logger.info("未检测到决策配置，决策层功能将被禁用")

    # 数据流协调器 (Layer 4-5)
    logger.info("初始化数据流协调器...")
    flow_coordinator: Optional[FlowCoordinator] = FlowCoordinator(event_bus) if rendering_config else None
    if flow_coordinator:
        try:
            await flow_coordinator.setup(rendering_config)
            logger.info("数据流协调器已设置（Layer 4-5）")
        except Exception as e:
            logger.error(f"设置数据流协调器失败: {e}", exc_info=True)
            logger.warning("数据流协调器功能不可用，继续启动其他服务")
            flow_coordinator = None

    # 核心
    core = AmaidesuCore(
        platform=platform_id,
        pipeline_manager=pipeline_manager,
        context_manager=context_manager,
        event_bus=event_bus,
        llm_service=llm_service,
        flow_coordinator=flow_coordinator,
    )

    await core.connect()

    return core, flow_coordinator, input_layer, llm_service, decision_manager, input_provider_manager


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
    flow_coordinator: Optional[FlowCoordinator],
    input_layer: InputLayer,
    llm_service: LLMService,
    core: AmaidesuCore,
    decision_manager: Optional[DecisionManager],
    input_provider_manager: Optional[InputProviderManager],
) -> None:
    """按顺序执行关闭与清理。"""
    if flow_coordinator:
        logger.info("正在清理数据流协调器...")
        try:
            await flow_coordinator.cleanup()
            logger.info("数据流协调器清理完成")
        except Exception as e:
            logger.error(f"清理数据流协调器时出错: {e}")

    # 清理决策层
    if decision_manager:
        logger.info("正在清理决策管理器...")
        try:
            await decision_manager.cleanup()
            logger.info("决策管理器清理完成")
        except Exception as e:
            logger.error(f"清理决策管理器时出错: {e}")

    # 停止输入Provider
    if input_provider_manager:
        logger.info("正在停止输入Provider...")
        try:
            await input_provider_manager.stop_all_providers()
            logger.info("输入Provider已停止")
        except Exception as e:
            logger.error(f"停止输入Provider时出错: {e}")

    logger.info("正在清理输入层组件...")
    try:
        await input_layer.cleanup()
        logger.info("输入层组件清理完成")
    except Exception as e:
        logger.error(f"清理输入层组件时出错: {e}")

    logger.info("正在关闭核心服务...")
    try:
        if llm_service:
            await llm_service.cleanup()
            logger.info("LLM 服务已清理")
        await asyncio.wait_for(core.disconnect(), timeout=2.0)
        logger.info("核心服务关闭完成")
    except asyncio.TimeoutError:
        logger.warning("核心服务关闭超时，强制退出")
    except Exception as e:
        logger.error(f"核心服务关闭时出错: {e}")

    logger.info("Amaidesu 应用程序已关闭。")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def main() -> None:
    """应用程序主入口点。"""
    args = parse_args()
    setup_logging(args)

    config_service, config, main_cfg_copied, plugin_cfg_copied, pipeline_cfg_copied = load_config()
    validate_config(config)
    exit_if_config_copied(main_cfg_copied, plugin_cfg_copied, pipeline_cfg_copied)

    pipeline_manager = await load_pipeline_manager(config)

    core, flow_coordinator, input_layer, llm_service, decision_manager, input_provider_manager = await create_app_components(
        config, pipeline_manager, config_service
    )

    stop_event = asyncio.Event()
    orig_sigint, orig_sigterm = setup_signal_handlers(stop_event)

    logger.info("应用程序正在运行。按 Ctrl+C 退出。")

    try:
        await stop_event.wait()
        logger.info("收到关闭信号，开始执行清理...")
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，开始清理...")

    restore_signal_handlers(orig_sigint, orig_sigterm)
    await run_shutdown(flow_coordinator, input_layer, llm_service, core, decision_manager, input_provider_manager)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，强制退出。")
