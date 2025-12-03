import asyncio
import importlib
import inspect
import os
import sys
from typing import TYPE_CHECKING, Dict, Any, Optional, Type

# 避免循环导入，使用 TYPE_CHECKING
if TYPE_CHECKING:
    from .amaidesu_core import AmaidesuCore

from src.utils.logger import get_logger
from src.utils.config import load_component_specific_config, merge_component_configs


# --- 插件基类 (可选但推荐) ---
class BasePlugin:
    """所有插件的基础类，定义插件的基本接口。"""

    def __init__(self, core: "AmaidesuCore", plugin_config: Dict[str, Any]):
        """
        初始化插件。

        Args:
            core: AmaidesuCore 的实例，用于插件与核心交互。
            plugin_config: 该插件在 config.toml 中的配置。
        """
        self.core = core
        self.plugin_config = plugin_config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

    async def setup(self):
        """设置插件，例如注册处理器。"""
        self.logger.debug(f"设置插件: {self.__class__.__name__}")
        # 子类应在此处实现具体的设置逻辑，例如：
        # await self.core.register_websocket_handler("text", self.handle_text_message)
        # await self.core.register_http_handler("http_callback", self.handle_http_callback)
        pass

    async def cleanup(self):
        """清理插件资源。"""
        self.logger.debug(f"清理插件: {self.__class__.__name__}")
        # 子类应在此处实现清理逻辑
        pass


class PluginManager:
    """负责加载、管理和卸载插件。"""

    def __init__(self, core: "AmaidesuCore", global_plugin_config: Dict[str, Any]):
        """
        初始化插件管理器。

        Args:
            core: AmaidesuCore 实例。
            global_plugin_config: config.toml 中 [plugins] 部分的配置。
        """
        self.core = core
        self.global_plugin_config = global_plugin_config
        self.loaded_plugins: Dict[str, BasePlugin] = {}
        # 初始化 PluginManager 自己的 logger
        self.logger = get_logger("PluginManager")
        self.logger.debug("PluginManager 初始化完成")

    async def load_plugins(self, plugin_dir: str = "src/plugins"):
        """扫描指定目录下的子目录，加载所有有效的插件。"""
        # 使用 self.logger 而不是全局 logger
        self.logger.info(f"开始从目录加载插件: {plugin_dir}")
        plugin_dir_abs = os.path.abspath(plugin_dir)

        if not os.path.isdir(plugin_dir_abs):
            self.logger.warning(f"插件目录不存在: {plugin_dir_abs}，跳过插件加载。")
            return

        # 将 src 目录（插件目录的父目录）添加到 sys.path，以便导入插件包
        src_dir = os.path.dirname(plugin_dir_abs)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
            self.logger.debug(f"已将目录添加到 sys.path: {src_dir}")

        for item in os.listdir(plugin_dir_abs):
            item_path = os.path.join(plugin_dir_abs, item)
            if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, "__init__.py")):
                plugin_name = item
                plugin_module_file = os.path.join(item_path, "plugin.py")
                self.logger.debug(f"检测到潜在插件目录: {plugin_name}")

                if not os.path.exists(plugin_module_file):
                    self.logger.warning(f"在插件目录 '{plugin_name}' 中未找到主文件 'plugin.py'，跳过。")
                    continue

                # --- 检查插件是否在配置中启用 ---
                is_enabled_key = f"enable_{plugin_name}"
                is_enabled = self.global_plugin_config.get(is_enabled_key, True)
                self.logger.debug(f"检查插件 '{plugin_name}' 是否启用: Key='{is_enabled_key}', Enabled={is_enabled}")

                if not is_enabled:
                    self.logger.info(f"插件 '{plugin_name}' 在配置中被禁用，跳过加载。")
                    continue

                # --- 加载插件模块 ---
                module = None  # 初始化为 None
                try:
                    module_import_path = f"plugins.{plugin_name}.plugin"
                    self.logger.debug(f"尝试导入模块: {module_import_path}")
                    module = importlib.import_module(module_import_path)
                    self.logger.debug(f"成功导入插件模块: {module_import_path}")

                    # --- 查找并实例化插件类 (使用入口点) ---
                    plugin_class: Optional[Type[BasePlugin]] = None
                    entrypoint = None  # 初始化为 None
                    if hasattr(module, "plugin_entrypoint"):
                        entrypoint = module.plugin_entrypoint
                        self.logger.debug(
                            f"在模块 '{module_import_path}' 中找到入口点 'plugin_entrypoint' 指向: {entrypoint}"
                        )
                        # 检查 entrypoint 是否是类，并且是 BasePlugin 的子类
                        if inspect.isclass(entrypoint) and issubclass(entrypoint, BasePlugin):
                            plugin_class = entrypoint
                            self.logger.debug(
                                f"入口点验证成功 (通过继承 BasePlugin)，插件类为: {plugin_class.__name__}"
                            )
                        else:
                            self.logger.warning(
                                f"模块 '{module_import_path}' 中的 'plugin_entrypoint' ({entrypoint}) 不是 BasePlugin 的有效子类。"
                            )
                    else:
                        self.logger.warning(f"在模块 '{module_import_path}' 中未找到入口点 'plugin_entrypoint'。")

                    if plugin_class:
                        # 1. 获取主 config.toml 中 [plugins.plugin_name] 下的配置
                        main_provided_config = self.global_plugin_config.get(plugin_name, {}).copy()
                        self.logger.debug(f"从主配置为插件 '{plugin_name}' 获取的配置: {main_provided_config}")

                        # 2. 加载插件自身目录下的 config.toml (如果存在)
                        plugin_own_config_data = load_component_specific_config(item_path, plugin_name, "插件")

                        # 3. 合并配置：主配置覆盖插件独立配置
                        final_plugin_config = merge_component_configs(
                            plugin_own_config_data, main_provided_config, plugin_name, "插件"
                        )

                        self.logger.debug(f"准备实例化插件: {plugin_class.__name__}")
                        # 实例化插件
                        plugin_instance = plugin_class(self.core, final_plugin_config)
                        
                        # 手动将插件目录路径设置到实例上，实现向后兼容
                        plugin_instance.plugin_dir = item_path
                        self.logger.debug(f"已为插件 '{plugin_class.__name__}' 设置 'plugin_dir' 属性: {item_path}")

                        self.logger.debug(f"插件 '{plugin_class.__name__}' 实例化完成，准备调用 setup()")
                        await plugin_instance.setup()
                        self.loaded_plugins[plugin_name] = plugin_instance
                        self.logger.info(f"成功加载并设置插件: {plugin_class.__name__} (来自 {plugin_name}/plugin.py)")
                    else:
                        # 如果没有找到有效的 plugin_class (无论是没找到入口点还是入口点无效)
                        self.logger.warning(f"未能为模块 '{module_import_path}' 找到并验证有效的插件类。")

                except ImportError as e:
                    self.logger.error(
                        f"导入插件模块 '{module_import_path if module else plugin_name}' 失败: {e}", exc_info=True
                    )
                except Exception as e:
                    self.logger.exception(f"加载或设置插件 '{plugin_name}' 时发生错误: {e}", exc_info=True)
            # else: # 可以选择性地记录非插件目录的项
            #     if item != "__pycache__" and not item.startswith("."):
            #         logger.debug(f"跳过非插件目录项: {item}")

        # 注意：不再需要在每次循环后清理 sys.path，因为我们添加的是 src 目录

        self.logger.info(
            f"插件加载完成，共加载 {len(self.loaded_plugins)} 个插件:{','.join(self.loaded_plugins.keys())}"
        )

    async def unload_plugins(self):
        """卸载所有已加载的插件，调用它们的 cleanup 方法。"""
        self.logger.info("开始卸载所有插件...")
        unload_tasks = []
        for plugin_name, plugin_instance in self.loaded_plugins.items():
            self.logger.debug(f"准备清理插件: {plugin_name}")
            unload_tasks.append(asyncio.create_task(plugin_instance.cleanup()))

        if unload_tasks:
            results = await asyncio.gather(*unload_tasks, return_exceptions=True)
            for i, task in enumerate(unload_tasks):
                plugin_name = list(self.loaded_plugins.keys())[i]
                if isinstance(results[i], Exception):
                    self.logger.error(f"清理插件 '{plugin_name}' 时出错: {results[i]}", exc_info=results[i])

        self.loaded_plugins.clear()
        self.logger.info("所有插件已卸载。")
