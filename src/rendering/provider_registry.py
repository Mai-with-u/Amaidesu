"""
Provider Registry - Provider 注册表

统一管理所有 Provider 的注册和创建，支持：
1. 内置 Provider 自动注册
2. 第三方插件动态注册自定义 Provider
3. Provider 自动发现和加载
"""

from typing import Dict, Type, Any, List
import inspect
from src.utils.logger import get_logger

# 导入 Provider 接口
from src.core.providers.input_provider import InputProvider
from src.core.providers.output_provider import OutputProvider
from src.core.providers.decision_provider import DecisionProvider


class ProviderRegistry:
    """
    Provider 注册表 - 统一管理所有 Provider

    内置 Provider 在模块加载时自动注册
    第三方插件可以通过此接口注册自定义 Provider
    """

    # 类级别的 Provider 注册表
    _input_providers: Dict[str, Type[InputProvider]] = {}
    _output_providers: Dict[str, Type[OutputProvider]] = {}
    _decision_providers: Dict[str, Type[DecisionProvider]] = {}

    # 已注册的 Provider 来源（用于调试）
    _provider_sources: Dict[str, str] = {}

    _logger = get_logger("ProviderRegistry")

    @classmethod
    def register_input(
        cls,
        name: str,
        provider_class: Type[InputProvider],
        source: str = "unknown"
    ) -> None:
        """
        注册输入 Provider

        Args:
            name: Provider 名称（唯一标识符）
            provider_class: Provider 类
            source: 注册来源（用于调试，如 "builtin:tts" 或 "plugin:my_plugin"）

        Raises:
            ValueError: 如果名称已存在
            TypeError: 如果不是 InputProvider 的子类
        """
        if not inspect.isclass(provider_class):
            raise TypeError(f"{name} must be a class, got {type(provider_class)}")

        if not issubclass(provider_class, InputProvider):
            raise TypeError(
                f"{name} must be a subclass of InputProvider, "
                f"got {provider_class.__mro__}"
            )

        if name in cls._input_providers:
            cls._logger.warning(
                f"InputProvider '{name}' already registered from "
                f"'{cls._provider_sources.get(name, 'unknown')}', overwriting with '{source}'"
            )

        cls._input_providers[name] = provider_class
        cls._provider_sources[name] = source
        cls._logger.debug(f"Registered InputProvider: {name} from {source}")

    @classmethod
    def register_output(
        cls,
        name: str,
        provider_class: Type[OutputProvider],
        source: str = "unknown"
    ) -> None:
        """
        注册输出 Provider

        Args:
            name: Provider 名称（唯一标识符）
            provider_class: Provider 类
            source: 注册来源（用于调试）

        Raises:
            ValueError: 如果名称已存在
            TypeError: 如果不是 OutputProvider 的子类
        """
        if not inspect.isclass(provider_class):
            raise TypeError(f"{name} must be a class, got {type(provider_class)}")

        if not issubclass(provider_class, OutputProvider):
            raise TypeError(
                f"{name} must be a subclass of OutputProvider, "
                f"got {provider_class.__mro__}"
            )

        if name in cls._output_providers:
            cls._logger.warning(
                f"OutputProvider '{name}' already registered from "
                f"'{cls._provider_sources.get(name, 'unknown')}', overwriting with '{source}'"
            )

        cls._output_providers[name] = provider_class
        cls._provider_sources[name] = source
        cls._logger.debug(f"Registered OutputProvider: {name} from {source}")

    @classmethod
    def register_decision(
        cls,
        name: str,
        provider_class: Type[DecisionProvider],
        source: str = "unknown"
    ) -> None:
        """
        注册决策 Provider

        Args:
            name: Provider 名称（唯一标识符）
            provider_class: Provider 类
            source: 注册来源（用于调试）

        Raises:
            ValueError: 如果名称已存在
            TypeError: 如果不是 DecisionProvider 的子类
        """
        if not inspect.isclass(provider_class):
            raise TypeError(f"{name} must be a class, got {type(provider_class)}")

        if not issubclass(provider_class, DecisionProvider):
            raise TypeError(
                f"{name} must be a subclass of DecisionProvider, "
                f"got {provider_class.__mro__}"
            )

        if name in cls._decision_providers:
            cls._logger.warning(
                f"DecisionProvider '{name}' already registered from "
                f"'{cls._provider_sources.get(name, 'unknown')}', overwriting with '{source}'"
            )

        cls._decision_providers[name] = provider_class
        cls._provider_sources[name] = source
        cls._logger.debug(f"Registered DecisionProvider: {name} from {source}")

    @classmethod
    def create_input(cls, name: str, config: Dict[str, Any]) -> InputProvider:
        """
        创建输入 Provider 实例

        Args:
            name: Provider 名称
            config: Provider 配置

        Returns:
            InputProvider 实例

        Raises:
            ValueError: 如果 Provider 未注册
        """
        if name not in cls._input_providers:
            available = ", ".join(cls._input_providers.keys())
            raise ValueError(
                f"Unknown input provider: '{name}'. "
                f"Available providers: {available or 'none'}"
            )

        provider_class = cls._input_providers[name]
        return provider_class(config)

    @classmethod
    def create_output(cls, name: str, config: Dict[str, Any]) -> OutputProvider:
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
            raise ValueError(
                f"Unknown output provider: '{name}'. "
                f"Available providers: {available or 'none'}"
            )

        provider_class = cls._output_providers[name]
        return provider_class(config)

    @classmethod
    def create_decision(cls, name: str, config: Dict[str, Any]) -> DecisionProvider:
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
            raise ValueError(
                f"Unknown decision provider: '{name}'. "
                f"Available providers: {available or 'none'}"
            )

        provider_class = cls._decision_providers[name]
        return provider_class(config)

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
            是否成功注销（如果不存在则返回 False）
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
            是否成功注销（如果不存在则返回 False）
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
        cls._logger.debug("Cleared all registered providers")

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
                    "source": cls._provider_sources.get(name, "unknown"),
                }
                for name in cls._input_providers
            },
            "output_providers": {
                name: {
                    "class": cls._output_providers[name].__name__,
                    "source": cls._provider_sources.get(name, "unknown"),
                }
                for name in cls._output_providers
            },
            "decision_providers": {
                name: {
                    "class": cls._decision_providers[name].__name__,
                    "source": cls._provider_sources.get(name, "unknown"),
                }
                for name in cls._decision_providers
            },
        }
