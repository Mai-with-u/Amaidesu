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
import os
import sys
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.di.context import ProviderContext
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import ProviderDisconnectedPayload
from src.modules.events.payloads.decision import ProviderConnectedPayload
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.logging import get_logger
from src.modules.registry import ProviderRegistry
from src.modules.types import SourceContext
from src.modules.types.base.normalized_message import NormalizedMessage

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService
    from src.modules.context.service import ContextService
    from src.modules.events.event_bus import EventBus
    from src.modules.llm.manager import LLMManager
    from src.modules.prompts.manager import PromptManager
    from src.modules.types.base.decision_provider import DecisionProvider


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
        prompt_manager: "PromptManager" = None,
    ):
        """
        初始化DecisionProviderManager

        Args:
            event_bus: EventBus实例
            llm_service: 可选的LLMManager实例，将作为依赖注入到DecisionProvider
            config_service: 可选的ConfigService实例，将作为依赖注入到DecisionProvider
            context_service: 可选的ContextService实例，将作为依赖注入到DecisionProvider
            prompt_manager: 可选的PromptManager实例，将作为依赖注入到DecisionProvider
        """
        self.event_bus = event_bus
        self._llm_service = llm_service
        self._config_service = config_service
        self._context_service = context_service
        self._prompt_manager = prompt_manager
        self.logger = get_logger("DecisionProviderManager")
        self._current_provider: Optional["DecisionProvider"] = None
        self._provider_name: Optional[str] = None
        self._switch_lock = asyncio.Lock()
        self._event_subscribed = False
        self._provider_ready = False  # 标记 Provider 是否已创建（但未启动）

    async def setup(
        self,
        provider_name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        decision_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        创建决策Provider实例（不启动）

        此方法只创建 Provider 实例，不启动 Provider。
        需要显式调用 start() 来启动 Provider 并订阅事件。

        Args:
            provider_name: Provider名称（如果为None，则从decision_config中读取active_provider）
            config: Provider配置（如果为None，则从decision_config中构建）
            decision_config: 完整的决策层配置（包含active_provider、available_providers等）

        Raises:
            ValueError: 如果Provider未注册到ProviderRegistry

        注意:
            - 调用此方法后，必须调用 start() 来启动 Provider
            - Provider 实例创建后存储在 _current_provider 中
            - _provider_ready 标志会设置为 True
        """
        # 确保所有 Provider 已注册
        await self.ensure_providers_registered()

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
            # 清理当前Provider（如果存在）
            if self._current_provider:
                self.logger.info(f"清理当前Provider: {self._provider_name}")
                try:
                    await self._current_provider.cleanup()
                except Exception as e:
                    self.logger.error(f"清理Provider失败: {e}", exc_info=True)

            # 通过 ProviderRegistry 创建新Provider
            try:
                self.logger.info(f"通过 ProviderRegistry 创建 DecisionProvider: {provider_name}")

                # 创建 ProviderContext
                context = ProviderContext(
                    event_bus=self.event_bus,
                    llm_service=self._llm_service,
                    config_service=self._config_service,
                    context_service=self._context_service,
                    prompt_service=self._prompt_manager,
                )

                # 创建 Provider 并传入 context
                self._current_provider = ProviderRegistry.create_decision(
                    provider_name, provider_config, context=context
                )
                self._provider_name = provider_name
                self._provider_ready = True
                self.logger.info(f"DecisionProvider '{provider_name}' 已创建（未启动）")

            except ValueError as e:
                available = ", ".join(ProviderRegistry.get_registered_decision_providers())
                self.logger.error(f"DecisionProvider '{provider_name}' 未找到。可用: {available}")
                raise ValueError(f"DecisionProvider '{provider_name}' 未找到。可用: {available}") from e

    async def start(self) -> None:
        """
        启动当前 Provider

        此方法会：
        1. 调用 Provider 的 start() 方法启动 Provider
        2. 发布 Provider 连接事件
        3. 订阅 data.message 事件

        注意：
            - 必须先调用 setup() 创建 Provider
            - 如果没有已创建的 Provider，会跳过启动并记录警告

        Raises:
            ConnectionError: 如果 Provider 启动失败
        """
        if not self._current_provider:
            self.logger.warning("没有已创建的 Provider，跳过启动")
            return

        provider_name = self._provider_name

        try:
            # 启动 Provider
            await self._current_provider.start()
            self.logger.info(f"DecisionProvider '{provider_name}' 已启动")

            # 发布Provider连接事件
            await self._emit_provider_connected_event()

            # 订阅 data.message 事件
            self._subscribe_data_message_event()

        except Exception as e:
            self.logger.error(f"DecisionProvider '{provider_name}' 启动失败: {e}", exc_info=True)
            raise ConnectionError(f"无法启动DecisionProvider '{provider_name}': {e}") from e

    async def stop(self) -> None:
        """
        停止当前 Provider（不删除实例）

        此方法会：
        1. 取消订阅 data.message 事件
        2. 发布 Provider 断开事件
        3. 调用 Provider 的 cleanup() 方法

        注意：
            - 停止后 Provider 实例仍然存在
            - 可以通过调用 start() 重新启动
            - 如需删除实例，请调用 cleanup()
        """
        # 取消事件订阅
        self._unsubscribe_data_message_event()

        provider_name = self._provider_name

        if self._current_provider:
            self.logger.info(f"停止 Provider: {provider_name}")
            try:
                await self._current_provider.cleanup()
            except Exception as e:
                self.logger.error(f"停止 Provider 失败: {e}", exc_info=True)

            # 发布Provider断开事件
            await self._emit_provider_disconnected_event(provider_name, reason="stop", will_retry=False)

            self._provider_ready = False
            self.logger.info(f"DecisionProvider '{provider_name}' 已停止")

    async def _emit_provider_connected_event(self) -> None:
        """发布 Provider 连接事件"""
        if not self._current_provider or not self._provider_name:
            return

        try:
            await self.event_bus.emit(
                CoreEvents.DECISION_PROVIDER_CONNECTED,
                ProviderConnectedPayload(
                    provider=self._provider_name,
                    metadata={"provider_ready": self._provider_ready},
                ),
                source="DecisionProviderManager",
            )
        except Exception as e:
            self.logger.warning(f"发布Provider连接事件失败: {e}")

    async def _emit_provider_disconnected_event(
        self, provider_name: Optional[str], reason: str = "unknown", will_retry: bool = False
    ) -> None:
        """发布 Provider 断开事件"""
        if not provider_name:
            return

        try:
            await self.event_bus.emit(
                CoreEvents.DECISION_PROVIDER_DISCONNECTED,
                ProviderDisconnectedPayload(
                    provider=provider_name,
                    reason=reason,
                    will_retry=will_retry,
                ),
                source="DecisionProviderManager",
            )
        except Exception as e:
            self.logger.warning(f"发布Provider断开事件失败: {e}")

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        """
        触发决策（fire-and-forget）

        调用当前 Provider 的 decide() 方法，但不等待返回值。
        决策结果通过 Provider 内部发布的事件传递。

        注意:
            - 此方法不返回任何值
            - Provider 内部通过 event_bus 发布 decision.intent 事件
        """
        if not self._current_provider:
            self.logger.warning("当前未设置 DecisionProvider，跳过决策")
            return

        self.logger.debug(f"触发决策 (Provider: {self._provider_name})")

        try:
            # Fire-and-forget: 不等待返回值
            await self._current_provider.decide(normalized_message)
        except Exception as e:
            self.logger.error(f"触发决策失败: {e}", exc_info=True)

    async def switch_provider(self, provider_name: str, config: Dict[str, Any]) -> None:
        """
        切换决策Provider(运行时)

        Args:
            provider_name: 新Provider名称
            config: 新Provider配置

        注意:
            - 切换时会先停止并清理旧Provider
            - 如果新Provider初始化失败，会回退到旧Provider
        """

        old_provider = self._current_provider
        old_name = self._provider_name

        self.logger.info(f"切换Provider: {old_name} -> {provider_name}")

        # 使用锁保护切换过程
        async with self._switch_lock:
            try:
                # 通过 ProviderRegistry 创建新Provider
                try:
                    self.logger.info(f"通过 ProviderRegistry 创建 DecisionProvider: {provider_name}")

                    # 创建 ProviderContext
                    context = ProviderContext(
                        event_bus=self.event_bus,
                        llm_service=self._llm_service,
                        config_service=self._config_service,
                        context_service=self._context_service,
                        prompt_service=self._prompt_manager,
                    )

                    # 创建 Provider 并传入 context
                    new_provider = ProviderRegistry.create_decision(provider_name, config, context=context)
                except ValueError as e:
                    available = ", ".join(ProviderRegistry.get_registered_decision_providers())
                    self.logger.error(f"DecisionProvider '{provider_name}' 未找到。可用: {available}")
                    # 保留原始错误消息格式以兼容测试
                    raise ValueError(f"DecisionProvider '{provider_name}' 未找到") from e

                # 启动新Provider
                try:
                    await new_provider.start()
                    self.logger.info(f"DecisionProvider '{provider_name}' 启动成功")
                except Exception as e:
                    self.logger.error(f"DecisionProvider '{provider_name}' 启动失败: {e}", exc_info=True)
                    raise ConnectionError(f"无法启动DecisionProvider '{provider_name}': {e}") from e

                # 新Provider启动成功后，清理旧Provider
                if old_provider:
                    self.logger.info(f"清理旧Provider: {old_name}")
                    try:
                        await old_provider.cleanup()
                    except Exception as e:
                        self.logger.error(f"清理旧Provider失败: {e}", exc_info=True)

                # 更新当前Provider
                self._current_provider = new_provider
                self._provider_name = provider_name
                self._provider_ready = True

                # 发布新Provider连接事件
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

                # 订阅 data.message 事件（如果尚未订阅）
                self._subscribe_data_message_event()

                self.logger.info(f"Provider切换成功: {old_name} -> {provider_name}")

            except Exception as e:
                self.logger.error(f"Provider切换失败，回退到旧Provider: {e}", exc_info=True)
                # 回退到旧Provider
                self._current_provider = old_provider
                self._provider_name = old_name
                self._provider_ready = old_provider is not None
                raise

    async def cleanup(self) -> None:
        """
        清理资源并删除 Provider 实例

        此方法会：
        1. 取消订阅 data.message 事件
        2. 调用 Provider 的 cleanup() 方法
        3. 发布 Provider 断开事件
        4. 删除 Provider 实例（设置 _current_provider 为 None）

        注意：
            - 此方法会完全删除 Provider 实例
            - 如需保留实例仅停止运行，请调用 stop()
            - 清理后需要重新调用 setup() 和 start() 才能再次使用
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

                # 发布Provider断开事件
                await self._emit_provider_disconnected_event(provider_name, reason="cleanup", will_retry=False)

                # 删除 Provider 实例
                self._current_provider = None
                self._provider_name = None
                self._provider_ready = False

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
        return ProviderRegistry.get_registered_decision_providers()

    async def ensure_providers_registered(self) -> None:
        """
        确保所有 DecisionProvider 已注册

        通过导入 providers 包触发所有 Provider 的 Schema 注册。
        """
        # 将 src 目录添加到 sys.path（确保可以导入 src.modules 和 src.domains）
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src_dir = os.path.dirname(src_dir)  # 从 provider_manager.py -> decision -> src
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
            self.logger.debug(f"已将 src 目录添加到 sys.path: {src_dir}")

        # 导入 providers 包会执行 __init__.py，注册所有 Provider
        try:
            from src.domains.decision import providers  # noqa: F401

            self.logger.debug("已导入 decision providers 包，所有 Provider 应已注册")
        except ImportError as e:
            self.logger.warning(f"导入 decision providers 包失败: {e}")

        registered = ProviderRegistry.get_registered_decision_providers()
        self.logger.debug(f"已注册的 DecisionProvider: {registered}")

    # =========================================================================
    # 事件处理（原 DecisionCoordinator 功能）
    # =========================================================================

    def _subscribe_data_message_event(self) -> None:
        """
        订阅 input.message.ready 事件（防止重复订阅）
        """
        if not self._event_subscribed:
            from src.modules.events.payloads.input import MessageReadyPayload

            self.event_bus.on(
                CoreEvents.INPUT_MESSAGE_READY,
                self._on_data_message,
                model_class=MessageReadyPayload,
            )
            self._event_subscribed = True
            self.logger.info(f"DecisionProviderManager 已订阅 '{CoreEvents.INPUT_MESSAGE_READY}' 事件（类型化）")
        else:
            self.logger.debug(f"DecisionProviderManager 已订阅过 '{CoreEvents.INPUT_MESSAGE_READY}' 事件，跳过重复订阅")

    def _unsubscribe_data_message_event(self) -> None:
        """
        取消订阅 input.message.ready 事件
        """
        if self._event_subscribed:
            self.event_bus.off(CoreEvents.INPUT_MESSAGE_READY, self._on_data_message)
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
        处理 input.message.ready 事件（类型化）

        当 InputDomain 生成 NormalizedMessage 时：
        1. 从字典格式的 payload.message 提取数据
        2. 重建 NormalizedMessage 对象
        3. 提取 SourceContext
        4. 调用 decide() 进行决策
        5. 将 SourceContext 注入 Intent
        6. 发布 decision.intent.generated 事件（3域架构）

        Args:
            event_name: 事件名称 (CoreEvents.INPUT_MESSAGE_READY)
            payload: 类型化的事件数据（MessageReadyPayload 对象）
            source: 事件源
        """
        # 获取消息数据（字典格式）
        message_data = payload.message
        if not message_data:
            self.logger.warning("收到空的 NormalizedMessage 事件")
            return

        # 字典格式处理（MessageReadyPayload.message 定义为 Dict[str, Any]）
        if isinstance(message_data, dict):
            normalized_dict = message_data

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
            self.logger.debug(f'触发决策: "{normalized.text[:50]}..." (来源: {normalized.source})')

            # Fire-and-forget: Provider 内部会发布 decision.intent 事件
            await self.decide(normalized)
        except Exception as e:
            self.logger.error(f"触发决策时出错: {e}", exc_info=True)
