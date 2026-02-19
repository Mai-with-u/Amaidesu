"""
ConfigService - 统一的配置管理服务

职责:
- 提供统一的配置加载接口
- 集中管理所有配置加载逻辑
- 支持 Provider、Pipeline 等组件的配置获取
- 配置合并策略：主配置覆盖组件配置
- 二级配置合并：Schema默认值 → 主配置覆盖
"""

from typing import Any, Dict, Literal, Optional

from src.modules.config.config_utils import initialize_configurations, load_config
from src.modules.config.version_manager import ConfigVersionManager
from src.modules.logging import get_logger


class ConfigService:
    """
    统一的配置管理服务

    提供一个集中的配置管理入口，所有组件的配置加载都通过此服务进行。

    使用示例:
        # 初始化配置服务
        config_service = ConfigService(base_dir="/path/to/project")
        config, main_copied, plugin_copied, pipeline_copied, config_updated = await config_service.initialize()

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

        # 版本管理器
        self.version_manager = ConfigVersionManager(base_dir)

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

    def initialize(self) -> tuple[Dict[str, Any], bool, bool, bool, bool]:
        """
        初始化所有配置文件

        执行以下步骤:
        1. 检查并设置主配置文件
        2. 加载主配置
        3. 检查并设置插件配置文件
        4. 检查并设置管道配置文件
        5. 检查并更新配置版本

        Returns:
            (main_config, main_config_copied, plugin_configs_copied,
             pipeline_configs_copied, main_config_updated)
        """
        if self._initialized:
            self.logger.warning("ConfigService 已经初始化，跳过重复初始化")
            return (
                self._main_config,
                self._main_config_copied,
                self._plugin_configs_copied,
                self._pipeline_configs_copied,
                False,
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

        # 新增：检查并更新配置版本
        # 仅当配置已存在（未从模板复制）时才检查版本
        main_config_updated = False
        if not self._main_config_copied and self.version_manager is not None:
            needs_update, message = self.version_manager.check_main_config()

            if needs_update:
                self.logger.info(f"配置文件需要更新: {message}")
                updated, message = self.version_manager.update_main_config()

                if updated:
                    self.logger.info(f"配置文件已更新: {message}")
                    # 重新加载更新后的配置
                    self._main_config = load_config("config.toml", self.base_dir)
                    main_config_updated = True
                else:
                    self.logger.warning(f"配置文件更新失败: {message}")

        self._initialized = True
        self.logger.info("配置服务初始化完成")

        return (
            self._main_config,
            self._main_config_copied,
            self._plugin_configs_copied,
            self._pipeline_configs_copied,
            main_config_updated,
        )

    def get_section(self, section: str, default: Any = None) -> Dict[str, Any]:
        """
        获取配置节

        支持点分路径访问嵌套配置节，例如：
        - "providers" → config["providers"]
        - "providers.input" → config["providers"]["input"]
        - "providers.decision.maicore" → config["providers"]["decision"]["maicore"]

        Args:
            section: 配置节名称（如 "general", "plugins", "providers.input"）
            default: 如果配置节不存在，返回的默认值

        Returns:
            配置节字典

        Example:
            general_config = config_service.get_section("general")
            input_config = config_service.get_section("providers.input")
            maicore_config = config_service.get_section("providers.decision.maicore")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {} if default is None else default

        # 支持点分路径（如 "providers.input"）
        if "." in section:
            parts = section.split(".")
            current = self._main_config

            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    self.logger.debug(f"配置节 '{section}' 不存在（在 '{part}' 处中断）")
                    return {} if default is None else default

            return current if isinstance(current, dict) else {}
        else:
            # 单层路径（原有逻辑）
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
    ) -> Dict[str, Any]:
        """
        获取管道配置

        配置来源：主配置文件中 [pipelines.pipeline_name]

        Args:
            pipeline_name: 管道名称（如 "rate_limit", "similar_filter"）

        Returns:
            管道配置字典

        Example:
            throttle_config = config_service.get_pipeline_config("throttle")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        # 获取主配置中的管道配置
        pipeline_config = self.get_section("pipelines", {}).get(pipeline_name, {}).copy()

        return pipeline_config

    def get_all_provider_configs(self, provider_type: str = "input") -> Dict[str, Dict[str, Any]]:
        """
        获取所有Provider的配置

        Args:
            provider_type: Provider类型（"input"、"output" 或 "decision"，默认 "input"）

        Returns:
            字典，键为Provider名称，值为该Provider的配置

        Example:
            # 获取所有输入Provider配置
            all_inputs = config_service.get_all_provider_configs("input")
            for provider_name, provider_config in all_inputs.items():
                logger.debug(f"{provider_name}: {provider_config}")

            # 获取所有输出Provider配置
            all_outputs = config_service.get_all_provider_configs("output")

            # 获取所有决策Provider配置
            all_decisions = config_service.get_all_provider_configs("decision")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        all_provider_configs = {}

        # 根据Provider类型选择配置节
        # 新配置结构: [providers.{domain}.{provider_name}]
        if provider_type in ("input", "output", "decision"):
            config_section = self.get_section(f"providers.{provider_type}", {})
            # 收集所有Provider配置（排除元数据字段如 enabled、enabled_inputs 等）
            metadata_fields = {
                "input": ["enabled", "enabled_inputs"],
                "output": ["enabled", "enabled_outputs", "concurrent_rendering", "error_handling", "render_timeout"],
                "decision": ["enabled", "active_provider", "available_providers"],
            }
            exclude_fields = metadata_fields.get(provider_type, [])

            for provider_name, provider_config in config_section.items():
                if provider_name not in exclude_fields:
                    all_provider_configs[provider_name] = provider_config.copy()
        else:
            self.logger.warning(f"未知的Provider类型: {provider_type}")
            return all_provider_configs

        return all_provider_configs

    def get_all_pipeline_configs(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有管道的配置

        Returns:
            字典，键为管道名称，值为该管道的配置

        Example:
            all_pipelines = config_service.get_all_pipeline_configs()
            for pipeline_name, pipeline_config in all_pipelines.items():
                logger.debug(f"{pipeline_name}: {pipeline_config}")
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        # 从主配置中获取所有管道配置
        return self.get_section("pipelines", {}).copy()

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

    # ========== Provider 配置方法 ==========

    def get_provider_config_with_defaults(
        self,
        provider_name: str,
        provider_layer: Literal["input", "output", "decision"],
        schema_class: Optional[type] = None,
    ) -> Dict[str, Any]:
        """
        获取Provider配置（二级合并）

        配置合并顺序（后者覆盖前者）:
        1. Schema默认值（从Pydantic Schema类获取）
        2. 主配置覆盖 (config.toml中的[providers.*.{provider_name}])

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
            from src.modules.config.schemas.input_providers import ConsoleInputProviderConfig
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

        # 确定配置节名称
        if provider_layer == "input":
            config_section = "providers.input"
        elif provider_layer == "output":
            config_section = "providers.output"
        elif provider_layer == "decision":
            config_section = "providers.decision"
        else:
            self.logger.error(f"未知的Provider层级: {provider_layer}")
            return {}

        # 步骤1: 获取Schema默认值
        result = self._get_schema_defaults(schema_class, provider_name)

        # 步骤2: 应用主配置覆盖
        global_override = self.load_global_overrides(config_section, provider_name)
        if global_override:
            result = deep_merge_configs(result, global_override)

        # 步骤3: Schema验证（如果提供）
        if schema_class is not None:
            try:
                schema_fields = set(schema_class.model_fields.keys())
                extra_fields = {k: v for k, v in result.items() if k not in schema_fields}

                validated = schema_class(**result)
                result = validated.model_dump(exclude_unset=False)
                result.update(extra_fields)
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
            # 使用 model_construct 安全创建Schema实例，避免required字段的验证错误
            schema_instance = schema_class.model_construct()
            # 使用 exclude_unset=False 以包含所有字段，包括有默认值的字段
            return schema_instance.model_dump(exclude_unset=False)
        except Exception as e:
            self.logger.warning(f"Schema默认值获取失败 ({provider_name}): {e}")
            return {}

    def load_global_overrides(
        self,
        config_section: str,
        provider_name: str,
    ) -> Dict[str, Any]:
        """
        加载主配置覆盖

        新配置结构（推荐使用）：
        - [providers.input.{provider_name}] - Input Provider 配置
        - [providers.output.{provider_name}] - Output Provider 配置
        - [providers.decision.{provider_name}] - Decision Provider 配置

        向后兼容旧配置结构（已废弃，但仍支持）：
        - [providers.input.inputs.{provider_name}]
        - [providers.input.overrides.{provider_name}]
        - [providers.output.outputs.{provider_name}]
        - [providers.output.overrides.{provider_name}]
        - [providers.decision.providers.{provider_name}]

        Args:
            config_section: 配置节名称（如 "providers.input"）
            provider_name: Provider名称

        Returns:
            主配置覆盖字典（如果不存在则返回空字典）

        Example:
            # 新配置结构（推荐）
            overrides = config_service.load_global_overrides(
                "providers.input", "bili_danmaku_official"
            )

            # 读取 [providers.input.bili_danmaku_official] 配置
        """
        # 处理点分路径 (如 "providers.input")
        parts = config_section.split(".")
        if len(parts) != 2 or parts[0] != "providers":
            self.logger.warning(f"无效的配置节路径: {config_section}")
            return {}

        domain = parts[1]  # input, output, decision
        if domain not in ("input", "output", "decision"):
            self.logger.warning(f"未知的Provider域: {domain}")
            return {}

        # 获取 providers.{domain} 配置节
        domain_config = self._main_config.get("providers", {}).get(domain, {})
        if not isinstance(domain_config, dict):
            return {}

        # 优先使用新配置结构：[providers.{domain}.{provider_name}]
        provider_config = domain_config.get(provider_name, {})

        # 向后兼容旧配置结构（已废弃，但仍支持）
        if not provider_config:
            # 尝试读取旧路径
            if domain == "input":
                # [providers.input.inputs.{provider_name}]（已废弃）
                inputs = domain_config.get("inputs", {})
                provider_config = inputs.get(provider_name, {})
                if not provider_config:
                    # [providers.input.overrides.{provider_name}]（已废弃）
                    overrides = domain_config.get("overrides", {})
                    provider_config = overrides.get(provider_name, {})
            elif domain == "output":
                # [providers.output.outputs.{provider_name}]（已废弃）
                outputs = domain_config.get("outputs", {})
                provider_config = outputs.get(provider_name, {})
                if not provider_config:
                    # [providers.output.overrides.{provider_name}]（已废弃）
                    overrides = domain_config.get("overrides", {})
                    provider_config = overrides.get(provider_name, {})
            elif domain == "decision":
                # [providers.decision.providers.{provider_name}]（已废弃）
                providers = domain_config.get("providers", {})
                provider_config = providers.get(provider_name, {})

        return provider_config.copy() if isinstance(provider_config, dict) else {}


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
