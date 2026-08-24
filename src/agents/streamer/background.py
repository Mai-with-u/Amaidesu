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
    """

    def __init__(
        self,
        config: Any,
        *,
        room_state: RoomState,
        llm_service: Optional[Any] = None,
        live_session_store: Optional[Any] = None,
        context_service: Optional[Any] = None,
        session_id: str = "live",
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
            context_service: 上下文服务（可选；供压缩 worker 读历史）
            session_id: 当前场次 ID（默认 "live"）
        """
        self._config = config
        self._room_state = room_state
        self._llm_service = llm_service
        self._live_session_store = live_session_store
        self._context_service = context_service
        self._session_id = session_id
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
        self._light_task = asyncio.create_task(self._light_loop())
        self._compress_task = asyncio.create_task(self._compress_loop())
        self._logger.info(
            f"BackgroundMaintainer 已启动 "
            f"(light_tick={self._light_tick_ms}ms, "
            f"summary_interval={self._summary_interval_ms}ms, "
            f"window_threshold={self._window_event_threshold})"
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
        """把当前 RoomState 快照写入 live_sessions 表（§1.7 后台记账）。"""
        snapshot = self._room_state.get_snapshot(now_ms=now_ms)
        # 热度数字映射：low=1, medium=2, high=3
        heat_map = {"low": 1, "medium": 2, "high": 3}
        heat_int = heat_map.get(snapshot.heat, 1)

        if self._live_session_store is not None and hasattr(self._live_session_store, "update_live_session_heartbeat"):
            await self._live_session_store.update_live_session_heartbeat(
                session_id=self._session_id,
                heat=heat_int,
                viewer_count=0,  # TODO: 接入观众统计（W7+）
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
            self._last_summary_ms = now_ms
            self._logger.debug(f"话题摘要已更新: {summary[:50]}")
        else:
            self._logger.warning("话题摘要 LLM 返回失败")

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
