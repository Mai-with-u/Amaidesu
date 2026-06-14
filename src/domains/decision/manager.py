"""
DeciderManager - 决策Decider管理器

职责:
- 管理多个DecisionDecider生命周期（支持多Decider并行）
- 使用 _DECIDERS 字典直接创建Decider
- 支持从配置加载多个启用的Decider
- 提供decide()方法进行决策（每个Decider独立决策）
- 订阅 Input Domain 的 input.message.ready 事件
- 发布 decision.intent.generated 事件到 Output Domain
- 异常处理和优雅降级
- Speech冲突警告机制

架构约束（3域架构）:
- 只订阅 Input Domain 的事件
- 只发布到 Output Domain
- 不订阅 Output Domain 的事件（避免循环依赖）
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads import ProviderDisconnectedPayload
from src.modules.events.payloads.decision import ProviderConnectedPayload
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.logging import get_logger
from src.modules.types.base.normalized_message import NormalizedMessage
from src.domains.decision.registry import _DECIDERS

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService
    from src.modules.context.service import ContextService
    from src.modules.events.event_bus import EventBus
    from src.modules.llm.manager import LLMManager
    from src.modules.prompts.manager import PromptManager


# 产生语音输出的Decider列表（可能产生音频交叠）
SPEECH_DECIDERS = {"maibot", "llm"}


class DeciderManager:
    """
    决策Decider管理器 (Decision Domain: Decider管理 + 事件协调)

    职责:
    - 管理多个DecisionDecider生命周期（支持多Decider并行）
    - 使用 _DECIDERS 字典直接创建Decider
    - 支持从配置加载多个启用的Decider
    - 提供decide()方法进行决策（每个Decider独立决策）
    - 订阅 input.message.ready 事件（来自 Input Domain）
    - 发布 decision.intent.generated 事件（到 Output Domain）
    - 异常处理和优雅降级
    - Speech冲突警告机制

    架构约束（3域架构）:
    - 只订阅 Input Domain 的事件
    - 只发布到 Output Domain
    - 不订阅 Output Domain 的事件（避免循环依赖）
    - 不订阅其他 Decision Domain 事件
    """

    def __init__(
        self,
        event_bus: "EventBus",
        llm_service: Optional["LLMManager"] = None,
        config_service: Optional["ConfigService"] = None,
        context_service: Optional["ContextService"] = None,
        prompt_manager: Optional["PromptManager"] = None,
    ):
        """
        初始化DeciderManager

        Args:
            event_bus: EventBus实例
            llm_service: 可选的LLMManager实例，将作为依赖注入到Decider
            config_service: 可选的ConfigService实例，将作为依赖注入到Decider
            context_service: 可选的ContextService实例，将作为依赖注入到Decider
            prompt_manager: 可选的PromptManager实例，将作为依赖注入到Decider
        """
        self.event_bus = event_bus
        self._llm_service = llm_service
        self._config_service = config_service
        self._context_service = context_service
        self._prompt_manager = prompt_manager
        self.logger = get_logger("DeciderManager")

        # 多Decider支持：存储所有已加载的Decider实例
        self._deciders: Dict[str, Any] = {}
        self._decider_names: List[str] = []

        # 向后兼容：保留 _current_decider 和 _decider_name（指向第一个Decider）
        self._current_decider: Optional[Any] = None
        self._decider_name: Optional[str] = None

        self._switch_lock = asyncio.Lock()
        self._event_subscribed = False
        self._decider_ready = False

    async def setup(
        self,
        decider_name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        decision_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        创建决策Decider实例（不启动）

        支持两种配置格式：
        1. 新格式（多Decider）：config["deciders"]["enabled"] = ["maibot", "llm"]
        2. 旧格式（单Decider）：config["active_provider"] = "maibot"

        Args:
            decider_name: Decider名称（如果为None，则从decision_config中读取）
            config: Decider配置
            decision_config: 完整的决策层配置（包含deciders.enabled或active_provider等）

        Raises:
            ValueError: 如果Decider未注册
        """
        # 确定配置来源
        if decision_config is None:
            decision_config = config or {}

        # 获取启用的Decider列表
        enabled_deciders = self._get_enabled_deciders(decision_config, decider_name)

        if not enabled_deciders:
            self.logger.warning("没有启用的Decider，将使用默认的maibot")
            enabled_deciders = ["maibot"]

        # 记录已加载的Decider
        self._decider_names = enabled_deciders
        self._deciders = {}

        async with self._switch_lock:
            # 创建并初始化所有启用的Decider
            for name in enabled_deciders:
                await self._create_decider(name, decision_config)

            # 向后兼容：_current_decider 指向第一个Decider
            if self._deciders:
                first_name = list(self._deciders.keys())[0]
                self._current_decider = self._deciders[first_name]
                self._decider_name = first_name
                self._decider_ready = True

            # 检查Speech冲突
            self._check_speech_conflict(enabled_deciders)

            self.logger.info(f"已加载 {len(self._deciders)} 个Decider: {list(self._deciders.keys())}")

    def _get_enabled_deciders(self, decision_config: Dict[str, Any], decider_name: Optional[str]) -> List[str]:
        """
        从配置中获取启用的Decider列表

        支持两种格式：
        1. 新格式：decision_config["enabled"] = ["maibot", "llm"]
        2. 旧格式：decision_config["active_provider"] = "maibot"

        注意：decision_config 已经是 deciders 配置节（来自 config.get("deciders", {})），
        所以直接读取 enabled 或 active_provider，不要再次 get("deciders", {})。

        Args:
            decision_config: 决策层配置（deciders配置节）
            decider_name: 显式指定的Decider名称

        Returns:
            启用的Decider名称列表
        """
        # 如果显式指定了decider_name，优先使用
        if decider_name:
            return [decider_name]

        # 尝试新格式：decision_config["enabled"] 列表
        # decision_config 已经是 deciders 配置节，不需要再 get("deciders", {})
        enabled = decision_config.get("enabled", [])
        if isinstance(enabled, list) and enabled:
            return enabled

        # 尝试旧格式：active_provider 单值
        active_provider = decision_config.get("active_provider")
        if active_provider:
            return [active_provider]

        # 默认返回空列表
        return []

    def _check_speech_conflict(self, decider_names: List[str]) -> None:
        """
        检查是否有多个 speech-producing Decider 同时启用

        如果多个产生语音的Decider同时启用，会导致音频交叠问题。
        仅发出警告，不阻止启动。

        Args:
            decider_names: 启用的Decider名称列表
        """
        speech_deciders = [name for name in decider_names if name in SPEECH_DECIDERS]
        if len(speech_deciders) > 1:
            self.logger.warning(
                f"多个 speech-producing Decider 同时启用: {speech_deciders}。"
                f"这可能导致音频交叠。如非预期，请在配置中只启用一个。"
            )

    async def _create_decider(self, decider_name: str, decision_config: Dict[str, Any]) -> None:
        """
        创建单个Decider实例

        Args:
            decider_name: Decider名称
            decision_config: 决策层配置

        Raises:
            ValueError: 如果Decider未注册
        """
        if decider_name not in _DECIDERS:
            available = list(_DECIDERS.keys())
            self.logger.error(f"Decider '{decider_name}' 未找到。可用: {available}")
            raise ValueError(f"Decider '{decider_name}' 未找到。可用: {available}")

        decider_cls = _DECIDERS[decider_name]

        # 获取Decider特定配置
        decider_config = decision_config.get(decider_name, {})

        # 直接构造 Decider，传递依赖
        decider = decider_cls(
            config=decider_config,
            event_bus=self.event_bus,
            llm_service=self._llm_service,
            prompt_service=self._prompt_manager,
        )

        self._deciders[decider_name] = decider
        self.logger.info(f"Decider '{decider_name}' 已创建（未启动）")

    async def start(self) -> None:
        """启动所有已创建的Decider"""
        if not self._deciders:
            self.logger.warning("没有已创建的 Decider，跳过启动")
            return

        # 启动所有Decider
        for name, decider in self._deciders.items():
            try:
                await decider.setup()
                self.logger.info(f"Decider '{name}' 已启动")
            except Exception as e:
                self.logger.error(f"Decider '{name}' 启动失败: {e}", exc_info=True)
                # 继续启动其他Decider，不因为一个失败而全部停止

        # 发布连接事件（向后兼容，只发布第一个Decider的事件）
        if self._current_decider and self._decider_name:
            await self._emit_decider_connected_event()

        # 订阅事件
        self._subscribe_data_message_event()

        self._decider_ready = True
        self.logger.info(f"所有 {len(self._deciders)} 个Decider已启动")

    async def stop(self) -> None:
        """停止所有Decider（不删除实例）"""
        self._unsubscribe_data_message_event()

        # 停止所有Decider
        for name, decider in self._deciders.items():
            try:
                await decider.cleanup()
                self.logger.info(f"Decider '{name}' 已停止")
            except Exception as e:
                self.logger.error(f"停止 Decider '{name}' 失败: {e}", exc_info=True)

        # 发布断开事件（向后兼容，只发布第一个Decider的事件）
        if self._decider_name:
            await self._emit_decider_disconnected_event(self._decider_name, reason="stop", will_retry=False)

        self._decider_ready = False
        self.logger.info("所有 Decider 已停止")

    async def _emit_decider_connected_event(self) -> None:
        """发布 Decider 连接事件"""
        if not self._current_decider or not self._decider_name:
            return

        try:
            await self.event_bus.emit(
                CoreEvents.DECISION_PROVIDER_CONNECTED,
                ProviderConnectedPayload(
                    provider=self._decider_name,
                    metadata={"decider_ready": self._decider_ready, "decider_count": len(self._deciders)},
                ),
                source="DeciderManager",
            )
        except Exception as e:
            self.logger.warning(f"发布Decider连接事件失败: {e}")

    async def _emit_decider_disconnected_event(
        self, decider_name: Optional[str], reason: str = "unknown", will_retry: bool = False
    ) -> None:
        """发布 Decider 断开事件"""
        if not decider_name:
            return

        try:
            await self.event_bus.emit(
                CoreEvents.DECISION_PROVIDER_DISCONNECTED,
                ProviderDisconnectedPayload(
                    provider=decider_name,
                    reason=reason,
                    will_retry=will_retry,
                ),
                source="DeciderManager",
            )
        except Exception as e:
            self.logger.warning(f"发布Decider断开事件失败: {e}")

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        """
        触发所有Decider进行决策（fire-and-forget）

        每个Decider都会独立处理同一条消息。
        EventBus天然支持多订阅者，所以不需要特殊处理。

        Args:
            normalized_message: 标准化消息
        """
        if not self._deciders:
            self.logger.warning("当前未设置任何 Decider，跳过决策")
            return

        # 向后兼容：如果没有多Decider，行为与原来相同
        if len(self._deciders) == 1 and self._current_decider:
            self.logger.debug(f"触发决策 (Decider: {self._decider_name})")
            try:
                await self._current_decider.decide(normalized_message)
            except Exception as e:
                self.logger.error(f"触发决策失败: {e}", exc_info=True)
            return

        # 多Decider模式：触发所有Decider
        self.logger.debug(f"触发 {len(self._deciders)} 个Decider进行决策")

        # 并行触发所有Decider
        tasks = []
        for name, decider in self._deciders.items():
            self.logger.debug(f"触发决策 (Decider: {name})")
            tasks.append(self._safe_decide(decider, name, normalized_message))

        # 等待所有Decider完成（但不使用结果）
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_decide(self, decider: Any, name: str, normalized_message: "NormalizedMessage") -> None:
        """
        安全地触发单个Decider进行决策

        捕获异常防止一个Decider失败影响其他Decider。

        Args:
            decider: Decider实例
            name: Decider名称
            normalized_message: 标准化消息
        """
        try:
            await decider.decide(normalized_message)
        except Exception as e:
            self.logger.error(f"Decider '{name}' 触发决策失败: {e}", exc_info=True)

    async def switch_provider(self, decider_name: str, config: Dict[str, Any]) -> None:
        """
        切换决策Decider（向后兼容方法）

        注意：此方法保留用于向后兼容。
        在多Decider模式下，推荐直接修改配置并重新加载。

        行为：
        - 如果 decider_name 在已加载的 Decider 列表中，切换 _current_decider 为该 Decider
        - 如果不在列表中，则重新加载所有 Decider

        Args:
            decider_name: 要切换到的Decider名称
            config: Decider配置
        """
        self.logger.info(f"切换Decider: {self._decider_name} -> {decider_name}")

        async with self._switch_lock:
            # 检查目标Decider是否已加载
            if decider_name in self._deciders:
                # 已加载，直接切换
                self._current_decider = self._deciders[decider_name]
                self._decider_name = decider_name
                self.logger.info(f"Decider切换成功（已加载）: {decider_name}")
            else:
                # 未加载，需要重新加载
                self.logger.info(f"Decider '{decider_name}' 未加载，需要重新加载")
                try:
                    # 创建新的Decider
                    await self._create_decider(decider_name, config)

                    # 清理旧的所有Decider
                    for name, decider in self._deciders.items():
                        if name != decider_name:
                            try:
                                await decider.cleanup()
                            except Exception as e:
                                self.logger.error(f"清理Decider '{name}' 失败: {e}", exc_info=True)

                    # 只保留目标Decider
                    self._deciders = {decider_name: self._deciders[decider_name]}
                    self._decider_names = [decider_name]
                    self._current_decider = self._deciders[decider_name]
                    self._decider_name = decider_name

                    self.logger.info(f"Decider切换成功（重新加载）: {decider_name}")
                except Exception as e:
                    self.logger.error(f"Decider切换失败: {e}", exc_info=True)
                    raise ConnectionError(f"无法切换Decider '{decider_name}': {e}") from e

    async def cleanup(self) -> None:
        """清理资源并删除所有 Decider 实例"""
        self._unsubscribe_data_message_event()

        async with self._switch_lock:
            # 清理所有Decider
            for name, decider in self._deciders.items():
                self.logger.info(f"清理Decider: {name}")
                try:
                    await decider.cleanup()
                except Exception as e:
                    self.logger.error(f"清理Decider '{name}' 失败: {e}", exc_info=True)

                await self._emit_decider_disconnected_event(name, reason="cleanup", will_retry=False)

            self._deciders = {}
            self._decider_names = []
            self._current_decider = None
            self._decider_name = None
            self._decider_ready = False

            self.logger.info("DeciderManager 已清理")

    def get_current_decider(self) -> Optional[Any]:
        """获取当前Decider实例（向后兼容）"""
        return self._current_decider

    def get_current_decider_name(self) -> Optional[str]:
        """获取当前Decider名称（向后兼容）"""
        return self._decider_name

    def get_deciders(self) -> Dict[str, Any]:
        """获取所有已加载的Decider实例"""
        return self._deciders.copy()

    def get_decider_names(self) -> List[str]:
        """获取所有已加载的Decider名称"""
        return self._decider_names.copy()

    def get_available_deciders(self) -> list[str]:
        """获取所有可用的Decider名称"""
        return list(_DECIDERS.keys())

    def _subscribe_data_message_event(self) -> None:
        """订阅 input.message.ready 事件（防止重复订阅）

        注意：EventBus天然支持多订阅者，这里只订阅一次。
        每个Decider的setup()会调用event_bus.on()注册自己的处理器。
        """
        if not self._event_subscribed:
            from src.modules.events.payloads.input import MessageReadyPayload

            self.event_bus.on(
                CoreEvents.INPUT_MESSAGE_READY,
                self._on_data_message,
                model_class=MessageReadyPayload,
            )
            self._event_subscribed = True
            self.logger.info(f"DeciderManager 已订阅 '{CoreEvents.INPUT_MESSAGE_READY}' 事件（类型化）")
        else:
            self.logger.debug(f"DeciderManager 已订阅过 '{CoreEvents.INPUT_MESSAGE_READY}' 事件，跳过重复订阅")

    def _unsubscribe_data_message_event(self) -> None:
        """取消订阅 input.message.ready 事件"""
        if self._event_subscribed:
            self.event_bus.off(CoreEvents.INPUT_MESSAGE_READY, self._on_data_message)
            self._event_subscribed = False
            self.logger.debug("DeciderManager 已取消事件订阅")

    async def _on_data_message(self, event_name: str, payload: "MessageReadyPayload", source: str) -> None:
        """处理 input.message.ready 事件（类型化）"""
        message_data = payload.message
        if not message_data:
            self.logger.warning("收到空的 NormalizedMessage 事件")
            return

        if isinstance(message_data, dict):
            normalized_dict = message_data

            try:
                normalized = NormalizedMessage.model_validate(normalized_dict)
            except Exception:
                normalized = NormalizedMessage(
                    text=normalized_dict.get("text", ""),
                    source=normalized_dict.get("source", ""),
                    data_type=normalized_dict.get("data_type", "text"),
                    importance=normalized_dict.get("importance", 0.5),
                    timestamp=normalized_dict.get("timestamp", 0.0),
                )
        else:
            self.logger.warning(f"不支持的消息数据类型: {type(message_data)}，期望 Dict[str, Any]")
            return

        try:
            self.logger.debug(f'触发决策: "{normalized.text[:50]}..." (来源: {normalized.source})')

            await self.decide(normalized)
        except Exception as e:
            self.logger.error(f"触发决策时出错: {e}", exc_info=True)
