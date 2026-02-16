"""Amaidesu 应用程序主入口。"""

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from loguru import logger as loguru_logger
from src.modules.events import EventBus
from src.modules.logging import get_logger
from src.modules.registry import ProviderRegistry
from src.modules.config.service import ConfigService
from src.modules.llm.manager import LLMManager
from src.modules.context import ContextService, ContextServiceConfig
from src.modules.prompts import get_prompt_manager

from src.domains.decision import DecisionProviderManager
from src.domains.input.pipelines.manager import InputPipelineManager
from src.domains.input.provider_manager import InputProviderManager
from src.domains.output import OutputProviderManager

if TYPE_CHECKING:
    from src.modules.dashboard import DashboardServer

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
        "--update-configs",
        action="store_true",
        help="更新所有配置文件到最新版本",
    )
    return parser.parse_args()


def setup_logging_early(args: argparse.Namespace) -> None:
    """早期日志配置，在导入providers之前调用。

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


def load_config() -> Tuple[ConfigService, Dict[str, Any], bool, bool, bool, bool]:
    """加载配置，失败时直接退出进程。返回 (config_service, config, main_copied, plugin_copied, pipeline_copied, config_updated)。"""
    config_service = ConfigService(base_dir=_BASE_DIR)
    try:
        config, main_copied, plugin_copied, pipeline_copied, config_updated = config_service.initialize()
        return config_service, config, main_copied, plugin_copied, pipeline_copied, config_updated
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

    # 检查输入Provider配置（新格式）
    if not config.get("providers", {}).get("input"):
        logger.warning("未检测到 [providers.input] 配置，输入Provider功能将被禁用")

    # 检查决策Provider配置（新格式）
    if not config.get("providers", {}).get("decision"):
        logger.warning("未检测到 [providers.decision] 配置，决策Provider功能将被禁用")

    # 检查输出Provider配置（新格式）
    if not config.get("providers", {}).get("output"):
        logger.warning("未检测到 [providers.output] 配置，输出Provider功能将被禁用")

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
        logger.warning("!! 配置文件已更新。                                      !!")
        logger.warning("!! 请检查并修改 config.toml 中的 Provider 和 Pipeline 配置。  !!")
        logger.warning("!! 特别是 API 密钥、房间号、设备名称等需要您修改的配置。   !!")
        logger.warning("!! 修改完成后，请重新运行程序。                           !!")
        logger.warning(box)
        sys.exit(0)

    logger.info("所有必要的配置文件已存在或已处理。继续正常启动...")


def run_update_configs_command() -> None:
    """执行配置更新命令"""
    from src.modules.config.version_manager import ConfigVersionManager

    version_manager = ConfigVersionManager(base_dir=".")

    print("=" * 60)
    print("配置文件批量更新")
    print("=" * 60)

    # 扫描 Provider 配置
    version_manager.scan_provider_configs()

    # 更新主配置
    needs_update, message = version_manager.check_main_config()
    if needs_update:
        print(f"\n主配置: {message}")
        updated, message = version_manager.update_main_config()
        print(f"  结果: {message}")
    else:
        print(f"\n主配置: {message}")

    # 更新 Provider 配置
    print("\nProvider 配置:")
    results = version_manager.update_all_provider_configs()
    for key, updated, message in results:
        status = "[已更新]" if updated else "[跳过]"
        print(f"  {status} {key}: {message}")

    print("=" * 60)
    print("更新完成")
    sys.exit(0)


def log_config_update(main_cfg_updated: bool) -> None:
    """如果配置文件已更新，输出日志"""
    if main_cfg_updated:
        logger.info("=" * 60)
        logger.info("配置文件已根据新模板自动更新")
        logger.info("备份文件: config.toml.backup")
        logger.info("请检查更新后的配置，如有问题可从备份恢复")
        logger.info("提示: 使用 --update-configs 命令可批量更新所有配置")
        logger.info("=" * 60)


# ---------------------------------------------------------------------------
# 管道与核心组件
# ---------------------------------------------------------------------------


async def load_pipeline_manager(config: Dict[str, Any]) -> Optional[InputPipelineManager]:
    """加载输入管道管理器（Input Domain: 消息预处理）。"""
    pipeline_config = config.get("pipelines", {})
    if not pipeline_config:
        logger.info("配置中未启用管道功能")
        return None

    pipeline_load_dir = os.path.join(_BASE_DIR, "src", "domains", "input", "pipelines")
    logger.info(f"准备加载管道 (从目录: {pipeline_load_dir})...")

    try:
        manager = InputPipelineManager()

        # 加载管道（Input Domain: 消息预处理）
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
    Optional[OutputProviderManager],
    Optional[InputProviderManager],
    LLMManager,
    Optional[DecisionProviderManager],
    Optional["DashboardServer"],
]:
    """创建并连接核心组件。

    创建顺序：
    1. AudioStreamChannel
    2. LLMManager
    3. ContextService（新增，在 EventBus 之前）
    4. EventBus
    5. InputProviderManager
    6. DecisionProviderManager
    7. OutputProviderManager
    8. DashboardServer

    返回顺序（7个）：
    1. ContextService
    2. EventBus
    3. OutputProviderManager
    4. InputProviderManager
    5. LLMManager
    6. DecisionProviderManager
    7. DashboardServer
    """
    # 使用新的 [providers.*] 配置格式
    output_config = config.get("providers", {}).get("output", {})
    decision_config = config.get("providers", {}).get("decision", {})
    input_config = config.get("providers", {}).get("input", {})

    if output_config:
        logger.info("检测到输出Provider配置，将启用输出协调器")
    else:
        logger.info("未检测到输出Provider配置，输出协调器功能将被禁用")

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

    # 输入Provider管理器 (Input Domain)
    input_provider_manager: Optional[InputProviderManager] = None
    if input_config:
        logger.info("初始化输入Provider管理器（Input Domain）...")
        try:
            # 直接参数注入：在构造时传入 pipeline_manager
            input_provider_manager = InputProviderManager(
                event_bus=event_bus,
                pipeline_manager=input_pipeline_manager,
            )
            if input_pipeline_manager:
                logger.info("已注入 InputPipelineManager 到 InputProviderManager")

            # 加载并启动 Provider
            await input_provider_manager.setup(input_config, config_service=config_service)
            await input_provider_manager.start()
        except Exception as e:
            logger.error(f"设置输入Provider管理器失败: {e}", exc_info=True)
            logger.warning("输入Provider功能不可用，继续启动其他服务")
            input_provider_manager = None
    else:
        logger.info("未检测到输入配置，输入Provider功能将被禁用")

    # InputProviderManager 直接订阅事件，无需协调器
    # input_coordinator 已被移除

    # 决策域 (Decision Domain)
    decision_provider_manager: Optional[DecisionProviderManager] = None

    # 初始化 prompt_manager（供 decision 和 output domain 使用）
    prompt_manager = get_prompt_manager()

    if decision_config:
        logger.info("初始化决策域组件（Decision Domain）...")
        try:
            decision_provider_manager = DecisionProviderManager(
                event_bus, llm_service, config_service, context_service, prompt_manager
            )

            # 设置决策Provider（通过 ProviderRegistry 自动创建）
            # 使用新的配置格式：decision_config 包含 active_provider 和 available_providers
            await decision_provider_manager.setup(decision_config=decision_config)
            await decision_provider_manager.start()
            active_provider = decision_provider_manager.get_current_provider_name()
            logger.info(f"DecisionProviderManager 已设置并启动（Provider: {active_provider}）")
        except Exception as e:
            logger.error(f"设置决策域组件失败: {e}", exc_info=True)
            logger.warning("决策域功能不可用，继续启动其他服务")
            decision_provider_manager = None
    else:
        logger.info("未检测到决策配置，决策域功能将被禁用")

    # 输出Provider管理器 (Output Domain)
    logger.info("初始化输出Provider管理器...")
    output_provider_manager: Optional[OutputProviderManager] = (
        OutputProviderManager(event_bus, prompt_manager=prompt_manager) if output_config else None
    )
    if output_provider_manager:
        try:
            await output_provider_manager.setup(
                output_config,
                config_service=config_service,
                root_config=config,
                audio_stream_channel=audio_stream_channel,
                prompt_manager=prompt_manager,
            )
            await output_provider_manager.start()
            logger.info("输出Provider管理器已设置（Output Domain）")
        except Exception as e:
            logger.error(f"设置输出Provider管理器失败: {e}", exc_info=True)
            logger.warning("输出Provider管理器功能不可用，继续启动其他服务")
            output_provider_manager = None

    # ========================================
    # 初始化 Dashboard Server
    # ========================================
    dashboard_server: Optional["DashboardServer"] = None
    dashboard_config = config.get("dashboard", {})

    if dashboard_config.get("enabled", True):
        try:
            from src.modules.dashboard import DashboardConfig, DashboardServer

            typed_dashboard_config = DashboardConfig(**dashboard_config)
            dashboard_server = DashboardServer(
                event_bus=event_bus,
                input_manager=input_provider_manager,
                decision_manager=decision_provider_manager,
                output_manager=output_provider_manager,
                context_service=context_service,
                config_service=config_service,
                port=typed_dashboard_config.port,
                host=typed_dashboard_config.host,
                cors_origins=typed_dashboard_config.cors_origins,
                max_history_messages=typed_dashboard_config.max_history_messages,
                websocket_heartbeat=typed_dashboard_config.websocket_heartbeat,
            )
            await dashboard_server.start()
            logger.info(f"Dashboard 已启动: http://{typed_dashboard_config.host}:{typed_dashboard_config.port}")
        except ImportError as e:
            logger.warning(f"Dashboard 模块导入失败（可能缺少依赖）: {e}")
            logger.warning("Dashboard 功能将被禁用。请运行: uv add fastapi 'uvicorn[standard]'")
        except Exception as e:
            logger.error(f"Dashboard 启动失败: {e}")
            logger.warning("Dashboard 功能将被禁用")

    return (
        context_service,
        event_bus,
        output_provider_manager,
        input_provider_manager,
        llm_service,
        decision_provider_manager,
        dashboard_server,
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
    output_provider_manager: Optional[OutputProviderManager],
    input_provider_manager: Optional[InputProviderManager],
    llm_service: LLMManager,
    event_bus: EventBus,
    decision_provider_manager: Optional[DecisionProviderManager],
    dashboard_server: Optional["DashboardServer"] = None,
) -> None:
    """按顺序执行关闭与清理。

    关闭顺序（关键）：
    1. 先停止数据生产者（InputProvider）
    2. 组件取消订阅（InputProviderManager、DecisionProviderManager、OutputProviderManager）- 在 EventBus.cleanup 之前
       2.1 清理输入域协调器（取消订阅 data.raw）
       2.2 清理决策Provider管理器（取消订阅 data.message 和 decision.intent）
       2.3 清理输出Provider管理器（取消订阅 decision.intent）
       2.4 清理 OutputProvider（必须在 EventBus.cleanup 之前，因为会调用 event_bus.off()）
    3. 清理 Dashboard（停止 WebSocket 连接和服务器）
    4. 等待待处理事件完成（EventBus.cleanup）- 清除所有监听器
    5. 清理基础设施（LLM等）

    关键原则：
    - 所有订阅者的 cleanup() 必须在 EventBus.cleanup() 之前执行
    - 否则 cleanup() 中的 event_bus.off() 会因为监听器已被清除而失败
    - OutputProvider 的 cleanup() 也会调用 event_bus.off()，因此必须在步骤 2.4 中清理
    """
    # 1. 先停止输入Provider（数据生产者）
    if input_provider_manager:
        logger.info("正在停止输入Provider（数据生产者）...")
        try:
            await input_provider_manager.stop()
            await input_provider_manager.cleanup()
            logger.info("输入Provider已停止并清理")
        except Exception as e:
            logger.error(f"停止输入Provider时出错: {e}")

    # 2. 组件取消订阅（必须在 EventBus.cleanup 之前）
    # 这样组件可以正确地移除它们的监听器

    # 2.1 清理决策Provider管理器（取消订阅 data.message）
    if decision_provider_manager:
        logger.info("正在清理决策Provider管理器（取消订阅）...")
        try:
            await decision_provider_manager.cleanup()
            logger.info("决策Provider管理器清理完成")
        except Exception as e:
            logger.error(f"清理决策Provider管理器时出错: {e}")

    # 2.2 清理输出Provider管理器（取消订阅 decision.intent）
    if output_provider_manager:
        logger.info("正在清理输出Provider管理器（取消订阅）...")
        try:
            await output_provider_manager.cleanup()
            logger.info("输出Provider管理器清理完成")
        except Exception as e:
            logger.error(f"清理输出Provider管理器时出错: {e}")

    # 2.3 清理 OutputProvider（必须在 EventBus.cleanup 之前）
    #     因为 OutputProvider.cleanup() 会调用 event_bus.off()
    #     而这需要在 EventBus 清理监听器之前完成
    if output_provider_manager:
        logger.info("正在清理 OutputProvider...")
        try:
            await output_provider_manager.stop_all_providers()
            logger.info("OutputProvider 已清理")
        except Exception as e:
            logger.error(f"清理 OutputProvider 失败: {e}")

    # 3. 清理 Dashboard（停止 WebSocket 连接和服务器）
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

    # 检查 --update-configs 命令
    if args.update_configs:
        # 需要先配置日志，否则 print 输出可能被 logger 干扰
        setup_logging_early(args)
        try:
            run_update_configs_command()
        except ImportError as e:
            logger.error(f"ConfigVersionManager 尚未实现: {e}")
            logger.error("请先实现 ConfigVersionManager 类以使用 --update-configs 功能")
            sys.exit(1)
        except Exception as e:
            logger.error(f"配置更新失败: {e}", exc_info=True)
            sys.exit(1)

    # 2. 早期日志配置（使用默认INFO级别）
    #    这必须在导入providers之前完成，避免过早的DEBUG日志输出
    setup_logging_early(args)

    # 3. 加载配置文件
    config_service, config, main_cfg_copied, plugin_cfg_copied, pipeline_cfg_copied, config_updated = load_config()

    # 4. 获取完整的日志配置并重新配置日志（应用完整配置）
    logging_config = config_service.get_section("logging", default={})
    setup_logging(args, logging_config)

    # 5. 配置驱动的Provider注册（按需加载）
    #    根据配置文件中启用的Provider动态导入和注册
    logger.info("开始注册Provider...")
    stats = ProviderRegistry.discover_and_register_providers(config_service, config)
    logger.info(
        f"Provider注册完成: Input={stats['input']}, "
        f"Decision={stats['decision']}, Output={stats['output']}, "
        f"Total={stats['total']}"
    )

    validate_config(config)
    exit_if_config_copied(main_cfg_copied, plugin_cfg_copied, pipeline_cfg_copied)

    # 记录配置更新状态
    log_config_update(config_updated)

    input_pipeline_manager = await load_pipeline_manager(config)

    (
        context_service,
        event_bus,
        output_provider_manager,
        input_provider_manager,
        llm_service,
        decision_provider_manager,
        dashboard_server,
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
        output_provider_manager,
        input_provider_manager,
        llm_service,
        event_bus,
        decision_provider_manager,
        dashboard_server,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，强制退出。")
