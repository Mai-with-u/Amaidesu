"""直播场次管理（LiveSessionManager）。

场次 = 一段有开始/结束边界的直播时间段；房间/频道是场次之上的静态属性，
一房多场。本模块是场次的**唯一事实源**：

- 行级写入经 ``SQLiteStore`` 的 live_sessions 领域方法（主键 AUTOINCREMENT）；
- 生命周期广播 ``live.started`` / ``live.ended`` 事件；
- 对下游（StorageLedger / 场次盖章拦截器 / 模拟器 / Dashboard API）暴露
  ``resolve_pk()``——显式场次进行中返回其主键，否则返回临时场次主键。

## 防膨胀设计

把"开场次"从进程启动的副作用变为**显式业务动作**：

- 进程启动**不**自动开新场次；
- 无显式场次期间，所有消息归属一个**临时场次**（scratch 兜底桶，固定复用
  一行）——链路行为与正式场次完全一致（落库/回灌/决策上下文），但不膨胀；
- 显式场次由手动开关或模拟器回放开启；结束时若无任何明细行则整行丢弃
  （空场次不留行）；
- 场次可删除（级联清明细），调试产生的场次随手清理。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, List, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.storage.sqlite_store import SQLiteStore

logger = get_logger("LiveSessionManager")

# 临时兜底场次的 stream_id 标记（房间语义上的"无房间"哨兵值）
SCRATCH_STREAM_ID = "__scratch__"


class LiveSessionManager:
    """直播场次管理器：开启/结束/删除场次 + 场次归属解析。

    不变量：
    - 至多一个显式进行中场次（开启新场次前自动结束旧场次）；
    - 临时场次行存在且未结束（懒创建，被删除后下次解析自动重建）；
    - 空显式场次在结账时整行丢弃。
    """

    def __init__(
        self,
        sqlite_store: "SQLiteStore",
        event_bus: "EventBus",
        *,
        platform: str = "unknown",
        room_id: str = "",
    ) -> None:
        self._store = sqlite_store
        self._event_bus = event_bus
        self._platform = platform
        self._room_id = room_id
        self._active_pk: Optional[int] = None
        self._active_source: str = ""
        self._scratch_pk: Optional[int] = None
        self._lock = asyncio.Lock()
        self._started = False

    # -------------------- 生命周期 --------------------

    async def start(self) -> None:
        """启动：收口上次残留 + 确保临时场次就位。幂等。"""
        if self._started:
            return

        # 1. 残留收口：上次进程未正常退出的"进行中"显式场次，以最后活动
        #    时刻封闭（没有可重建的真实结束边界）
        for row in await self._store.list_dangling_live_sessions():
            ended = int(row["updated_at_ms"] or row["started_at_ms"])
            await self._store.close_live_session(live_session_id=int(row["id"]), ended_at_ms=ended)
            logger.warning(
                f"收口上次残留的进行中场次: id={row['id']} title={row['title']!r} "
                f"（ended_at_ms 补为最后活动时刻 {ended}）"
            )

        # 2. 临时场次就位
        await self._ensure_scratch()

        self._started = True
        logger.info("LiveSessionManager 已启动（启动不自动开新场次；显式场次经 open_session 开启）")

    async def stop(self) -> None:
        """停止（进程退出收口由 run_shutdown 显式调 close_session，这里仅复位标记）。"""
        self._started = False

    # -------------------- 显式场次生命周期 --------------------

    async def open_session(
        self,
        *,
        title: Optional[str] = None,
        room_id: Optional[str] = None,
        platform: Optional[str] = None,
        source: str = "manual",
    ) -> int:
        """开启一场新直播场次，返回场次主键。

        已有进行中场次时先自动结束（不变量：至多一个显式进行中场次），
        随后 ``live.started`` 事件广播。
        """
        async with self._lock:
            if self._active_pk is not None:
                await self._close_active(reason="开启新场次前自动结束", ended_at_ms=now_ms())
            started = now_ms()
            pk = await self._store.insert_live_session(
                stream_id=room_id if room_id is not None else self._room_id,
                platform=platform or self._platform,
                started_at_ms=started,
                title=title,
                source=source,
            )
            self._active_pk = pk
            self._active_source = source
            await self._event_bus.emit(
                CoreEvents.LIVE_STARTED,
                LiveStartedPayload(
                    live_session_id=pk,
                    source=source,
                    title=title,
                    room_id=room_id if room_id is not None else self._room_id,
                    platform=platform or self._platform,
                    started_at_ms=started,
                ),
                source="LiveSessionManager",
            )
            logger.info(f"场次已开启: id={pk} source={source} title={title!r}")
            return pk

    async def close_session(self, *, reason: str = "手动结束") -> bool:
        """结束当前显式场次（含空场次丢弃）。无进行中场次返回 False。"""
        async with self._lock:
            if self._active_pk is None:
                logger.debug("close_session: 无进行中的显式场次，忽略")
                return False
            return await self._close_active(reason=reason, ended_at_ms=now_ms())

    async def _close_active(self, *, reason: str, ended_at_ms: int) -> bool:
        """结束当前显式场次（调用方持锁）。空场次（无任何明细行）整行丢弃。"""
        assert self._active_pk is not None
        pk = self._active_pk
        source = self._active_source or "manual"

        row = await self._store.get_live_session(live_session_id=pk)
        started_at = int(row["started_at_ms"]) if row is not None else ended_at_ms
        await self._store.close_live_session(live_session_id=pk, ended_at_ms=ended_at_ms)

        details = await self._store.count_session_details(live_session_id=pk)
        empty_discarded = False
        if details == 0:
            # 空场次不留行：开启后没有任何消息的场次直接删除
            await self._store.delete_live_session(live_session_id=pk)
            empty_discarded = True
            logger.info(f"空场次已丢弃: id={pk}（无任何明细行，不保留）")

        self._active_pk = None
        self._active_source = ""
        await self._event_bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(
                live_session_id=pk,
                source=source,
                reason=reason,
                duration_ms=max(ended_at_ms - started_at, 0) if row is not None else None,
                empty_discarded=empty_discarded,
                ended_at_ms=ended_at_ms,
            ),
            source="LiveSessionManager",
        )
        logger.info(f"场次已结束: id={pk} reason={reason!r} 明细行={details} 空场次丢弃={empty_discarded}")
        return True

    # -------------------- 删除 --------------------

    async def delete_session(self, live_session_id: int) -> bool:
        """删除场次（级联清明细）。进行中场次先自动结束再删。

        临时场次也可删（下次解析自动重建）。目标行不存在返回 False——
        注意进行中的空场次在收口阶段即被整行丢弃，本方法先探明行存在
        再收口，避免"收口即删光 → DELETE 落空"被误报为不存在。
        """
        async with self._lock:
            row = await self._store.get_live_session(live_session_id=live_session_id)
            if row is None:
                return False
            if live_session_id == self._active_pk:
                await self._close_active(reason="删除场次前自动结束", ended_at_ms=now_ms())
            await self._store.delete_live_session(live_session_id=live_session_id)
            if live_session_id == self._scratch_pk:
                self._scratch_pk = None
            logger.info(f"场次已删除: id={live_session_id}（明细级联清除）")
            return True

    # -------------------- 场次归属解析 --------------------

    async def resolve_pk(self) -> int:
        """解析"当前场次"主键：显式场次进行中返回其主键，否则临时场次主键。

        下游（StorageLedger 写明细 / 场次盖章拦截器 / 模拟器世界窗口）统一
        经此归属，不再各自维护场次语义。
        """
        if self._active_pk is not None:
            return self._active_pk
        return await self._ensure_scratch()

    async def _ensure_scratch(self) -> int:
        """确保临时兜底场次行存在并返回其主键（幂等，跨进程单行）。"""
        if self._scratch_pk is not None:
            return self._scratch_pk
        row = await self._store.get_scratch_live_session()
        if row is not None:
            self._scratch_pk = int(row["id"])
            return self._scratch_pk
        self._scratch_pk = await self._store.insert_live_session(
            stream_id=SCRATCH_STREAM_ID,
            platform="scratch",
            started_at_ms=now_ms(),
            title="临时场次（未显式开启期间的消息兜底）",
            source="scratch",
        )
        logger.info(f"临时兜底场次已创建: id={self._scratch_pk}")
        return self._scratch_pk

    # -------------------- 查询访问面 --------------------

    @property
    def active_pk(self) -> Optional[int]:
        """当前显式进行中场次主键；无则 None（不回退临时场次）。"""
        return self._active_pk

    @property
    def active_source(self) -> str:
        """当前显式场次的来源（manual / replay）；无显式场次为空串。"""
        return self._active_source

    async def list_sessions(self, *, limit: int = 50) -> List:
        """场次列表（倒序 + 消息数），供 Dashboard API / 控制台侧边栏。"""
        return await self._store.list_live_sessions(limit=limit)


__all__ = ["LiveSessionManager", "SCRATCH_STREAM_ID"]
