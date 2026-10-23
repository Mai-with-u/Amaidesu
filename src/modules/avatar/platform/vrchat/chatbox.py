"""VRChat 头顶聊天框：字幕的又一个显示面

作为字幕服务的后端注册（``SubtitleBackend`` 结构契约），跟随字幕服务已经选定的
那一路字幕源（跟随 TTS 播放或编排层直出），不另起一路：字幕显示一句 → 聊天框
显示同一句，字幕清空 → 聊天框清空。

VRChat 对聊天框有两条限制，这里就地处理：

- 单条最多 144 字，超出截断并以省略号收尾；
- 发送过快会被冷却（社区实测约每 1.5 秒一条），两条之间至少隔
  ``min_interval_ms``；间隔内来了多条只发最新一条（字幕本来就是"当前这句"）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from src.modules.logging import get_logger

# 聊天框单条字数上限（VRChat 限制）
CHATBOX_MAX_CHARS = 144


def fit_chatbox_text(text: str) -> str:
    """压成单段并截到聊天框上限：换行并成空格，超长截断加省略号"""
    flat = " ".join(text.split())
    if len(flat) <= CHATBOX_MAX_CHARS:
        return flat
    return flat[: CHATBOX_MAX_CHARS - 1] + "…"


class VRChatChatboxBackend:
    """VRChat 聊天框字幕后端（发送节流 + 只保留最新一条）"""

    def __init__(
        self,
        send_text: Callable[[str], Any],
        *,
        min_interval_ms: int = 1600,
        enabled: bool = True,
    ) -> None:
        """Args:
        send_text: 发出一条聊天框文本（空串 = 清空）；由 VRChat 适配器提供，未连接时自行跳过
        min_interval_ms: 两条之间的最短间隔
        enabled: 关闭时 show / clear 直接忽略
        """
        self._send_text = send_text
        self._min_interval_s = min_interval_ms / 1000
        self.enabled = enabled
        self._pending: Optional[str] = None
        self._last_sent_at = float("-inf")
        self._flush_task: Optional[asyncio.Task] = None
        self.logger = get_logger("VRChatChatbox")

    async def show(self, text: str, utterance_id: Optional[str] = None) -> None:
        """显示一句：放进待发槽，节流到点后发出"""
        if self.enabled:
            self._queue(fit_chatbox_text(text))

    async def clear(self) -> None:
        """清空：发空串让 VRChat 立即收起聊天框"""
        if self.enabled:
            self._queue("")

    def _queue(self, text: str) -> None:
        # 新内容直接覆盖待发槽（间隔内的旧句不再发）；已有在途等待任务时由它取最新一条
        self._pending = text
        if self._flush_task is not None and not self._flush_task.done():
            return
        # 距上一条已满间隔 → 立即发出；否则起一个任务等到点再发
        if time.monotonic() - self._last_sent_at >= self._min_interval_s:
            self._send_pending()
        else:
            self._flush_task = asyncio.create_task(self._flush(), name="VRChat_Chatbox")

    async def _flush(self) -> None:
        # 等到距上一条满最短间隔 → 取槽里最新的一条发出；等待期间来的新句会覆盖槽
        while self._pending is not None:
            wait = self._last_sent_at + self._min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._send_pending()

    def _send_pending(self) -> None:
        text, self._pending = self._pending, None
        if text is None:
            return
        try:
            self._send_text(text)
        except Exception as exc:  # noqa: BLE001 - 聊天框失败不影响其他字幕面
            self.logger.error(f"发送聊天框失败: {type(exc).__name__}: {exc}", exc=True)
        self._last_sent_at = time.monotonic()

    async def close(self) -> None:
        """停用并取消在途发送（适配器清理时调用）"""
        self.enabled = False
        self._pending = None
        task, self._flush_task = self._flush_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                self.logger.debug("聊天框发送任务已取消")


__all__ = ["CHATBOX_MAX_CHARS", "VRChatChatboxBackend", "fit_chatbox_text"]
