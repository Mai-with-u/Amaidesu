"""SimulatedLiveStreamCollector - 模拟直播间 InputCollector

LLM 驱动的模拟直播间调试工具，实时生成多样化的观众消息
（文本弹幕/礼物/SuperChat），用于功能演示、压力测试和 Prompt 调试。

架构合规：
- ContextService 通过 pull 模式（get_history）读取，不订阅 output.*
- 不向 ContextService 写入
- source="simulated_live_stream"，不冒充真实平台
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.event_history import EventHistoryService
from src.modules.events.payloads.decision import IntentPayload
from src.modules.llm.manager import LLMManager
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager
from src.modules.types.base.normalized_message import NormalizedMessage
from src.stages.input.collectors.simulated_live_stream.cadence import (
    CadenceGenerator,
)
from src.stages.input.collectors.simulated_live_stream.config_schema import (
    SimulatorConfigSchema,
)
from src.stages.input.collectors.simulated_live_stream.context_reader import (
    ContextServiceReader,
    EventHistoryReader,
)
from src.stages.input.collectors.simulated_live_stream.llm_wrapper import (
    SimulatorLLMWrapper,
)
from src.stages.input.collectors.simulated_live_stream.persona_pool import (
    PersonaPool,
)
from src.stages.input.collectors.simulated_live_stream.session_selector import (
    SessionSelector,
)
from src.stages.input.collectors.simulated_live_stream.token_budget import (
    TokenBudgetController,
)
from src.stages.input.collectors.simulated_live_stream.types import (
    GeneratedMessage,
    Persona,
    StreamerContextSnapshot,
)
from src.stages.input.registry import collector
from src.modules.context.service import ContextService as ContextServiceClass

# GiftGenerator is imported lazily in start() — Task 14 creates it
if TYPE_CHECKING:
    from src.stages.input.collectors.simulated_live_stream.gift_generator import (  # noqa: F401
        GiftGenerator,
    )


@collector("simulated_live_stream")
class SimulatedLiveStreamCollector:
    """模拟直播间 InputCollector

    生命周期：start() → collect() [主循环] → stop()
    配置驱动启停，通过 DI 注入 LLMManager、ContextService 等依赖。

    自动检测上下文来源：
    - ContextService 可用 → 使用 ContextServiceReader（主路径）
    - 仅 EventHistoryService → 使用 EventHistoryReader（maibot 降级）
    """

    # ConfigSchema 使用独立的 SimulatorConfigSchema（不在内部嵌套）
    # 这是 @collector 装饰器兼容性要求的占位

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        llm_service: Optional[LLMManager] = None,
        prompt_service: Optional[PromptManager] = None,
        context_service: Optional[ContextServiceClass] = None,
        event_history_service: Optional[EventHistoryService] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        self._cfg: SimulatorConfigSchema = SimulatorConfigSchema.model_validate(config)  # type: ignore[call-overload]

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._context_service = context_service
        self._event_history_service = event_history_service

        # 子组件（在 start() 中初始化）
        self._cadence: Optional[CadenceGenerator] = None
        self._persona_pool: Optional[PersonaPool] = None
        self._llm_wrapper: Optional[SimulatorLLMWrapper] = None
        self._context_reader = None  # ContextServiceReader 或 EventHistoryReader
        self._session_selector: Optional[SessionSelector] = None
        self._gift_generator: Optional[GiftGenerator] = None
        self._token_budget: Optional[TokenBudgetController] = None

        self._stop_event = asyncio.Event()
        self.is_started = False

        # 订阅者队列（供 Dashboard WebSocket 推送）
        self._subscribers: "set[asyncio.Queue]" = set()

        # 运行时控制
        self._last_streamer_context: StreamerContextSnapshot = StreamerContextSnapshot()

        # 消息统计
        self._total_messages: int = 0
        self._messages_by_type: Dict[str, int] = {}

        # 主播发言订阅（auto 模式用）
        self._speech_unsub = None

        # 话题注入（Dashboard 手动触发）
        self._injected_topic: Optional[str] = None

    # ------------------------------------------------------------------
    # 公开属性（供 Dashboard API 读取）
    # ------------------------------------------------------------------

    @property
    def typed_config(self) -> SimulatorConfigSchema:
        """返回解析后的配置快照（Dashboard API 使用）"""
        return self._cfg

    async def start(self) -> None:
        """初始化所有子组件并启动模拟器"""
        try:
            cfg = self._cfg

            # 初始化组件
            self._cadence = CadenceGenerator(cfg)
            self._persona_pool = PersonaPool()
            await self._persona_pool.load(cfg)

            if self._llm_service is not None:
                self._llm_wrapper = SimulatorLLMWrapper(cfg, self._llm_service, self._prompt_service)

            # 自动选择上下文读取器
            if self._context_service is not None:
                self._context_reader = ContextServiceReader(self._context_service, cfg)
                self.logger.info("使用 ContextServiceReader（主路径）")
            elif self._event_history_service is not None:
                self._context_reader = EventHistoryReader(self._event_history_service, cfg)
                self.logger.info("使用 EventHistoryReader（maibot 降级路径）")
            else:
                self.logger.warning("未注入上下文服务，模拟器将无法感知主播发言")

            if self._context_service is not None:
                self._session_selector = SessionSelector(self._context_service)
            else:
                self._session_selector = SessionSelector()

            # GiftGenerator 由 Task 14 创建 — 延迟导入
            try:
                from src.stages.input.collectors.simulated_live_stream.gift_generator import (  # noqa: E402
                    GiftGenerator,
                )

                self._gift_generator = GiftGenerator(cfg, llm_wrapper=self._llm_wrapper)
                if self._gift_generator is not None:
                    await self._gift_generator.load()
            except ImportError:
                self._gift_generator = None
                self.logger.warning("GiftGenerator 未导入（Task 14 尚未完成）")
            self._token_budget = TokenBudgetController(cfg.token_budget_per_hour)

            # auto 模式：订阅主播发言事件触发突发
            if cfg.cadence_mode == "auto" and self._cadence is not None:
                self.event_bus.on(
                    CoreEvents.OUTPUT_INTENT_FINISHED,
                    self._on_streamer_speech,
                    model_class=IntentPayload,
                    priority=50,
                )
                self._speech_unsub = (CoreEvents.OUTPUT_INTENT_FINISHED, self._on_streamer_speech)
                self.logger.info("auto 模式：已订阅 output.intent.finished 触发突发")

            self.is_started = True
            self.logger.info("模拟直播间 Collector 启动完成")
        except Exception as e:
            self.logger.error(f"模拟直播间 Collector 启动失败: {e}", exc_info=True)
            raise

    async def stop(self) -> None:
        """停止模拟器"""
        self.is_started = False
        self._stop_event.set()
        self._unsubscribe_speech_event()
        self.logger.info("模拟直播间 Collector 已停止")

    async def cleanup(self) -> None:
        """清理资源"""
        self._stop_event.set()
        self.is_started = False
        self._unsubscribe_speech_event()
        self._subscribers.clear()
        self.logger.info("模拟直播间 Collector 已清理")

    def _unsubscribe_speech_event(self) -> None:
        """取消主播发言事件订阅"""
        if self._speech_unsub is not None:
            event_name, handler = self._speech_unsub
            try:
                self.event_bus.off(event_name, handler)
            except Exception:
                pass
            self._speech_unsub = None

    def stream(self) -> AsyncIterator[NormalizedMessage]:
        """InputManager 契约：返回 AsyncIterator"""
        if not self.is_started:
            raise RuntimeError("Collector 未启动，请先调用 start()")

        async def _generate():
            try:
                async for message in self.collect():
                    yield message
            finally:
                self.is_started = False

        return _generate()

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """主消息生成循环

        基于泊松间隔产生模拟观众消息，自动检测突发期。
        """
        if self._cadence is None:
            self.logger.error("CadenceGenerator 未初始化")
            return

        while not self._stop_event.is_set():
            try:
                # 1. 等待泊松间隔
                delay = await self._cadence.next_delay_seconds()
                self.logger.debug(f"collect: 泊松等待 {delay:.1f}s 后开始生成")
                await asyncio.sleep(delay)

                if self._stop_event.is_set():
                    break

                # 2. 获取主播上下文（带 session 选择）
                session_id = await self._select_session()
                context = await self._read_streamer_context(session_id)

                # 3. 检测新活动 → 触发突发期
                if context.has_new_activity_since_last_check:
                    self._cadence.notify_streamer_activity()
                    self._cadence.trigger_burst()

                # 4. 生成消息
                self.logger.debug("collect: 开始 _generate_message")
                message = await self._generate_message(context)
                self.logger.debug(f"collect: _generate_message 返回 {type(message).__name__ if message else 'None'}")

                # 5. 发布
                if message is not None:
                    self._total_messages += 1
                    self._messages_by_type[message.data_type] = self._messages_by_type.get(message.data_type, 0) + 1
                    normalized = self._to_normalized_message(message, session_id)
                    self.logger.info(f"collect: 生成消息 [{message.data_type}] {normalized.text}")
                    # 推送给 WebSocket 订阅者
                    self._broadcast_to_subscribers(normalized)
                    yield normalized

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"消息生成循环异常: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    # --- 内部方法 ---

    async def _select_session(self) -> str:
        """选择或返回缓存 session"""
        if self._session_selector is not None and self._context_service is not None:
            try:
                return await self._session_selector.select_session(self._cfg.fallback_session_id)
            except Exception:
                pass
        return self._cfg.fallback_session_id

    async def _read_streamer_context(self, session_id: str) -> StreamerContextSnapshot:
        """读取主播上下文（含注入话题）"""
        snapshot = StreamerContextSnapshot()
        try:
            if isinstance(self._context_reader, ContextServiceReader):
                snapshot = await self._context_reader.get_streamer_context(session_id, self._cfg.context_window_size)
            elif isinstance(self._context_reader, EventHistoryReader):
                snapshot = await self._context_reader.get_streamer_context(self._cfg.context_window_size)
        except Exception as e:
            self.logger.debug(f"读取主播上下文失败: {e}")

        # 注入话题（Dashboard 触发，一次性消费）
        if self._injected_topic:
            snapshot.recent_messages = [f"[话题引导] {self._injected_topic}"] + snapshot.recent_messages
            snapshot.has_new_activity_since_last_check = True
            self._injected_topic = None

        return snapshot

    async def _generate_message(self, context: StreamerContextSnapshot) -> Optional[GeneratedMessage]:
        """生成一条消息（文本/礼物/SC）"""
        if self._llm_wrapper is None:
            self.logger.warning("_generate_message: _llm_wrapper 为 None，跳过")
            return None

        if self._token_budget is not None and self._token_budget.is_budget_exceeded():
            self.logger.warning("_generate_message: token 预算超限，跳过")
            return None

        state = self._cadence.get_state() if self._cadence is not None else "?"
        self.logger.debug(f"_generate_message: 当前状态={state}")

        # 暖场期
        if self._cadence is not None and state == "WARMUP":
            persona = self._select_persona()
            if persona is None:
                self.logger.warning("_generate_message: WARMUP 选择人设失败，跳过")
                return None
            msg = await self._llm_wrapper.generate_warmup_message(persona)
            self._record_usage(msg)
            self.logger.info(f"_generate_message: WARMUP 生成消息成功, persona={persona.user_nickname}")
            return msg

        # 选择消息类型
        msg_type = self._pick_message_type()

        if msg_type == "gift" and self._gift_generator is not None:
            return await self._gift_generator.generate_gift(context)

        if msg_type == "sc" and self._gift_generator is not None:
            return await self._gift_generator.generate_sc(context)

        # 文本弹幕
        persona = self._select_persona()
        if persona is None:
            self.logger.warning("_generate_message: 选择人设失败，跳过")
            return None

        if persona.is_temporary:
            msg = await self._llm_wrapper.generate_passerby_message(persona, context)
        else:
            msg = await self._llm_wrapper.generate_viewer_message(persona, context)

        self._record_usage(msg)
        if msg is None:
            self.logger.warning(f"_generate_message: {persona.user_nickname} LLM 返回 None，跳过")
        return msg

    def _select_persona(self) -> Optional[Persona]:
        """从人设池中选择一个活跃人设"""
        if self._persona_pool is None:
            return None
        try:
            return self._persona_pool.pick_one()
        except Exception:
            return None

    def _pick_message_type(self) -> str:
        """按概率选择消息类型：text / gift / sc"""
        cfg = self._cfg
        roll = random.random()
        if roll < cfg.sc_probability:
            return "sc"
        elif roll < cfg.sc_probability + cfg.gift_probability:
            return "gift"
        return "text"

    def _to_normalized_message(self, msg: GeneratedMessage, session_id: str) -> NormalizedMessage:
        """将生成的内部消息转换为 NormalizedMessage"""
        text = msg.text
        if msg.data_type == "gift" and msg.gift is not None:
            text = f"{msg.persona.user_nickname} 送出了 1 个 {msg.gift.gift_name}"
        elif msg.data_type == "super_chat" and msg.sc_amount_rmb:
            text = f"[SC {msg.sc_amount_rmb}元] {msg.persona.user_nickname}: {msg.text}"

        importance = self._calculate_importance(msg)

        return NormalizedMessage(
            source="simulated_live_stream",
            data_type=msg.data_type,
            text=text,
            user_id=msg.persona.user_id,
            user_nickname=msg.persona.user_nickname,
            platform="simulated",
            room_id=session_id,
            importance=importance,
        )

    def _calculate_importance(self, msg: GeneratedMessage) -> float:
        """计算消息重要性（0-1）

        参考 bili_danmaku_official 的归一化公式：
        - 基础值 = 0.5
        - 粉丝牌加成 = min(fans_medal_level / 40, 0.2)
        - 大航海加成 = {1: 0.3, 2: 0.2, 3: 0.1}
        - SC 额外加成 = min(sc_amount_rmb / 500, 0.2)
        - 礼物加成 = 0.1
        """
        p = msg.persona
        base = 0.5
        medal_bonus = min(p.fans_medal_level / 40, 0.2)
        guard_bonus = {1: 0.3, 2: 0.2, 3: 0.1}.get(p.guard_level, 0)

        importance = base + medal_bonus + guard_bonus

        if msg.data_type == "super_chat" and msg.sc_amount_rmb:
            importance += min(msg.sc_amount_rmb / 500, 0.2)
        elif msg.data_type == "gift":
            importance += 0.1

        return min(importance, 1.0)

    def _record_usage(self, msg: Optional[GeneratedMessage]) -> None:
        """记录 token 使用"""
        if msg is not None and self._token_budget is not None:
            self._token_budget.record_usage(msg.tokens_used)
        if msg is not None and self._persona_pool is not None:
            self._persona_pool.record_message(msg.persona)

    def _broadcast_to_subscribers(self, message: NormalizedMessage) -> None:
        """将消息推送给所有 WebSocket 订阅者"""
        stale = set()
        for queue in self._subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                stale.add(queue)
            except Exception:
                stale.add(queue)
        self._subscribers -= stale

    # --- 主播发言事件处理 ---

    async def _on_streamer_speech(self, event_name: str, data: IntentPayload, source: str) -> None:
        """auto 模式下：主播发言 → 触发突发"""
        if self._cadence is None:
            return
        self._cadence.notify_streamer_activity()
        self._cadence.trigger_burst()
        speech = data.intent_data.get("speech", "")[:20]
        self.logger.debug(f"auto 突发: 检测到主播发言 '{speech}...'")

    # --- Dashboard 控制接口 ---

    def subscribe_for_ws(self) -> asyncio.Queue:
        """为 WebSocket 客户端创建订阅队列"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe_ws(self, queue: asyncio.Queue) -> None:
        """移除 WebSocket 订阅队列"""
        self._subscribers.discard(queue)

    def update_runtime_config(self, **kwargs) -> None:
        """运行时更新配置参数（由 Dashboard API 调用）"""
        from pydantic import ValidationError

        try:
            old_mode = self._cfg.cadence_mode
            updated = self._cfg.model_copy(update=kwargs)
            self._cfg = updated

            if self._cadence is not None:
                self._cadence.update_config(updated)
            if self._persona_pool is not None:
                self._persona_pool.update_config(updated)
            if self._llm_wrapper is not None:
                self._llm_wrapper.update_config(updated)

            # 节奏模式切换时管理事件订阅
            new_mode = updated.cadence_mode
            if new_mode != old_mode:
                if new_mode == "auto" and self._speech_unsub is None:
                    self.event_bus.on(
                        CoreEvents.OUTPUT_INTENT_FINISHED,
                        self._on_streamer_speech,
                        model_class=IntentPayload,
                        priority=50,
                    )
                    self._speech_unsub = (CoreEvents.OUTPUT_INTENT_FINISHED, self._on_streamer_speech)
                    self.logger.info("运行时切换为 auto 模式，已订阅发言事件")
                elif new_mode != "auto" and self._speech_unsub is not None:
                    self._unsubscribe_speech_event()
                    self.logger.info("运行时退出 auto 模式，已取消订阅发言事件")

            self.logger.info(f"运行时配置已更新: {kwargs}")
        except ValidationError as e:
            self.logger.error(f"运行时配置更新失败: {e}")

    def trigger_gift_rain(self, duration_s: int = 30) -> None:
        """触发礼物雨模式（由 Dashboard 调用）"""
        if self._cadence is not None:
            self._cadence.trigger_burst()
        self.logger.info(f"礼物雨模式已触发，持续 {duration_s} 秒")

    def trigger_topic_injection(self, topic: str) -> None:
        """注入话题到上下文（由 Dashboard 调用），下一次消息生成时生效"""
        self._injected_topic = topic
        self.logger.info(f"话题注入: {topic}")

    def reset_token_budget(self) -> None:
        """重置 token 预算"""
        if self._token_budget is not None:
            self._token_budget.reset()
            self.logger.info("Token 预算已重置")
