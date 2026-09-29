"""录制回放引擎：把 ``live_chat`` 业务表中的历史弹幕按原节奏重新发射。

录制源是 ``live_chat`` 表（消息流的单一事实源，真实直播与模拟数据同表、
以 ``simulated`` 列区分）。本引擎取出指定日期的 ``danmaku`` 行、还原成
``RoomMessagePayload``，按相邻消息的毫秒时间戳差值调度重放。回放消息统一
携带 ``simulated=True`` 溯源标记——它们是"被重新注入的输入流"，不进真实
数据统计。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.logging import get_logger
from src.modules.simulator.config_schema import SimulatorConfigSchema

if TYPE_CHECKING:
    from src.modules.storage.repos import ChatRepo


class ReplayEngine:
    """单日录制回放队列。

    生命周期：``load(date)`` 构建队列 → 调用方逐条 ``pop_next()`` 并按
    ``next_gap_seconds()`` 的间隔调度 emit → 队列空即回放结束。
    """

    def __init__(self, config: SimulatorConfigSchema, chat_repo: "ChatRepo") -> None:
        self._config = config
        self._chat_repo = chat_repo
        self.logger = get_logger("ReplayEngine")
        self._queue: List[RoomMessagePayload] = []
        self._cursor = 0
        self._replay_date: Optional[str] = None

    @property
    def replay_date(self) -> Optional[str]:
        """当前加载的录制日期（未加载为 None）。"""
        return self._replay_date

    @property
    def remaining(self) -> int:
        """尚未回放的消息条数。"""
        return len(self._queue) - self._cursor

    @property
    def total(self) -> int:
        """本次回放加载的总条数。"""
        return len(self._queue)

    async def load(self, date_str: str, *, simulated_only: Optional[bool] = None) -> int:
        """读取指定日期录制并构建回放队列，返回加载条数。

        Args:
            date_str: 录制日期（``YYYY-MM-DD``，本地时区）
            simulated_only: 是否仅回放录制时已标记 simulated 的消息；
                None 时用配置的 ``replay_simulated_only``。
        """
        if simulated_only is None:
            simulated_only = self._config.replay_simulated_only

        rows = await self._chat_repo.list_danmaku_by_date(date_str, simulated_only=simulated_only)

        entries: List[RoomMessagePayload] = []
        for row in rows:
            try:
                payload = RoomMessagePayload(
                    # 行序还原：业务列 → payload 字段（写入映射见 storage_ledger）
                    message_type="danmaku",
                    user=RoomMessageUser(id=row["sender_id"] or "", name=row["sender_name"] or ""),
                    content=row["content"],
                    message_id=row["message_id"] or "",
                    timestamp_ms=row["timestamp_ms"],
                    # 回放消息一律标记 simulated=True：回放是重新注入的输入流，
                    # 不继承录制行原值，避免真实历史弹幕再次进入存储污染统计
                    simulated=True,
                    # live_session_id 统一清零：回放事件经场次盖章拦截器
                    # 归属到当前回放场次，而非沿用历史场次主键
                    live_session_id=0,
                )
            except Exception as exc:
                self.logger.debug(f"回放跳过无法解析的录制行: {exc}")
                continue
            if not payload.content:
                continue
            entries.append(payload)

        # 行序即时间正序，这里仅防御乱序录制：按 timestamp_ms 稳定排序
        entries.sort(key=lambda p: p.timestamp_ms)
        self._queue = entries
        self._cursor = 0
        self._replay_date = date_str if entries else None
        self.logger.info(f"回放队列已加载: date={date_str} 条数={len(self._queue)} simulated_only={simulated_only}")
        return len(self._queue)

    def next_gap_seconds(self) -> float:
        """距下一条消息的调度间隔（秒）。

        由相邻消息的 ``timestamp_ms`` 差值除以回放速度得到；首条消息无前驱
        间隔为 0；超长冷场按 ``replay_gap_cap_s`` 截断。
        """
        if self._cursor >= len(self._queue):
            return 0.0
        if self._cursor == 0:
            return 0.0
        gap_ms = self._queue[self._cursor].timestamp_ms - self._queue[self._cursor - 1].timestamp_ms
        gap_s = max(0.0, gap_ms / 1000.0) / max(self._config.replay_speed, 0.1)
        return min(gap_s, self._config.replay_gap_cap_s)

    def pop_next(self) -> Optional[RoomMessagePayload]:
        """弹出下一条待回放消息；队列耗尽返回 None。"""
        if self._cursor >= len(self._queue):
            return None
        payload = self._queue[self._cursor]
        self._cursor += 1
        return payload


__all__ = ["ReplayEngine"]
