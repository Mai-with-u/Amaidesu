"""SimulatorService - 世界模拟器（三模式）

定位：**官方开发基础设施**（与 Dashboard / ``--dry`` / 日志系统同类），不属于生产
直播组件。``[simulator].enabled = true`` 时由组合根装配（默认 ``false``，生产零沾染）。

世界模式（``[simulator].mode``）——唯一的 ``room.message.*`` 发射器，三态切换：
- ``generate``：LLM 驱动的生成式虚拟直播间（四态节奏 + 人设池 + 礼物/SC）
- ``replay``：录制回放（读 EventHistory 落盘的世界快照，按原节奏重放）
- ``off``：装配但不运行世界

设计决策：
- **不经过 Input Pipeline**：模拟器自带节奏控制（``CadenceGenerator``）和人设管理，
  Input Pipeline 的限流/去重对模拟器冗余且有损，故直接 ``emit`` 到 EventBus。
- **世界状态唯一事实源**：观众上下文不内存自存——弹幕经 StorageLedger 落
  ``live_chat``，主播发言经 ``streamer.speech`` 落同一张表，生成前按 persona
  窗口大小读取最近公共流（含真实+模拟混合场）。重启不丢上下文。
- **回放消息刷新时间戳**：StorageLedger 按 ``payload.timestamp_ms`` 落库，
  回放 emit 时把录制时的时间戳刷成当前时间，保证回放内容进入"最近窗口"
  查询语义；原始时刻保留在录制文件与日志中。
- **独立生命周期**：模拟器的启停与 ``collectors.enabled`` 列表无关，
  由 ``[simulator].enabled`` 控制自动启动（``setup()`` 内部判 ``enabled``）。
- **安装顺序**：在 main.py 中应于 CollectorManager 之后、DashboardServer 之前创建。
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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
from src.modules.simulator.replay_engine import ReplayEngine
from src.modules.simulator.seed_data import seed_simulator_data
from src.modules.simulator.token_budget import TokenBudgetController
from src.modules.simulator.types import PersonaRole, StreamerContextSnapshot
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService


# 角色默认世界窗口（条数）：表达"该角色对直播间的关注度"的天性，
# persona.context_window_size 可逐人覆盖，config.context_window_size 兜底
_ROLE_WINDOW_DEFAULTS: Dict[PersonaRole, int] = {
    PersonaRole.VETERAN: 12,
    PersonaRole.FAN: 10,
    PersonaRole.TEASER: 8,
    PersonaRole.NEWCOMER: 5,
    PersonaRole.HATER: 8,
    PersonaRole.PASSERBY: 2,
}


class SimulatorService:
    """世界模拟器 — 唯一的 room.message.* 模拟发射器

    管理 ``PersonaPool`` / ``CadenceGenerator`` / ``GiftGenerator`` /
    ``SimulatorLLMWrapper`` / ``TokenBudgetController`` /
    ``ReplayEngine``，按 ``mode`` 驱动生成或回放循环，向 EventBus 推送带
    ``simulated=True`` 溯源标记的 ``room.message.*`` 事件。
    """

    def __init__(
        self,
        event_bus: EventBus,
        sqlite_store: Optional[SQLiteStore] = None,
        services_by_type: Optional[Dict[type, Any]] = None,
        session_manager: Optional[Any] = None,
    ) -> None:
        self.event_bus = event_bus
        self._store = sqlite_store
        # 场次管理器：世界窗口读取按其解析当前场次；回放启停自动开/关场次
        self._session_manager = session_manager
        self._opened_session_pk: Optional[int] = None
        self._services_by_type = services_by_type or {}
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._is_started = False
        self._active_mode: str = "off"
        self.logger = get_logger("SimulatorService")

        # 核心实现类实例（setup 时构造）
        self._config_obj: Optional[SimulatorConfigSchema] = None
        self._persona_pool: Optional[PersonaPool] = None
        self._cadence: Optional[CadenceGenerator] = None
        self._gift_generator: Optional[GiftGenerator] = None
        self._llm_wrapper: Optional[SimulatorLLMWrapper] = None
        self._token_budget: Optional[TokenBudgetController] = None
        self._replay_engine: Optional[ReplayEngine] = None
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
        """从 ConfigService 加载配置并实例化各实现类。

        Args:
            config_service: 配置服务实例
            auto_start: 是否自动启动世界循环。
                - ``None``（默认）：按 ``[simulator].enabled`` + ``mode`` 决定；
                - ``True`` / ``False``：显式覆盖，用于 ``--dry`` 等场景避免
                  setup 触发 LLM 调用。
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

        if self._store is None:
            self.logger.warning("simulator: SQLiteStore 未注入，人设/礼物/上下文功能不可用，跳过创建")
            return

        # 2. 启动期一次性种子导入（空表才插内置默认值；幂等）
        await seed_simulator_data(self._store)

        # 3. 实例化数据平面（人设池 / 节奏 / 礼物 / 预算 / 会话 / 回放）
        self._persona_pool = PersonaPool(sqlite_store=self._store, rng=random.Random())
        await self._persona_pool.load(self._config_obj)

        self._cadence = CadenceGenerator(config=self._config_obj)

        self._gift_generator = GiftGenerator(
            config=self._config_obj,
            sqlite_store=self._store,
            rng=random.Random(),
        )
        await self._gift_generator.load()

        self._token_budget = TokenBudgetController(budget_per_hour=self._config_obj.token_budget_per_hour)
        self._replay_engine = ReplayEngine(config=self._config_obj)

        # 4. 实例化 LLM 包装器（需 LLMManager，DI 注入或 warning；replay 模式不需要）
        llm_service = self._find_llm_service()
        if llm_service is None:
            if self._config_obj.mode == "generate":
                self.logger.warning(
                    "simulator: LLMManager 未通过 services_by_type 注入，generate 模式不可用（仅数据平面就绪）"
                )
                return
            self.logger.info("simulator: LLMManager 未注入（replay/off 模式不需要，继续装配）")

        if llm_service is not None:
            self._llm_wrapper = SimulatorLLMWrapper(
                config=self._config_obj,
                llm_manager=llm_service,
            )
            # 让礼物生成器也能用同一个 LLM 包装器（生成 SC 文本）
            self._gift_generator._llm_wrapper = self._llm_wrapper

        # 5. 自动启动（按 [simulator].enabled 或 auto_start 显式覆盖）
        if auto_start is None:
            auto_start = self._config_obj.enabled
        if auto_start:
            self.logger.info(f"模拟器配置已启用（mode={self._config_obj.mode}），自动启动中...")
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

    async def start(self, *, replay_date: Optional[str] = None) -> None:
        """按当前 mode 启动世界循环（幂等）。

        Args:
            replay_date: replay 模式的录制日期覆盖（不传用配置的 replay_date）。
        """
        if self._is_started:
            self.logger.debug("模拟器已运行，忽略重复 start")
            return
        if self._config_obj is None:
            self.logger.warning("模拟器未 setup，无法启动")
            return

        mode = self._config_obj.mode
        if mode == "off":
            self.logger.info("模拟器 mode=off，不启动世界循环")
            return
        if mode == "replay":
            date_str = replay_date or self._config_obj.replay_date
            if not date_str:
                self.logger.warning("模拟器 mode=replay 但未指定回放日期（replay_date），不启动")
                return
            if self._replay_engine is None:
                self.logger.warning("模拟器回放引擎未构造，不启动")
                return
            loaded = self._replay_engine.load(date_str)
            if loaded == 0:
                self.logger.warning(f"回放日期 {date_str} 无可回放消息，不启动")
                return
        elif mode == "generate" and self._llm_wrapper is None:
            self.logger.warning("模拟器 generate 模式缺 LLM 包装器（setup 未注入 LLMManager？），不启动")
            return

        self._subscribe_streamer_speech()

        self._active_mode = mode
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_replay(replay_date=date_str) if mode == "replay" else self._run_generate(),
            name=f"SimulatorService-{mode}",
        )
        self._is_started = True

        # 回放自动开/关场次：一场回放天然是一场直播——启动即开（source=replay），
        # stop 时收口。其余模式不开场次（消息归临时兜底场次）。
        if mode == "replay" and self._session_manager is not None:
            self._opened_session_pk = await self._session_manager.open_session(
                title=f"回放 {date_str}",
                source="replay",
            )

        self.logger.info(f"模拟器服务已启动（mode={mode}）")

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
        - 主播发言的世界窗口读取不在此处缓存——生成时直接查 live_chat
        - 异常被 EventBus 包装层捕获记 ERROR，本方法不主动吞或抛
        """
        cadence = self._cadence
        if cadence is None:
            return
        cadence.notify_streamer_activity()
        self.logger.debug(f"收到 streamer.speech → 已通知 cadence：utterance_id={payload.utterance_id}")

    # ------------------------------------------------------------------
    # generate 模式
    # ------------------------------------------------------------------

    async def _run_generate(self) -> None:
        """LLM 生成循环：节奏 → 选人 → 读世界窗口 → 生成 → emit。

        TokenBudgetController 控制调用上限（含窗口注入的估算 token），
        预算耗尽则跳过本轮生成。
        """
        assert self._cadence is not None
        assert self._persona_pool is not None
        assert self._llm_wrapper is not None
        assert self._gift_generator is not None
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

                # 世界窗口：按 persona 关注度读 live_chat 最近公共流
                # （场次归属经 LiveSessionManager 解析当前场次）
                window = await self._fetch_world_window(persona=persona)
                context.recent_messages = window

                # 概率触发礼物事件（否则走普通弹幕）
                gift_roll = random.random()
                if gift_roll < self._config_obj.gift_probability:
                    gift_event = await self._gift_generator.generate_gift(context=context)
                    if gift_event is not None:
                        await self._emit_message(
                            message_type=gift_event.data_type or "gift",
                            text=gift_event.text,
                            persona=gift_event.persona,
                        )
                        self._persona_pool.record_message(gift_event.persona)
                        continue

                # 普通弹幕：调 LLM 生成文本
                generated = await self._llm_wrapper.generate_viewer_message(persona=persona, context=context)
                if generated is None or not generated.text:
                    continue

                # 累计 token 用量（生成消耗 + 窗口注入估算）
                tokens_used = generated.tokens_used + _estimate_window_tokens(window)
                if tokens_used > 0:
                    self._token_budget.record_usage(tokens_used)

                await self._emit_message(
                    message_type="danmaku",
                    text=generated.text,
                    persona=persona,
                )
                self._persona_pool.record_message(persona)
        except asyncio.CancelledError:
            # 保持取消传播语义：记 debug 后重新抛出，让外层 await 看到取消
            self.logger.debug("模拟器生成循环被取消")
            raise
        except Exception as exc:
            self.logger.error(f"模拟器生成循环异常: {exc}", exc_info=True)

    async def _fetch_world_window(self, *, persona: Any) -> List[str]:
        """按 persona 关注度读取 live_chat 最近公共流窗口。

        窗口大小优先级：persona.context_window_size（个性）> 角色默认（天性）
        > config.context_window_size（全局兜底）。公共流同时包含观众弹幕与
        主播发言（sender_role=viewer/assistant），即"这个观众眼中的直播间"。
        场次归属经 LiveSessionManager 解析当前场次（未显式开场次时为临时
        兜底场次）；管理器缺失或读取失败时返回空窗口（本轮无上下文）。
        """
        if self._store is None or self._config_obj is None or self._session_manager is None:
            return []
        role = getattr(persona, "role", None)
        limit = (
            getattr(persona, "context_window_size", None)
            or (_ROLE_WINDOW_DEFAULTS.get(role) if role is not None else None)
            or self._config_obj.context_window_size
        )
        try:
            live_pk = await self._session_manager.resolve_pk()
            rows = await self._store.list_recent_live_chat(
                live_session_id=live_pk,
                limit=limit,
            )
        except Exception as exc:
            self.logger.warning(f"世界窗口读取失败（本轮无上下文）: {exc}")
            return []
        return [f"{row['sender_name'] or row['sender_role']}: {row['content']}" for row in rows if row["content"]]

    # ------------------------------------------------------------------
    # replay 模式
    # ------------------------------------------------------------------

    async def _run_replay(self, *, replay_date: Optional[str] = None) -> None:
        """录制回放循环：按原节奏逐条重放录制队列。

        相邻消息间隔由录制时的毫秒时间戳差值除以速度倍率得到（超长冷场截断）；
        回放消息的 user/content 原样还原，时间戳刷新为当前时刻（落库最近窗口
        语义），live_session_id 替换为当前场次（回放内容作为"现在的输入流"注入）。
        """
        assert self._replay_engine is not None
        assert self._config_obj is not None

        engine = self._replay_engine
        try:
            while not self._stop_event.is_set():
                gap_s = engine.next_gap_seconds()
                if gap_s > 0:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=gap_s)
                        break
                    except asyncio.TimeoutError:
                        pass  # 间隔到期

                payload = engine.pop_next()
                if payload is None:
                    self.logger.info(f"回放完成: date={replay_date} 共 {engine.total} 条")
                    break

                await self._emit_replay_payload(payload)
        except asyncio.CancelledError:
            self.logger.debug("模拟器回放循环被取消")
            raise
        except Exception as exc:
            self.logger.error(f"模拟器回放循环异常: {exc}", exc_info=True)

    async def _emit_replay_payload(self, payload: RoomMessagePayload) -> None:
        """原样回放一条录制消息（时间戳刷新 + simulated 标记保持）。

        场次归属不在此填写——回放启动时已自动开启回放场次，事件经场次盖章
        拦截器归属到该场；message_id 保留录制值（跨回放可复现同一条消息）。
        """
        replayed = payload.model_copy(
            update={
                "live_session_id": 0,
                "timestamp_ms": now_ms(),
            }
        )
        await self.event_bus.emit(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            replayed,
            source="simulated_live_stream",
        )

    # ------------------------------------------------------------------
    # 发射
    # ------------------------------------------------------------------

    async def _emit_message(
        self,
        *,
        message_type: str,
        text: str,
        persona: Any,
    ) -> None:
        """构造带 simulated=True 溯源标记的 RoomMessagePayload 并 emit。

        场次归属（live_session_id）不在此填写——由事件总线的场次盖章拦截器
        统一注入当前场次；message_id 现场生成，作为回复关联键落库。
        """
        payload = RoomMessagePayload(
            message_id=uuid.uuid4().hex,
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

        # 回放场次收口（自动开启的场次随回放结束自动结束）
        if self._opened_session_pk is not None and self._session_manager is not None:
            try:
                await self._session_manager.close_session(reason="回放结束")
            except Exception as exc:  # noqa: BLE001 收口失败不阻断停止
                self.logger.warning(f"回放场次收口失败（已忽略）: {exc}")
            self._opened_session_pk = None

        self._active_mode = "off"
        self.logger.info("模拟器服务已停止")

    async def cleanup(self) -> None:
        """清理资源。"""
        if self._is_started:
            await self.stop()
        self._llm_wrapper = None
        self._persona_pool = None
        self._cadence = None
        self._gift_generator = None
        self._token_budget = None
        self._replay_engine = None
        self._config_obj = None
        self.logger.info("模拟器服务已清理")

    # ------------------------------------------------------------------
    # Dashboard 访问面
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """模拟器是否正在运行。"""
        return self._is_started

    @property
    def mode(self) -> str:
        """当前运行模式（off/generate/replay）。"""
        return self._active_mode

    @property
    def persona_pool(self) -> Optional[PersonaPool]:
        """人设池（setup 后可用；Dashboard CRUD 访问面）。"""
        return self._persona_pool

    @property
    def gift_generator(self) -> Optional[GiftGenerator]:
        """礼物生成器（setup 后可用；Dashboard CRUD 访问面）。"""
        return self._gift_generator

    @property
    def replay_engine(self) -> Optional[ReplayEngine]:
        """回放引擎（setup 后可用；Dashboard 回放日期/进度访问面）。"""
        return self._replay_engine

    @property
    def replay_progress(self) -> Optional[Dict[str, Any]]:
        """回放进度（replay 模式运行中返回 date/total/remaining，否则 None）。"""
        if self._active_mode != "replay" or self._replay_engine is None:
            return None
        engine = self._replay_engine
        return {
            "date": engine.replay_date,
            "total": engine.total,
            "remaining": engine.remaining,
        }


def _estimate_window_tokens(window: List[str]) -> int:
    """粗估世界窗口注入的 token 消耗（中文按每字符 2 token 近似）。

    预算控制目的是防失控而非精确计费；估算值计入 TokenBudget，使上下文
    注入的消耗与生成消耗共享同一硬上限。
    """
    return sum(len(line) for line in window) * 2


__all__ = ["SimulatorService"]
