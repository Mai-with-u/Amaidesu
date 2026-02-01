"""
ConfigService - 统一的配置管理服务

职责:
- 提供统一的配置加载接口
- 集中管理所有配置加载逻辑
- 支持插件、管道、Provider 等组件的配置获取
- 配置合并策略：主配置覆盖组件配置
"""

import os
from typing import Dict, Any, Optional

from src.utils.logger import get_logger
from src.utils.config import (
    load_component_specific_config,
    merge_component_configs,
    initialize_configurations,
)


class ConfigService:
    """
    统一的配置管理服务

    提供一个集中的配置管理入口，所有组件的配置加载都通过此服务进行。

    使用示例:
        # 初始化配置服务
        config_service = ConfigService(base_dir="/path/to/project")
        config, main_copied, plugin_copied, pipeline_copied = await config_service.initialize()

        # 获取主配置
        general_config = config_service.get_section("general")

        # 获取插件配置（已合并全局配置）
        plugin_config = config_service.get_plugin_config("tts", "/path/to/src/plugins/tts")

        # 获取管道配置（已合并全局配置）
        pipeline_config = config_service.get_pipeline_config("throttle", "/path/to/src/pipelines/throttle")
    """

    def __init__(self, base_dir: str):
        """
        初始化配置服务

        Args:
            base_dir: 项目根目录
        """
        self.base_dir = base_dir
        self._main_config: Dict[str, Any] = {}
        self._main_config_copied = False
        self._plugin_configs_copied = False
        self._pipeline_configs_copied = False
        self._initialized = False
        self.logger = get_logger("ConfigService")
        self.logger.debug("ConfigService 初始化完成")

    @property
    def main_config(self) -> Dict[str, Any]:
        """
        获取主配置（只读属性）

        Returns:
            主配置字典
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}
        return self._main_config

    def initialize(self) -> tuple[Dict[str, Any], bool, bool, bool]:
        """
        初始化所有配置文件

        执行以下步骤:
        1. 检查并设置主配置文件
        2. 加载主配置
        3. 检查并设置插件配置文件
        4. 检查并设置管道配置文件

        Returns:
            (main_config, main_config_copied, plugin_configs_copied, pipeline_configs_copied)
        """
        if self._initialized:
            self.logger.warning("ConfigService 已经初始化，跳过重复初始化")
            return (
                self._main_config,
                self._main_config_copied,
                self._plugin_configs_copied,
                self._pipeline_configs_copied,
            )

        self.logger.info("开始初始化配置服务...")

        (
            self._main_config,
            self._main_config_copied,
            self._plugin_configs_copied,
            self._pipeline_configs_copied,
        ) = initialize_configurations(
            base_dir=self.base_dir,
            main_cfg_name="config.toml",
            main_template_name="config-template.toml",
            plugin_dir_name="src/plugins",
            pipeline_dir_name="src/pipelines",
        )

        self._initialized = True
        self.logger.info("配置服务初始化完成")

        return (
            self._main_config,
            self._main_config_copied,
            self._plugin_configs_copied,
            self._pipeline_configs_copied,
        )

    def get_section(self, section: str, default: Any = None) -> Dict[str, Any]:
        """
        获取配置节

        Args:
            section: 配置节名称（如 "general", "plugins", "pipelines", "rendering"）
            default: 如果配置节不存在，返回的默认值

        Returns:
            配置节字典

        Example:
            general_config = config_service.get_section("general")
            plugins_config = config_service.get_section("plugins")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {} if default is None else default

        result = self._main_config.get(section, default)
        if result is None:
            self.logger.warning(f"配置节 '{section}' 不存在")
            return {}

        return result

    def get(self, key: str, default: Any = None, section: str = None) -> Any:
        """
        获取配置项

        Args:
            key: 配置项键
            default: 默认值
            section: 配置节名称（可选，如果提供则从该节中查找）

        Returns:
            配置项值

        Example:
            platform_id = config_service.get("platform_id", section="general")
            api_key = config_service.get("api_key", "default_key")
        """
        if section:
            section_config = self.get_section(section)
            return section_config.get(key, default)
        else:
            return self._main_config.get(key, default)

    def get_plugin_config(
        self,
        plugin_name: str,
        plugin_dir_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取合并后的插件配置

        合并顺序（后者覆盖前者）:
        1. 插件自身目录下的 config.toml
        2. 主配置文件中 [plugins.plugin_name] 的全局配置

        Args:
            plugin_name: 插件名称
            plugin_dir_path: 插件目录的绝对路径（可选，如果未提供则自动查找）

        Returns:
            合并后的插件配置

        Example:
            # 使用自动查找插件目录
            tts_config = config_service.get_plugin_config("tts")

            # 使用指定的插件目录路径
            tts_config = config_service.get_plugin_config("tts", "/path/to/src/plugins/tts")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        # 自动查找插件目录
        if plugin_dir_path is None:
            plugin_dir_path = os.path.join(self.base_dir, "src", "plugins", plugin_name)
            if not os.path.isdir(plugin_dir_path):
                self.logger.warning(f"插件目录不存在: {plugin_dir_path}")
                return {}

        # 加载插件自身配置
        plugin_own_config = load_component_specific_config(plugin_dir_path, plugin_name, "插件")

        # 获取全局配置覆盖
        main_provided_config = self.get_section("plugins", {}).get(plugin_name, {}).copy()

        # 合并配置（全局配置覆盖插件配置）
        final_plugin_config = merge_component_configs(plugin_own_config, main_provided_config, plugin_name, "插件")

        return final_plugin_config

    def get_pipeline_config(
        self,
        pipeline_name: str,
        pipeline_dir_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取合并后的管道配置

        合并顺序（后者覆盖前者）:
        1. 管道自身目录下的 config.toml
        2. 主配置文件中 [pipelines.pipeline_name] 的全局配置

        Args:
            pipeline_name: 管道名称（如 "throttle", "message_logger"）
            pipeline_dir_path: 管道目录的绝对路径（可选，如果未提供则自动查找）

        Returns:
            合并后的管道配置

        Example:
            # 使用自动查找管道目录
            throttle_config = config_service.get_pipeline_config("throttle")

            # 使用指定的管道目录路径
            throttle_config = config_service.get_pipeline_config("throttle", "/path/to/src/pipelines/throttle")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        # 自动查找管道目录
        if pipeline_dir_path is None:
            pipeline_dir_path = os.path.join(self.base_dir, "src", "pipelines", pipeline_name)
            if not os.path.isdir(pipeline_dir_path):
                self.logger.warning(f"管道目录不存在: {pipeline_dir_path}")
                return {}

        # 加载管道自身配置
        pipeline_own_config = load_component_specific_config(pipeline_dir_path, pipeline_name, "管道")

        # 获取全局配置覆盖
        main_provided_config = self.get_section("pipelines", {}).get(pipeline_name, {}).copy()

        # 合并配置（全局配置覆盖管道配置）
        final_pipeline_config = merge_component_configs(
            pipeline_own_config, main_provided_config, pipeline_name, "管道"
        )

        return final_pipeline_config

    def get_provider_config(
        self,
        provider_name: str,
        provider_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取 Provider 配置

        Provider 配置通常位于 [rendering.outputs] 节下。

        Args:
            provider_name: Provider 名称（如 "tts", "subtitle", "sticker"）
            provider_type: Provider 类型（可选，如果未提供则使用 provider_name）

        Returns:
            Provider 配置

        Example:
            tts_config = config_service.get_provider_config("tts")
            subtitle_config = config_service.get_provider_config("subtitle")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        provider_type = provider_type or provider_name

        # 从 rendering.outputs 中获取配置
        rendering_config = self.get_section("rendering", {})
        outputs_config = rendering_config.get("outputs", {})
        provider_config = outputs_config.get(provider_name, {}).copy()

        # 如果没有配置，则返回空配置
        if not provider_config:
            self.logger.debug(f"Provider '{provider_name}' 没有配置")
            return {}

        # 添加 type 字段（如果不存在）
        if "type" not in provider_config:
            provider_config["type"] = provider_type

        return provider_config

    def get_all_plugin_configs(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有插件的配置

        Returns:
            字典，键为插件名称，值为该插件的配置

        Example:
            all_plugins = config_service.get_all_plugin_configs()
            for plugin_name, plugin_config in all_plugins.items():
                print(f"{plugin_name}: {plugin_config}")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        all_plugin_configs = {}

        # 获取插件目录
        plugins_dir = os.path.join(self.base_dir, "src", "plugins")
        if not os.path.isdir(plugins_dir):
            self.logger.warning(f"插件目录不存在: {plugins_dir}")
            return all_plugin_configs

        # 遍历所有插件目录
        for item in os.listdir(plugins_dir):
            item_path = os.path.join(plugins_dir, item)
            if os.path.isdir(item_path) and not item.startswith("__"):
                plugin_config = self.get_plugin_config(item, item_path)
                all_plugin_configs[item] = plugin_config

        return all_plugin_configs

    def get_all_pipeline_configs(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有管道的配置

        Returns:
            字典，键为管道名称，值为该管道的配置

        Example:
            all_pipelines = config_service.get_all_pipeline_configs()
            for pipeline_name, pipeline_config in all_pipelines.items():
                print(f"{pipeline_name}: {pipeline_config}")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        all_pipeline_configs = {}

        # 获取管道目录
        pipelines_dir = os.path.join(self.base_dir, "src", "pipelines")
        if not os.path.isdir(pipelines_dir):
            self.logger.warning(f"管道目录不存在: {pipelines_dir}")
            return all_pipeline_configs

        # 遍历所有管道目录
        for item in os.listdir(pipelines_dir):
            item_path = os.path.join(pipelines_dir, item)
            if os.path.isdir(item_path) and not item.startswith("__"):
                pipeline_config = self.get_pipeline_config(item, item_path)
                all_pipeline_configs[item] = pipeline_config

        return all_pipeline_configs

    def is_plugin_enabled(self, plugin_name: str) -> bool:
        """
        检查插件是否启用

        Args:
            plugin_name: 插件名称

        Returns:
            是否启用
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回 False")
            return False

        plugins_config = self.get_section("plugins", {})

        # 1. 检查 enabled 列表（优先级最高）
        enabled_list = plugins_config.get("enabled", [])
        if plugin_name in enabled_list:
            return True

        # 2. 如果有 enabled 列表但插件不在其中，则禁用
        if enabled_list:
            return False

        # 3. 向后兼容：检查旧的 enable_xxx 格式
        old_format_key = f"enable_{plugin_name}"
        if old_format_key in plugins_config:
            return plugins_config[old_format_key]

        # 4. 默认行为：如果都没有配置，默认禁用（更安全）
        return False

    def is_pipeline_enabled(self, pipeline_name: str) -> bool:
        """
        检查管道是否启用

        管道启用的条件：在 [pipelines.pipeline_name] 配置节中定义了 `priority` 键。

        Args:
            pipeline_name: 管道名称

        Returns:
            是否启用
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回 False")
            return False

        pipelines_config = self.get_section("pipelines", {})
        pipeline_config = pipelines_config.get(pipeline_name, {})

        # 检查是否定义了 priority
        return "priority" in pipeline_config
