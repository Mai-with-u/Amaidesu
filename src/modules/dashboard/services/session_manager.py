"""场次清单（场次管理页的数据面）。

清单页回答的是"有哪些场、每场什么状态"——以全部场次为单位，不是一场。
数据来自两处：

- 场次列表行：标题、备注、来源、类型、起止时刻、消息数与整理三列
  （整理完成时刻 / 重新整理尝试次数 / 标已审时刻），由场次管理器转仓储读出；
- 话题标签：``topics`` 投影（每场当前话题快照），即"这场聊了什么"；
  话题仓储未装配时该列留空，不阻断清单本身。

分页总数与当页用同一套筛选条件，避免"第几页共多少场"与实际不符。
本模块只做取数与拼装，不含场次语义判断（场次生命周期归 ``LiveSessionManager``）。
"""

from __future__ import annotations

from typing import Any, List, NamedTuple, Optional

from src.modules.storage.models.sessions import LiveSessionListItem
from src.modules.types.session_kind import SessionKind


class SessionLedgerPage(NamedTuple):
    """场次清单的一页：行、筛选后总场次数、每场的话题。"""

    rows: List[LiveSessionListItem]
    total: int


async def list_session_ledger(
    manager: Any,
    *,
    limit: int = 50,
    offset: int = 0,
    source: Optional[str] = None,
    kind: Optional[SessionKind] = None,
    title_keyword: Optional[str] = None,
    started_after: Optional[int] = None,
    started_before: Optional[int] = None,
    settle_state: Optional[str] = None,
) -> SessionLedgerPage:
    """取场次清单的一页（筛选 + 分页 + 话题标签）。"""
    rows = await manager.list_sessions(
        limit=limit,
        offset=offset,
        source=source,
        kind=kind,
        title_keyword=title_keyword,
        started_after=started_after,
        started_before=started_before,
        settle_state=settle_state,
    )
    total = await manager.count_sessions(
        source=source,
        kind=kind,
        title_keyword=title_keyword,
        started_after=started_after,
        started_before=started_before,
        settle_state=settle_state,
    )
    return SessionLedgerPage(rows=rows, total=total)


__all__ = ["SessionLedgerPage", "list_session_ledger"]
