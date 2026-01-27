import asyncio
import importlib
import inspect
import os
import sys
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Type

# 避免循环导入，使用 TYPE_CHECKING
if TYPE_CHECKING:
    from .amaidesu_core import AmaidesuCore

from src.utils.logger import get_logger
from src.utils.config import load_component_specific_config, merge_component_configs
from src.openai_client.llm_request import LLMClient
from src.openai_client.modelconfig import ModelConfig
from src.config.config import global_config


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

        # 检查Core是否提供了EventBus（可选功能）
        if core.event_bus is not None:
            self.event_bus = core.event_bus
            self.logger.debug(f"{self.__class__.__name__} 检测到EventBus")
        else:
            self.event_bus = None

        # 检查Core是否提供了LLMClientManager（可选功能）
        if core.llm_client_manager is not None:
            self._llm_client_manager = core.llm_client_manager
            self.logger.debug(f"{self.__class__.__name__} 检测到LLMClientManager")
        else:
            self._llm_client_manager = None
            self.logger.warning(f"{self.__class__.__name__} 未检测到LLMClientManager，LLM功能将不可用")

        # 插件级LLM配置缓存（用于覆盖全局配置）
        self._plugin_llm_cache: Dict[str, LLMClient] = {}

    # 便捷方法（可选使用）
    async def emit_event(self, event_name: str, data: Any) -> None:
        """
        发布事件（如果EventBus可用）

        Args:
            event_name: 事件名称
            data: 事件数据
        """
        if self.event_bus:
            await self.event_bus.emit(event_name, data, self.__class__.__name__)
        else:
            self.logger.debug(f"EventBus不可用，忽略事件: {event_name}")

    def listen_event(self, event_name: str, handler: Callable) -> None:
        """
        订阅事件（如果EventBus可用）

        Args:
            event_name: 要监听的事件名称
            handler: 事件处理器函数
        """
        if self.event_bus:
            self.event_bus.on(event_name, handler)
        else:
            self.logger.debug(f"EventBus不可用，无法监听事件: {event_name}")

    def stop_listening_event(self, event_name: str, handler: Callable) -> None:
        """
        取消订阅事件（如果EventBus可用）

        Args:
            event_name: 事件名称
            handler: 要移除的事件处理器函数
        """
        if self.event_bus:
            self.event_bus.off(event_name, handler)
        else:
            self.logger.debug(f"EventBus不可用，无法取消监听事件: {event_name}")

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

    # --- LLM 客户端获取方法 ---

    def get_llm_client(self, config_type: str = "llm") -> LLMClient:
        """
        获取 LLM 客户端实例

        优先使用插件级覆盖配置（如果配置了 llm_config），否则使用全局配置。

        Args:
            config_type: 配置类型，可选值：
                - "llm": 标准 LLM 配置（默认）
                - "llm_fast": 快速 LLM 配置（低延迟场景）
                - "vlm": 视觉语言模型配置

        Returns:
            LLMClient 实例

        Raises:
            ValueError: 如果 LLMClientManager 未提供或配置无效
        """
        # 检查是否有插件级配置覆盖
        plugin_llm_config = self.plugin_config.get("llm_config", {})

        # 如果插件没有配置覆盖，直接使用全局管理器
        if not plugin_llm_config:
            if self._llm_client_manager is None:
                raise ValueError("LLM 客户端管理器未初始化，且插件未提供自己的 LLM 配置！")
            return self._llm_client_manager.get_client(config_type)

        # 插件有配置覆盖，检查缓存
        cache_key = f"{config_type}_plugin_override"
        if cache_key in self._plugin_llm_cache:
            self.logger.debug(f"从缓存返回插件覆盖的 {config_type} 客户端")
            return self._plugin_llm_cache[cache_key]

        # 创建插件专用的客户端（使用覆盖配置）
        try:
            # 验证 config_type
            if config_type not in ["llm", "llm_fast", "vlm"]:
                raise ValueError(f"无效的 config_type: {config_type}，必须是 'llm', 'llm_fast' 或 'vlm'")

            # 从全局配置获取基础配置
            if config_type == "llm":
                base_config = global_config.llm
            elif config_type == "llm_fast":
                base_config = global_config.llm_fast
            else:  # vlm
                base_config = global_config.vlm

            # 合并配置（插件配置优先）
            merged_config = self._merge_llm_config(base_config, plugin_llm_config)

            # 创建 ModelConfig
            model_config = ModelConfig(
                model_name=merged_config["model"],
                api_key=merged_config["api_key"],
                base_url=merged_config["base_url"],
                max_tokens=merged_config["max_tokens"],
                temperature=merged_config["temperature"],
            )

            # 创建 LLMClient
            client = LLMClient(model_config)

            # 缓存客户端
            self._plugin_llm_cache[cache_key] = client

            self.logger.info(
                f"已创建并缓存插件覆盖的 {config_type} 客户端 "
                f"(model: {model_config.model_name}, base_url: {model_config.base_url})"
            )

            return client

        except Exception as e:
            error_msg = f"创建插件覆盖的 {config_type} 客户端失败: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from e

    def _merge_llm_config(self, base_config, plugin_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        合并 LLM 配置（字段级合并）

        Args:
            base_config: 全局配置对象（LLMConfig/LLMConfigFast/VLMConfig）
            plugin_config: 插件配置字典

        Returns:
            合并后的配置字典
        """
        # 从 base_config 提取字段
        merged = {
            "model": base_config.model,
            "api_key": base_config.api_key,
            "base_url": base_config.base_url,
            "max_tokens": base_config.max_tokens,
            "temperature": base_config.temperature,
        }

        # 插件配置覆盖（仅覆盖非 None 和非空字符串的值）
        for key in ["model", "api_key", "base_url", "max_tokens", "temperature"]:
            if key in plugin_config:
                value = plugin_config[key]
                # 特殊处理 api_key：空字符串视为未配置
                if key == "api_key" and value == "":
                    continue
                # None 值不覆盖
                if value is not None:
                    merged[key] = value
                    self.logger.debug(f"插件配置覆盖 {key}: {value}")

        # 验证必需字段
        if not merged["api_key"]:
            raise ValueError("LLM API Key 未配置！请在全局配置或插件配置中设置 api_key。")

        return merged

    def get_fast_llm_client(self) -> LLMClient:
        """
        获取快速 LLM 客户端（低延迟场景）

        Returns:
            LLMClient 实例
        """
        return self.get_llm_client(config_type="llm_fast")

    def get_vlm_client(self) -> LLMClient:
        """
        获取视觉语言模型客户端

        Returns:
            LLMClient 实例
        """
        return self.get_llm_client(config_type="vlm")

    def create_custom_llm_client(self, model_config: ModelConfig) -> LLMClient:
        """
        创建自定义配置的 LLM 客户端（不使用缓存）

        Args:
            model_config: 自定义模型配置

        Returns:
            LLMClient 实例
        """
        self.logger.info(f"创建自定义 LLM 客户端 (model: {model_config.model_name}, base_url: {model_config.base_url})")
        return LLMClient(model_config)

    def _clear_llm_client_cache(self, config_type: Optional[str] = None) -> None:
        """
        清除 LLM 客户端缓存

        Args:
            config_type: 要清除的配置类型，如果为 None 则清除所有缓存
        """
        if config_type is None:
            self._plugin_llm_cache.clear()
            self.logger.debug("已清除所有插件级 LLM 客户端缓存")
        else:
            cache_key = f"{config_type}_plugin_override"
            if cache_key in self._plugin_llm_cache:
                del self._plugin_llm_cache[cache_key]
                self.logger.debug(f"已清除插件级 {config_type} 客户端缓存")
            else:
                self.logger.warning(f"缓存中不存在插件级 {config_type} 客户端")


class PluginManager:
    """负责加载、管理和卸载插件。

    支持两种插件类型：
    - BasePlugin（旧系统）：继承AmaidesuCore，通过 self.core 访问核心
    - Plugin（新系统）：实现Plugin协议，通过 event_bus 和 config 依赖注入

    向后兼容：两种插件类型都能正常工作。
    """

    def __init__(self, core: "AmaidesuCore", global_plugin_config: Dict[str, Any]):
        self.core = core
        self.global_plugin_config = global_plugin_config
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
                    plugin_type = "unknown"

                    if hasattr(module, "plugin_entrypoint"):
                        entrypoint = module.plugin_entrypoint
                        self.logger.debug(
                            f"在模块 '{module_import_path}' 中找到入口点 'plugin_entrypoint' 指向: {entrypoint}"
                        )

                        if inspect.isclass(entrypoint):
                            if issubclass(entrypoint, BasePlugin):
                                plugin_class = entrypoint
                                plugin_type = "base_plugin"
                                self.logger.debug(
                                    f"入口点验证成功 (通过继承 BasePlugin)，插件类为: {plugin_class.__name__}"
                                )
                            else:
                                if hasattr(entrypoint, "setup"):
                                    try:
                                        sig = inspect.signature(entrypoint.setup)
                                        params = list(sig.parameters.keys())
                                        if "event_bus" in params and "config" in params:
                                            plugin_class = entrypoint
                                            plugin_type = "new_plugin"
                                            self.logger.debug(
                                                f"入口点验证成功 (通过实现新 Plugin 接口)，插件类为: {plugin_class.__name__}"
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
                        main_provided_config = self.global_plugin_config.get(plugin_name, {}).copy()
                        self.logger.debug(f"从主配置为插件 '{plugin_name}' 获取的配置: {main_provided_config}")

                        plugin_own_config_data = load_component_specific_config(item_path, plugin_name, "插件")

                        final_plugin_config = merge_component_configs(
                            plugin_own_config_data, main_provided_config, plugin_name, "插件"
                        )

                        self.logger.debug(f"准备实例化插件: {plugin_class.__name__} (类型: {plugin_type})")

                        if plugin_type == "base_plugin":
                            plugin_instance = plugin_class(self.core, final_plugin_config)

                            plugin_instance.plugin_dir = item_path
                            self.logger.debug(f"已为插件 '{plugin_class.__name__}' 设置 'plugin_dir' 属性: {item_path}")

                            self.logger.debug(f"插件 '{plugin_class.__name__}' 实例化完成，准备调用 setup()")
                            await plugin_instance.setup()
                        elif plugin_type == "new_plugin":
                            plugin_instance = plugin_class(final_plugin_config)

                            self.logger.debug(
                                f"插件 '{plugin_class.__name__}' 实例化完成，准备调用 setup(event_bus, config)"
                            )

                            providers = await plugin_instance.setup(self.core.event_bus, final_plugin_config)
                            self.logger.info(f"插件 '{plugin_class.__name__}' 返回了 {len(providers)} 个 Provider")

                        self.loaded_plugins[plugin_name] = plugin_instance
                        self.logger.info(
                            f"成功加载并设置插件: {plugin_class.__name__} (来自 {plugin_name}/plugin.py, 类型: {plugin_type})"
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
            for i, task in enumerate(unload_tasks):
                plugin_name = list(self.loaded_plugins.keys())[i]
                if isinstance(results[i], Exception):
                    self.logger.error(f"清理插件 '{plugin_name}' 时出错: {results[i]}", exc_info=results[i])

        self.loaded_plugins.clear()
        self.logger.info("所有插件已卸载。")
