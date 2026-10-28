"""直播场次管理（LiveSessionManager）。

场次 = 一段有开始/结束边界的直播时间段；房间/频道是场次之上的静态属性，
一房多场。本模块是场次的**唯一事实源**：

- 行级写入经 ``SessionRepo`` 的 live_sessions 领域方法（主键 AUTOINCREMENT）；
- 生命周期广播 ``live.started`` / ``live.ended`` 事件；
- 对下游（StorageLedger / 场次盖章拦截器 / 模拟器 / Dashboard API）暴露
  ``resolve_pk()``——显式场次进行中返回其主键，否则返回 ``None``。

## 显式开启场次才落库

把"开场次"从进程启动的副作用变为**显式业务动作**：

- 进程启动**不**自动开新场次；
- 无显式场次期间 ``resolve_pk()`` 返回 ``None``——下游 StorageLedger 等落库
  路径据此跳过；事件总线分发、WebUI 显示、Agent 决策链路照常工作（消息仅
  在内存流转，不写入 live_chat / gifts / super_chats）；
- 显式场次由手动开关或模拟器开启；结束时若无任何明细行则整行丢弃
  （空场次不留行）；
- 场次可删除（级联清明细），调试产生的场次随手清理。

## 场次类型在开场时声明

每场开启时必须声明类型：正式（面向真实观众）或彩排（模拟、回放、调试）。
类型随 ``live.started`` / ``live.ended`` 广播，订阅方据此分流——主播记忆
只读同类场次的回顾，对外同步只认正式场。进行中的场次不改类型；已结账的
场次可经 ``set_session_kind`` 更正（存量回填判错时人手改）。

跨场次记忆由主播 Agent 的场次回顾 / 观众画像承载（收播时整理），本模块不参与启动期补写。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, List, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

from src.modules.storage.models.sessions import LiveSessionListItem
from src.modules.storage.repos import ChatRepo, SessionRepo
from src.modules.types.session_kind import SessionKind

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

logger = get_logger("LiveSessionManager")


class LiveSessionManager:
    """直播场次管理器：开启/结束/删除场次 + 场次归属解析。

    不变量：
    - 至多一个显式进行中场次（开启新场次前自动结束旧场次）；
    - 场次类型开场时声明，进行中不改；
    - 空显式场次在结账时整行丢弃；
    - 无显式场次期间 ``resolve_pk()`` 返回 ``None``，不创建任何兜底行。
    """

    def __init__(
        self,
        session_repo: SessionRepo,
        chat_repo: ChatRepo,
        event_bus: "EventBus",
        *,
        platform: str = "unknown",
        room_id: str = "",
    ) -> None:
        self._sessions = session_repo
        self._chat = chat_repo
        self._event_bus = event_bus
        self._platform = platform
        self._room_id = room_id
        self._active_pk: Optional[int] = None
        self._active_source: str = ""
        self._active_kind: Optional[SessionKind] = None
        self._lock = asyncio.Lock()
        self._started = False

    # -------------------- 生命周期 --------------------

    async def start(self) -> None:
        """启动：收口上次残留的显式进行中场次。幂等。"""
        if self._started:
            return

        # 残留收口：上次进程未正常退出的"进行中"显式场次，以最后活动
        # 时刻封闭（没有可重建的真实结束边界）。本次进程不会自动开新场次；
        # 无显式场次期间，``resolve_pk()`` 返回 None，下游落库路径据此跳过。
        for row in await self._sessions.list_dangling_live_sessions():
            ended = int(row.updated_at_ms or row.started_at_ms)
            await self._sessions.close_live_session(live_session_id=row.id, ended_at_ms=ended)
            logger.warning(
                f"收口上次残留的进行中场次: id={row.id} title={row.title!r} （ended_at_ms 补为最后活动时刻 {ended}）"
            )

        self._started = True
        logger.info("LiveSessionManager 已启动（启动不自动开新场次；显式场次经 open_session 开启；无场次时消息不落库）")

    async def stop(self) -> None:
        """停止（进程退出收口由 run_shutdown 显式调 close_session，这里仅复位标记）。"""
        self._started = False

    # -------------------- 显式场次生命周期 --------------------

    async def open_session(
        self,
        *,
        kind: SessionKind,
        title: Optional[str] = None,
        room_id: Optional[str] = None,
        platform: Optional[str] = None,
        source: str = "manual",
    ) -> int:
        """开启一场新直播场次，返回场次主键。

        ``kind`` 没有默认值：每个开场入口都要说清这场是正式还是彩排，
        漏写就是类型错误，不会悄悄落成正式场串进主播记忆。
        已有进行中场次时先自动结束（不变量：至多一个显式进行中场次），
        随后 ``live.started`` 事件广播。
        """
        async with self._lock:
            if self._active_pk is not None:
                await self._close_active(reason="开启新场次前自动结束", ended_at_ms=now_ms())
            started = now_ms()
            pk = await self._sessions.insert_live_session(
                stream_id=room_id if room_id is not None else self._room_id,
                platform=platform or self._platform,
                started_at_ms=started,
                title=title,
                source=source,
                kind=kind,
            )
            self._active_pk = pk
            self._active_source = source
            self._active_kind = kind
            await self._event_bus.emit(
                CoreEvents.LIVE_STARTED,
                LiveStartedPayload(
                    live_session_id=pk,
                    source=source,
                    kind=kind,
                    title=title,
                    room_id=room_id if room_id is not None else self._room_id,
                    platform=platform or self._platform,
                    started_at_ms=started,
                ),
                source="LiveSessionManager",
            )
            logger.info(f"场次已开启: id={pk} kind={kind} source={source} title={title!r}")
            return pk

    async def close_session(self, *, reason: str = "手动结束") -> bool:
        """结束当前显式场次（含空场次丢弃）。无进行中场次返回 False。"""
        async with self._lock:
            if self._active_pk is None:
                logger.debug("close_session: 无进行中的显式场次，忽略")
                return False
            return await self._close_active(reason=reason, ended_at_ms=now_ms())

    async def _close_active(self, *, reason: str, ended_at_ms: int) -> bool:
        """结束当前显式场次（调用方持锁）。空场次（无任何明细行）整行丢弃。

        幂等：目标行不存在或已结账（``ended_at_ms`` 非空）时只复位内存态
        并返回 False，不重复结算、不重复广播 ``live.ended``、不误删行——
        进程退出收尾链（``run_shutdown`` 常规结账 + 退出兜底）会对本方法
        发起多次调用，重复路径不得产生二次副作用。
        """
        assert self._active_pk is not None
        pk = self._active_pk
        source = self._active_source or "manual"

        row = await self._sessions.get_live_session(live_session_id=pk)
        if row is None or row.ended_at_ms is not None:
            self._reset_active()
            return False
        kind = row.kind
        started_at = int(row.started_at_ms)
        await self._sessions.close_live_session(live_session_id=pk, ended_at_ms=ended_at_ms)

        details = await self._chat.count_session_details(live_session_id=pk)
        empty_discarded = False
        if details == 0:
            # 空场次不留行：开启后没有任何消息的场次直接删除
            await self._sessions.delete_live_session(live_session_id=pk)
            empty_discarded = True
            logger.info(f"空场次已丢弃: id={pk}（无任何明细行，不保留）")

        self._reset_active()
        await self._event_bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(
                live_session_id=pk,
                source=source,
                kind=kind,
                reason=reason,
                duration_ms=max(ended_at_ms - started_at, 0),
                empty_discarded=empty_discarded,
                ended_at_ms=ended_at_ms,
            ),
            source="LiveSessionManager",
        )
        logger.info(f"场次已结束: id={pk} reason={reason!r} 明细行={details} 空场次丢弃={empty_discarded}")
        return True

    def _reset_active(self) -> None:
        """清空进行中场次的内存态（结账、收口与幂等重入路径共用）。"""
        self._active_pk = None
        self._active_source = ""
        self._active_kind = None

    # -------------------- 删除 --------------------

    async def delete_session(self, live_session_id: int) -> bool:
        """删除场次（级联清明细）。进行中场次先自动结束再删。

        目标行不存在返回 False——注意进行中的空场次在收口阶段即被整行
        丢弃，本方法先探明行存在再收口，避免"收口即删光 → DELETE 落空"
        被误报为不存在。
        """
        async with self._lock:
            row = await self._sessions.get_live_session(live_session_id=live_session_id)
            if row is None:
                return False
            if live_session_id == self._active_pk:
                await self._close_active(reason="删除场次前自动结束", ended_at_ms=now_ms())
            await self._sessions.delete_live_session(live_session_id=live_session_id)
            logger.info(f"场次已删除: id={live_session_id}（明细级联清除）")
            return True

    # -------------------- 重命名 --------------------

    async def rename_session(self, live_session_id: int, title: Optional[str]) -> bool:
        """改写场次标题（None/空串的清除语义由调用方规范化后传入）。行不存在返回 False。"""
        renamed = await self._sessions.update_live_session_title(
            live_session_id=live_session_id, title=title, updated_at_ms=now_ms()
        )
        if renamed:
            logger.info(f"场次已重命名: id={live_session_id} title={title!r}")
        return renamed

    # -------------------- 类型更正 --------------------

    async def set_session_kind(self, live_session_id: int, kind: SessionKind) -> bool:
        """更正已结账场次的类型。进行中或不存在的场次返回 False。

        进行中的场次不改：开播时主播已按类型读了对应的上场回顾、对外同步
        也已按类型决定推不推，中途改类型会让这一场前后两半分到两条记忆线。
        """
        if live_session_id == self._active_pk:
            logger.info(f"场次 {live_session_id} 进行中，不更正类型")
            return False
        changed = await self._sessions.update_live_session_kind(
            live_session_id=live_session_id, kind=kind, updated_at_ms=now_ms()
        )
        if changed:
            logger.info(f"场次类型已更正: id={live_session_id} kind={kind}")
        return changed

    # -------------------- 场次归属解析 --------------------

    async def resolve_pk(self) -> Optional[int]:
        """解析"当前场次"主键：显式场次进行中返回其主键，否则返回 ``None``。

        下游（StorageLedger 写明细 / 场次盖章拦截器 / 模拟器世界窗口）统一
        经此归属，不再各自维护场次语义。返回 ``None`` 时表示无进行中场次
        ——落库路径据此跳过，事件分发链路照常运行。
        """
        return self._active_pk

    # -------------------- 查询访问面 --------------------

    @property
    def active_pk(self) -> Optional[int]:
        """当前显式进行中场次主键；无则 None（不回退默认场次）。"""
        return self._active_pk

    @property
    def active_kind(self) -> Optional[SessionKind]:
        """当前显式进行中场次的类型（live / rehearsal）；无进行中场次为 None。"""
        return self._active_kind

    @property
    def active_source(self) -> str:
        """当前显式场次的来源（manual / replay / generate）；无显式场次为空串。"""
        return self._active_source

    @property
    def platform(self) -> str:
        """本管理器的平台标识（装配期常量，open_session 未显式指定时使用）。

        主播发言等无平台字段的落库行经此取 platform 归属。
        """
        return self._platform

    async def list_sessions(
        self,
        *,
        limit: int = 50,
        source: Optional[str] = None,
        kind: Optional[SessionKind] = None,
        title_keyword: Optional[str] = None,
    ) -> List[LiveSessionListItem]:
        """场次列表（显式场次按开始时间倒序 + 消息数），供 Dashboard API / 控制台侧边栏。"""
        return await self._sessions.list_live_sessions(
            limit=limit, source=source, kind=kind, title_keyword=title_keyword
        )


__all__ = ["LiveSessionManager"]
