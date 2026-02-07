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

from src.core.utils.logger import get_logger
from src.core.events.names import CoreEvents

# 导入所有 DecisionProvider 以确保它们注册到 ProviderRegistry
# noqa: F401 - 这些导入的副作用（注册）是必需的
from src.domains.decision.providers import (  # noqa: F401
    MaiCoreDecisionProvider,
    LocalLLMDecisionProvider,
    RuleEngineDecisionProvider,
    EmotionJudgeDecisionProvider,
    MockDecisionProvider,
)

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.base.decision_provider import DecisionProvider
    from src.core.base.normalized_message import NormalizedMessage
    from src.domains.decision.intent import Intent
    from src.services.llm.service import LLMService


class DecisionManager:
    """
    决策管理器 (Decision Domain: 决策域)

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

    async def setup(self, provider_name: Optional[str] = None, config: Optional[Dict[str, Any]] = None, decision_config: Optional[Dict[str, Any]] = None) -> None:
        """
        设置决策Provider

        Args:
            provider_name: Provider名称（如果为None，则从decision_config中读取active_provider）
            config: Provider配置（如果为None，则从decision_config中构建）
            decision_config: 完整的决策层配置（包含active_provider、available_providers等）

        Raises:
            ValueError: 如果Provider未注册到ProviderRegistry
            ConnectionError: 如果Provider初始化失败
        """
        # 确保所有 Provider 已注册
        await self.ensure_providers_registered()

        from src.core.provider_registry import ProviderRegistry

        # 确定配置来源
        if decision_config is None:
            decision_config = config or {}

        # 确定Provider名称
        if provider_name is None:
            provider_name = decision_config.get("active_provider", "maicore")

        # 获取可用Provider列表
        available_providers = decision_config.get("available_providers", [])

        # 检查Provider是否在可用列表中
        if available_providers and provider_name not in available_providers:
            self.logger.warning(
                f"Provider '{provider_name}' 不在可用列表中。可用: {available_providers}。继续尝试初始化。"
            )

        # 获取Provider特定配置
        provider_config = config or decision_config.get(provider_name, {})

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
                self._current_provider = ProviderRegistry.create_decision(provider_name, provider_config)
                self._provider_name = provider_name
            except ValueError as e:
                available = ", ".join(ProviderRegistry.get_registered_decision_providers())
                self.logger.error(f"DecisionProvider '{provider_name}' 未找到。可用: {available}")
                raise ValueError(f"DecisionProvider '{provider_name}' 未找到。可用: {available}") from e

            # 初始化Provider
            try:
                # 准备依赖注入
                dependencies = {"llm_service": self._llm_service} if self._llm_service else {}
                await self._current_provider.setup(self.event_bus, provider_config, dependencies)
                self.logger.info(f"DecisionProvider '{provider_name}' 初始化成功")

                # 发布Provider连接事件（使用emit_typed）
                from src.core.events.payloads.decision import ProviderConnectedPayload

                await self.event_bus.emit_typed(
                    CoreEvents.DECISION_PROVIDER_CONNECTED,
                    ProviderConnectedPayload(
                        provider=provider_name,
                        endpoint=provider_config.get("host") or provider_config.get("ws_url"),
                        metadata={"config": provider_config},
                    ),
                    source="DecisionManager",
                )
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

        self.logger.debug(f"进行决策 (Provider: {self._provider_name}, Text: {normalized_message.text[:50]})")
        try:
            result = await self._current_provider.decide(normalized_message)
            self.logger.debug(f"决策完成: {result}")
            return result
        except Exception as e:
            self.logger.error(f"决策失败: {e}", exc_info=True)
            # 优雅降级: 抛出异常让上层处理
            raise

    async def _on_normalized_message_ready(self, event_name: str, event_data: dict, source: str):
        """
        处理 normalization.message_ready 事件

        当 InputLayer 生成 NormalizedMessage 时，自动调用当前活动的 DecisionProvider 进行决策，
        并发布 decision.intent_generated 事件（3域架构）。

        Args:
            event_name: 事件名称 (CoreEvents.NORMALIZATION_MESSAGE_READY)
            event_data: 事件数据（MessageReadyPayload 序列化后的字典）
            source: 事件源
        """
        # 从 MessageReadyPayload 中提取 message 字段（字典格式）
        message_dict = event_data.get("message")
        if not message_dict:
            self.logger.warning("收到空的 NormalizedMessage 事件")
            return

        # 如果是 NormalizedMessage 对象，直接使用
        # 如果是字典，需要重新构造 NormalizedMessage
        if isinstance(message_dict, dict):
            # 从字典重建 NormalizedMessage（简化版本）
            # 注意：这里我们使用字典中的 text 字段作为主要数据
            # 完整的重建需要处理 content 对象的序列化问题
            from src.core.base.normalized_message import NormalizedMessage
            from src.domains.input.normalization.content import TextContent

            # 创建一个简化的 NormalizedMessage
            normalized = NormalizedMessage(
                text=message_dict.get("text", ""),
                content=TextContent(text=message_dict.get("text", "")),
                source=message_dict.get("source", event_data.get("source", "unknown")),
                data_type=message_dict.get("data_type", "text"),
                importance=message_dict.get("importance", 0.5),
                metadata=message_dict.get("metadata", {}),
                timestamp=message_dict.get("timestamp", 0.0),
            )
        else:
            normalized = message_dict

        try:
            self.logger.debug(f"收到 NormalizedMessage: {normalized.text[:50]}...")
            # 调用当前活动的 Provider 进行决策
            intent = await self.decide(normalized)

            # 发布 decision.intent_generated 事件（3域架构，使用emit_typed）
            from src.core.events.payloads import IntentPayload
            from src.core.events.names import CoreEvents

            await self.event_bus.emit_typed(
                CoreEvents.DECISION_INTENT_GENERATED,
                IntentPayload.from_intent(intent, self._provider_name or "unknown"),
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
        from src.core.provider_registry import ProviderRegistry

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

                # 发布新Provider连接事件（使用emit_typed）
                try:
                    from src.core.events.payloads.decision import ProviderConnectedPayload

                    await self.event_bus.emit_typed(
                        CoreEvents.DECISION_PROVIDER_CONNECTED,
                        ProviderConnectedPayload(
                            provider=provider_name,
                            metadata={"previous_provider": old_name, "switched": True},
                        ),
                        source="DecisionManager",
                    )
                except Exception as e:
                    self.logger.warning(f"发布Provider连接事件失败: {e}")

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

        provider_name = self._provider_name

        async with self._switch_lock:
            if self._current_provider:
                self.logger.info(f"清理当前Provider: {provider_name}")
                try:
                    await self._current_provider.cleanup()
                except Exception as e:
                    self.logger.error(f"清理Provider失败: {e}", exc_info=True)
                self._current_provider = None
                self._provider_name = None

                # 发布Provider断开事件
                try:
                    from src.core.events.payloads import ProviderDisconnectedPayload

                    await self.event_bus.emit(
                        CoreEvents.DECISION_PROVIDER_DISCONNECTED,
                        ProviderDisconnectedPayload(
                            provider=provider_name,
                            reason="cleanup",
                            will_retry=False,
                        ).model_dump(),
                        source="DecisionManager",
                    )
                except Exception as e:
                    self.logger.warning(f"发布Provider断开事件失败: {e}")

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
        from src.core.provider_registry import ProviderRegistry

        return ProviderRegistry.get_registered_decision_providers()

    async def ensure_providers_registered(self) -> None:
        """
        确保所有 DecisionProvider 已注册

        Provider 在模块导入时已自动注册（通过 __init__.py）。
        此方法仅用于验证。
        """
        from src.core.provider_registry import ProviderRegistry

        # 导入已在模块级别完成，这里仅验证
        registered = ProviderRegistry.get_registered_decision_providers()
        self.logger.debug(f"已注册的 DecisionProvider: {registered}")

    async def set_active_provider(self, provider_name: str, config: Optional[Dict[str, Any]] = None) -> None:
        """
        运行时切换活跃的DecisionProvider（便捷方法）

        这是 switch_provider 的别名，提供更直观的命名。

        Args:
            provider_name: 新Provider名称
            config: 可选的Provider配置

        Raises:
            ValueError: 如果Provider未注册
            ConnectionError: 如果Provider初始化失败
        """
        if config is None:
            config = {}
        await self.switch_provider(provider_name, config)

    def get_active_provider(self) -> Optional["DecisionProvider"]:
        """
        获取当前活跃的Provider实例（便捷方法）

        Returns:
            当前Provider实例，如果未设置则返回None
        """
        return self._current_provider

    async def make_decision(self, normalized_message: "NormalizedMessage") -> "Intent":
        """
        使用当前活跃Provider进行决策（便捷方法）

        这是 decide 的别名，提供更直观的命名。

        Args:
            normalized_message: 标准化消息

        Returns:
            Intent: 决策意图

        Raises:
            RuntimeError: 如果当前Provider未设置
        """
        return await self.decide(normalized_message)
