"""RoomStateLoop - 直播间后台预处理循环

低频 LLM 话题摘要：周期性读取 ContextService 历史 → 提炼话题摘要 → 存入
``room_state.snapshot.topic_summary``。频率由热度驱动，冷场时仍按热度间隔更新（效果优先）。

设计要点：
- **不订阅 EventBus**：热度信号由 ``AmaidesuDecider.decide()`` 调用
  ``room_state.update()`` 驱动，Loop 只读快照。
- **独立 client**：摘要用独立 profile ``llm_summary``，不与前台 Planner（``llm_fast``）共用连接池。
- **可注入时钟**：``_tick(now_ms=...)`` 接受确定性时间戳，测试无需 sleep。
- **热度驱动频率**：high → 间隔减半（30s），medium → 基准间隔（60s），
  low → 间隔翻倍（120s），冷场时仍按热度间隔更新（效果优先）。
"""

import asyncio
from typing import Any, Optional

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms as _real_now_ms
from src.stages.decision.deciders.amaidesu.room_state import RoomState

__all__ = ["RoomStateLoop"]

_DEFAULT_TICK_INTERVAL_MS = 5_000
_DEFAULT_COLD_TIMEOUT_MS = 60_000
_DEFAULT_SUMMARY_INTERVAL_MS = 60_000
# 独立 profile：LLMManager 按 profile 名缓存 client 实例，使用与 planner_client（llm_fast）
# 不同的 profile 名才能拿到独立 client 实例，避免共享连接池（Task 8 约束）
_SUMMARY_CLIENT = "llm_summary"
_SUMMARY_HISTORY_LIMIT = 20

_SYSTEM_PROMPT = (
    "你是直播话题摘要助手。根据最近的弹幕对话历史，用一句话（不超过30字）"
    "总结当前直播间讨论的主要话题。只输出摘要内容，不要添加额外说明。"
)


def _cfg(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class RoomStateLoop:
    """直播间后台预处理循环（低频 LLM 话题摘要）

    生命周期：``start()`` → 后台 ``_loop()`` 周期调用 ``_tick()``
    → ``stop()`` 取消任务。
    """

    def __init__(
        self,
        config: Any,
        room_state: RoomState,
        llm_service,
        prompt_service=None,
        context_service=None,
        *,
        session_id: str = "live",
    ):
        self._room_state = room_state
        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._context_service = context_service
        self._session_id = session_id
        self.logger = get_logger("RoomStateLoop")

        self._enabled: bool = _cfg(config, "room_state_enabled", True)
        self._cold_timeout_ms: int = _cfg(config, "room_state_cold_timeout_ms", _DEFAULT_COLD_TIMEOUT_MS)
        self._summary_interval_ms: int = _cfg(
            config, "room_state_llm_summary_interval_ms", _DEFAULT_SUMMARY_INTERVAL_MS
        )
        self._tick_interval_ms: int = _cfg(config, "room_state_tick_interval_ms", _DEFAULT_TICK_INTERVAL_MS)
        self._summary_client: str = _cfg(config, "room_state_summary_client", _SUMMARY_CLIENT)

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_summary_ms: int = 0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._enabled:
            self.logger.info("RoomStateLoop 已禁用（room_state_enabled=False），跳过启动")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self.logger.info(
            f"RoomStateLoop 已启动 (冷场阈值={self._cold_timeout_ms}ms, "
            f"摘要间隔={self._summary_interval_ms}ms, client={self._summary_client})"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.logger.info("RoomStateLoop 已停止")

    async def _loop(self) -> None:
        interval = max(self._tick_interval_ms / 1000.0, 0.1)
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self._tick()
                except Exception as e:
                    self.logger.error(f"RoomStateLoop tick 异常: {e}", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _tick(self, *, now_ms: Optional[int] = None) -> None:
        ts = now_ms if now_ms is not None else _real_now_ms()

        if not self._enabled:
            return

        snap = self._room_state.get_snapshot(now_ms=ts)
        interval = self._interval_for_heat(snap.heat)
        if ts - self._last_summary_ms < interval:
            return

        await self._summarize(now_ms=ts)

    def _interval_for_heat(self, heat: str) -> int:
        base = self._summary_interval_ms
        if heat == "high":
            return max(base // 2, 5_000)
        if heat == "low":
            return base * 2
        return base

    async def _summarize(self, *, now_ms: int) -> None:
        if self._context_service is None or self._llm_service is None:
            return

        try:
            history = await self._context_service.get_history(self._session_id, limit=_SUMMARY_HISTORY_LIMIT)
        except Exception as e:
            self.logger.warning(f"读取 ContextService 历史失败: {e}")
            return

        if not history:
            return

        history_text = self._format_history(history)
        if not history_text.strip():
            return

        prompt = f"以下是最近直播间弹幕历史，请总结当前讨论的主要话题：\n\n{history_text}"

        try:
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self._summary_client,
                system_message=_SYSTEM_PROMPT,
            )
        except Exception as e:
            self.logger.error(f"话题摘要 LLM 调用异常: {e}", exc_info=True)
            return

        if response.success and response.content:
            summary = response.content.strip()
            self._room_state.set_topic_summary(summary, now_ms=now_ms)
            self._last_summary_ms = now_ms
            self.logger.debug(f"话题摘要已更新: {summary[:50]}")
        else:
            self.logger.warning(f"话题摘要 LLM 返回失败: {response.error if response else 'None'}")

    @staticmethod
    def _format_history(history: list) -> str:
        lines = []
        for msg in history:
            role = getattr(msg, "role", None)
            role_str = getattr(role, "value", str(role)) if role else "user"
            content = getattr(msg, "content", "") or ""
            lines.append(f"{role_str}: {content}")
        return "\n".join(lines)
