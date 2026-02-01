"""
DecisionManager - 决策层管理器

职责:
- 管理DecisionProvider生命周期
- 支持工厂模式创建Provider
- 支持运行时切换Provider
- 异常处理和优雅降级
"""

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.base.decision_provider import DecisionProvider
    from src.core.base.normalized_message import NormalizedMessage
    from src.layers.decision.intent import Intent
    from src.core.llm_service import LLMService


class DecisionProviderFactory:
    """
    DecisionProvider工厂

    职责:
    - 注册DecisionProvider类
    - 根据名称创建Provider实例
    """

    def __init__(self):
        self._providers: Dict[str, type] = {}
        self.logger = get_logger("DecisionProviderFactory")

    def register(self, name: str, provider_class: type):
        """
        注册DecisionProvider类

        Args:
            name: Provider名称
            provider_class: Provider类
        """
        if name in self._providers:
            self.logger.warning(f"Provider '{name}' 已被注册，将被覆盖")
        self._providers[name] = provider_class
        self.logger.info(f"Provider '{name}' 已注册: {provider_class.__name__}")

    def create(self, name: str, config: dict) -> "DecisionProvider":
        """
        创建DecisionProvider实例

        Args:
            name: Provider名称
            config: Provider配置

        Returns:
            DecisionProvider实例

        Raises:
            ValueError: 如果Provider未找到
        """
        provider_class = self._providers.get(name)
        if not provider_class:
            available = ", ".join(self._providers.keys())
            raise ValueError(f"DecisionProvider '{name}' 未找到。可用: {available}")

        self.logger.debug(f"创建DecisionProvider '{name}': {provider_class.__name__}")
        return provider_class(config)

    def list_providers(self) -> list[str]:
        """
        列出所有已注册的Provider

        Returns:
            Provider名称列表
        """
        return list(self._providers.keys())


class DecisionManager:
    """
    决策管理器(Layer 3: 决策层)

    职责:
    - 管理DecisionProvider生命周期
    - 支持工厂模式创建Provider
    - 支持运行时切换Provider
    - 异常处理和优雅降级

    使用示例:
        ```python
        # 初始化
        manager = DecisionManager(event_bus)

        # 注册Provider
        factory = DecisionProviderFactory()
        factory.register("maicore", MaiCoreDecisionProvider)
        factory.register("local_llm", LocalLLMDecisionProvider)
        manager.set_factory(factory)

        # 设置当前Provider
        await manager.setup(provider_name="maicore", config={"host": "localhost", "port": 8000})

        # 进行决策
        normalized_message = NormalizedMessage(...)
        result = await manager.decide(normalized_message)

        # 运行时切换Provider
        await manager.switch_provider(provider_name="local_llm", config={"model": "gpt-4", "api_key": "..."})

        # 清理
        await manager.cleanup()
        ```
    """

    def __init__(self, event_bus: "EventBus", llm_service: Optional["LLMService"] = None):
        """
        初始化DecisionManager

        Args:
            event_bus: EventBus实例
            llm_service: 可选的LLMService实例，将作为依赖注入到DecisionProvider
        """
        self.event_bus = event_bus
        self._llm_service = llm_service
        self.logger = get_logger("DecisionManager")
        self._factory: Optional[DecisionProviderFactory] = None
        self._current_provider: Optional["DecisionProvider"] = None
        self._provider_name: Optional[str] = None
        self._switch_lock = asyncio.Lock()

    def set_factory(self, factory: DecisionProviderFactory):
        """
        设置Provider工厂

        Args:
            factory: DecisionProviderFactory实例
        """
        self._factory = factory
        self.logger.info(f"DecisionProviderFactory 已设置: {len(factory.list_providers())} 个Provider")

    async def setup(self, provider_name: str, config: Dict[str, Any]) -> None:
        """
        设置决策Provider

        Args:
            provider_name: Provider名称
            config: Provider配置

        Raises:
            ValueError: 如果Factory未设置或Provider未找到
            ConnectionError: 如果Provider初始化失败
        """
        if not self._factory:
            raise ValueError("DecisionProviderFactory 未设置，请先调用 set_factory()")

        async with self._switch_lock:
            # 清理当前Provider
            if self._current_provider:
                self.logger.info(f"清理当前Provider: {self._provider_name}")
                try:
                    await self._current_provider.cleanup()
                except Exception as e:
                    self.logger.error(f"清理Provider失败: {e}", exc_info=True)

            # 创建新Provider
            provider_class = self._factory._providers.get(provider_name)
            if not provider_class:
                available = ", ".join(self._factory.list_providers())
                raise ValueError(f"DecisionProvider '{provider_name}' 未找到。可用: {available}")

            self.logger.info(f"创建DecisionProvider: {provider_name}")
            self._current_provider = provider_class(config)
            self._provider_name = provider_name

            # 初始化Provider
            try:
                # 准备依赖注入
                dependencies = {"llm_service": self._llm_service} if self._llm_service else {}
                await self._current_provider.setup(self.event_bus, config, dependencies)
                self.logger.info(f"DecisionProvider '{provider_name}' 初始化成功")
            except Exception as e:
                self.logger.error(f"DecisionProvider '{provider_name}' 初始化失败: {e}", exc_info=True)
                self._current_provider = None
                self._provider_name = None
                raise ConnectionError(f"无法初始化DecisionProvider '{provider_name}': {e}") from e

        # 订阅 normalization.message_ready 事件
        self.event_bus.on("normalization.message_ready", self._on_normalized_message_ready)
        self.logger.info("DecisionManager 已订阅 normalization.message_ready 事件")

    async def decide(self, normalized_message: "NormalizedMessage") -> "Intent":
        """
        进行决策

        Args:
            normalized_message: 标准化消息

        Returns:
            Intent: 决策意图

        Raises:
            RuntimeError: 如果当前Provider未设置
        """
        if not self._current_provider:
            raise RuntimeError("当前未设置DecisionProvider，请先调用 setup()")

        self.logger.debug(
            f"进行决策 (Provider: {self._provider_name}, Text: {normalized_message.text[:50]})"
        )
        try:
            result = await self._current_provider.decide(normalized_message)
            self.logger.debug(f"决策完成: {result}")
            return result
        except Exception as e:
            self.logger.error(f"决策失败: {e}", exc_info=True)
            # 优雅降级: 抛出异常让上层处理
            raise

    async def _on_normalized_message_ready(
        self, event_name: str, event_data: dict, source: str
    ):
        """
        处理 normalization.message_ready 事件

        当 InputLayer 生成 NormalizedMessage 时，自动调用当前活动的 DecisionProvider 进行决策，
        并发布 decision.intent_generated 事件（5层架构）。

        Args:
            event_name: 事件名称
            event_data: 事件数据，包含 "message" (NormalizedMessage) 和 "source"
            source: 事件源
        """
        normalized: "NormalizedMessage" = event_data.get("message")
        if not normalized:
            self.logger.warning("收到空的 NormalizedMessage 事件")
            return

        try:
            self.logger.debug(f"收到 NormalizedMessage: {normalized.text[:50]}...")
            # 调用当前活动的 Provider 进行决策
            intent = await self.decide(normalized)

            # 发布 decision.intent_generated 事件（5层架构）
            await self.event_bus.emit(
                "decision.intent_generated",
                {
                    "intent": intent,
                    "original_message": normalized,
                    "provider": self._provider_name,
                },
                source="DecisionManager",
            )
            self.logger.debug(f"已发布 decision.intent_generated 事件: {intent.response_text[:50]}...")
        except Exception as e:
            self.logger.error(f"处理 NormalizedMessage 时出错: {e}", exc_info=True)

    async def switch_provider(self, provider_name: str, config: Dict[str, Any]) -> None:
        """
        切换决策Provider(运行时)

        Args:
            provider_name: 新Provider名称
            config: 新Provider配置

        注意:
            - 切换时会先清理旧Provider
            - 如果新Provider初始化失败，会回退到旧Provider
        """
        async with self._switch_lock:
            old_provider = self._current_provider
            old_name = self._provider_name

            self.logger.info(f"切换Provider: {old_name} -> {provider_name}")

            try:
                # 初始化新Provider
                await self.setup(provider_name, config)
                self.logger.info(f"Provider切换成功: {old_name} -> {provider_name}")
            except Exception as e:
                self.logger.error(f"Provider切换失败，回退到旧Provider: {e}", exc_info=True)
                # 回退到旧Provider
                self._current_provider = old_provider
                self._provider_name = old_name
                raise

    async def cleanup(self) -> None:
        """
        清理资源

        清理当前Provider、取消事件订阅和工厂。
        """
        # 取消事件订阅
        self.event_bus.off("normalization.message_ready", self._on_normalized_message_ready)

        async with self._switch_lock:
            if self._current_provider:
                self.logger.info(f"清理当前Provider: {self._provider_name}")
                try:
                    await self._current_provider.cleanup()
                except Exception as e:
                    self.logger.error(f"清理Provider失败: {e}", exc_info=True)
                self._current_provider = None
                self._provider_name = None

            self._factory = None
            self.logger.info("DecisionManager 已清理")

    def get_current_provider(self) -> Optional["DecisionProvider"]:
        """
        获取当前Provider实例

        Returns:
            当前Provider实例，如果未设置则返回None
        """
        return self._current_provider

    def get_current_provider_name(self) -> Optional[str]:
        """
        获取当前Provider名称

        Returns:
            当前Provider名称，如果未设置则返回None
        """
        return self._provider_name

    def get_available_providers(self) -> list[str]:
        """
        获取所有可用的Provider名称

        Returns:
            Provider名称列表
        """
        if not self._factory:
            return []
        return self._factory.list_providers()
