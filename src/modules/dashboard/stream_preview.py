"""思考流旁路 hub：缓冲 + 合帧 + WebSocket 直推（ADR-008）。

实现 ``ThinkingStreamSink`` 结构契约（agents 侧 Protocol 的鸭子匹配，
不产生 import 依赖）。delta 在 flush 窗口内合帧为单条 WS 流消息批量推送，
窗口语义：

- 同步 ``on_thinking_delta`` 只做缓冲 append（Agent 决策循环内调用，禁止慢操作）
- 惰性 flush 循环：首批 delta 到达时创建，空闲时自动退出，下批再启
- 推送失败仅记 debug（best-effort 契约：不重试、不持久化、断线丢尾部）
"""

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional

from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.websocket.handler import WebSocketHandler

logger = get_logger("StreamPreviewHub")

THINKING_STREAM_TYPE = "thinking.delta"


class StreamPreviewHub:
    """思考流旁路 hub（观察面专用，ADR-008）。

    Agent 侧经 ``ThinkingStreamSink`` Protocol 注入；dashboard 侧实现。
    """

    def __init__(
        self,
        ws_handler: Optional["WebSocketHandler"] = None,
        *,
        flush_interval_ms: int = 100,
        buffer_max: int = 400,
    ) -> None:
        """ws_handler 可延迟绑定：Agent 装配先于 dashboard 启动，由 main 在
        dashboard 就绪后调用 ``attach_ws``；未绑定期间的 delta 直接丢弃
        （思考流是实时观测，启动前的增量无回放价值）。"""
        self._ws = ws_handler
        self._flush_interval_s = max(flush_interval_ms, 10) / 1000
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=buffer_max)
        self._flush_task: Optional[asyncio.Task] = None
        self._stopped = False

    def attach_ws(self, ws_handler: "WebSocketHandler") -> None:
        """dashboard 启动后绑定 WS 通道（幂等）。"""
        self._ws = ws_handler

    # ==== ThinkingStreamSink 契约（同步，Agent 决策循环内调用）====

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None:
        """缓冲一条思考增量（同步；仅 append + 惰性启 flush，无 IO）。"""
        if self._stopped:
            return
        self._buffer.append(
            {
                "round_id": round_id,
                "phase": phase,
                "step": step,
                "seq": seq,
                "text_delta": text_delta,
            }
        )
        self._ensure_flush_task()

    async def stop(self) -> None:
        """停止 flush 循环（dashboard 关闭时调用）；残余缓冲丢弃（best-effort）。"""
        self._stopped = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

    # ==== 内部 ====

    def _ensure_flush_task(self) -> None:
        if self._flush_task is not None or self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无运行 loop（理论上不会发生：Agent 决策循环在 async 上下文内）——
            # 只缓冲不推送，等下次有 loop 的调用再启 flush
            return
        self._flush_task = loop.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        """合帧推送循环：窗口内有增量则批量推一条流消息，空闲即退出。"""
        try:
            while not self._stopped:
                await asyncio.sleep(self._flush_interval_s)
                if not self._buffer:
                    break
                batch: List[Dict[str, Any]] = []
                while self._buffer:
                    batch.append(self._buffer.popleft())
                if self._ws is None:
                    continue
                try:
                    await self._ws.broadcast_stream(THINKING_STREAM_TYPE, {"deltas": batch})
                except Exception as exc:
                    logger.debug(f"思考流推送失败（已忽略）: {exc}")
        except asyncio.CancelledError:
            raise
        finally:
            self._flush_task = None
