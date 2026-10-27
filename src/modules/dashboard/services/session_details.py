"""单场明细行打平（回看时间线的数据面）。

把 live_chat + gifts + super_chats 三张明细表拉平为带 kind 的条目流：
消息行带 message_id / reply_to_message_id 关联键；``before_timestamp_ms``
游标只保留严格早于该时刻的行（回看"加载更早"翻页），三张明细表各取
游标前最近 ``limit`` 行后合并截断。
"""

from __future__ import annotations

from typing import List, Optional

from src.modules.storage.repos import ChatRepo
from src.modules.types.currency import to_cny


async def list_session_details(
    chat_repo: ChatRepo,
    live_session_id: int,
    *,
    limit: int = 300,
    before_timestamp_ms: Optional[int] = None,
) -> List[dict]:
    """单场明细行（live_chat + gifts + super_chats 合并，时间正序）。"""
    rows = await chat_repo.list_recent_live_chat(
        live_session_id=live_session_id,
        limit=limit,
        before_timestamp_ms=before_timestamp_ms,
    )
    items: List[dict] = []
    for row in rows:
        ts = row.timestamp_ms
        if row.sender_role == "assistant":
            items.append(
                {
                    "kind": "speech",
                    "ts_ms": ts,
                    "text": row.content,
                    "reply_to_message_id": row.reply_to_message_id,
                    "simulated": False,
                }
            )
        else:
            items.append(
                {
                    "kind": row.message_type,
                    "ts_ms": ts,
                    "user_name": row.sender_name or "",
                    "user_id": row.sender_id or "",
                    "content": row.content,
                    "message_id": row.message_id,
                    "simulated": bool(row.simulated),
                }
            )
    gift_rows = await chat_repo.list_session_gifts(
        live_session_id=live_session_id,
        limit=limit,
        before_timestamp_ms=before_timestamp_ms,
    )
    for row in gift_rows:
        items.append(
            {
                "kind": "gift_row",
                "ts_ms": row.timestamp_ms,
                "user_name": row.user_name,
                "user_id": row.user_id,
                "gift_name": row.gift_name,
                "gift_count": row.quantity,
                "total_price": row.total_price or 0,
                "simulated": bool(row.simulated),
            }
        )
    sc_rows = await chat_repo.list_session_super_chats(
        live_session_id=live_session_id,
        limit=limit,
        before_timestamp_ms=before_timestamp_ms,
    )
    for row in sc_rows:
        items.append(
            {
                "kind": "super_chat_row",
                "ts_ms": row.timestamp_ms,
                "user_name": row.user_name,
                "user_id": row.user_id,
                "content": row.message,
                "amount": to_cny(row.total_price or 0, row.currency or "") or 0.0,
                "simulated": bool(row.simulated),
            }
        )
    items.sort(key=lambda item: item["ts_ms"])
    return items[-limit:]


__all__ = ["list_session_details"]
