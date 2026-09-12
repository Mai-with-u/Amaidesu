"""BackgroundMaintainer - 主播 Agent 后台维护（双任务模型）

双任务模型（不是 1 个、不是 N 个）：
- 轻循环（周期 tick ~5s，纯机械，永不被阻塞）
- 压缩 worker（触发驱动，等 asyncio.Queue）
- Why 2 个不回 1 个：慢任务（LLM 秒级）不冻结快任务节拍
- Why 不 N 个：快任务合并无成本、管理复杂度封顶
- 生命周期：BackgroundMaintainer（非 Agent 无 LLM）统一 start/stop/cleanup
- 后台 Loop 的角色是"记账者+提醒者"——不注入上下文，
  只写状态（live_sessions）+发提醒；话题摘要从 live_chat 读观众弹幕
  （单一事实源，与写路径同源）

职责：
- **轻循环**（periodic tick ~5s）：
  - 直播间状态记账（热度/统计 → 写 live_sessions 表）
  - 话题增量聚合（关键词计数 O(1)，内存态）
  - 空转检测信号（由 ProactiveTrigger 承载；流程单超时提醒见 rundown_overdue 触发源）
  - 窗口滑动检查（事件量/时间阈值 → put 压缩队列）
- **压缩 worker**（asyncio.Queue 触发）：
  - queue.get() → LLM 压缩（一次调用：时间线摘要 + 话题总结句）→ 写摘要层
  - 并发 = 1（顺序保证：摘要块必须按时间序——乱序 = 倒叙）
  - 失败可丢弃可重算；LLM 用 chat_fast（不抢主决策优先级）
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger
from src.modules.prompts import PromptManager, get_prompt_manager
from src.modules.time_utils import now_ms as _real_now_ms

from .canonical import canonical_content
from .room_state import RoomState

__all__ = ["BackgroundMaintainer"]


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

_DEFAULT_LIGHT_TICK_MS = 5_000
_DEFAULT_COLD_TIMEOUT_MS = 60_000
_DEFAULT_SUMMARY_INTERVAL_MS = 60_000
# 摘要专用 LLM profile（与 Planner / Replyer 隔离；model.toml [llm_profiles.summary]）
_DEFAULT_SUMMARY_CLIENT = "summary"
# 窗口触发压缩的条数阈值
_DEFAULT_WINDOW_EVENT_THRESHOLD = 200
# 压缩队列上限（与 StreamerCompressorConfig.queue_max 默认对齐）
_DEFAULT_COMPRESSOR_QUEUE_MAX = 100
# 压缩 worker 并发（与 StreamerCompressorConfig.concurrency 默认对齐）
_DEFAULT_COMPRESSOR_CONCURRENCY = 1
# 高价值事件记忆去抖窗口（同一用户相邻写入最小间隔，毫秒）
_EVENT_INGEST_DEBOUNCE_MS = 60_000

# 摘要系统提示词模板键（正文见 src/agents/streamer/prompts/summary_system.md，
# 由包内 prompts/ 目录内聚承载）
_SUMMARY_SYSTEM_TEMPLATE = "summary_system"


def _cfg(config: Any, key: str, default: Any) -> Any:
    """从配置对象读取字段值（dict 用 .get，其他对象用 getattr 兜底）。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class BackgroundMaintainer:
    """主播 Agent 后台维护器（双任务模型：轻循环 + 压缩 worker）。

    非 Agent（无 LLM 主决策权），纯机械循环 + 后台压缩任务。
    通过构造器注入依赖（room_state / storage store / llm_service）。

    记忆写入面：摘要成功落地后 ``await memory.ingest(...)`` 写入"topic_summary"
    事实；并通过 ``EventBus`` 订阅高价值事件（礼物 / SC），同样写入事实记忆。
    两者均做异常降级，避免下游故障阻塞后台记账主循环。
    """

    def __init__(
        self,
        config: Any,
        *,
        room_state: RoomState,
        llm_service: Optional[Any] = None,
        sessions_repo: Optional[Any] = None,
        chat_repo: Optional[Any] = None,
        topic_repo: Optional[Any] = None,
        session_manager: Optional[Any] = None,
        memory: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        prompt_manager: Optional[PromptManager] = None,
    ) -> None:
        """初始化。

        Args:
            config: 配置字典或对象；读取
                - ``enabled``（默认 True）
                - ``light_tick_ms``（默认 5000）
                - ``cold_timeout_ms``（默认 60000）
                - ``summary_interval_ms``（默认 60000）
                - ``summary_client``（默认 ``summary``）
                - ``window_event_threshold``（默认 200）
                - ``compressor_concurrency``（默认 1）
                - ``compressor_queue_max``（默认 100）
            room_state: ``RoomState`` 实例（轻循环读取快照）
            llm_service: LLM 管理器（可选；压缩 worker 调用）
            sessions_repo: ``SessionRepo``（轻循环写场次实时状态）
            session_manager: 场次管理器（``LiveSessionManager`` 或鸭子类型；
                提供 ``async resolve_pk() -> Optional[int]``）。心跳、话题快照
                与话题摘要的场次归属经它解析（与 live_chat 写路径同源）；
                ``None`` 时相关路径整体降级跳过。
            memory: 记忆后端（鸭子类型 ``MemoryProvider``）。``None`` 时关闭
                摘要/事件两路写入功能——BackgroundMaintainer 整体降级为"只记账"。
            event_bus: 可选 ``EventBus``；提供时 ``start()`` 阶段订阅礼物/SC 事件。
            chat_repo: 可选 ``ChatRepo``；提供时话题摘要读取 live_chat
                最近观众行（``sender_role="viewer"``）。
            topic_repo: 可选 ``TopicRepo``；提供时每次摘要成功后写
                ``timeline_summary``（摘要历史）与 ``topics``（当前话题快照投影）。
            prompt_manager: ``PromptManager`` 实例（摘要系统提示词经其渲染）。
                ``None`` 时回退全局单例 ``get_prompt_manager()``（惰性、仅首次
                使用时触发，避免未触达摘要路径的测试被动加载全仓模板）。
        """
        self._config = config
        self._room_state = room_state
        self._llm_service = llm_service
        self._sessions_repo = sessions_repo
        self._chat_repo = chat_repo
        self._session_manager = session_manager
        # 写入面——memory / event_bus / chat_repo / topic_repo 由 main.py 装配；None 时各自降级
        self._memory = memory
        self._event_bus = event_bus
        self._topic_repo = topic_repo
        # 提示词面——prompt_manager 由 StreamerAgent 构造透传；None 时首次使用回退全局单例
        self._prompt_manager = prompt_manager
        # 摘要系统提示词渲染缓存（零变量模板，渲染结果恒定）
        self._summary_system_prompt: Optional[str] = None
        # 同用户去抖时间戳表（user_id → last_ingest_ms）
        self._last_ingest_ms: Dict[str, int] = {}
        self._subscribed = False
        self._logger = get_logger("BackgroundMaintainer")

        self._enabled: bool = bool(_cfg(config, "enabled", True))
        self._light_tick_ms: int = _cfg(config, "light_tick_ms", _DEFAULT_LIGHT_TICK_MS)
        self._cold_timeout_ms: int = _cfg(config, "cold_timeout_ms", _DEFAULT_COLD_TIMEOUT_MS)
        self._summary_interval_ms: int = _cfg(config, "summary_interval_ms", _DEFAULT_SUMMARY_INTERVAL_MS)
        self._summary_client: str = _cfg(config, "summary_client", _DEFAULT_SUMMARY_CLIENT)
        self._window_event_threshold: int = _cfg(config, "window_event_threshold", _DEFAULT_WINDOW_EVENT_THRESHOLD)

        self._light_task: Optional[asyncio.Task] = None
        self._compress_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_summary_ms: int = 0
        self._compress_queue: asyncio.Queue = asyncio.Queue(
            maxsize=_cfg(config, "compressor_queue_max", _DEFAULT_COMPRESSOR_QUEUE_MAX)
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动轻循环 + 压缩 worker（创建 asyncio.Task）。

        ``config.enabled=False`` 时整体短路：轻循环不跑、压缩 worker 不创建、
        高价值事件也不订阅——所有后台维护功能降级为"关闭"。
        """
        if self._running:
            return
        if not self._enabled:
            self._logger.info("BackgroundMaintainer 配置 enabled=false，跳过启动")
            return
        self._running = True
        # 记忆写入面：高价值事件订阅（礼物 / SC）→ memory.ingest
        # 仅当 memory 与 event_bus 同时存在时启用（功能可关闭）
        if self._event_bus is not None and self._memory is not None:
            self._subscribe_high_value_events()
        self._light_task = asyncio.create_task(self._light_loop())
        self._compress_task = asyncio.create_task(self._compress_loop())
        self._logger.info(
            f"BackgroundMaintainer 已启动 "
            f"(light_tick={self._light_tick_ms}ms, "
            f"summary_interval={self._summary_interval_ms}ms, "
            f"window_threshold={self._window_event_threshold}, "
            f"memory={'on' if self._memory is not None else 'off'}, "
            f"event_bus={'on' if self._event_bus is not None else 'off'})"
        )

    async def stop(self) -> None:
        """停止双任务（取消 asyncio.Task + 排空队列）。"""
        self._running = False
        for task in (self._light_task, self._compress_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._light_task = None
        self._compress_task = None
        self._logger.info("BackgroundMaintainer 已停止")

    # ------------------------------------------------------------------
    # 记忆写入面：摘要 ingest + 高价值事件订阅
    # ------------------------------------------------------------------

    async def _ingest_topic_summary(self, summary: str) -> None:
        """把摘要成功落地的 topic_summary 写入记忆。

        调用契约：仅在 ``_summarize_topic`` 成功拿到非空 summary 后调用。
        异常降级——下游故障不应阻塞后台记账主循环。
        """
        if self._memory is None or not summary:
            return
        try:
            await self._memory.ingest(
                text=summary,
                source="topic_summary",
                tags=["topic", "auto_summary"],
            )
        except Exception as exc:
            # ingest 失败仅记 warning，不阻断后台主循环
            self._logger.warning(f"记忆写入失败 (topic_summary): {exc}")

    def _subscribe_high_value_events(self) -> None:
        """订阅礼物 / SC 事件（仅在 memory 与 event_bus 都注入时启用）。"""
        assert self._event_bus is not None  # noqa: S101  start() 已 guard
        # 防重复订阅：subscribe 标识——start 多次调用只挂一次
        if getattr(self, "_subscribed", False):
            return
        self._event_bus.on(
            CoreEvents.ROOM_MESSAGE_GIFT,
            self._handle_memory_event,
            model_class=RoomMessagePayload,
        )
        self._event_bus.on(
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            self._handle_memory_event,
            model_class=RoomMessagePayload,
        )
        self._subscribed = True
        self._logger.info("BackgroundMaintainer 已订阅礼物/SC 事件 → 记忆 ingest")

    async def _handle_memory_event(
        self,
        event_name: str,
        payload: RoomMessagePayload,
        source: str,
    ) -> None:
        """处理礼物 / SC 事件：格式化中文事实 → memory.ingest。

        去抖策略：60 秒内同一 user_id 只写一次（成员 dict 记 last_ingest_ms），
        避免高价值事件高频刷屏时把记忆库塞爆。
        """
        if self._memory is None:
            return
        try:
            user_id = getattr(payload.user, "id", "") or ""
            nickname = getattr(payload.user, "name", "") or "观众"

            # 按事件类型拼事实文本
            if payload.message_type == "gift":
                gift_name = getattr(payload.gift, "name", "礼物") if payload.gift else "礼物"
                count = getattr(payload.gift, "count", 1) if payload.gift else 1
                fact = (
                    f"{nickname} 送出礼物 {gift_name}（×{count}）" if count > 1 else f"{nickname} 送出礼物 {gift_name}"
                )
                tags = ["gift"]
            elif payload.message_type == "super_chat":
                amount = getattr(payload.sc, "amount", 0.0) if payload.sc else 0.0
                text = (payload.content or "").strip()
                if text:
                    fact = f"{nickname} 发送 SC（¥{amount:.0f}）：{text}"
                else:
                    fact = f"{nickname} 发送 SC（¥{amount:.0f}）"
                tags = ["super_chat"]
            else:
                return

            # 同用户 60 秒去抖
            if user_id:
                now = _real_now_ms()
                last_map = getattr(self, "_last_ingest_ms", {})
                last = last_map.get(user_id, 0)
                if last and now - last < _EVENT_INGEST_DEBOUNCE_MS:
                    return
                last_map[user_id] = now

            await self._memory.ingest(text=fact, source="live_event", tags=tags)
        except Exception as exc:
            # ingest 失败仅记 warning——下游故障不阻断记账主循环
            self._logger.warning(f"高价值事件记忆写入失败 ({event_name}): {exc}")

    # ------------------------------------------------------------------
    # 轻循环（周期 tick ~5s）
    # ------------------------------------------------------------------

    async def _light_loop(self) -> None:
        """轻循环主入口（纯机械，永不被阻塞）。"""
        interval = max(self._light_tick_ms / 1000.0, 0.1)
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self._light_tick()
                except Exception as exc:
                    self._logger.error(f"BackgroundMaintainer 轻循环 tick 异常: {exc}", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _light_tick(self, *, now_ms: Optional[int] = None) -> None:
        """单次轻 tick：记账 + 摘要门控 + 窗口滑动检查。"""
        ts = now_ms if now_ms is not None else _real_now_ms()

        # 1. 写 live_sessions（热度/计数快照）
        if self._sessions_repo is not None:
            try:
                await self._write_live_session(ts)
            except Exception as exc:
                self._logger.warning(f"写 live_sessions 失败: {exc}")

        # 2. 摘要门控（按热度间隔；上次摘要后无新弹幕则跳过 LLM 调用）
        try:
            await self._maybe_summarize(ts)
        except Exception as exc:
            self._logger.warning(f"摘要门控失败: {exc}")

        # 3. 窗口滑动检查（事件量/时间阈值 → put 压缩队列）
        try:
            self._check_compression_window(ts)
        except Exception as exc:
            self._logger.warning(f"压缩窗口检查失败: {exc}")

    async def _write_live_session(self, now_ms: int) -> None:
        """把当前 RoomState 快照写入 live_sessions 表（后台记账，每轻 tick 一次心跳）。

        场次归属经 ``LiveSessionManager.resolve_pk()`` 解析（显式场次进行中
        取其主键，否则默认场次）；管理器缺失时降级跳过——心跳不建行，
        场次行的创建/结账归 LiveSessionManager。
        """
        if self._sessions_repo is None or self._session_manager is None:
            return
        snapshot = self._room_state.get_snapshot(now_ms=now_ms)
        # 热度数字映射：low=1, medium=2, high=3
        heat_map = {"low": 1, "medium": 2, "high": 3}
        heat_int = heat_map.get(snapshot.heat, 1)

        try:
            live_pk = await self._session_manager.resolve_pk()
        except Exception as exc:  # noqa: BLE001 记账降级，不阻断轻循环
            self._logger.warning(f"场次归属解析失败，跳过本次心跳: {exc}")
            return
        await self._sessions_repo.update_live_session_stats(
            live_session_id=live_pk,
            heat=heat_int,
            viewer_count=0,  # TODO: 接入观众统计
            audience_total=0,
            updated_at_ms=now_ms,
        )

    async def _maybe_summarize(self, now_ms: int) -> None:
        """摘要门控：按热度频率调用 LLM（走 chat_fast profile）。"""
        if self._llm_service is None or self._chat_repo is None:
            return
        snap = self._room_state.get_snapshot(now_ms=now_ms)
        interval = self._interval_for_heat(snap.heat)
        if now_ms - self._last_summary_ms < interval:
            return

        last_msg_ms = self._room_state.last_message_ms
        if last_msg_ms is not None and last_msg_ms <= self._last_summary_ms:
            return

        # 把压缩任务放入压缩队列（而非同步调用 → 不阻塞轻循环）
        try:
            self._compress_queue.put_nowait({"type": "summary", "now_ms": now_ms})
        except asyncio.QueueFull:
            self._logger.warning("压缩队列已满，丢弃本次摘要请求")

    def _interval_for_heat(self, heat: str) -> int:
        """根据热度调整摘要频率（高热 → 半间隔）。"""
        base = self._summary_interval_ms
        if heat == "high":
            return max(base // 2, 5_000)
        return base

    def _check_compression_window(self, now_ms: int) -> None:
        """窗口滑动检查：事件量/时间阈值 → put 压缩队列。"""
        # 暂用 last_message_ms + size 触发；当前为基于热度阈值的简化判定
        snap = self._room_state.get_snapshot(now_ms=now_ms)
        # TODO: 后续可接入更复杂的窗口判定（事件量 > threshold）
        # 当前实现：每 5 分钟触发一次窗口压缩（与 summary 同步）
        if snap.topics and len(snap.topics) >= 5:
            try:
                self._compress_queue.put_nowait({"type": "window", "now_ms": now_ms})
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    # 压缩 worker（asyncio.Queue 触发）
    # ------------------------------------------------------------------

    async def _compress_loop(self) -> None:
        """压缩 worker 主入口（并发=1，顺序保证）。"""
        try:
            while self._running:
                try:
                    task = await asyncio.wait_for(
                        self._compress_queue.get(),
                        timeout=self._light_tick_ms / 1000.0,
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise

                try:
                    await self._handle_compress_task(task)
                except Exception as exc:
                    self._logger.error(f"压缩 worker 处理失败: {exc}", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _handle_compress_task(self, task: Dict[str, Any]) -> None:
        """处理压缩任务（当前支持 summary / window 两种类型）。"""
        task_type = task.get("type")
        if task_type == "summary":
            await self._summarize_topic(task.get("now_ms", _real_now_ms()))
        # 其它类型（暂不实现；留给后续）

    async def _summarize_topic(self, now_ms: int) -> None:
        """调 LLM 生成话题摘要（chat_fast profile）。

        摘要输入 = live_chat 当前场次的最近 viewer 行（"真实观众弹幕"语义
        由 SQL 的 ``sender_role='viewer'`` 过滤承载——主播发言行是
        assistant、礼物/SC 不落 live_chat）。无显式场次时静默跳过；
        窗口内无观众弹幕（仅主播自嗨）则清空 topic_summary 防自嗨循环。
        """
        if self._llm_service is None or self._chat_repo is None or self._session_manager is None:
            return
        try:
            live_pk = await self._session_manager.resolve_pk()
            if live_pk is None:
                return
            rows = await self._chat_repo.list_recent_live_chat(
                live_session_id=live_pk,
                limit=20,
                sender_role="viewer",
            )
        except Exception as exc:
            self._logger.warning(f"读取 live_chat 观众弹幕失败: {exc}")
            return

        if not rows:
            # 清空 topic_summary（防自嗨循环）
            self._room_state.set_topic_summary("", now_ms=now_ms)
            self._last_summary_ms = now_ms
            return

        # 摘要输入由 canonical 映射派生（与 Planner/Replyer 同源；此处只取 content）
        history_text = "\n".join(
            canonical_content(role="user", nickname=row["sender_name"] or "观众", text=row["content"]) for row in rows
        )
        if not history_text.strip():
            return

        prompt = f"以下是最近直播间弹幕历史，请总结当前讨论的主要话题：\n\n{history_text}"
        try:
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self._summary_client,
                system_message=self._get_summary_system_prompt(),
            )
        except Exception as exc:
            self._logger.warning(f"话题摘要 LLM 调用异常: {exc}")
            return

        if getattr(response, "success", False) and getattr(response, "content", None):
            summary = response.content.strip()
            self._room_state.set_topic_summary(summary, now_ms=now_ms)
            previous_summary_ms = self._last_summary_ms
            self._last_summary_ms = now_ms
            self._logger.debug(f"话题摘要已更新: {summary[:50]}")
            # 摘要落地 → 记忆 + 存储两路写入，失败各自降级不阻断记账
            await self._ingest_topic_summary(summary)
            await self._persist_topic_snapshot(summary, now_ms=now_ms, previous_summary_ms=previous_summary_ms)
        else:
            self._logger.warning("话题摘要 LLM 返回失败")

    def _get_summary_system_prompt(self) -> str:
        """渲染摘要系统提示词（零变量模板，结果缓存复用）。

        PromptManager 构造注入；未注入时回退全局单例 ``get_prompt_manager()``
        （该单例启用 src/**/prompts/ 约定扫描，可发现包内 summary_system 模板）。
        """
        if self._summary_system_prompt is None:
            manager = self._prompt_manager or get_prompt_manager()
            self._summary_system_prompt = manager.render(_SUMMARY_SYSTEM_TEMPLATE)
        return self._summary_system_prompt

    async def _persist_topic_snapshot(self, summary: str, *, now_ms: int, previous_summary_ms: int) -> None:
        """摘要成功后把话题状态写入 ``timeline_summary`` 与 ``topics`` 表。

        - ``timeline_summary``：一行一段摘要历史，窗口为 [上次摘要时刻, 本次]
        - ``topics``：当前话题快照投影——先清本场旧行再插最新关键词 + 摘要句，
          消费者读到的永远是当前话题状态（历史轨迹由 timeline_summary 承担）
        - 场次归属经 ``LiveSessionManager.resolve_pk()`` 解析；管理器缺失时降级跳过

        异常降级：落库失败仅 warning，不阻断后台记账循环。
        """
        if self._topic_repo is None or self._session_manager is None:
            return
        try:
            live_pk = await self._session_manager.resolve_pk()
            window_start = previous_summary_ms or max(now_ms - self._summary_interval_ms, 0)
            await self._topic_repo.insert_timeline_summary(
                live_session_id=live_pk, start_ms=window_start, end_ms=now_ms, summary=summary, tags=None
            )
            snapshot = self._room_state.get_snapshot(now_ms=now_ms)
            await self._topic_repo.delete_session_topics(live_session_id=live_pk)
            for rank, keyword in enumerate(snapshot.topics):
                await self._topic_repo.insert_topic(
                    live_session_id=live_pk,
                    label=keyword,
                    source="word_freq",
                    score=1.0 / (rank + 1),
                    trend=0.0,
                    duration_ms=self._summary_interval_ms,
                )
            if summary:
                await self._topic_repo.insert_topic(
                    live_session_id=live_pk,
                    label=summary,
                    source="summary",
                    score=1.0,
                    trend=0.0,
                    duration_ms=self._summary_interval_ms,
                    count=1,
                )
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志，不阻断记账循环
            self._logger.warning(f"话题快照落库失败（timeline_summary/topics）: {exc}")
