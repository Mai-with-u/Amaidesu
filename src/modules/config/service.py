"""
ConfigService - 统一的配置管理服务

职责:
- 提供统一的配置加载接口
- 集中管理所有配置加载逻辑
- 支持 Collector/Decider/Handler 等组件的配置获取
- 配置合并策略：主配置覆盖组件配置
- 二级配置合并：Schema默认值 → 主配置覆盖
"""

from typing import Any, Dict, Literal, Optional

from src.modules.config.config_utils import initialize_configurations, load_config
from src.modules.config.version_manager import ConfigVersionManager
from src.modules.logging import get_logger

# 阶段 → 主配置节名映射
_PHASE_SECTION: Dict[str, str] = {
    "input": "collectors",
    "output": "handlers",
    "decision": "deciders",
}

# 各阶段配置节中的元数据字段（非组件配置，需排除）
_PHASE_METADATA_FIELDS: Dict[str, set] = {
    "input": {"enabled"},
    "output": {"enabled", "concurrent_rendering", "error_handling", "render_timeout"},
    "decision": {"active", "available"},
}


class ConfigService:
    """
    统一的配置管理服务

    提供一个集中的配置管理入口，所有组件的配置加载都通过此服务进行。

    使用示例:
        config_service = ConfigService(base_dir="/path/to/project")
        config, main_copied, plugin_copied, pipeline_copied, config_updated = await config_service.initialize()

        general_config = config_service.get_section("general")
        input_config = config_service.get_config_with_defaults("console", "input")
        pipeline_config = config_service.get_pipeline_config("throttle")
    """

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._main_config: Dict[str, Any] = {}
        self._main_config_copied = False
        self._plugin_configs_copied = False
        self._pipeline_configs_copied = False
        self._initialized = False
        self.logger = get_logger("ConfigService")
        self.version_manager = ConfigVersionManager(base_dir)
        self.logger.debug("ConfigService 初始化完成")

    @property
    def main_config(self) -> Dict[str, Any]:
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
            plugin_dir_name="src/stages",
            pipeline_dir_name="src/stages/input/pipelines",
        )

        main_config_updated = False
        if not self._main_config_copied and self.version_manager is not None:
            needs_update, message = self.version_manager.check_main_config()

            if needs_update:
                self.logger.info(f"配置文件需要更新: {message}")
                updated, message = self.version_manager.update_main_config()

                if updated:
                    self.logger.info(f"配置文件已更新: {message}")
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
        - "general" → config["general"]
        - "collectors" → config["collectors"]
        - "collectors.stt" → config["collectors"]["stt"]

        Args:
            section: 配置节名称（如 "general", "collectors", "collectors.stt"）
            default: 如果配置节不存在，返回的默认值

        Returns:
            配置节字典
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {} if default is None else default

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
            result: Any = self._main_config.get(section, default)
            if result is None:
                self.logger.warning(f"配置节 '{section}' 不存在")
                return {}

            return result if isinstance(result, dict) else {}

    def get(self, key: str, default: Any = None, section: str = None) -> Any:
        """
        获取配置项

        Args:
            key: 配置项键
            default: 默认值
            section: 配置节名称（可选，如果提供则从该节中查找）

        Returns:
            配置项值
        """
        if section:
            section_config = self.get_section(section)
            return section_config.get(key, default)
        else:
            return self._main_config.get(key, default)

    def get_pipeline_config(self, pipeline_name: str) -> Dict[str, Any]:
        """
        获取管道配置

        配置来源：主配置文件中 [pipelines.pipeline_name]

        Args:
            pipeline_name: 管道名称（如 "rate_limit", "similar_filter"）

        Returns:
            管道配置字典
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        return self.get_section("pipelines", {}).get(pipeline_name, {}).copy()

    def get_all_configs(self, phase: str = "input") -> Dict[str, Dict[str, Any]]:
        """
        获取指定阶段所有组件的配置

        Args:
            phase: 阶段（"input"、"output" 或 "decision"，默认 "input"）

        Returns:
            字典，键为组件名称，值为该组件的配置
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        section_name = _PHASE_SECTION.get(phase)
        if section_name is None:
            self.logger.warning(f"未知的组件阶段: {phase}")
            return {}

        section = self.get_section(section_name, {})
        exclude_fields = _PHASE_METADATA_FIELDS.get(phase, set())

        return {
            name: cfg.copy() for name, cfg in section.items() if name not in exclude_fields and isinstance(cfg, dict)
        }

    def get_all_pipeline_configs(self) -> Dict[str, Dict[str, Any]]:
        """获取所有管道的配置"""
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        return self.get_section("pipelines", {}).copy()

    def is_config_enabled(self, name: str, phase: str = "input") -> bool:
        """
        检查组件是否启用

        Args:
            name: 组件名称
            phase: 阶段（"input" 或 "output"，默认 "input"）

        Returns:
            是否启用
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回 False")
            return False

        section_name = _PHASE_SECTION.get(phase)
        if phase == "rendering":
            section_name = "handlers"

        if section_name is None:
            self.logger.warning(f"未知的组件阶段: {phase}")
            return False

        enabled_list = self.get_section(section_name, {}).get("enabled", [])
        return name in enabled_list

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

        return "priority" in pipeline_config

    # ========== 组件配置方法 ==========

    def get_config_with_defaults(
        self,
        name: str,
        phase: Literal["input", "output", "decision"],
        schema_class: Optional[type] = None,
    ) -> Dict[str, Any]:
        """
        获取组件配置（二级合并）

        配置合并顺序（后者覆盖前者）:
        1. Schema默认值（从Pydantic Schema类获取）
        2. 主配置覆盖 (config.toml中的 [collectors.{name}] / [deciders.{name}] / [handlers.{name}])

        Args:
            name: 组件名称
            phase: 阶段（input/output/decision）
            schema_class: Pydantic Schema类（可选，用于类型验证和默认值）

        Returns:
            合并后的配置字典

        Raises:
            ValidationError: 如果schema_class提供且配置验证失败
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        if phase not in _PHASE_SECTION:
            self.logger.error(f"未知的组件阶段: {phase}")
            return {}

        result = self._get_schema_defaults(schema_class, name)

        global_override = self.load_global_overrides(phase, name)
        if global_override:
            result = deep_merge_configs(result, global_override)

        if schema_class is not None:
            try:
                schema_fields = set(schema_class.model_fields.keys())
                extra_fields = {k: v for k, v in result.items() if k not in schema_fields}

                validated = schema_class(**result)
                result = validated.model_dump(exclude_unset=False)
                result.update(extra_fields)
            except Exception as e:
                self.logger.error(f"配置验证失败: {name} - {e}")
                raise

        return result

    def _get_schema_defaults(self, schema_class: Optional[type], name: str) -> Dict[str, Any]:
        """从Schema类获取默认值"""
        if schema_class is None:
            return {}

        try:
            schema_instance = schema_class.model_construct()
            return schema_instance.model_dump(exclude_unset=False)
        except Exception as e:
            self.logger.warning(f"Schema默认值获取失败 ({name}): {e}")
            return {}

    def load_global_overrides(self, phase: str, name: str) -> Dict[str, Any]:
        """
        加载主配置中指定组件的覆盖配置

        读取路径：
        - input 阶段：[collectors.{name}]
        - output 阶段：[handlers.{name}]
        - decision 阶段：[deciders.{name}]

        Args:
            phase: 阶段（input/output/decision）
            name: 组件名称

        Returns:
            主配置覆盖字典（如果不存在则返回空字典）
        """
        section_name = _PHASE_SECTION.get(phase)
        if section_name is None:
            self.logger.warning(f"未知的组件阶段: {phase}")
            return {}

        section = self._main_config.get(section_name, {})
        if not isinstance(section, dict):
            return {}

        config = section.get(name, {})
        return config.copy() if isinstance(config, dict) else {}


def deep_merge_configs(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    深度合并两个字典，后者覆盖前者

    合并规则:
    - 基本类型（str, int, float, bool）: override直接覆盖
    - 字典类型: 递归合并
    - 列表类型: override完全替换（不合并）
    - None: 跳过
    """
    result = base.copy()

    for key, value in override.items():
        if value is None:
            continue

        if key not in result:
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_configs(result[key], value)
        elif isinstance(result[key], list) and isinstance(value, list):
            result[key] = value
        else:
            result[key] = value

    return result
