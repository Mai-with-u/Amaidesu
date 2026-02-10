"""Amaidesu 应用程序主入口。"""

import asyncio
import signal
import sys
import os
import argparse
import contextlib
from typing import Any, Dict, Optional, Tuple

from src.core.amaidesu_core import AmaidesuCore
from src.core.provider_registry import ProviderRegistry
from src.services.config.service import ConfigService
from src.core.event_bus import EventBus
from src.core.events import register_core_events
from src.domains.output import OutputCoordinator
from src.services.llm.manager import LLMManager
from src.domains.input.pipelines.manager import InputPipelineManager
from src.core.utils.logger import get_logger
from loguru import logger as loguru_logger
from src.domains.input.coordinator import InputCoordinator
from src.domains.input.provider_manager import InputProviderManager
from src.domains.decision import DecisionProviderManager

logger = get_logger("Main")

# 可选服务导入
try:
    from src.services.dg_lab import DGLabService, DGLabConfig

    DG_LAB_AVAILABLE = True
except ImportError:
    DG_LAB_AVAILABLE = False
    logger.debug("DGLab 服务不可用（aiohttp 未安装）")
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
        "--arch-validate",
        action="store_true",
        help="启用架构约束运行时验证（防止违反3域分层原则）",
    )
    return parser.parse_args()


def setup_logging_early(args: argparse.Namespace) -> None:
    """早期日志配置，在导入providers之前调用。

    使用默认的INFO级别，避免DEBUG日志过早输出。
    完整的日志配置会在load_config后再次调用。
    """
    from src.core.utils.logger import configure_from_config

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
    from src.core.utils.logger import configure_from_config

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
        if plugin_cfg_copied:
            logger.warning("!! 已根据模板创建了部分插件的 config.toml 文件。          !!")
            logger.warning("!! 请检查 src/domains/ 下各Provider目录中的 config.toml 文件， !!")
        if pipeline_cfg_copied:
            logger.warning("!! 已根据模板创建了部分管道的 config.toml 文件。          !!")
            logger.warning("!! 请检查 src/domains/input/pipelines/ 下各管道目录中的 config.toml 文件，!!")
        logger.warning("!! 特别是 API 密钥、房间号、设备名称等需要您修改的配置。   !!")
        logger.warning("!! 修改完成后，请重新运行程序。                           !!")
        logger.warning(box)
        sys.exit(0)

    logger.info("所有必要的配置文件已存在或已处理。继续正常启动...")


# ---------------------------------------------------------------------------
# 管道与核心组件
# ---------------------------------------------------------------------------


async def load_pipeline_manager(config: Dict[str, Any]) -> Optional[InputPipelineManager]:
    """加载输入管道管理器，仅包含 TextPipeline（Input Domain: 文本预处理）。"""
    pipeline_config = config.get("pipelines", {})
    if not pipeline_config:
        logger.info("配置中未启用管道功能")
        return None

    pipeline_load_dir = os.path.join(_BASE_DIR, "src", "domains", "input", "pipelines")
    logger.info(f"准备加载管道 (从目录: {pipeline_load_dir})...")

    try:
        manager = InputPipelineManager()

        # 加载 TextPipeline（Input Domain: 文本预处理）
        await manager.load_text_pipelines(pipeline_load_dir, pipeline_config)
        text_pipeline_count = len(manager._text_pipelines)

        if text_pipeline_count > 0:
            logger.info(f"管道加载完成，共 {text_pipeline_count} 个 TextPipeline。")
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
    input_pipeline_manager: Optional[InputPipelineManager],
    config_service: ConfigService,
    arch_validate: bool = False,
) -> Tuple[
    AmaidesuCore,
    Optional[OutputCoordinator],
    InputCoordinator,
    LLMManager,
    Optional[DecisionProviderManager],
    Optional[InputProviderManager],
    Optional[DGLabService],
]:
    """创建并连接核心组件（事件总线、输入域、决策域、协调器、核心、插件）。"""
    general_config = config.get("general", {})
    # 使用新的 [providers.*] 配置格式
    output_config = config.get("providers", {}).get("output", {})
    decision_config = config.get("providers", {}).get("decision", {})
    input_config = config.get("providers", {}).get("input", {})
    platform_id = general_config.get("platform_id", "amaidesu")

    if output_config:
        logger.info("检测到输出Provider配置，将启用输出协调器")
    else:
        logger.info("未检测到输出Provider配置，输出协调器功能将被禁用")

    # 创建 AudioStreamChannel
    from src.core.streaming.audio_stream_channel import AudioStreamChannel

    logger.info("初始化 AudioStreamChannel...")
    audio_stream_channel = AudioStreamChannel("tts")
    await audio_stream_channel.start()
    logger.info("AudioStreamChannel 已创建并启动")

    # LLM 服务
    logger.info("初始化 LLM 服务...")
    llm_service = LLMManager()
    await llm_service.setup(config)
    logger.info("已创建 LLM 服务实例")

    # DGLab 服务（可选）
    dg_lab_service: Optional[DGLabService] = None
    dg_lab_config = config.get("dg_lab", {})
    if dg_lab_config and DG_LAB_AVAILABLE:
        try:
            logger.info("初始化 DGLab 服务...")
            dg_lab_config_obj = DGLabConfig(**dg_lab_config)
            dg_lab_service = DGLabService(dg_lab_config_obj)
            await dg_lab_service.setup()
            logger.info("DGLab 服务已初始化")
        except Exception as e:
            logger.error(f"初始化 DGLab 服务失败: {e}", exc_info=True)
            logger.warning("DGLab 服务不可用，继续启动其他服务")
            dg_lab_service = None
    elif dg_lab_config and not DG_LAB_AVAILABLE:
        logger.warning("配置中启用了 DGLab 服务，但 aiohttp 未安装。请运行 `uv add aiohttp`")
    else:
        logger.info("未检测到 DGLab 配置，DGLab 服务将被禁用")

    # 事件总线
    logger.info("初始化事件总线、数据流协调器和 AmaidesuCore...")
    event_bus = EventBus()
    register_core_events()

    # 启用架构验证（如果指定了 --arch-validate 参数）
    if arch_validate:
        from src.core.events.architectural_validator import ArchitecturalValidator

        validator = ArchitecturalValidator(event_bus, enabled=True, strict=False)
        # 保存引用防止被垃圾回收
        event_bus._arch_validator = validator
        logger.info("已启用架构约束运行时验证器")

    # 输入Provider管理器 (Input Domain)
    input_provider_manager: Optional[InputProviderManager] = None
    if input_config:
        logger.info("初始化输入Provider管理器（Input Domain）...")
        try:
            input_provider_manager = InputProviderManager(event_bus)
            providers = await input_provider_manager.load_from_config(input_config, config_service=config_service)
            await input_provider_manager.start_all_providers(providers)
        except Exception as e:
            logger.error(f"设置输入Provider管理器失败: {e}", exc_info=True)
            logger.warning("输入Provider功能不可用，继续启动其他服务")
            input_provider_manager = None
    else:
        logger.info("未检测到输入配置，输入Provider功能将被禁用")

    # 输入域 (Input Domain)
    input_coordinator = InputCoordinator(event_bus, pipeline_manager=input_pipeline_manager)
    await input_coordinator.setup()

    # 决策域 (Decision Domain)
    decision_provider_manager: Optional[DecisionProviderManager] = None
    if decision_config:
        logger.info("初始化决策域组件（Decision Domain）...")
        try:
            decision_provider_manager = DecisionProviderManager(event_bus, llm_service)

            # 设置决策Provider（通过 ProviderRegistry 自动创建）
            # 使用新的配置格式：decision_config 包含 active_provider 和 available_providers
            await decision_provider_manager.setup(decision_config=decision_config)
            active_provider = decision_provider_manager.get_current_provider_name()
            logger.info(f"DecisionProviderManager 已设置（Provider: {active_provider}）")
        except Exception as e:
            logger.error(f"设置决策域组件失败: {e}", exc_info=True)
            logger.warning("决策域功能不可用，继续启动其他服务")
            decision_provider_manager = None
    else:
        logger.info("未检测到决策配置，决策域功能将被禁用")

    # 输出协调器 (Output Domain)
    logger.info("初始化输出协调器...")
    output_coordinator: Optional[OutputCoordinator] = OutputCoordinator(event_bus) if output_config else None
    if output_coordinator:
        try:
            await output_coordinator.setup(
                output_config,
                config_service=config_service,
                root_config=config,
                audio_stream_channel=audio_stream_channel,
            )
            logger.info("输出协调器已设置（Output Domain）")
        except Exception as e:
            logger.error(f"设置输出协调器失败: {e}", exc_info=True)
            logger.warning("输出协调器功能不可用，继续启动其他服务")
            output_coordinator = None

    # 核心
    core = AmaidesuCore(
        platform=platform_id,
        pipeline_manager=input_pipeline_manager,
        event_bus=event_bus,
        llm_service=llm_service,
        decision_provider_manager=decision_provider_manager,
        output_coordinator=output_coordinator,
    )

    await core.connect()

    # 注册 DGLab 服务到服务管理器
    if dg_lab_service:
        core.register_service("dg_lab", dg_lab_service)
        logger.info("DGLab 服务已注册到服务管理器")

    return (
        core,
        output_coordinator,
        input_coordinator,
        llm_service,
        decision_provider_manager,
        input_provider_manager,
        dg_lab_service,
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
    output_coordinator: Optional[OutputCoordinator],
    input_coordinator: InputCoordinator,
    llm_service: LLMManager,
    core: AmaidesuCore,
    decision_provider_manager: Optional[DecisionProviderManager],
    input_provider_manager: Optional[InputProviderManager],
    dg_lab_service: Optional[DGLabService] = None,
) -> None:
    """按顺序执行关闭与清理。

    关闭顺序（关键）：
    1. 先停止数据生产者（InputProvider）
    2. 组件取消订阅（InputCoordinator、OutputCoordinator、DecisionProviderManager）- 在 EventBus.cleanup 之前
    2.4 清理 OutputProvider（必须在 EventBus.cleanup 之前，因为会调用 event_bus.off()）
    3. 等待待处理事件完成（EventBus.cleanup）- 清除所有监听器
    4. 清理基础设施（LLM、Core等）

    关键原则：
    - 所有订阅者的 cleanup() 必须在 EventBus.cleanup() 之前执行
    - 否则 cleanup() 中的 event_bus.off() 会因为监听器已被清除而失败
    - OutputProvider 的 cleanup() 也会调用 event_bus.off()，因此必须在步骤 2.4 中清理
    """
    # 1. 先停止输入Provider（数据生产者）
    if input_provider_manager:
        logger.info("正在停止输入Provider（数据生产者）...")
        try:
            await input_provider_manager.stop_all_providers()
            logger.info("输入Provider已停止")
        except Exception as e:
            logger.error(f"停止输入Provider时出错: {e}")

    # 2. 组件取消订阅（必须在 EventBus.cleanup 之前）
    # 这样组件可以正确地移除它们的监听器

    # 2.1 清理输入域协调器（取消订阅 perception.raw_data.generated）
    if input_coordinator:
        logger.info("正在清理输入域协调器（取消订阅）...")
        try:
            await input_coordinator.cleanup()
            logger.info("输入域协调器清理完成")
        except Exception as e:
            logger.error(f"清理输入域协调器时出错: {e}")

    # 2.2 清理输出协调器（取消订阅 decision.intent_generated）
    if output_coordinator:
        logger.info("正在清理输出协调器（取消订阅）...")
        try:
            await output_coordinator.cleanup()
            logger.info("输出协调器清理完成")
        except Exception as e:
            logger.error(f"清理输出协调器时出错: {e}")

    # 2.3 清理决策Provider管理器（取消订阅 normalization.message_ready）
    if decision_provider_manager:
        logger.info("正在清理决策Provider管理器（取消订阅）...")
        try:
            await decision_provider_manager.cleanup()
            logger.info("决策Provider管理器清理完成")
        except Exception as e:
            logger.error(f"清理决策Provider管理器时出错: {e}")

    # 2.4 清理 OutputProvider（必须在 EventBus.cleanup 之前）
    #     因为 OutputProvider.cleanup() 会调用 event_bus.off()
    #     而这需要在 EventBus 清理监听器之前完成
    if output_coordinator and output_coordinator.output_provider_manager:
        logger.info("正在清理 OutputProvider...")
        try:
            await output_coordinator.output_provider_manager.stop_all_providers()
            logger.info("OutputProvider 已清理")
        except Exception as e:
            logger.error(f"清理 OutputProvider 失败: {e}")

    # 3. 等待待处理事件完成并清除所有监听器（EventBus 清理）
    logger.info("等待待处理事件完成...")
    if core and core.event_bus:
        try:
            await core.event_bus.cleanup()
            logger.info("EventBus 清理完成")
        except Exception as e:
            logger.error(f"EventBus 清理失败: {e}")

    # 4. 清理基础设施（LLM、Core等）
    logger.info("正在清理核心服务...")
    try:
        # 注意：DGLab 服务现在由服务管理器自动清理
        # 不需要在这里手动清理

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
    # 1. 解析命令行参数
    args = parse_args()

    # 2. 早期日志配置（使用默认INFO级别）
    #    这必须在导入providers之前完成，避免过早的DEBUG日志输出
    setup_logging_early(args)

    # 3. 加载配置文件
    config_service, config, main_cfg_copied, plugin_cfg_copied, pipeline_cfg_copied = load_config()

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

    input_pipeline_manager = await load_pipeline_manager(config)

    (
        core,
        output_coordinator,
        input_coordinator,
        llm_service,
        decision_provider_manager,
        input_provider_manager,
        dg_lab_service,
    ) = await create_app_components(config, input_pipeline_manager, config_service, args.arch_validate)

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
        output_coordinator,
        input_coordinator,
        llm_service,
        core,
        decision_provider_manager,
        input_provider_manager,
        dg_lab_service,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，强制退出。")
