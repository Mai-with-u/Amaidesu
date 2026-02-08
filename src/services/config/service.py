"""
ConfigService - 统一的配置管理服务

职责:
- 提供统一的配置加载接口
- 集中管理所有配置加载逻辑
- 支持 Provider、Pipeline 等组件的配置获取
- 配置合并策略：主配置覆盖组件配置
- 三级配置合并：Schema默认值 → 主配置覆盖 → Provider本地配置
- 自动从Schema生成缺失的config.toml文件
"""

import os
from typing import Dict, Any, Optional, Literal
from pathlib import Path

from src.core.utils.logger import get_logger
from src.core.utils.config import (
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

        # 获取Provider配置（推荐使用新方法）
        input_config = config_service.get_provider_config_with_defaults("console", "input")

        # 获取管道配置
        pipeline_config = config_service.get_pipeline_config("throttle")
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
            plugin_dir_name="src/domains",  # 更新：插件系统已移除，使用domains目录
            pipeline_dir_name="src/domains/input/pipelines",  # 更新：Pipeline已移入Input Domain
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
            throttle_config = config_service.get_pipeline_config("throttle", "/path/to/src/domains/input/pipelines/throttle")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        # 自动查找管道目录
        if pipeline_dir_path is None:
            pipeline_dir_path = os.path.join(self.base_dir, "src", "domains", "input", "pipelines", pipeline_name)
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

    def get_all_provider_configs(self, provider_type: str = "input") -> Dict[str, Dict[str, Any]]:
        """
        获取所有Provider的配置

        Args:
            provider_type: Provider类型（"input" 或 "rendering"，默认 "input"）

        Returns:
            字典，键为Provider名称，值为该Provider的配置

        Example:
            # 获取所有输入Provider配置
            all_inputs = config_service.get_all_provider_configs("input")
            for provider_name, provider_config in all_inputs.items():
                print(f"{provider_name}: {provider_config}")

            # 获取所有输出Provider配置
            all_outputs = config_service.get_all_provider_configs("rendering")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        all_provider_configs = {}

        # 根据Provider类型选择配置节
        if provider_type == "input":
            config_section = self.get_section("providers", {}).get("input", {})
            providers_dict = config_section.get("inputs", {})
        elif provider_type == "rendering" or provider_type == "output":
            config_section = self.get_section("providers.output", {})
            providers_dict = config_section.get("outputs", {})
        else:
            self.logger.warning(f"未知的Provider类型: {provider_type}")
            return all_provider_configs

        # 收集所有Provider配置
        for provider_name, provider_config in providers_dict.items():
            all_provider_configs[provider_name] = provider_config.copy()

        return all_provider_configs

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
        pipelines_dir = os.path.join(self.base_dir, "src", "domains", "input", "pipelines")
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

    def is_provider_enabled(self, provider_name: str, provider_type: str = "input") -> bool:
        """
        检查Provider是否启用

        Args:
            provider_name: Provider名称
            provider_type: Provider类型（"input" 或 "rendering"，默认 "input"）

        Returns:
            是否启用
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回 False")
            return False

        # 根据Provider类型选择配置节
        if provider_type == "input":
            config_section = self.get_section("providers.input", {})
            list_key = "enabled_inputs"
        elif provider_type == "rendering" or provider_type == "output":
            config_section = self.get_section("providers.output", {})
            list_key = "enabled_outputs"
        else:
            self.logger.warning(f"未知的Provider类型: {provider_type}")
            return False

        # 检查是否在启用列表中
        enabled_list = config_section.get(list_key, [])
        return provider_name in enabled_list

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

    # ========== 三级配置合并方法 ==========

    def get_provider_config_with_defaults(
        self,
        provider_name: str,
        provider_layer: Literal["input", "output", "decision"],
        schema_class: Optional[type] = None,
    ) -> Dict[str, Any]:
        """
        获取Provider配置（三级合并）

        新的配置合并顺序（后者覆盖前者）:
        1. Schema默认值（从Pydantic Schema类获取）
        2. 主配置覆盖 (config.toml中的[providers.*.overrides.{provider_name}])
        3. Provider本地配置 (src/layers/*/providers/{name}/config.toml)

        注意: 已移除config-defaults.toml加载逻辑，所有默认值由Schema定义。

        Args:
            provider_name: Provider名称
            provider_layer: Provider层级（input/output/decision）
            schema_class: Pydantic Schema类（可选，用于类型验证和默认值）

        Returns:
            合并后的配置字典

        Raises:
            ValidationError: 如果schema_class提供且配置验证失败

        Example:
            # 获取输入Provider配置（带Schema验证）
            from src.core.config.schemas import ConsoleInputProviderConfig
            config = config_service.get_provider_config_with_defaults(
                "console_input", "input", ConsoleInputProviderConfig
            )

            # 获取输出Provider配置（无Schema验证）
            config = config_service.get_provider_config_with_defaults(
                "tts", "output"
            )
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        # 构建Provider目录路径
        if provider_layer == "input":
            provider_dir = os.path.join(self.base_dir, "src", "domains", "input", "providers", provider_name)
            config_section = "providers.input"
        elif provider_layer == "output":
            provider_dir = os.path.join(self.base_dir, "src", "domains", "output", "providers", provider_name)
            config_section = "providers.output"
        elif provider_layer == "decision":
            provider_dir = os.path.join(self.base_dir, "src", "domains", "decision", "providers", provider_name)
            config_section = "providers.decision"
        else:
            self.logger.error(f"未知的Provider层级: {provider_layer}")
            return {}

        # 步骤1: 获取Schema默认值（第一优先级最低，是基础层）
        result = self._get_schema_defaults(schema_class, provider_name)
        if result:
            self.logger.debug(f"从Schema获取默认值: {provider_name}")

        # 步骤2: 应用主配置覆盖 ([providers.*.overrides.{provider_name}])
        global_override = self.load_global_overrides(config_section, provider_name)
        if global_override:
            result = deep_merge_configs(result, global_override)
            self.logger.debug(f"应用主配置覆盖: {provider_name}")

        # 步骤3: 加载Provider本地配置 (config.toml，优先级最高)
        # 如果本地配置不存在，尝试从Schema自动生成
        local_config = self._load_or_generate_local_config(provider_dir, provider_name, schema_class, provider_layer)
        if local_config:
            result = deep_merge_configs(result, local_config)
            self.logger.debug(f"加载本地配置: {provider_name}")

        # 步骤4: Schema验证（如果提供）
        # 注意：我们保留额外字段（不在Schema中定义的字段）
        if schema_class is not None:
            try:
                # 保存额外字段（不在Schema中的字段）
                schema_fields = set(schema_class.model_fields.keys())
                extra_fields = {k: v for k, v in result.items() if k not in schema_fields}

                # 验证配置
                validated = schema_class(**result)

                # 合并验证后的配置和额外字段
                result = validated.model_dump(exclude_unset=False)
                result.update(extra_fields)

                self.logger.debug(f"配置验证通过: {provider_name}")
            except Exception as e:
                self.logger.error(f"配置验证失败: {provider_name} - {e}")
                raise

        return result

    def _get_schema_defaults(
        self,
        schema_class: Optional[type],
        provider_name: str,
    ) -> Dict[str, Any]:
        """
        从Schema类获取默认值

        Args:
            schema_class: Pydantic Schema类
            provider_name: Provider名称（用于日志）

        Returns:
            Schema默认值字典
        """
        if schema_class is None:
            return {}

        try:
            # 创建Schema实例获取默认值
            schema_instance = schema_class()
            return schema_instance.model_dump(exclude_unset=True)
        except Exception as e:
            self.logger.warning(f"Schema默认值获取失败 ({provider_name}): {e}")
            return {}

    def _load_or_generate_local_config(
        self,
        provider_dir: str,
        provider_name: str,
        schema_class: Optional[type],
        provider_layer: Literal["input", "output", "decision"],
    ) -> Dict[str, Any]:
        """
        加载Provider本地配置，如果不存在则尝试从Schema生成

        Args:
            provider_dir: Provider目录路径
            provider_name: Provider名称
            schema_class: Pydantic Schema类（用于生成配置）
            provider_layer: Provider层级

        Returns:
            本地配置字典
        """
        local_config_path = os.path.join(provider_dir, "config.toml")

        # 如果本地配置存在，直接加载
        if os.path.exists(local_config_path):
            return self._load_local_config_file(local_config_path, provider_name)

        # 本地配置不存在，尝试从Schema自动生成
        self.logger.info(f"Provider本地配置不存在: {local_config_path}，尝试从Schema生成")

        # 如果没有Schema，无法生成
        if schema_class is None:
            # 尝试从Schema Registry查找Schema
            schema_class = self._find_schema_for_provider(provider_name, provider_layer)

        if schema_class is not None:
            generated = self._generate_config_from_schema(schema_class, local_config_path, provider_name)
            if generated:
                return generated

        # 无法生成配置，返回空字典
        return {}

    def _load_local_config_file(
        self,
        config_path: str,
        provider_name: str,
    ) -> Dict[str, Any]:
        """
        加载本地配置文件

        Args:
            config_path: 配置文件路径
            provider_name: Provider名称

        Returns:
            配置字典
        """
        try:
            # 尝试使用tomllib (Python 3.11+)
            try:
                import tomllib

                with open(config_path, "rb") as f:
                    local_config = tomllib.load(f)
            except ImportError:
                # 回退到toml
                import toml

                with open(config_path, "r", encoding="utf-8") as f:
                    local_config = toml.load(f)

            # 提取Provider配置（支持 [provider_name] 节或直接使用根配置）
            provider_local = local_config.get(provider_name, local_config)
            return provider_local
        except Exception as e:
            self.logger.warning(f"本地配置加载失败: {config_path} - {e}")
            return {}

    def _find_schema_for_provider(
        self,
        provider_name: str,
        provider_layer: Literal["input", "output", "decision"],
    ) -> Optional[type]:
        """
        从Schema Registry查找Provider的Schema类

        Args:
            provider_name: Provider名称
            provider_layer: Provider层级

        Returns:
            Schema类，如果找不到返回None
        """
        try:
            from src.services.config.schemas import PROVIDER_SCHEMA_REGISTRY

            return PROVIDER_SCHEMA_REGISTRY.get(provider_name)
        except (ImportError, AttributeError):
            return None

    def _generate_config_from_schema(
        self,
        schema_class: type,
        output_path: str,
        provider_name: str,
    ) -> Optional[Dict[str, Any]]:
        """
        从Schema生成config.toml文件

        注意: 此方法只生成配置文件作为模板，返回空字典。
        这样可以让主配置覆盖 (overrides) 优先生效，用户可以后续编辑本地配置文件。

        Args:
            schema_class: Pydantic Schema类
            output_path: 输出文件路径
            provider_name: Provider名称

        Returns:
            空字典（因为生成的配置文件仅作为模板）
        """
        try:
            # 获取Schema的默认值（使用 exclude_unset=False 确保包含所有默认值）
            schema_instance = schema_class()
            config_data = schema_instance.model_dump(exclude_unset=False)

            # 确保输出目录存在
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # 生成TOML文件（仅作为模板）
            try:
                import tomllib

                # Python 3.13+ 有 tomllib.write
                if hasattr(tomllib, "write"):
                    with open(output_path, "wb") as f:
                        tomllib.write(f, {provider_name: config_data})
                else:
                    raise AttributeError("tomllib.write not available")
            except (ImportError, AttributeError):
                # 回退到tomli_w或toml
                try:
                    import tomli_w

                    with open(output_path, "wb") as f:
                        tomli_w.dump({provider_name: config_data}, f)
                except ImportError:
                    # 使用简单的TOML写入
                    import toml

                    with open(output_path, "w", encoding="utf-8") as f:
                        toml.dump({provider_name: config_data}, f)

            self.logger.info(f"已从Schema自动生成配置文件模板: {output_path}")
            # 返回空字典，让全局覆盖优先生效
            return {}

        except Exception as e:
            self.logger.warning(f"从Schema生成配置失败 ({provider_name}): {e}")
            return None

    def load_global_overrides(
        self,
        config_section: str,
        provider_name: str,
    ) -> Dict[str, Any]:
        """
        加载主配置覆盖 ([providers.*.overrides])

        Args:
            config_section: 配置节名称（如 "providers.input"）
            provider_name: Provider名称

        Returns:
            主配置覆盖字典（如果不存在则返回空字典）

        Example:
            overrides = config_service.load_global_overrides(
                "providers.input", "console_input"
            )
        """
        # 处理点分路径 (如 "providers.input")
        parts = config_section.split(".")
        current = self._main_config

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return {}

        if not isinstance(current, dict):
            return {}

        overrides = current.get("overrides", {})
        provider_override = overrides.get(provider_name, {})
        return provider_override


def deep_merge_configs(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    深度合并两个字典，后者覆盖前者

    合并规则:
    - 基本类型（str, int, float, bool）: override直接覆盖
    - 字典类型: 递归合并
    - 列表类型: override完全替换（不合并）
    - None: 跳过

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        合并后的字典

    Example:
        base = {"a": 1, "b": {"x": 10, "y": 20}}
        override = {"b": {"y": 200}, "c": 3}
        result = deep_merge_configs(base, override)
        # result = {"a": 1, "b": {"x": 10, "y": 200}, "c": 3}
    """
    result = base.copy()

    for key, value in override.items():
        if value is None:
            # 跳过None值
            continue

        if key not in result:
            # 基础字典中没有该键，直接添加
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            # 都是字典，递归合并
            result[key] = deep_merge_configs(result[key], value)
        elif isinstance(result[key], list) and isinstance(value, list):
            # 都是列表，后者替换前者
            result[key] = value
        else:
            # 其他类型，直接覆盖
            result[key] = value

    return result
