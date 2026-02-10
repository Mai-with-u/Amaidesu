"""
Provider Registry - Provider 注册表

统一管理所有 Provider 的注册和创建，支持：
1. 内置 Provider 自动注册
2. 第三方插件动态注册自定义 Provider
3. Provider 自动发现和加载
4. 配置驱动的 Provider 注册

架构说明：
- 注册表位于 core（基础设施层），因为它是所有 Provider 的基础注册机制
- 三个域（input/decision/output）的 Provider 都注册到同一个注册表
- Provider 在模块导入时自动注册（通过 __init__.py）
- Manager 通过注册表创建 Provider 实例
- 配置驱动的注册：只加载配置中启用的 Provider
"""

import importlib
import inspect
from typing import Any, Dict, List, Optional, Type

from src.modules.logging import get_logger

# 导入 Provider 接口（延迟导入避免循环依赖）
# 实际类型检查在运行时进行
# BaseProviderConfig 延迟到需要时再导入，避免循环依赖


class ProviderRegistry:
    """
    Provider 注册表 - 统一管理所有 Provider

    设计原则：
    1. 内置 Provider 在模块加载时自动注册
    2. 第三方插件可以通过此接口注册自定义 Provider
    3. 配置驱动启用（enabled_inputs/available_providers/enabled_outputs）
    4. 注册表不负责 Provider 生命周期，仅负责注册和创建

    使用方式：
        # Provider 在 __init__.py 中自动注册
        from src.modules.registry import ProviderRegistry
        from .my_provider import MyProvider
        ProviderRegistry.register_input("my_provider", MyProvider, source="builtin:my_provider")

        # Manager 从注册表创建实例
        provider = ProviderRegistry.create_input("my_provider", config)
    """

    # 类级别的 Provider 注册表（跨所有域共享）
    _input_providers: Dict[str, Type] = {}
    _output_providers: Dict[str, Type] = {}
    _decision_providers: Dict[str, Type] = {}

    # 已注册的 Provider 来源（用于调试）
    _provider_sources: Dict[str, str] = {}

    # Config Schema 注册表（由 Provider 注册时自动填充）
    _config_schemas: Dict[str, Type] = {}

    _logger = get_logger("ProviderRegistry")

    @classmethod
    def register_input(cls, name: str, provider_class: Type, source: str = "unknown") -> None:
        """
        注册输入 Provider

        Args:
            name: Provider 名称（唯一标识符，如 "console_input", "bili_danmaku"）
            provider_class: Provider 类
            source: 注册来源（用于调试，如 "builtin:console_input" 或 "plugin:my_plugin"）

        Raises:
            TypeError: 如果不是有效的 Provider 子类
        """
        if not inspect.isclass(provider_class):
            raise TypeError(f"{name} must be a class, got {type(provider_class)}")

        # 延迟导入避免循环依赖
        from src.modules.types.base.input_provider import InputProvider

        if not issubclass(provider_class, InputProvider):
            raise TypeError(f"{name} must be a subclass of InputProvider, got {provider_class.__mro__}")

        if name in cls._input_providers:
            cls._logger.warning(
                f"InputProvider '{name}' already registered from "
                f"'{cls._provider_sources.get(name, 'unknown')}', overwriting with '{source}'"
            )

        cls._input_providers[name] = provider_class
        cls._provider_sources[name] = source
        cls._logger.debug(f"Registered InputProvider: {name} from {source}")

        # 自动提取并注册 ConfigSchema
        if hasattr(provider_class, "ConfigSchema"):
            schema_cls = provider_class.ConfigSchema
            # 延迟导入 BaseProviderConfig 避免循环依赖
            from src.modules.config.schemas.schemas.schemas.base import BaseProviderConfig

            try:
                if issubclass(schema_cls, BaseProviderConfig):
                    cls._config_schemas[name] = schema_cls
                    cls._logger.debug(f"自动注册 ConfigSchema: {name} -> {schema_cls.__name__}")
                else:
                    cls._logger.warning(f"Provider '{name}' 有 ConfigSchema 但不是 BaseProviderConfig 的子类")
            except TypeError:
                cls._logger.warning(f"Provider '{name}' 的 ConfigSchema 不是有效的类")

    @classmethod
    def register_output(cls, name: str, provider_class: Type, source: str = "unknown") -> None:
        """
        注册输出 Provider

        Args:
            name: Provider 名称（唯一标识符，如 "subtitle", "vts", "tts"）
            provider_class: Provider 类
            source: 注册来源（用于调试）

        Raises:
            TypeError: 如果不是有效的 Provider 子类
        """
        if not inspect.isclass(provider_class):
            raise TypeError(f"{name} must be a class, got {type(provider_class)}")

        from src.modules.types.base.output_provider import OutputProvider

        if not issubclass(provider_class, OutputProvider):
            raise TypeError(f"{name} must be a subclass of OutputProvider, got {provider_class.__mro__}")

        if name in cls._output_providers:
            cls._logger.warning(
                f"OutputProvider '{name}' already registered from "
                f"'{cls._provider_sources.get(name, 'unknown')}', overwriting with '{source}'"
            )

        cls._output_providers[name] = provider_class
        cls._provider_sources[name] = source
        cls._logger.debug(f"Registered OutputProvider: {name} from {source}")

        # 自动提取并注册 ConfigSchema
        if hasattr(provider_class, "ConfigSchema"):
            schema_cls = provider_class.ConfigSchema
            # 延迟导入 BaseProviderConfig 避免循环依赖
            from src.modules.config.schemas.schemas.schemas.base import BaseProviderConfig

            try:
                if issubclass(schema_cls, BaseProviderConfig):
                    cls._config_schemas[name] = schema_cls
                    cls._logger.debug(f"自动注册 ConfigSchema: {name} -> {schema_cls.__name__}")
                else:
                    cls._logger.warning(f"Provider '{name}' 有 ConfigSchema 但不是 BaseProviderConfig 的子类")
            except TypeError:
                cls._logger.warning(f"Provider '{name}' 的 ConfigSchema 不是有效的类")

    @classmethod
    def register_decision(cls, name: str, provider_class: Type, source: str = "unknown") -> None:
        """
        注册决策 Provider

        Args:
            name: Provider 名称（唯一标识符，如 "maicore", "local_llm"）
            provider_class: Provider 类
            source: 注册来源（用于调试）

        Raises:
            TypeError: 如果不是有效的 Provider 子类
        """
        if not inspect.isclass(provider_class):
            raise TypeError(f"{name} must be a class, got {type(provider_class)}")

        from src.modules.types.base.decision_provider import DecisionProvider

        if not issubclass(provider_class, DecisionProvider):
            raise TypeError(f"{name} must be a subclass of DecisionProvider, got {provider_class.__mro__}")

        if name in cls._decision_providers:
            cls._logger.warning(
                f"DecisionProvider '{name}' already registered from "
                f"'{cls._provider_sources.get(name, 'unknown')}', overwriting with '{source}'"
            )

        cls._decision_providers[name] = provider_class
        cls._provider_sources[name] = source
        cls._logger.debug(f"Registered DecisionProvider: {name} from {source}")

        # 自动提取并注册 ConfigSchema
        if hasattr(provider_class, "ConfigSchema"):
            schema_cls = provider_class.ConfigSchema
            # 延迟导入 BaseProviderConfig 避免循环依赖
            from src.modules.config.schemas.schemas.schemas.base import BaseProviderConfig

            try:
                if issubclass(schema_cls, BaseProviderConfig):
                    cls._config_schemas[name] = schema_cls
                    cls._logger.debug(f"自动注册 ConfigSchema: {name} -> {schema_cls.__name__}")
                else:
                    cls._logger.warning(f"Provider '{name}' 有 ConfigSchema 但不是 BaseProviderConfig 的子类")
            except TypeError:
                cls._logger.warning(f"Provider '{name}' 的 ConfigSchema 不是有效的类")

    @classmethod
    def create_input(cls, name: str, config: Dict[str, Any]):
        """
        创建输入 Provider 实例

        Args:
            name: Provider 名称
            config: Provider 配置（传递给 Provider.__init__）

        Returns:
            InputProvider 实例

        Raises:
            ValueError: 如果 Provider 未注册
        """
        if name not in cls._input_providers:
            available = ", ".join(cls._input_providers.keys())
            raise ValueError(f"Unknown input provider: '{name}'. Available providers: {available or 'none'}")

        provider_class = cls._input_providers[name]
        return provider_class(config)

    @classmethod
    def create_output(cls, name: str, config: Dict[str, Any]):
        """
        创建输出 Provider 实例

        Args:
            name: Provider 名称
            config: Provider 配置

        Returns:
            OutputProvider 实例

        Raises:
            ValueError: 如果 Provider 未注册
        """
        if name not in cls._output_providers:
            available = ", ".join(cls._output_providers.keys())
            raise ValueError(f"Unknown output provider: '{name}'. Available providers: {available or 'none'}")

        provider_class = cls._output_providers[name]
        return provider_class(config)

    @classmethod
    def create_decision(cls, name: str, config: Dict[str, Any]):
        """
        创建决策 Provider 实例

        Args:
            name: Provider 名称
            config: Provider 配置

        Returns:
            DecisionProvider 实例

        Raises:
            ValueError: 如果 Provider 未注册
        """
        if name not in cls._decision_providers:
            available = ", ".join(cls._decision_providers.keys())
            raise ValueError(f"Unknown decision provider: '{name}'. Available providers: {available or 'none'}")

        provider_class = cls._decision_providers[name]
        return provider_class(config)

    # ==================== 查询方法 ====================

    @classmethod
    def get_registered_input_providers(cls) -> List[str]:
        """获取所有已注册的输入 Provider 名称"""
        return list(cls._input_providers.keys())

    @classmethod
    def get_registered_output_providers(cls) -> List[str]:
        """获取所有已注册的输出 Provider 名称"""
        return list(cls._output_providers.keys())

    @classmethod
    def get_registered_decision_providers(cls) -> List[str]:
        """获取所有已注册的决策 Provider 名称"""
        return list(cls._decision_providers.keys())

    @classmethod
    def get_all_providers(cls) -> Dict[str, List[str]]:
        """获取所有已注册的 Provider（按域分类）"""
        return {
            "input": cls.get_registered_input_providers(),
            "decision": cls.get_registered_decision_providers(),
            "output": cls.get_registered_output_providers(),
        }

    @classmethod
    def is_input_provider_registered(cls, name: str) -> bool:
        """检查输入 Provider 是否已注册"""
        return name in cls._input_providers

    @classmethod
    def is_output_provider_registered(cls, name: str) -> bool:
        """检查输出 Provider 是否已注册"""
        return name in cls._output_providers

    @classmethod
    def is_decision_provider_registered(cls, name: str) -> bool:
        """检查决策 Provider 是否已注册"""
        return name in cls._decision_providers

    # ==================== 注销方法 ====================

    @classmethod
    def unregister_input(cls, name: str) -> bool:
        """
        注销输入 Provider

        Args:
            name: Provider 名称

        Returns:
            是否成功注销（如果不存在则返回 False）
        """
        if name in cls._input_providers:
            del cls._input_providers[name]
            cls._provider_sources.pop(name, None)
            cls._logger.debug(f"Unregistered InputProvider: {name}")
            return True
        return False

    @classmethod
    def unregister_output(cls, name: str) -> bool:
        """
        注销输出 Provider

        Args:
            name: Provider 名称

        Returns:
            是否成功注销
        """
        if name in cls._output_providers:
            del cls._output_providers[name]
            cls._provider_sources.pop(name, None)
            cls._logger.debug(f"Unregistered OutputProvider: {name}")
            return True
        return False

    @classmethod
    def unregister_decision(cls, name: str) -> bool:
        """
        注销决策 Provider

        Args:
            name: Provider 名称

        Returns:
            是否成功注销
        """
        if name in cls._decision_providers:
            del cls._decision_providers[name]
            cls._provider_sources.pop(name, None)
            cls._logger.debug(f"Unregistered DecisionProvider: {name}")
            return True
        return False

    @classmethod
    def clear_all(cls) -> None:
        """清除所有已注册的 Provider（主要用于测试）"""
        cls._input_providers.clear()
        cls._output_providers.clear()
        cls._decision_providers.clear()
        cls._provider_sources.clear()
        cls._config_schemas.clear()
        cls._logger.debug("Cleared all registered providers")

    # ==================== 调试方法 ====================

    @classmethod
    def get_registry_info(cls) -> Dict[str, Any]:
        """
        获取注册表信息（用于调试）

        Returns:
            包含所有注册信息的字典
        """
        return {
            "input_providers": {
                name: {
                    "class": cls._input_providers[name].__name__,
                    "module": cls._input_providers[name].__module__,
                    "source": cls._provider_sources.get(name, "unknown"),
                }
                for name in cls._input_providers
            },
            "output_providers": {
                name: {
                    "class": cls._output_providers[name].__name__,
                    "module": cls._output_providers[name].__module__,
                    "source": cls._provider_sources.get(name, "unknown"),
                }
                for name in cls._output_providers
            },
            "decision_providers": {
                name: {
                    "class": cls._decision_providers[name].__name__,
                    "module": cls._decision_providers[name].__module__,
                    "source": cls._provider_sources.get(name, "unknown"),
                }
                for name in cls._decision_providers
            },
        }

    @classmethod
    def print_registry_info(cls) -> None:
        """打印注册表信息（用于调试）"""
        info = cls.get_registry_info()
        import json

        cls._logger.debug(json.dumps(info, indent=2, ensure_ascii=False))

    # ==================== 配置驱动注册方法 ====================

    @classmethod
    def discover_and_register_providers(cls, config_service, config: Dict[str, Any]) -> Dict[str, int]:
        """
        从配置发现并注册 Provider

        只加载并注册配置中启用的 Provider，避免加载不必要的模块。

        Args:
            config_service: ConfigService 实例
            config: 配置字典（包含 providers 配置）

        Returns:
            注册统计字典：{"input": N, "decision": M, "output": K}

        Example:
            from src.modules.config.service import ConfigService
            config_service = ConfigService(base_dir=".")
            config, _, _, _ = config_service.initialize()
            stats = ProviderRegistry.discover_and_register_providers(config_service, config)
            cls._logger.debug(f"注册完成: {stats}")
        """
        cls._logger.info("开始配置驱动的 Provider 注册...")

        # 清空现有注册（避免重复注册）
        # 注意：这会清除之前通过 __init__.py 自动注册的 Provider
        # cls.clear_all()

        # 从配置字典读取 providers 配置
        providers_config = config.get("providers", {})
        input_config = providers_config.get("input", {})
        decision_config = providers_config.get("decision", {})
        output_config = providers_config.get("output", {})

        # 从各个域配置中读取启用的 Provider 列表
        enabled_inputs = input_config.get("enabled_inputs", [])
        available_decision = decision_config.get("available_providers", [])
        enabled_outputs = output_config.get("enabled_outputs", [])

        # 按域注册 Provider
        input_count = cls._register_provider_by_domain("input", enabled_inputs, config_service)
        decision_count = cls._register_provider_by_domain("decision", available_decision, config_service)
        output_count = cls._register_provider_by_domain("output", enabled_outputs, config_service)

        stats = {
            "input": input_count,
            "decision": decision_count,
            "output": output_count,
            "total": input_count + decision_count + output_count,
        }

        cls._logger.info(
            f"Provider 注册完成: Input={input_count}, "
            f"Decision={decision_count}, Output={output_count}, "
            f"Total={stats['total']}"
        )

        return stats

    @classmethod
    def _register_provider_by_domain(cls, domain: str, provider_names: List[str], config_service) -> int:
        """
        按域注册 Provider

        Args:
            domain: 域名称（input/decision/output）
            provider_names: Provider 名称列表
            config_service: ConfigService 实例（用于获取自定义模块路径）

        Returns:
            成功注册的 Provider 数量
        """
        registered_count = 0

        if not provider_names:
            cls._logger.debug(f"域 '{domain}' 没有启用的 Provider")
            return 0

        cls._logger.info(f"开始注册 {domain} 域的 Provider: {provider_names}")

        for provider_name in provider_names:
            try:
                # 获取 Provider 配置（检查是否有自定义模块路径）
                provider_config = config_service.get(
                    f"providers.{domain}.{provider_name}", default={}, section=f"providers.{domain}"
                )

                # 确定模块导入路径
                if "module_path" in provider_config:
                    # 使用自定义模块路径
                    module_path = provider_config["module_path"]
                    source = f"custom:{provider_name}"
                    cls._logger.debug(f"使用自定义模块路径: {provider_name} -> {module_path}")
                else:
                    # 使用内置 Provider 路径
                    module_path = f"src.domains.{domain}.providers.{provider_name}"
                    source = f"builtin:{provider_name}"

                # 动态导入 Provider 模块
                # 导入 __init__.py 会自动触发注册
                importlib.import_module(module_path)

                # 验证是否注册成功
                is_registered = False
                if domain == "input":
                    is_registered = cls.is_input_provider_registered(provider_name)
                elif domain == "decision":
                    is_registered = cls.is_decision_provider_registered(provider_name)
                elif domain == "output":
                    is_registered = cls.is_output_provider_registered(provider_name)

                if is_registered:
                    cls._logger.info(f"成功注册 {domain} Provider: {provider_name} (来源: {source})")
                    registered_count += 1
                else:
                    cls._logger.warning(
                        f"模块导入成功但 {domain} Provider 未注册: {provider_name} "
                        f"(请检查 __init__.py 是否调用了注册方法)"
                    )

            except ImportError as e:
                cls._logger.warning(f"导入 {domain} Provider 失败: {provider_name} - {e} (模块路径: {module_path})")
            except Exception as e:
                cls._logger.error(f"注册 {domain} Provider 时发生错误: {provider_name} - {e}", exc_info=True)

        cls._logger.info(f"{domain} 域注册完成: {registered_count}/{len(provider_names)} 个成功")
        return registered_count

    @classmethod
    def get_registration_stats(cls) -> Dict[str, int]:
        """
        获取注册统计信息

        Returns:
            统计字典：{
                "input": N,
                "decision": M,
                "output": K,
                "total": T
            }

        Example:
            stats = ProviderRegistry.get_registration_stats()
            cls._logger.debug(f"已注册 Provider: Input={stats['input']}, "
                             f"Decision={stats['decision']}, Output={stats['output']}")
        """
        input_count = len(cls._input_providers)
        decision_count = len(cls._decision_providers)
        output_count = len(cls._output_providers)

        return {
            "input": input_count,
            "decision": decision_count,
            "output": output_count,
            "total": input_count + decision_count + output_count,
        }

    @classmethod
    def get_config_schema(cls, name: str) -> Optional[Type]:
        """
        获取 Provider 的配置 Schema

        Args:
            name: Provider 名称

        Returns:
            ConfigSchema 类，如果 Provider 没有定义 Schema 则返回 None

        Example:
            schema = ProviderRegistry.get_config_schema("mock_danmaku")
            if schema:
                config_instance = schema(**config_dict)
        """
        return cls._config_schemas.get(name)

    # ==================== 显式注册方法 ====================

    @classmethod
    def register_from_info(cls, info: Dict[str, Any]) -> None:
        """
        从注册信息字典注册 Provider

        这是显式注册模式的核心方法，与 Provider.get_registration_info() 配合使用。
        避免了模块导入时的自动注册，使测试更容易隔离。

        Args:
            info: 注册信息字典（来自 Provider.get_registration_info()）
                - layer: "input", "decision", 或 "output"
                - name: Provider 名称
                - class: Provider 类
                - source: 注册来源（可选）

        Raises:
            ValueError: 如果注册信息无效
            TypeError: 如果 Provider 类型不匹配

        Example:
            # 在 main.py 中显式注册
            from src.domains.input.providers.console_input import ConsoleInputProvider

            info = ConsoleInputProvider.get_registration_info()
            ProviderRegistry.register_from_info(info)
        """
        # 验证必需字段
        required_fields = ["layer", "name", "class"]
        for field in required_fields:
            if field not in info:
                raise ValueError(f"注册信息缺少必需字段: {field}")

        layer = info["layer"]
        name = info["name"]
        provider_class = info["class"]
        source = info.get("source", "manual")

        # 根据域类型调用对应的注册方法
        if layer == "input":
            cls.register_input(name, provider_class, source=source)
        elif layer == "decision":
            cls.register_decision(name, provider_class, source=source)
        elif layer == "output":
            cls.register_output(name, provider_class, source=source)
        else:
            raise ValueError(f"无效的 Provider 域: {layer}. 必须是 'input', 'decision', 或 'output'")

    @classmethod
    def register_provider_class(cls, provider_class: Type) -> None:
        """
        直接从 Provider 类注册（便捷方法）

        自动调用 Provider.get_registration_info() 并注册。

        Args:
            provider_class: Provider 类

        Raises:
            NotImplementedError: 如果 Provider 未实现 get_registration_info()
            ValueError/TypeError: 如果注册信息无效

        Example:
            from src.domains.input.providers.console_input import ConsoleInputProvider
            ProviderRegistry.register_provider_class(ConsoleInputProvider)
        """
        if not hasattr(provider_class, "get_registration_info"):
            raise NotImplementedError(
                f"{provider_class.__name__} 未实现 get_registration_info() 类方法。"
                "请先在 Provider 类中实现此方法，或使用传统的 register_input/output/decision() 方法。"
            )

        info = provider_class.get_registration_info()
        cls.register_from_info(info)


# 便捷导入：从 src.core.provider_registry 导入 ProviderRegistry
__all__ = ["ProviderRegistry"]
