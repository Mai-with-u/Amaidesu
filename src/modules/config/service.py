"""
ConfigService - 统一的配置管理服务

职责:
- 提供统一的配置加载接口
- 集中管理所有配置加载逻辑
- 支持 Collector/Decider/Handler 等组件的配置获取
- 配置合并策略：主配置覆盖组件配置
- 二级配置合并：Schema默认值 → 主配置覆盖
- 配置热重载 (Task 7): FileWatcher + 回调机制 + Schema API
"""

import asyncio
import inspect
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
)

from pydantic import BaseModel

from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.config.file_watcher import FileChange, FileWatcher

# 阶段 → 主配置节名映射
_PHASE_SECTION: Dict[str, str] = {
    "input": "collectors",
    "output": "handlers",
    "decision": "deciders",
}

# 各阶段配置节中的元数据字段（非组件配置，需排除）
_PHASE_METADATA_FIELDS: Dict[str, set] = {
    "input": {"enabled"},
    "output": {"enabled", "concurrent_rendering", "error_handling", "render_timeout_ms"},
    "decision": {"enabled"},
}

# 热重载回调签名: 支持 (scopes) 或 () 两种调用形态
ConfigReloadCallback = Union[
    Callable[[Sequence[str]], Any],
    Callable[[], Any],
]

# 配置文件名 → 内部 scope 名的映射 (Amaidesu 多文件约定)
_CONFIG_FILE_TO_SCOPE: Dict[str, str] = {
    "core.toml": "core",
    "model.toml": "model",
    "input.toml": "input",
    "decision.toml": "decision",
    "output.toml": "output",
}


class ConfigService:
    """
    统一的配置管理服务

    提供一个集中的配置管理入口，所有组件的配置加载都通过此服务进行。

    使用示例:
        config_service = ConfigService(base_dir="/path/to/project")
        config, was_created = config_service.initialize()

        general_config = config_service.get_section("general")
        input_config = config_service.get_config_with_defaults("console", "input")
        pipeline_config = config_service.get_pipeline_config("throttle")

        # 热重载
        config_service.register_reload_callback(my_callback)
        await config_service.start_file_watcher()
    """

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._main_config: Dict[str, Any] = {}
        self._main_config_copied = False
        self._initialized = False
        # 热重载状态 (Task 7 新增)
        self._reload_callbacks: List[ConfigReloadCallback] = []
        self._file_watcher: Optional["FileWatcher"] = None
        self._file_watcher_subscription_id: Optional[str] = None
        self._reload_lock: asyncio.Lock = asyncio.Lock()
        self._reload_revision: int = 0
        # Task 11: schema_registry ↔ generator 覆盖率门禁结果
        # 在 ``initialize()`` 末尾填充;测试与运维可读取
        self._last_coverage_result: Optional[Any] = None
        self.logger = get_logger("ConfigService")
        self.logger.debug("ConfigService 初始化完成")

    @property
    def main_config(self) -> Dict[str, Any]:
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}
        return self._main_config

    def _try_multi_file_init(self) -> tuple[bool, bool]:
        """尝试使用多文件配置模式（首次运行自动生成/迁移）

        决策逻辑：
        1. config/ 存在且有 .toml → 直接加载
        2. config/ 不存在 + 旧 config.toml 存在 → 迁移到 config/
        3. config/ 不存在 + 无旧 config.toml → 从 Schema 生成默认配置

        成功时将配置拍平到 _main_config 以保持向后兼容。

        Returns:
            (success, was_created) — success=True 表示多文件模式就绪；
            was_created=True 表示本次执行了生成或迁移
        """
        from src.modules.config.migration import migrate_old_config
        from src.modules.config.multi_file_loader import (
            CONFIG_VERSION,
            generate_default_configs,
            get_config_version,
            load_config_dir,
        )

        config_dir = Path(self.base_dir) / "config"
        old_config = Path(self.base_dir) / "config.toml"
        was_created = False

        if not config_dir.exists() or not any(config_dir.glob("*.toml")):
            if old_config.exists():
                self.logger.info("检测到旧 config.toml，开始迁移到 config/ 多文件结构...")
                report = migrate_old_config(old_config, config_dir)
                self.logger.info(
                    f"迁移完成: {len(report.migrated_sections)} 段迁移, {len(report.dropped_sections)} 段丢弃"
                )
                was_created = True
            else:
                self.logger.info("首次运行：从 Schema 生成默认配置到 config/...")
                generate_default_configs(config_dir)
                was_created = True

        try:
            multi_config, drift = load_config_dir(config_dir)
        except Exception as e:
            self.logger.error(f"多文件配置加载失败: {e}")
            return False, False

        if drift.has_drift:
            for key in drift.redundant:
                self.logger.warning(f"漂移检测: 冗余配置项 '{key}'")
            for key in drift.missing:
                self.logger.info(f"漂移检测: 补充缺失配置项 '{key}'")

        current_ver = get_config_version(config_dir)
        if current_ver and current_ver != CONFIG_VERSION:
            self.logger.info(f"配置版本更新: {current_ver} → {CONFIG_VERSION}")

        self._main_config = {}
        for category in ("core", "model", "input", "decision", "output"):
            section_data = multi_config.get(category, {})
            if isinstance(section_data, dict):
                for key, value in section_data.items():
                    self._main_config[key] = value

        return True, was_created

    def initialize(self) -> tuple[Dict[str, Any], bool]:
        """
        初始化配置（多文件模式）。

        自动检测 config/ 目录：
        1. 已存在 → 直接加载
        2. 不存在 + 旧 config.toml 存在 → 迁移到 config/
        3. 不存在 + 无旧 config.toml → 从 Schema 生成默认配置

        Returns:
            (main_config, was_created) — was_created 表示本次执行了生成或迁移
        """
        if self._initialized:
            self.logger.warning("ConfigService 已经初始化，跳过重复初始化")
            return self._main_config, False

        self.logger.info("开始初始化配置服务...")

        success, was_created = self._try_multi_file_init()
        if not success:
            raise RuntimeError(
                "配置初始化失败：无法加载 config/ 目录下的配置文件。请检查配置文件格式或删除 config/ 目录重新生成。"
            )

        self._initialized = True
        self._main_config_copied = was_created
        self.logger.info("配置服务初始化完成")

        # Task 11: 启动时执行 schema_registry ↔ generator 覆盖率门禁
        self._check_schema_registry_coverage()

        return self._main_config, was_created

    def _check_schema_registry_coverage(self) -> None:
        """启动时执行 schema_registry vs generator 覆盖率门禁 (Task 11)。

        行为契约:
        - 100% 字段覆盖且类型兼容 → 输出 ``SCHEMA_REGISTRY_COVERED`` (INFO)
        - 否则输出 ``SCHEMA_REGISTRY_COVERAGE_FAIL`` 警告 + 详细 DEBUG 信息

        设计动机: ``schema_registry`` 是手写元数据(前端 Dashboard 仍依赖其
        ``groups`` 形状),而 ``ConfigSchemaGenerator`` 是 Pydantic 自动生成的真相源。
        迁移期间必须保持 100% 字段覆盖,否则 UI 上会出现"看不到某字段"或"读到
        错误约束"等不易察觉的回归。

        注意:
        - 此检查是**只读 + 日志**,不会中断 initialize() 流程
        - 即使覆盖率失败,服务仍可正常启动(供回滚使用)
        """
        try:
            from src.modules.config.schema_coverage import (
                REGISTRY_GROUP_TO_PYDANTIC,
                check_registry_coverage,
            )
            from src.modules.config.schema_registry import get_schema_registry

            registry = get_schema_registry()
            result = check_registry_coverage(
                registry.get_all_groups(),
                REGISTRY_GROUP_TO_PYDANTIC,
            )
            self._last_coverage_result = result
            result.log_to(self.logger)
        except Exception as exc:
            # 门禁失败不能阻塞启动 (registry 可能在 schema 演进期暂时不可用)
            self.logger.warning(
                f"SCHEMA_REGISTRY_COVERAGE_SKIPPED: 启动 coverage gate 异常: {type(exc).__name__}: {exc}"
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

    def get(self, key: str, default: Any = None, section: Optional[str] = None) -> Any:
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

    def get_pipeline_config(self, pipeline_name: str, phase: str = "input") -> Dict[str, Any]:
        """
        获取管道配置

        配置来源：主配置文件中 [pipelines.phase.pipeline_name]

        Args:
            pipeline_name: 管道名称（如 "rate_limit", "similar_filter"）
            phase: 阶段（"input" 或 "output"，默认 "input"）

        Returns:
            管道配置字典
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回空配置")
            return {}

        return self.get_section("pipelines", {}).get(phase, {}).get(pipeline_name, {}).copy()

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
        if section_name is None:
            self.logger.warning(f"未知的组件阶段: {phase}")
            return False

        enabled_list = self.get_section(section_name, {}).get("enabled", [])
        return name in enabled_list

    def is_pipeline_enabled(self, pipeline_name: str, phase: str = "input") -> bool:
        """
        检查管道是否启用

        管道启用的条件：在 [pipelines.phase.pipeline_name] 中定义了 `priority` 键。

        Args:
            pipeline_name: 管道名称
            phase: 阶段（"input" 或 "output"，默认 "input"）

        Returns:
            是否启用
        """
        if not self._initialized:
            self.logger.warning("ConfigService 未初始化，返回 False")
            return False

        pipelines_config = self.get_section("pipelines", {})
        phase_config = pipelines_config.get(phase, {})
        pipeline_config = phase_config.get(pipeline_name, {})

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

                # 通过 from_dict 加载，自动剥离未知字段（配合 extra="forbid"）
                if hasattr(schema_class, "from_dict"):
                    validated = schema_class.from_dict(result)
                else:
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

    # ========== 热重载 + Schema API (Task 7) ==========

    def register_reload_callback(self, callback: ConfigReloadCallback) -> None:
        """注册配置热重载回调。

        Args:
            callback: 配置热重载回调。允许无参回调 ``()``，也允许
                ``(scopes: Sequence[str]) -> Any`` 接收变更范围列表的回调。
                async 回调会被自动 await。
        """
        self._reload_callbacks.append(callback)

    def unregister_reload_callback(self, callback: ConfigReloadCallback) -> None:
        """注销已注册的回调。未注册的回调静默忽略。"""
        try:
            self._reload_callbacks.remove(callback)
        except ValueError:
            return

    @staticmethod
    def _callback_accepts_scopes(callback: ConfigReloadCallback) -> bool:
        """通过 inspect 检查回调是否接受位置参数 (scopes)。"""
        try:
            params = inspect.signature(callback).parameters.values()
        except (TypeError, ValueError):
            return False

        positional_kinds = {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
        for param in params:
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                return True
            if param.kind in positional_kinds:
                return True
        return False

    async def _invoke_reload_callback(
        self,
        callback: ConfigReloadCallback,
        changed_scopes: Sequence[str],
    ) -> None:
        """调用单个回调，根据签名决定是否传 scopes；async 回调自动 await。"""
        if self._callback_accepts_scopes(callback):
            cb_with_scopes = cast(Callable[[Sequence[str]], Any], callback)
            result = cb_with_scopes(changed_scopes)
        else:
            cb_no_scopes = cast(Callable[[], Any], callback)
            result = cb_no_scopes()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _normalize_changed_scopes(
        scopes: Optional[Sequence[str]],
    ) -> Tuple[str, ...]:
        """规范化输入 scopes: 去重/小写/过滤未知值。None → 全部 scope。"""
        all_scopes = tuple(_CONFIG_FILE_TO_SCOPE.values())
        if scopes is None:
            return all_scopes
        seen: List[str] = []
        valid = set(all_scopes)
        for raw in scopes:
            if not isinstance(raw, str):
                continue
            s = raw.strip().lower()
            if not s or s not in valid or s in seen:
                continue
            seen.append(s)
        return tuple(seen)

    @staticmethod
    def _resolve_changed_scopes(
        changes: Sequence["FileChange"],
    ) -> Tuple[str, ...]:
        """FileChange 列表 → scope 元组 (基于文件名)。"""
        scopes: List[str] = []
        for change in changes:
            scope = _CONFIG_FILE_TO_SCOPE.get(change.path.name)
            if scope and scope not in scopes:
                scopes.append(scope)
        return tuple(scopes)

    async def reload_config(
        self,
        changed_scopes: Optional[Sequence[str]] = None,
    ) -> bool:
        """重载配置文件并调用所有已注册回调。

        Args:
            changed_scopes: 要重载的 scope 列表 (core/model/input/decision/output)。
                None 表示重载全部。

        Returns:
            True 表示重载流程完成 (即使个别回调失败)，False 表示加载失败。
        """
        scopes = self._normalize_changed_scopes(changed_scopes)
        if not scopes:
            self.logger.debug("reload_config: 无有效 scope，跳过")
            return True

        async with self._reload_lock:
            try:
                self._main_config = self._load_main_config_from_disk()
            except Exception as exc:
                self.logger.error(f"配置重载失败: {exc}")
                return False

            self._reload_revision += 1
            self.logger.info(f"配置已重载 (revision={self._reload_revision}, scopes={list(scopes)})")

            for callback in list(self._reload_callbacks):
                try:
                    await self._invoke_reload_callback(callback, scopes)
                except Exception as exc:
                    self.logger.warning(f"reload 回调执行失败: {exc}")
        return True

    def _load_main_config_from_disk(self) -> Dict[str, Any]:
        """从磁盘重新加载所有配置文件, 返回扁平化的 _main_config。"""
        from src.modules.config.multi_file_loader import load_config_dir

        config_dir = Path(self.base_dir) / "config"
        multi_config, _drift = load_config_dir(config_dir)

        flattened: Dict[str, Any] = {}
        for category in ("core", "model", "input", "decision", "output"):
            section_data = multi_config.get(category, {})
            if isinstance(section_data, dict):
                for key, value in section_data.items():
                    flattened[key] = value
        return flattened

    async def _handle_reload(
        self,
        changes: Sequence["FileChange"],
    ) -> None:
        """FileWatcher 回调入口: 解析 scopes → 触发 reload_config。"""
        if not changes:
            return
        scopes = self._resolve_changed_scopes(changes)
        if not scopes:
            return
        await self.reload_config(changed_scopes=scopes)

    async def start_file_watcher(self) -> None:
        """启动 FileWatcher 监听 config/ 下所有 5 个 TOML 文件。

        幂等: 重复调用不会重启 watcher。
        """
        if self._file_watcher is not None and self._file_watcher.running:
            return

        from src.modules.config.file_watcher import FileWatcher

        config_dir = Path(self.base_dir) / "config"
        watched = [config_dir / name for name in _CONFIG_FILE_TO_SCOPE.keys()]

        self._file_watcher = FileWatcher(
            paths=watched,
            debounce_ms=600,
            callback_timeout_s=15.0,
            callback_failure_threshold=3,
            callback_cooldown_s=30.0,
        )
        self._file_watcher_subscription_id = self._file_watcher.subscribe(
            self._handle_reload,
            paths=watched,
        )
        await self._file_watcher.start()
        self.logger.info(f"ConfigService 文件监视器已启动 (paths={len(watched)})")

    async def stop_file_watcher(self) -> None:
        """停止 FileWatcher。未启动时调用是安全的 (no-op)。"""
        if self._file_watcher is None:
            return
        if self._file_watcher_subscription_id is not None:
            self._file_watcher.unsubscribe(self._file_watcher_subscription_id)
            self._file_watcher_subscription_id = None
        await self._file_watcher.stop()
        self._file_watcher = None
        self.logger.info("ConfigService 文件监视器已停止")

    def get_config_schema(
        self,
        type_or_class: Union[str, type],
    ) -> Dict[str, Any]:
        """根据配置类型返回完整 UI schema。

        Args:
            type_or_class: 配置类型标识 (``"core"`` / ``"model"``) 或
                ``CoreConfig`` / ``ModelConfig`` 类引用。

        Returns:
            ``ConfigSchemaGenerator.generate_config_schema`` 输出。

        Raises:
            ValueError: 未知的类型名。
            TypeError: 非字符串且非 BaseModel 子类。
        """
        from src.modules.config.core_schemas import CoreConfig
        from src.modules.config.model_schemas import ModelConfig
        from src.modules.config.schema_generator import ConfigSchemaGenerator

        type_map: Dict[str, type] = {
            "core": CoreConfig,
            "model": ModelConfig,
        }

        if isinstance(type_or_class, str):
            if type_or_class not in type_map:
                raise ValueError(f"未知的配置类型: {type_or_class!r}。支持: {sorted(type_map.keys())}")
            cls = type_map[type_or_class]
        elif isinstance(type_or_class, type) and issubclass(type_or_class, BaseModel):
            cls = type_or_class
        else:
            raise TypeError(f"type_or_class 必须是 str 或 BaseModel 子类, 收到: {type(type_or_class).__name__}")

        return ConfigSchemaGenerator.generate_config_schema(cls)

    def get_config_schema_for_section(self, section: str) -> Dict[str, Any]:
        """根据配置节名返回该节的 UI schema。

        Args:
            section: 节名 (例如 ``persona`` / ``llm`` / ``maicore`` /
                ``dashboard`` / ``context`` / ``meta`` / ``general`` /
                ``logging`` / ``llm_fast`` / ``vlm`` / ``llm_local``)。

        Returns:
            ``ConfigSchemaGenerator.generate_config_schema`` 输出。

        Raises:
            ValueError: 未知节名。
        """
        from src.modules.config.core_schemas import (
            ContextConfig,
            DashboardConfig,
            GeneralConfig,
            MaiCoreConfig,
            MetaConfig,
            PersonaConfig,
        )
        from src.modules.config.model_schemas import (
            FastLLMConfig,
            LLMConfig,
            LocalLLMConfig,
            VLMConfig,
        )
        from src.modules.config.schema_generator import ConfigSchemaGenerator
        from src.modules.config.schemas.logging import LoggingConfig

        section_map: Dict[str, type] = {
            # core 子节
            "meta": MetaConfig,
            "general": GeneralConfig,
            "persona": PersonaConfig,
            "maicore": MaiCoreConfig,
            "context": ContextConfig,
            "dashboard": DashboardConfig,
            "logging": LoggingConfig,
            # model 子节
            "llm": LLMConfig,
            "llm_fast": FastLLMConfig,
            "vlm": VLMConfig,
            "llm_local": LocalLLMConfig,
        }

        if section not in section_map:
            raise ValueError(f"未知的配置节: {section!r}。支持: {sorted(section_map.keys())}")
        return ConfigSchemaGenerator.generate_config_schema(section_map[section])


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
