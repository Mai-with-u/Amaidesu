"""
DecisionProviderManager - 决策Provider管理器（合并自原 DecisionCoordinator）

职责:
- 管理DecisionProvider生命周期
- 支持通过ProviderRegistry创建Provider
- 支持运行时切换Provider
- 提供decide()方法进行决策
- 订阅 Input Domain 的 DATA_MESSAGE 事件
- 构建 SourceContext
- 发布 DECISION_INTENT 事件到 Output Domain
- 异常处理和优雅降级

架构约束（3域架构）:
- 只订阅 Input Domain 的事件（data.message）
- 只发布到 Output Domain（decision.intent）
- 不订阅 Output Domain 的事件（避免循环依赖）
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.types import Intent, SourceContext
    from src.modules.config.service import ConfigService
    from src.modules.context.service import ContextService
    from src.modules.events.event_bus import EventBus
    from src.modules.events.payloads.input import MessageReadyPayload
    from src.modules.llm.manager import LLMManager
    from src.modules.types.base.decision_provider import DecisionProvider
    from src.modules.types.base.normalized_message import NormalizedMessage


class DecisionProviderManager:
    """
    决策Provider管理器 (Decision Domain: Provider管理 + 事件协调)

    职责:
    - 管理DecisionProvider生命周期
    - 通过ProviderRegistry创建Provider
    - 支持运行时切换Provider
    - 提供decide()方法进行决策
    - 订阅 data.message 事件（来自 Input Domain）
    - 构建 SourceContext 传递消息来源信息
    - 发布 decision.intent 事件（到 Output Domain）
    - 异常处理和优雅降级

    架构约束（3域架构）:
    - 只订阅 Input Domain 的事件
    - 只发布到 Output Domain
    - 不订阅 Output Domain 的事件（避免循环依赖）
    - 不订阅其他 Decision Domain 事件

    注意：
        - DecisionProvider 应预先在各个 Provider 模块的 __init__.py 中注册到 ProviderRegistry
        - ProviderRegistry 统一管理所有类型的 Provider（Input/Decision/Output）
    """

    def __init__(
        self,
        event_bus: "EventBus",
        llm_service: Optional["LLMManager"] = None,
        config_service: Optional["ConfigService"] = None,
        context_service: Optional["ContextService"] = None,
    ):
        """
        初始化DecisionProviderManager

        Args:
            event_bus: EventBus实例
            llm_service: 可选的LLMManager实例，将作为依赖注入到DecisionProvider
            config_service: 可选的ConfigService实例，将作为依赖注入到DecisionProvider
            context_service: 可选的ContextService实例，将作为依赖注入到DecisionProvider
        """
        self.event_bus = event_bus
        self._llm_service = llm_service
        self._config_service = config_service
        self._context_service = context_service
        self.logger = get_logger("DecisionProviderManager")
        self._current_provider: Optional["DecisionProvider"] = None
        self._provider_name: Optional[str] = None
        self._switch_lock = asyncio.Lock()
        self._event_subscribed = False

    async def setup(
        self,
        provider_name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        decision_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        设置决策Provider并订阅事件

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

        from src.modules.registry import ProviderRegistry

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
                dependencies = {}
                if self._llm_service:
                    dependencies["llm_service"] = self._llm_service
                if self._config_service:
                    dependencies["config_service"] = self._config_service
                if self._context_service:
                    dependencies["context_service"] = self._context_service
                await self._current_provider.setup(self.event_bus, provider_config, dependencies)
                self.logger.info(f"DecisionProvider '{provider_name}' 初始化成功")

                # 发布Provider连接事件（使用emit）
                from src.modules.events.payloads.decision import ProviderConnectedPayload

                await self.event_bus.emit(
                    CoreEvents.DECISION_PROVIDER_CONNECTED,
                    ProviderConnectedPayload(
                        provider=provider_name,
                        endpoint=provider_config.get("host") or provider_config.get("ws_url"),
                        metadata={"config": provider_config},
                    ),
                    source="DecisionProviderManager",
                )
            except Exception as e:
                self.logger.error(f"DecisionProvider '{provider_name}' 初始化失败: {e}", exc_info=True)
                self._current_provider = None
                self._provider_name = None
                raise ConnectionError(f"无法初始化DecisionProvider '{provider_name}': {e}") from e

        # 订阅 data.message 事件
        self._subscribe_data_message_event()

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
        from src.modules.registry import ProviderRegistry

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

                # 发布新Provider连接事件（使用emit）
                try:
                    from src.modules.events.payloads.decision import ProviderConnectedPayload

                    await self.event_bus.emit(
                        CoreEvents.DECISION_PROVIDER_CONNECTED,
                        ProviderConnectedPayload(
                            provider=provider_name,
                            metadata={"previous_provider": old_name, "switched": True},
                        ),
                        source="DecisionProviderManager",
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

        取消事件订阅，清理当前Provider。
        """
        # 取消事件订阅
        self._unsubscribe_data_message_event()

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
                    from src.modules.events.payloads import ProviderDisconnectedPayload

                    await self.event_bus.emit(
                        CoreEvents.DECISION_PROVIDER_DISCONNECTED,
                        ProviderDisconnectedPayload(
                            provider=provider_name,
                            reason="cleanup",
                            will_retry=False,
                        ),
                        source="DecisionProviderManager",
                    )
                except Exception as e:
                    self.logger.warning(f"发布Provider断开事件失败: {e}")

            self.logger.info("DecisionProviderManager 已清理")

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
        from src.modules.registry import ProviderRegistry

        return ProviderRegistry.get_registered_decision_providers()

    async def ensure_providers_registered(self) -> None:
        """
        确保所有 DecisionProvider 已注册

        通过导入 providers 包触发所有 Provider 的 Schema 注册。
        """
        from src.modules.registry import ProviderRegistry

        # 导入 providers 包会执行 __init__.py，注册所有 Provider
        try:
            from src.domains.decision import providers  # noqa: F401

            self.logger.debug("已导入 decision providers 包，所有 Provider 应已注册")
        except ImportError as e:
            self.logger.warning(f"导入 decision providers 包失败: {e}")

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

    # =========================================================================
    # 事件处理（原 DecisionCoordinator 功能）
    # =========================================================================

    def _subscribe_data_message_event(self) -> None:
        """
        订阅 data.message 事件（防止重复订阅）
        """
        if not self._event_subscribed:
            from src.modules.events.payloads.input import MessageReadyPayload

            self.event_bus.on(
                CoreEvents.DATA_MESSAGE,
                self._on_data_message,
                model_class=MessageReadyPayload,
            )
            self._event_subscribed = True
            self.logger.info(f"DecisionProviderManager 已订阅 '{CoreEvents.DATA_MESSAGE}' 事件（类型化）")
        else:
            self.logger.debug(f"DecisionProviderManager 已订阅过 '{CoreEvents.DATA_MESSAGE}' 事件，跳过重复订阅")

    def _unsubscribe_data_message_event(self) -> None:
        """
        取消订阅 data.message 事件
        """
        if self._event_subscribed:
            self.event_bus.off(CoreEvents.DATA_MESSAGE, self._on_data_message)
            self._event_subscribed = False
            self.logger.debug("DecisionProviderManager 已取消事件订阅")

    def _extract_source_context_from_dict(self, normalized_dict: Dict[str, Any]) -> "SourceContext":
        """
        从字典格式的 NormalizedMessage 提取 SourceContext

        Args:
            normalized_dict: NormalizedMessage 字典格式

        Returns:
            SourceContext 对象

        支持两种序列化格式：
        - model_dump(): raw 字段
        - to_dict(): raw_data 字段
        """
        from src.modules.types import SourceContext

        source = normalized_dict.get("source", "")
        data_type = normalized_dict.get("data_type", "")
        importance = normalized_dict.get("importance", 0.5)

        # 新版 NormalizedMessage 没有 metadata 字段
        # user_id 和 user_name 从 raw/raw_data 中提取
        # model_dump() 输出 raw，to_dict() 输出 raw_data
        raw_data = normalized_dict.get("raw_data") or normalized_dict.get("raw") or {}
        user_id = raw_data.get("open_id") if isinstance(raw_data, dict) else None
        user_nickname = raw_data.get("uname") if isinstance(raw_data, dict) else None

        return SourceContext(
            source=source,
            data_type=data_type,
            user_id=user_id,
            user_nickname=user_nickname,
            importance=importance,
            extra={},  # 新版 NormalizedMessage 没有 metadata
        )

    async def _on_data_message(self, event_name: str, payload: "MessageReadyPayload", source: str) -> None:
        """
        处理 data.message 事件（类型化）

        当 InputDomain 生成 NormalizedMessage 时：
        1. 从字典格式的 payload.message 提取数据
        2. 重建 NormalizedMessage 对象
        3. 提取 SourceContext
        4. 调用 decide() 进行决策
        5. 将 SourceContext 注入 Intent
        6. 发布 decision.intent 事件（3域架构）

        Args:
            event_name: 事件名称 (CoreEvents.DATA_MESSAGE)
            payload: 类型化的事件数据（MessageReadyPayload 对象）
            source: 事件源
        """
        from src.modules.types.base.normalized_message import NormalizedMessage

        # 获取消息数据（字典格式）
        message_data = payload.message
        if not message_data:
            self.logger.warning("收到空的 NormalizedMessage 事件")
            return

        # 字典格式处理（MessageReadyPayload.message 定义为 Dict[str, Any]）
        if isinstance(message_data, dict):
            normalized_dict = message_data

            # 提取 SourceContext
            source_context = self._extract_source_context_from_dict(normalized_dict)

            # 重建 NormalizedMessage 对象
            try:
                normalized = NormalizedMessage.model_validate(normalized_dict)
            except Exception:
                # 如果重建失败，创建临时对象
                normalized = NormalizedMessage(
                    text=normalized_dict.get("text", ""),
                    content=None,
                    source=normalized_dict.get("source", ""),
                    data_type=normalized_dict.get("data_type", ""),
                    importance=normalized_dict.get("importance", 0.5),
                    metadata=normalized_dict.get("metadata", {}),
                    timestamp=normalized_dict.get("timestamp", 0.0),
                )
        else:
            # 错误处理：不支持的消息类型
            self.logger.warning(f"不支持的消息数据类型: {type(message_data)}，期望 Dict[str, Any]")
            return

        try:
            self.logger.debug(f'收到消息: "{normalized.text[:50]}..." (来源: {normalized.source})')

            # 调用 decide() 进行决策
            intent = await self.decide(normalized)

            # 注入 source_context（如果 Provider 未设置）
            if intent.source_context is None:
                intent.source_context = source_context

            # 获取当前 Provider 名称
            provider_name = self.get_current_provider_name() or "unknown"

            # 发布 decision.intent 事件（3域架构）
            from src.modules.events.payloads import IntentPayload

            await self.event_bus.emit(
                CoreEvents.DECISION_INTENT,
                IntentPayload.from_intent(intent, provider_name),
                source="DecisionProviderManager",
            )

            if intent.actions:
                first_action = intent.actions[0]
                self.logger.info(f'生成响应: "{intent.response_text[:50]}..." (动作: {first_action.type})')
            else:
                self.logger.info(f'生成响应: "{intent.response_text[:50]}..." (无动作)')
        except Exception as e:
            self.logger.error(f"处理 NormalizedMessage 时出错: {e}", exc_info=True)
