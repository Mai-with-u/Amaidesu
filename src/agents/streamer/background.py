"""BackgroundMaintainer - 主播 Agent 后台维护（Wave 6 / §1.7 双任务模型）

§1.7 定案：
- 双任务（不是 1 个、不是 N 个）：
  ① 轻循环（周期 tick ~5s，纯机械，永不被阻塞）
  ② 压缩 worker（触发驱动，等 asyncio.Queue）
- Why 2 个不回 1 个：慢任务（LLM 秒级）不冻结快任务节拍
- Why 不 N 个：快任务合并无成本、管理复杂度封顶
- 生命周期：BackgroundMaintainer（非 Agent 无 LLM）统一 start/stop/cleanup
- 后台 Loop 重定位（Wave 6）：
  从"注入上下文者"改为"记账者+提醒者"——不再注入上下文，
  只写状态（live_sessions）+发提醒；Planner 上下文统一由
  ContextAssembler 从存储/事件装配（单一路径，无多路注入冲突）

职责（Wave 6）：
- **轻循环**（periodic tick ~5s）：
  - 直播间状态记账（热度/统计 → 写 live_sessions 表）
  - 话题增量聚合（关键词计数 O(1)，内存态）
  - 空转检测信号（check_idle → emit planner.checkpoint，由 AgendaIdle 接管）
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
from src.modules.time_utils import now_ms as _real_now_ms

from .room_state import RoomState

__all__ = ["BackgroundMaintainer"]


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

_DEFAULT_LIGHT_TICK_MS = 5_000
_DEFAULT_COLD_TIMEOUT_MS = 60_000
_DEFAULT_SUMMARY_INTERVAL_MS = 60_000
# 摘要专用 LLM profile（与 Planner / Replyer 隔离）
_DEFAULT_SUMMARY_CLIENT = "llm_summary"
# 窗口触发压缩的条数阈值
_DEFAULT_WINDOW_EVENT_THRESHOLD = 200
# 压缩队列上限
_DEFAULT_COMPRESSOR_QUEUE_MAX = 100
# 高价值事件记忆去抖窗口（同一用户相邻写入最小间隔，毫秒）
_EVENT_INGEST_DEBOUNCE_MS = 60_000

# 摘要 LLM 系统提示词（与原 RoomStateLoop 同构）
_SUMMARY_SYSTEM_PROMPT = (
    "你是直播话题摘要助手。根据最近的观众弹幕，用一句话（不超过30字）"
    "总结当前直播间观众正在讨论的主要话题。只输出摘要内容，不要添加额外说明。"
)


def _cfg(config: Any, key: str, default: Any) -> Any:
    """从配置对象读取字段值（dict 用 .get，其他对象用 getattr 兜底）。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class BackgroundMaintainer:
    """主播 Agent 后台维护器（§1.7 双任务模型：轻循环 + 压缩 worker）。

    非 Agent（无 LLM 主决策权），纯机械循环 + 后台压缩任务。
    通过构造器注入依赖（room_state / storage store / llm_service）。

    §1.50 写入面：摘要成功落地后 ``await memory.ingest(...)`` 写入"topic_summary"
    事实；并通过 ``EventBus`` 订阅高价值事件（礼物 / SC），同样写入事实记忆。
    两者均做异常降级，避免下游故障阻塞后台记账主循环。
    """

    def __init__(
        self,
        config: Any,
        *,
        room_state: RoomState,
        llm_service: Optional[Any] = None,
        live_session_store: Optional[Any] = None,
        session_manager: Optional[Any] = None,
        context_service: Optional[Any] = None,
        session_id: str = "live",
        memory: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        sqlite_store: Optional[Any] = None,
    ) -> None:
        """初始化。

        Args:
            config: 配置字典或对象；读取
                - ``light_tick_ms``（默认 5000）
                - ``cold_timeout_ms``（默认 60000）
                - ``summary_interval_ms``（默认 60000）
                - ``summary_client``（默认 ``llm_summary``）
                - ``window_event_threshold``（默认 200）
            room_state: ``RoomState`` 实例（轻循环读取快照）
            llm_service: LLM 管理器（可选；压缩 worker 调用）
            live_session_store: ``live_sessions`` 存储接口（duck-typed；轻循环写状态）
            session_manager: 场次管理器（``LiveSessionManager`` 或鸭子类型；
                提供 ``async resolve_pk() -> int``）。心跳与话题快照的场次归属
                经它解析（显式场次进行中取其主键，否则默认场次）；``None``
                时两路写入整体降级跳过。
            context_service: 上下文服务（可选；供压缩 worker 读历史）
            session_id: ContextService 会话键（默认 "live"——L1 对话窗口的
                逻辑键，与存储层场次主键无关）
            memory: §1.50 记忆后端（鸭子类型 ``MemoryProvider``）。``None`` 时关闭
                摘要/事件两路写入功能——BackgroundMaintainer 整体降级为"只记账"。
            event_bus: 可选 ``EventBus``；提供时 ``start()`` 阶段订阅礼物/SC 事件。
            sqlite_store: 可选 ``SQLiteStore``；提供时每次摘要成功后写
                ``timeline_summary``（摘要历史）与 ``topics``（当前话题快照投影）。
        """
        self._config = config
        self._room_state = room_state
        self._llm_service = llm_service
        self._live_session_store = live_session_store
        self._session_manager = session_manager
        self._context_service = context_service
        self._session_id = session_id
        # 写入面——memory / event_bus / sqlite_store 由 main.py 装配；None 时各自降级
        self._memory = memory
        self._event_bus = event_bus
        self._sqlite_store = sqlite_store
        # 同用户去抖时间戳表（user_id → last_ingest_ms）
        self._last_ingest_ms: Dict[str, int] = {}
        self._subscribed = False
        self._logger = get_logger("BackgroundMaintainer")

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
        """启动轻循环 + 压缩 worker（创建 asyncio.Task）。"""
        if self._running:
            return
        self._running = True
        # §1.50 写入面：高价值事件订阅（礼物 / SC）→ memory.ingest
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
    # §1.50 写入面：摘要 ingest + 高价值事件订阅
    # ------------------------------------------------------------------

    async def _ingest_topic_summary(self, summary: str) -> None:
        """把摘要成功落地的 topic_summary 写入 §1.50 记忆。

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
            # ingest 失败仅记 warning，不阻断 §1.7 主循环
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
        """轻循环主入口（§1.7 ①：纯机械，永不被阻塞）。"""
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
        if self._live_session_store is not None:
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
        if self._live_session_store is None or self._session_manager is None:
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
        await self._live_session_store.update_live_session_stats(
            live_session_id=live_pk,
            heat=heat_int,
            viewer_count=0,  # TODO: 接入观众统计
            audience_total=0,
            updated_at_ms=now_ms,
        )

    async def _maybe_summarize(self, now_ms: int) -> None:
        """摘要门控（§1.7）：按热度频率调用 LLM（走 chat_fast profile）。"""
        if self._llm_service is None or self._context_service is None:
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
        # 暂用 last_message_ms + size 触发；Wave 6 简化为基于热度阈值
        snap = self._room_state.get_snapshot(now_ms=now_ms)
        # TODO: Wave 6 简化——后续可接入更复杂的窗口判定（事件量 > threshold）
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
        """压缩 worker 主入口（§1.7 ②：并发=1，顺序保证）。"""
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
        # 其它类型（W6 暂不实现；留给后续）

    async def _summarize_topic(self, now_ms: int) -> None:
        """调 LLM 生成话题摘要（chat_fast profile）。"""
        if self._llm_service is None or self._context_service is None:
            return
        try:
            history = await self._context_service.get_history(self._session_id, limit=20)
        except Exception as exc:
            self._logger.warning(f"读取 ContextService 历史失败: {exc}")
            return
        if not history:
            return

        # 过滤非观众弹幕（主动发言占位符 / 主播回复）
        real_history = [m for m in history if _is_real_danmaku(m)]
        if not real_history:
            # 清空 topic_summary（防自嗨循环）
            self._room_state.set_topic_summary("", now_ms=now_ms)
            self._last_summary_ms = now_ms
            return

        history_text = self._format_history(real_history)
        if not history_text.strip():
            return

        prompt = f"以下是最近直播间弹幕历史，请总结当前讨论的主要话题：\n\n{history_text}"
        try:
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self._summary_client,
                system_message=_SUMMARY_SYSTEM_PROMPT,
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

    async def _persist_topic_snapshot(self, summary: str, *, now_ms: int, previous_summary_ms: int) -> None:
        """摘要成功后把话题状态写入 ``timeline_summary`` 与 ``topics`` 表。

        - ``timeline_summary``：一行一段摘要历史，窗口为 [上次摘要时刻, 本次]
        - ``topics``：当前话题快照投影——先清本场旧行再插最新关键词 + 摘要句，
          消费者读到的永远是当前话题状态（历史轨迹由 timeline_summary 承担）
        - 场次归属经 ``LiveSessionManager.resolve_pk()`` 解析；管理器缺失时降级跳过

        异常降级：落库失败仅 warning，不阻断后台记账循环。
        """
        if self._sqlite_store is None or self._session_manager is None:
            return
        try:
            live_pk = await self._session_manager.resolve_pk()
            window_start = previous_summary_ms or max(now_ms - self._summary_interval_ms, 0)
            await self._sqlite_store.execute(
                "INSERT INTO timeline_summary (live_session_id, start_ms, end_ms, summary, tags) VALUES (?, ?, ?, ?, ?)",
                (live_pk, window_start, now_ms, summary, None),
            )
            snapshot = self._room_state.get_snapshot(now_ms=now_ms)
            await self._sqlite_store.execute(
                "DELETE FROM topics WHERE live_session_id=?",
                (live_pk,),
            )
            for rank, keyword in enumerate(snapshot.topics):
                await self._sqlite_store.execute(
                    "INSERT INTO topics (live_session_id, label, source, score, trend, duration_ms, count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (live_pk, keyword, "word_freq", 1.0 / (rank + 1), 0.0, self._summary_interval_ms, 0),
                )
            if summary:
                await self._sqlite_store.execute(
                    "INSERT INTO topics (live_session_id, label, source, score, trend, duration_ms, count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (live_pk, summary, "llm_summary", 1.0, 0.0, self._summary_interval_ms, 1),
                )
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志，不阻断记账循环
            self._logger.warning(f"话题快照落库失败（timeline_summary/topics）: {exc}")

    @staticmethod
    def _format_history(history: list) -> str:
        lines = []
        for msg in history:
            role = getattr(msg, "role", None)
            role_str = getattr(role, "value", str(role)) if role else "user"
            content = getattr(msg, "content", "") or ""
            lines.append(f"{role_str}: {content}")
        return "\n".join(lines)


def _is_real_danmaku(msg: Any) -> bool:
    """判断消息是否为真实观众弹幕（话题摘要的唯一合法输入）。"""
    role = getattr(msg, "role", None)
    role_str = getattr(role, "value", str(role)) if role else ""
    if role_str != "user":
        return False
    content = getattr(msg, "content", "") or ""
    return not content.startswith("（主动发言")
