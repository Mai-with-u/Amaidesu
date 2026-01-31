import asyncio
import signal
import sys
import os
import argparse  # 导入 argparse


# 从 src 目录导入核心类和插件管理器
from src.core.amaidesu_core import AmaidesuCore
from src.core.plugin_manager import PluginManager
from src.core.pipeline_manager import PipelineManager  # 导入管道管理器
from src.core.event_bus import EventBus  # 导入事件总线
from src.core.llm_service import LLMService  # 导入 LLM 服务
from src.utils.logger import get_logger
from src.utils.config import initialize_configurations  # Updated import
from src.core.avatar.avatar_manager import AvatarControlManager

# 导入输入层组件（Layer 1-2-3 数据流）
from src.perception.input_layer import InputLayer
from src.canonical.canonical_layer import CanonicalLayer

logger = get_logger("Main")

# 获取 main.py 文件所在的目录 (项目根目录)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


async def main():
    """应用程序主入口点。"""

    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description="Amaidesu 应用程序")
    # 添加 --debug 参数，用于控制日志级别
    parser.add_argument("--debug", action="store_true", help="启用 DEBUG 级别日志输出")
    # 添加 --filter 参数，用于过滤特定模块的日志
    parser.add_argument(
        "--filter",
        nargs="+",  # 允许一个或多个参数
        metavar="MODULE_NAME",  # 在帮助信息中显示的参数名
        help="仅显示指定模块的 INFO/DEBUG 级别日志 (WARNING 及以上级别总是显示)",
    )
    # 解析命令行参数
    args = parser.parse_args()

    # --- 配置日志 ---
    base_level = "DEBUG" if args.debug else "INFO"
    # log_format = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{line: <4}</cyan> | <cyan>{extra[module]}</cyan> - <level>{message}</level>"
    log_format = "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <2}</level> | <cyan>{line: <4}</cyan> | <cyan>{extra[module]}</cyan> | <level>{message}</level>"

    # 清除所有预设的 handler (包括 src/utils/logger.py 中添加的)
    logger.remove()

    module_filter_func = None
    if args.filter:
        filtered_modules = set(args.filter)  # 使用集合提高查找效率

        def filter_logic(record):
            # 总是允许 WARN/ERROR/CRITICAL 级别通过
            if record["level"].no >= logger.level("WARNING").no:
                return True
            # 检查模块名是否在过滤列表中
            module_name = record["extra"].get("module")
            if module_name and module_name in filtered_modules:
                return True
            # 其他 DEBUG/INFO 级别的日志，如果模块不在列表里，则过滤掉
            return False

        module_filter_func = filter_logic
        # 使用一个临时 logger 配置来打印这条信息，确保它不被自身过滤掉
        logger.add(sys.stderr, level="INFO", format=log_format, colorize=True, filter=None)  # 临时添加无过滤的 handler
        logger.info(f"日志过滤器已激活，将主要显示来自模块 {list(filtered_modules)} 的日志")
        logger.remove()  # 移除临时 handler

    # 添加最终的 handler，应用过滤器（如果定义了）
    logger.add(
        sys.stderr,
        level=base_level,
        colorize=True,
        format=log_format,
        filter=module_filter_func,  # 如果 args.filter 为 None，filter 参数为 None，表示不过滤
    )

    # 打印日志级别和过滤器状态相关的提示信息
    if args.debug:
        if args.filter:
            logger.info(f"已启用 DEBUG 日志级别，并激活模块过滤器: {list(filtered_modules)}")
        else:
            logger.info("已启用 DEBUG 日志级别。")
    elif args.filter:
        # 如果只设置了 filter 但没设置 debug
        logger.info(f"日志过滤器已激活: {list(filtered_modules)} (INFO 级别)")

    logger.info("启动 Amaidesu 应用程序...")

    # --- 初始化所有配置 ---
    try:
        config, main_cfg_copied, plugin_cfg_copied, pipeline_cfg_copied = initialize_configurations(
            base_dir=_BASE_DIR,
            main_cfg_name="config.toml",  # Default, but explicit
            main_template_name="config-template.toml",  # Default, but explicit
            plugin_dir_name="src/plugins",  # Default, but explicit
            pipeline_dir_name="src/pipelines",  # Default, but explicit
        )
    except (IOError, FileNotFoundError) as e:  # Catch errors from config_manager
        logger.critical(f"配置文件初始化失败: {e}")
        logger.critical("请检查错误信息并确保配置文件或模板存在且可访问。")
        sys.exit(1)
    except Exception as e:  # Catch any other unexpected error during config init
        logger.critical(f"加载配置时发生未知严重错误: {e}", exc_info=True)
        sys.exit(1)

    # --- 处理配置文件复制后的用户提示和退出 ---
    if main_cfg_copied:
        logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        logger.warning("!! 主配置文件 config.toml 已根据模板创建。                 !!")
        logger.warning("!! 请检查根目录下的 config.toml 文件，并根据需要进行修改。   !!")
        logger.warning("!! 修改完成后，请重新运行程序。                           !!")
        logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        sys.exit(0)  # 正常退出，让用户去修改配置

    if plugin_cfg_copied or pipeline_cfg_copied:
        logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        if plugin_cfg_copied:
            logger.warning("!! 已根据模板创建了部分插件的 config.toml 文件。          !!")
            logger.warning("!! 请检查 src/plugins/ 下各插件目录中的 config.toml 文件， !!")
        if pipeline_cfg_copied:
            logger.warning("!! 已根据模板创建了部分管道的 config.toml 文件。          !!")
            logger.warning("!! 请检查 src/pipelines/ 下各管道目录中的 config.toml 文件，!!")
        logger.warning("!! 特别是 API 密钥、房间号、设备名称等需要您修改的配置。   !!")
        logger.warning("!! 修改完成后，请重新运行程序。                           !!")
        logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        sys.exit(0)  # 正常退出，让用户去修改配置

    # 如果没有配置文件被复制，config_manager 已经记录了相关信息，可以简单记录继续
    if not main_cfg_copied and not plugin_cfg_copied and not pipeline_cfg_copied:
        logger.info("所有必要的配置文件已存在或已处理。继续正常启动...")

    # --- 提取配置部分 ---
    # 从配置中提取参数，提供默认值或进行错误处理
    general_config = config.get("general", {})
    maicore_config = config.get("maicore", {})
    http_config = config.get("http_server", {})
    pipeline_config = config.get("pipelines", {})  # 添加管道配置
    rendering_config = config.get("rendering", {})  # Phase 4: 渲染配置

    platform_id = general_config.get("platform_id", "amaidesu_default")

    maicore_host = maicore_config.get("host", "127.0.0.1")
    maicore_port = maicore_config.get("port", 8000)
    # maicore_token = maicore_config.get("token") # 如果需要 token

    http_enabled = http_config.get("enable", False)
    http_host = http_config.get("host", "127.0.0.1") if http_enabled else None
    http_port = http_config.get("port", 8080) if http_enabled else None
    http_callback_path = http_config.get("callback_path", "/maicore_callback")

    # Phase 4: 记录渲染配置状态
    if rendering_config:
        logger.info("检测到渲染配置，将启用输出层")
    else:
        logger.info("未检测到渲染配置，输出层功能将被禁用")

    # --- 加载管道 ---
    pipeline_manager = None
    if pipeline_config:
        pipeline_load_dir = os.path.join(_BASE_DIR, "src", "pipelines")  # 确保变量名不冲突且清晰
        logger.info(f"准备加载管道 (从目录: {pipeline_load_dir})...")

        try:
            # 创建管道管理器并加载管道
            pipeline_manager = PipelineManager()
            await pipeline_manager.load_pipelines(pipeline_load_dir, pipeline_config)

            # 计算加载的管道总数并记录日志
            total_pipelines = len(pipeline_manager._inbound_pipelines) + len(pipeline_manager._outbound_pipelines)
            if total_pipelines > 0:
                logger.info(
                    f"管道加载完成，共 {total_pipelines} 个管道 "
                    f"(入站: {len(pipeline_manager._inbound_pipelines)}, 出站: {len(pipeline_manager._outbound_pipelines)})。"
                )
            else:
                logger.warning("未找到任何有效的管道，管道功能将被禁用。")
                pipeline_manager = None
        except Exception as e:
            logger.error(f"加载管道时出错: {e}", exc_info=True)
            logger.warning("由于加载失败，管道处理功能将被禁用")
            pipeline_manager = None
    else:
        logger.info("配置中未启用管道功能")

    # 从配置中读取上下文管理器配置
    context_manager_config = config.get("context_manager", {})
    logger.info("已读取上下文管理器配置")

    # 创建上下文管理器实例
    from src.core.context_manager import ContextManager

    context_manager = ContextManager(context_manager_config)
    logger.info("已创建上下文管理器实例")

    # --- 初始化 LLM 服务 ---
    logger.info("初始化 LLM 服务...")
    llm_service = LLMService()
    await llm_service.setup(config)
    logger.info("已创建 LLM 服务实例")

    # --- 初始化虚拟形象控制管理器 ---
    avatar_config = config.get("avatar", {})
    if avatar_config.get("enabled", True):
        try:
            avatar = AvatarControlManager(None, avatar_config)
            logger.info("已创建虚拟形象控制管理器实例")
        except ImportError as e:
            logger.warning(f"无法导入虚拟形象控制模块: {e}")
            avatar = None
    else:
        logger.info("虚拟形象控制功能已禁用")
        avatar = None

    # --- 初始化事件总线和核心 ---
    logger.info("初始化事件总线和AmaidesuCore...")

    # 从配置中读取事件总线配置
    event_bus_config = config.get("event_bus", {})
    enable_validation = event_bus_config.get("enable_validation", False)

    event_bus = EventBus(enable_validation=enable_validation)  # 创建事件总线

    # 注册核心事件
    from src.core.events import register_core_events

    register_core_events()
    logger.info("核心事件已注册到 EventRegistry")

    # --- 初始化输入层组件（Layer 1-2-3 数据流） ---
    logger.info("初始化输入层组件（Layer 1-2-3 数据流）...")

    # InputLayer: Layer 1→2（RawData → NormalizedText）
    # 注意: InputProviderManager 目前由插件单独管理，InputLayer 只订阅事件
    input_layer = InputLayer(event_bus)
    await input_layer.setup()
    logger.info("InputLayer 已设置（Layer 1→2）")

    # CanonicalLayer: Layer 2→3（NormalizedText → CanonicalMessage）
    canonical_layer = CanonicalLayer(event_bus, pipeline_manager=pipeline_manager)
    await canonical_layer.setup()
    logger.info("CanonicalLayer 已设置（Layer 2→3）")

    # 创建核心
    core = AmaidesuCore(
        platform=platform_id,
        pipeline_manager=pipeline_manager,  # 传入加载好的管道管理器或None
        context_manager=context_manager,  # 传入创建好的上下文管理器
        event_bus=event_bus,  # 传入事件总线
        avatar=avatar,  # 传入创建好的虚拟形象控制管理器
        llm_service=llm_service,  # 传入创建好的 LLM 服务
    )

    # --- 插件加载 ---
    logger.info("加载插件...")
    plugin_manager = PluginManager(core, config.get("plugins", {}))  # 传入插件全局配置
    plugin_load_dir = os.path.join(_BASE_DIR, "src", "plugins")  # 确保变量名不冲突且清晰
    await plugin_manager.load_plugins(plugin_load_dir)
    logger.info("插件加载完成。")

    # --- 连接核心服务 ---
    await core.connect(rendering_config=rendering_config)  # 连接 WebSocket 并启动 HTTP 服务器，Phase 4: 设置输出层

    # --- 保持运行并处理退出信号 ---
    stop_event = asyncio.Event()
    shutdown_initiated = False

    def signal_handler(signum=None, frame=None):
        nonlocal shutdown_initiated
        if shutdown_initiated:
            logger.warning("已经在关闭中，忽略重复信号")
            return
        shutdown_initiated = True
        logger.info("收到退出信号，开始关闭...")
        stop_event.set()

    # 优先使用系统信号处理
    try:
        loop = asyncio.get_running_loop()
        # 在 Windows 上，SIGINT (Ctrl+C) 通常可用
        # 在 Unix/Linux 上，可以添加 SIGTERM
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except (NotImplementedError, ValueError):
                # Windows 可能不支持 add_signal_handler
                pass
    except Exception:
        pass

    # 为Windows等不支持loop.add_signal_handler的系统设置信号处理
    original_sigint = signal.signal(signal.SIGINT, signal_handler)
    try:
        original_sigterm = signal.signal(signal.SIGTERM, signal_handler)
    except (ValueError, OSError):
        # 某些系统可能不支持SIGTERM
        original_sigterm = None

    logger.info("应用程序正在运行。按 Ctrl+C 退出。")

    try:
        await stop_event.wait()
        logger.info("收到关闭信号，开始执行清理...")
    except KeyboardInterrupt:
        logger.info("检测到 KeyboardInterrupt，开始清理...")
        shutdown_initiated = True

    # 立即移除信号处理器，防止重复触发
    try:
        signal.signal(signal.SIGINT, original_sigint)
        if original_sigterm is not None:
            signal.signal(signal.SIGTERM, original_sigterm)
        logger.debug("信号处理器已恢复")
    except Exception as e:
        logger.debug(f"恢复信号处理器时出错: {e}")

    # --- 执行清理 ---

    # 清理输入层组件（Layer 1-2-3）
    logger.info("正在清理输入层组件...")
    try:
        await canonical_layer.cleanup()
        await input_layer.cleanup()
        logger.info("输入层组件清理完成")
    except Exception as e:
        logger.error(f"清理输入层组件时出错: {e}")

    logger.info("正在卸载插件...")
    try:
        await asyncio.wait_for(plugin_manager.unload_plugins(), timeout=2.0)  # 减少到5秒
        logger.info("插件卸载完成")
    except asyncio.TimeoutError:
        logger.warning("插件卸载超时，强制继续")
    except Exception as e:
        logger.error(f"插件卸载时出错: {e}")

    logger.info("正在关闭核心服务...")
    try:
        # 清理 LLM 服务
        if llm_service:
            await llm_service.cleanup()
            logger.info("LLM 服务已清理")
        await asyncio.wait_for(core.disconnect(), timeout=2.0)  # 减少到3秒
        logger.info("核心服务关闭完成")
    except asyncio.TimeoutError:
        logger.warning("核心服务关闭超时，强制退出")
    except Exception as e:
        logger.error(f"核心服务关闭时出错: {e}")

    logger.info("Amaidesu 应用程序已关闭。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # 在 asyncio.run 之外捕获 KeyboardInterrupt (尽管上面的信号处理应该先触发)
        logger.info("检测到 KeyboardInterrupt，强制退出。")
