"""
ProviderManager 公共接口定义

定义了 Input/Decision/Output 三种 ProviderManager 的公共接口 Protocol。
"""

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from src.modules.types.base.input_provider import InputProvider
    from src.modules.types.base.output_provider import OutputProvider


class BaseProviderManager(Protocol):
    """
    ProviderManager 基础接口 Protocol

    定义了所有 ProviderManager 的公共方法。
    具体的 Manager 类可以选择实现这些接口。

    注意：由于三种 Manager 的生命周期不完全一致，
    此 Protocol 定义了最常用的公共方法。
    """

    async def load_from_config(self, config: dict[str, Any], config_service=None) -> list:
        """
        从配置加载 Provider

        Args:
            config: Provider 配置
            config_service: 可选的 ConfigService 实例

        Returns:
            创建的 Provider 列表
        """
        ...

    async def setup(self, *args, **kwargs) -> None:
        """
        设置 Manager 和所有 Provider
        """
        ...

    async def start(self) -> None:
        """
        启动 Manager 和所有 Provider
        """
        ...

    async def stop(self) -> None:
        """
        停止 Manager 和所有 Provider
        """
        ...

    async def cleanup(self) -> None:
        """
        清理资源
        """
        ...


class InputProviderManagerProtocol(BaseProviderManager):
    """
    InputProviderManager 接口

    定义了 InputProviderManager 特有的方法。
    """

    async def start_all_providers(self, providers: list["InputProvider"]) -> None:
        """
        并发启动所有 InputProvider

        Args:
            providers: InputProvider 列表
        """
        ...

    async def stop_all_providers(self) -> None:
        """
        优雅停止所有 InputProvider
        """
        ...


class DecisionProviderManagerProtocol(BaseProviderManager):
    """
    DecisionProviderManager 接口

    定义了 DecisionProviderManager 特有的方法。
    """

    async def decide(self, normalized_message) -> Any:
        """
        进行决策

        Args:
            normalized_message: NormalizedMessage

        Returns:
            Intent
        """
        ...

    async def switch_provider(self, provider_name: str, config: dict[str, Any]) -> None:
        """
        切换决策 Provider

        Args:
            provider_name: 新 Provider 名称
            config: 新 Provider 配置
        """
        ...


class OutputProviderManagerProtocol(BaseProviderManager):
    """
    OutputProviderManager 接口

    定义了 OutputProviderManager 特有的方法。
    """

    async def register_provider(self, provider: "OutputProvider") -> None:
        """
        注册 Provider

        Args:
            provider: OutputProvider 实例
        """
        ...

    async def setup_all_providers(self, event_bus, audio_stream_channel=None) -> None:
        """
        启动所有 Provider

        Args:
            event_bus: EventBus 实例
            audio_stream_channel: 可选的 AudioStreamChannel 实例
        """
        ...

    async def stop_all_providers(self) -> None:
        """
        停止所有 Provider
        """
        ...
