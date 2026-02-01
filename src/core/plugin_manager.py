import asyncio
import importlib
import inspect
import os
import sys
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

# 避免循环导入，使用 TYPE_CHECKING
if TYPE_CHECKING:
    from .amaidesu_core import AmaidesuCore

from src.utils.logger import get_logger
from src.services.config_service import ConfigService
from src.utils.config import load_component_specific_config, merge_component_configs


class PluginManager:
    """
    负责加载、管理和卸载插件

    插件系统：
    - 实现 Plugin 协议（不继承任何基类）
    - 通过 event_bus 和 config 进行依赖注入
    - 返回 Provider 列表（InputProvider、OutputProvider 等）
    - 参考：src/core/plugin.py
    """

    def __init__(
        self, core: "AmaidesuCore", global_plugin_config: Dict[str, Any], config_service: Optional[ConfigService] = None
    ):
        self.core = core
        self.global_plugin_config = global_plugin_config
        self.config_service = config_service
        self.loaded_plugins: Dict[str, Any] = {}
        self.logger = get_logger("PluginManager")
        self.logger.debug("PluginManager 初始化完成")

    def _is_plugin_enabled(self, plugin_name: str) -> bool:
        """
        检查插件是否启用（支持新格式和旧格式的向后兼容）

        Args:
            plugin_name: 插件名称

        Returns:
            bool: 是否启用
        """
        # 如果有 ConfigService，使用它
        if self.config_service:
            return self.config_service.is_plugin_enabled(plugin_name)

        # 否则使用旧的逻辑（向后兼容）
        # 1. 检查 enabled 列表（优先级最高）
        enabled_list = self.global_plugin_config.get("enabled", [])
        if plugin_name in enabled_list:
            return True

        # 2. 如果有enabled列表但插件不在其中，则禁用
        if enabled_list:
            return False

        # 3. 向后兼容：检查旧的 enable_xxx 格式
        old_format_key = f"enable_{plugin_name}"
        if old_format_key in self.global_plugin_config:
            return self.global_plugin_config[old_format_key]

        # 4. 默认行为：如果都没有配置，默认禁用（更安全）
        return False

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
                is_enabled = self._is_plugin_enabled(plugin_name)
                self.logger.debug(f"检查插件 '{plugin_name}' 是否启用: Enabled={is_enabled}")

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

                    plugin_class = None
                    entrypoint = None

                    if hasattr(module, "plugin_entrypoint"):
                        entrypoint = module.plugin_entrypoint
                        self.logger.debug(
                            f"在模块 '{module_import_path}' 中找到入口点 'plugin_entrypoint' 指向: {entrypoint}"
                        )

                        if inspect.isclass(entrypoint):
                            if hasattr(entrypoint, "setup"):
                                try:
                                    sig = inspect.signature(entrypoint.setup)
                                    params = list(sig.parameters.keys())
                                    if "event_bus" in params and "config" in params:
                                        plugin_class = entrypoint
                                        self.logger.debug(
                                            f"入口点验证成功 (实现 Plugin 接口)，插件类为: {plugin_class.__name__}"
                                        )
                                    else:
                                        self.logger.warning(
                                            f"模块 '{module_import_path}' 中的插件类 setup() 方法签名不符合要求。"
                                            f"应该包含 event_bus 和 config 参数，当前参数: {params}"
                                        )
                                except Exception as e:
                                    self.logger.warning(
                                        f"检查 '{module_import_path}' 中插件类的 setup() 方法时出错: {e}"
                                    )
                            else:
                                self.logger.warning(f"模块 '{module_import_path}' 中的插件类没有 setup() 方法。")
                        else:
                            self.logger.warning(
                                f"模块 '{module_import_path}' 中的 'plugin_entrypoint' ({entrypoint}) 不是有效的类。"
                            )
                    else:
                        self.logger.warning(f"在模块 '{module_import_path}' 中未找到入口点 'plugin_entrypoint'。")

                    if plugin_class:
                        # 使用 ConfigService 或旧的配置加载方式
                        if self.config_service:
                            final_plugin_config = self.config_service.get_plugin_config(plugin_name, item_path)
                        else:
                            main_provided_config = self.global_plugin_config.get(plugin_name, {}).copy()
                            self.logger.debug(f"从主配置为插件 '{plugin_name}' 获取的配置: {main_provided_config}")

                            plugin_own_config_data = load_component_specific_config(item_path, plugin_name, "插件")

                            final_plugin_config = merge_component_configs(
                                plugin_own_config_data, main_provided_config, plugin_name, "插件"
                            )

                        self.logger.debug(f"准备实例化插件: {plugin_class.__name__}")

                        plugin_instance = plugin_class(final_plugin_config)

                        self.logger.debug(
                            f"插件 '{plugin_class.__name__}' 实例化完成，准备调用 setup(event_bus, config)"
                        )

                        providers = await plugin_instance.setup(self.core.event_bus, final_plugin_config)
                        self.logger.info(f"插件 '{plugin_class.__name__}' 返回了 {len(providers)} 个 Provider")

                        self.loaded_plugins[plugin_name] = plugin_instance
                        self.logger.info(
                            f"成功加载并设置插件: {plugin_class.__name__} (来自 {plugin_name}/plugin.py)"
                        )
                    else:
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
            self.logger.debug(f"准备清理插件: {plugin_name} ({plugin_instance.__class__.__name__})")
            unload_tasks.append(asyncio.create_task(plugin_instance.cleanup()))

        if unload_tasks:
            results = await asyncio.gather(*unload_tasks, return_exceptions=True)
            for i, _task in enumerate(unload_tasks):
                plugin_name = list(self.loaded_plugins.keys())[i]
                if isinstance(results[i], Exception):
                    self.logger.error(f"清理插件 '{plugin_name}' 时出错: {results[i]}", exc_info=results[i])

        self.loaded_plugins.clear()
        self.logger.info("所有插件已卸载。")
