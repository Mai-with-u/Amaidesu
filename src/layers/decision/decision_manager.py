"""
DecisionManager - 决策层管理器

职责:
- 管理DecisionProvider生命周期
- 支持通过ProviderRegistry创建Provider
- 支持运行时切换Provider
- 异常处理和优雅降级
"""

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from src.utils.logger import get_logger
from src.core.events.names import CoreEvents

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.base.decision_provider import DecisionProvider
    from src.core.base.normalized_message import NormalizedMessage
    from src.layers.decision.intent import Intent
    from src.core.llm_service import LLMService


class DecisionManager:
    """
    决策管理器(Layer 3: 决策层)

    职责:
    - 管理DecisionProvider生命周期
    - 通过ProviderRegistry创建Provider
    - 支持运行时切换Provider
    - 异常处理和优雅降级

    使用示例:
        ```python
        # 初始化
        manager = DecisionManager(event_bus, llm_service)

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

    注意：
        - DecisionProvider 应预先在各个 Provider 模块的 __init__.py 中注册到 ProviderRegistry
        - ProviderRegistry 统一管理所有类型的 Provider（Input/Decision/Output）
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
        self._current_provider: Optional["DecisionProvider"] = None
        self._provider_name: Optional[str] = None
        self._switch_lock = asyncio.Lock()

    async def setup(self, provider_name: str, config: Dict[str, Any]) -> None:
        """
        设置决策Provider

        Args:
            provider_name: Provider名称
            config: Provider配置

        Raises:
            ValueError: 如果Provider未注册到ProviderRegistry
            ConnectionError: 如果Provider初始化失败
        """
        from src.layers.rendering.provider_registry import ProviderRegistry

        async with self._switch_lock:
            # 清理当前Provider
            if self._current_provider:
                self.logger.info(f"清理当前Provider: {self._provider_name}")
                try:
                    await self._current_provider.cleanup()
                except Exception as e:
                    self.logger.error(f"清理Provider失败: {e}", exc_info=True)

            # 通过 ProviderRegistry 创建新Provider
            try:
                self.logger.info(f"通过 ProviderRegistry 创建 DecisionProvider: {provider_name}")
                self._current_provider = ProviderRegistry.create_decision(provider_name, config)
                self._provider_name = provider_name
            except ValueError as e:
                available = ", ".join(ProviderRegistry.get_registered_decision_providers())
                self.logger.error(f"DecisionProvider '{provider_name}' 未找到。可用: {available}")
                raise ValueError(f"DecisionProvider '{provider_name}' 未找到。可用: {available}") from e

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
        self.event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY, self._on_normalized_message_ready)
        self.logger.info(f"DecisionManager 已订阅 '{CoreEvents.NORMALIZATION_MESSAGE_READY}' 事件")

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
            event_name: 事件名称 (CoreEvents.NORMALIZATION_MESSAGE_READY)
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
        from src.layers.rendering.provider_registry import ProviderRegistry

        old_provider = self._current_provider
        old_name = self._provider_name

        self.logger.info(f"切换Provider: {old_name} -> {provider_name}")

        # 使用锁保护切换过程
        async with self._switch_lock:
            try:
                # 通过 ProviderRegistry 创建新Provider
                try:
                    self.logger.info(f"通过 ProviderRegistry 创建 DecisionProvider: {provider_name}")
                    new_provider = ProviderRegistry.create_decision(provider_name, config)
                except ValueError as e:
                    available = ", ".join(ProviderRegistry.get_registered_decision_providers())
                    self.logger.error(f"DecisionProvider '{provider_name}' 未找到。可用: {available}")
                    # 保留原始错误消息格式以兼容测试
                    raise ValueError(f"DecisionProvider '{provider_name}' 未找到") from e

                # 设置新Provider
                try:
                    await new_provider.setup(self.event_bus, config, {})
                    self.logger.info(f"DecisionProvider '{provider_name}' 初始化成功")
                except Exception as e:
                    self.logger.error(f"DecisionProvider '{provider_name}' setup 失败: {e}", exc_info=True)
                    raise ConnectionError(f"无法初始化DecisionProvider '{provider_name}': {e}") from e

                # 新Provider设置成功后，清理旧Provider
                if old_provider:
                    self.logger.info(f"清理旧Provider: {old_name}")
                    try:
                        await old_provider.cleanup()
                    except Exception as e:
                        self.logger.error(f"清理旧Provider失败: {e}", exc_info=True)

                # 更新当前Provider
                self._current_provider = new_provider
                self._provider_name = provider_name

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

        清理当前Provider和取消事件订阅。
        """
        # 取消事件订阅（如果已订阅）
        if CoreEvents.NORMALIZATION_MESSAGE_READY in self.event_bus._handlers:
            self.event_bus.off(CoreEvents.NORMALIZATION_MESSAGE_READY, self._on_normalized_message_ready)

        async with self._switch_lock:
            if self._current_provider:
                self.logger.info(f"清理当前Provider: {self._provider_name}")
                try:
                    await self._current_provider.cleanup()
                except Exception as e:
                    self.logger.error(f"清理Provider失败: {e}", exc_info=True)
                self._current_provider = None
                self._provider_name = None

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
        from src.layers.rendering.provider_registry import ProviderRegistry
        return ProviderRegistry.get_registered_decision_providers()
