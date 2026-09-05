"""SimulatorService - 模拟直播间服务的生命周期管理器

定位：**官方开发基础设施**（与 Dashboard / ``--dry`` / 日志系统同类），不属于生产
直播组件。``[simulator].enabled = true`` 时由组合根装配并自动启动（默认 ``false``，
生产零沾染）。

职责：
- 实例化本包 8 个核心实现类（``PersonaPool`` / ``CadenceGenerator`` /
  ``GiftGenerator`` / ``SimulatorLLMWrapper`` / ``SessionSelector`` /
  ``TokenBudgetController``），构建 LLM 驱动的观众生成循环
- 在独立 asyncio task 中驱动``start() → 生成循环 → stop()``生命周期
- 将模拟消息发布到 ``CoreEvents.ROOM_MESSAGE_DANMAKU`` 事件，
  payload 全部携带 ``simulated=True`` 数据溯源标记
- 暴露 ``is_running`` 属性供 Dashboard API 访问控制面

设计决策：
- **不经过 Input Pipeline**：模拟器自带节奏控制（``CadenceGenerator``）和人设管理，
  Input Pipeline 的限流/去重对模拟器冗余且有损，故直接 ``emit`` 到 EventBus。
- **独立生命周期**：模拟器的启停与 ``collectors.enabled`` 列表无关，
  由 ``[simulator].enabled`` 控制自动启动（``setup()`` 内部判 ``enabled``）。
- **安装顺序**：在 main.py 中应于 CollectorManager 之后、DashboardServer 之前创建。
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.logging import get_logger
from src.modules.simulator.cadence import CadenceGenerator
from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.gift_generator import GiftGenerator
from src.modules.simulator.llm_wrapper import SimulatorLLMWrapper
from src.modules.simulator.persona_pool import PersonaPool
from src.modules.simulator.session_selector import SessionSelector
from src.modules.simulator.token_budget import TokenBudgetController
from src.modules.simulator.types import StreamerContextSnapshot
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService


class SimulatorService:
    """模拟直播间服务 — 生命周期管理器

    管理 8 个核心实现类（``PersonaPool`` / ``CadenceGenerator`` /
    ``GiftGenerator`` / ``SimulatorLLMWrapper`` / ``SessionSelector`` /
    ``TokenBudgetController``），驱动 LLM 生成循环，向 EventBus 推送带
    ``simulated=True`` 溯源标记的 ``room.message.*`` 事件。
    """

    def __init__(
        self,
        event_bus: EventBus,
        services_by_type: Optional[Dict[type, Any]] = None,
    ) -> None:
        self.event_bus = event_bus
        self._services_by_type = services_by_type or {}
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._is_started = False
        self.logger = get_logger("SimulatorService")

        # 8 个核心实现类实例（setup 时构造）
        self._config_obj: Optional[SimulatorConfigSchema] = None
        self._persona_pool: Optional[PersonaPool] = None
        self._cadence: Optional[CadenceGenerator] = None
        self._gift_generator: Optional[GiftGenerator] = None
        self._llm_wrapper: Optional[SimulatorLLMWrapper] = None
        self._session_selector: Optional[SessionSelector] = None
        self._token_budget: Optional[TokenBudgetController] = None
        # 防重复订阅：start 多次调用只挂一次（与 background._subscribed 模式一致）
        self._subscribed_streamer_speech: bool = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def setup(
        self,
        config_service: "ConfigService",
        *,
        auto_start: Optional[bool] = None,
    ) -> None:
        """从 ConfigService 加载配置并实例化 8 个实现类。

        Args:
            config_service: 配置服务实例
            auto_start: 是否自动启动主循环。
                - ``None``（默认）：按 ``[simulator].enabled`` 决定；
                - ``True`` / ``False``：显式覆盖，用于 ``--dry`` 等场景避免
                  setup 触发 LLM 调用。

        若 ``simulator.enabled = true`` 则自动启动。
        """
        simulator_config = config_service.main_config.get("simulator", {})
        if not isinstance(simulator_config, dict):
            self.logger.warning("simulator 配置不是 dict，跳过创建")
            return

        # 1. 解析配置（dict → SimulatorConfigSchema Pydantic 实例）
        try:
            self._config_obj = SimulatorConfigSchema(**simulator_config)
        except Exception as exc:
            self.logger.warning(f"simulator 配置解析失败，跳过创建: {exc}")
            return

        # 2. 实例化数据平面（人设池 / 节奏 / 礼物 / 预算 / 会话）
        self._persona_pool = PersonaPool(rng=random.Random())
        await self._persona_pool.load(self._config_obj)

        self._cadence = CadenceGenerator(config=self._config_obj)

        self._gift_generator = GiftGenerator(
            config=self._config_obj,
            rng=random.Random(),
        )
        await self._gift_generator.load()

        self._session_selector = SessionSelector()
        self._token_budget = TokenBudgetController(budget_per_hour=self._config_obj.token_budget_per_hour)

        # 3. 实例化 LLM 包装器（需 LLMManager，DI 注入或 warning）
        llm_service = self._find_llm_service()
        if llm_service is None:
            self.logger.warning(
                "simulator: LLMManager 未通过 services_by_type 注入，LLM 生成循环将被禁用（仅数据平面就绪）"
            )
            return

        self._llm_wrapper = SimulatorLLMWrapper(
            config=self._config_obj,
            llm_manager=llm_service,
        )
        # 让礼物生成器也能用同一个 LLM 包装器（生成 SC 文本）
        self._gift_generator._llm_wrapper = self._llm_wrapper

        # 4. 自动启动（按 [simulator].enabled 或 auto_start 显式覆盖）
        if auto_start is None:
            auto_start = self._config_obj.enabled
        if auto_start:
            self.logger.info("模拟器配置已启用，自动启动中...")
            await self.start()

    def _find_llm_service(self) -> Optional[Any]:
        """从 services_by_type 探测 LLMManager（duck-type：拥有 chat/chat_fast/setup）。

        组合根在 main.py 装配时通常以 ``{LLMManager: llm_service}`` 注入；
        同时支持通过对象特征识别（不依赖具体类，避免循环导入）。
        """
        for service in self._services_by_type.values():
            if service is None:
                continue
            if hasattr(service, "chat") and hasattr(service, "setup") and hasattr(service, "chat_fast"):
                return service
        return None

    async def start(self) -> None:
        """启动模拟器（幂等）。"""
        if self._is_started:
            self.logger.debug("模拟器已运行，忽略重复 start")
            return
        if self._llm_wrapper is None:
            self.logger.warning("模拟器实例未创建（setup 未注入 LLMManager？），请先调用 setup()")
            return

        self._subscribe_streamer_speech()

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="SimulatorService")
        self._is_started = True
        self.logger.info("模拟器服务已启动")

    def _subscribe_streamer_speech(self) -> None:
        """订阅 ``streamer.speech`` 业务事件 → cadence.notify_streamer_activity。

        防重复订阅：start 多次调用只挂一次（与 background._subscribed 同模式）。
        cadence 暂未构造时跳过（setup 早期阶段或 enabled=false 路径）。
        """
        if self._subscribed_streamer_speech:
            return
        if self._cadence is None:
            return
        self.event_bus.on(
            CoreEvents.STREAMER_SPEECH,
            self._on_streamer_speech,
            model_class=StreamerSpeechPayload,
        )
        self._subscribed_streamer_speech = True
        self.logger.debug("已订阅 streamer.speech → cadence.notify_streamer_activity")

    def _unsubscribe_streamer_speech(self) -> None:
        """解绑 ``streamer.speech`` 订阅（stop 时调用）。"""
        if not self._subscribed_streamer_speech:
            return
        try:
            self.event_bus.off(CoreEvents.STREAMER_SPEECH, self._on_streamer_speech)
        except Exception as exc:
            self.logger.debug(f"streamer.speech 解绑失败（已忽略）: {exc}")
        self._subscribed_streamer_speech = False

    def _on_streamer_speech(
        self,
        event_name: str,
        payload: StreamerSpeechPayload,
        source: str,
    ) -> None:
        """处理 ``streamer.speech`` 事件：唤醒 cadence 节奏。

        handler 内部仅同步调 ``notify_streamer_activity`` + 记 DEBUG 日志：
        - 不同步触发任何 LLM 调用或决策出口（防环：本事件为业务信号，订阅者不得
          反向触发表演类副作用）
        - 异常被 EventBus 包装层捕获记 ERROR，本方法不主动吞或抛
        """
        cadence = self._cadence
        if cadence is None:
            return
        cadence.notify_streamer_activity()
        self.logger.debug(f"收到 streamer.speech → 已通知 cadence：utterance_id={payload.utterance_id}")

    async def _run(self) -> None:
        """驱动模拟器主循环并发布 ROOM_MESSAGE_DANMAKU 事件。

        循环：按 CadenceGenerator 计算间隔 → 概率触发礼物 → 否则调 LLM 生成弹幕 →
        构造 RoomMessagePayload(simulated=True) → emit 到 EventBus。
        TokenBudgetController 控制 LLM 调用上限，预算耗尽则跳过本轮生成。
        """
        assert self._cadence is not None
        assert self._persona_pool is not None
        assert self._llm_wrapper is not None
        assert self._gift_generator is not None
        assert self._session_selector is not None
        assert self._token_budget is not None
        assert self._config_obj is not None

        context = StreamerContextSnapshot()
        try:
            while not self._stop_event.is_set():
                # 预算硬上限：超出后跳过生成但保留主循环可被 stop_event 唤醒
                if self._token_budget.is_budget_exceeded():
                    self.logger.debug("模拟器 token 预算耗尽，等待预算恢复或 stop")
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                        break
                    except asyncio.TimeoutError:
                        continue

                # 计算下一条消息间隔
                delay_s = await self._cadence.next_delay_seconds()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay_s)
                    # stop_event 被 set → 跳出循环
                    break
                except asyncio.TimeoutError:
                    pass  # 正常：间隔到期，未收到 stop 信号

                # 选人设（临时路人 / 常驻）
                persona = self._persona_pool.pick_one()

                # 选会话上下文
                session_id = await self._session_selector.select_session(
                    fallback_id=self._config_obj.fallback_session_id
                )

                # 概率触发礼物事件（否则走普通弹幕）
                gift_roll = random.random()
                if gift_roll < self._config_obj.gift_probability:
                    gift_event = await self._gift_generator.generate_gift(context=context)
                    if gift_event is not None:
                        await self._emit_message(
                            message_type=gift_event.data_type or "gift",
                            text=gift_event.text,
                            persona=gift_event.persona,
                            session_id=session_id,
                        )
                        self._persona_pool.record_message(gift_event.persona)
                        continue

                # 普通弹幕：调 LLM 生成文本
                generated = await self._llm_wrapper.generate_viewer_message(persona=persona, context=context)
                if generated is None or not generated.text:
                    continue

                # 累计 token 用量
                if generated.tokens_used > 0:
                    self._token_budget.record_usage(generated.tokens_used)

                await self._emit_message(
                    message_type="danmaku",
                    text=generated.text,
                    persona=persona,
                    session_id=session_id,
                )
                self._persona_pool.record_message(persona)
        except asyncio.CancelledError:
            # 保持取消传播语义：记 debug 后重新抛出，让外层 await 看到取消
            self.logger.debug("模拟器主循环被取消")
            raise
        except Exception as exc:
            self.logger.error(f"模拟器主循环异常: {exc}", exc_info=True)

    async def _emit_message(
        self,
        *,
        message_type: str,
        text: str,
        persona: Any,
        session_id: str,
    ) -> None:
        """构造带 simulated=True 溯源标记的 RoomMessagePayload 并 emit。"""
        assert self._config_obj is not None
        payload = RoomMessagePayload(
            live_session_id=session_id or self._config_obj.fallback_session_id,
            message_type=message_type,  # type: ignore[arg-type]
            user=RoomMessageUser(
                id=str(getattr(persona, "user_id", "") or f"sim-{uuid.uuid4().hex[:6]}"),
                name=str(getattr(persona, "user_nickname", "") or "模拟观众"),
            ),
            content=str(text or ""),
            timestamp_ms=now_ms(),
            simulated=True,  # 数据溯源标记：模拟/回放源，统计与入库需过滤
        )
        await self.event_bus.emit(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            payload,
            source="simulated_live_stream",
        )

    async def stop(self) -> None:
        """停止模拟器。

        取消传播语义：
        - ``self._task.cancel()`` 由 stop() 主动发起 → 等 task 终止时观察到的
          CancelledError 属正常 stop 路径，可吞（用户调 stop() 期望正常返回）；
        - stop() 协程本身被外层 ``cancel()`` → 当前 task 的 ``cancelling()`` 计数
          > 0，必须 ``raise`` 让外层看到取消状态。
        """
        if not self._is_started:
            return
        self._is_started = False
        self._stop_event.set()

        # 解绑 streamer.speech 订阅（防 stop 后旧 handler 残留触发 cadence）
        self._unsubscribe_streamer_speech()

        if self._task is not None:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                self.logger.debug("模拟器 task 停止超时（>10s）")
            except asyncio.CancelledError:
                # 区分"自身主动 cancel self._task" vs "stop() 协程被外层 cancel"
                current = asyncio.current_task()
                if current is not None and current.cancelling() > 0:
                    self.logger.debug("模拟器 stop() 协程被外层取消，传播 CancelledError")
                    raise
                self.logger.debug("模拟器 task 取消完成（stop 主动发起）")
            self._task = None
        self.logger.info("模拟器服务已停止")

    async def cleanup(self) -> None:
        """清理资源。"""
        if self._is_started:
            await self.stop()
        self._llm_wrapper = None
        self._persona_pool = None
        self._cadence = None
        self._gift_generator = None
        self._session_selector = None
        self._token_budget = None
        self._config_obj = None
        self.logger.info("模拟器服务已清理")

    # ------------------------------------------------------------------
    # Dashboard 访问面
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """模拟器是否正在运行。"""
        return self._is_started
