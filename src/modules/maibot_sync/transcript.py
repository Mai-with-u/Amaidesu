"""整场记录：从库里读出一场直播的全部对话，整理成推给 MaiBot 适配器插件的契约格式。

适配器插件把这份记录按原始时间回放进 MaiBot 里固定的直播间会话，由 MaiBot 自己的聊天
摘要吸收进长期记忆。这里只负责"读库 + 换成插件认识的行"，不关心插件怎么回放。

收录的行（全部按场次主键取、跳过模拟数据）：
- ``live_chat`` 的观众行（弹幕、上舰描述）、联动对象发言、主播发言；
- ``super_chats`` 的醒目留言（带金额，写成"[醒目留言 ¥30] 正文"）。
礼物不收：连击礼物量大而信息少，谁撑了场已经写在场次回顾里。

模拟器回放出来的场次（``source="replay"``）、还没结账的场次不出记录。

观众行的 ``user_id`` 用内部观众 ID（``viewer:<编号>``），不用收到时的外部 ID：
同一位观众换了数据源（账号 uid / 开放平台 open_id）在 MaiBot 里仍是同一个人。
没归到观众的旧明细行回退为原始外部 ID；联动对象沿用原值。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.logging import get_logger
from src.modules.types.currency import to_cny

if TYPE_CHECKING:
    from src.modules.storage.repos.chat import ChatRepo
    from src.modules.storage.repos.sessions import SessionRepo

logger = get_logger("StorageTranscriptSource")

# 与适配器插件约定的单场行数上限；超出时保留最后这么多行（收尾部分离回顾最近）
MAX_TRANSCRIPT_LINES = 20_000
# 读醒目留言的条数上限（一场里付费留言不会到这个量级）
_SUPER_CHAT_ROWS = 2_000
# 模拟器回放场次的来源标记
_REPLAY_SOURCE = "replay"
# live_chat.sender_role → 契约里的说话人角色
_ROLE_BY_SENDER = {"viewer": "viewer", "partner": "partner", "assistant": "streamer"}


def _viewer_user_id(row: Any, external_attr: str) -> str:
    """明细行推给插件的 user_id：有内部观众 ID 用 ``viewer:<编号>``，否则回退原始外部 ID。

    加前缀是为了不和平台账号 uid 撞号（B 站 uid 也是纯数字）。
    ``row`` 是明细仓储返回的数据模型（live_chat 弹幕行 / super_chats 醒目留言行）。
    """
    if row.viewer_id is not None:
        return f"viewer:{row.viewer_id}"
    return str(getattr(row, external_attr) or "")


class StorageTranscriptSource:
    """按场次主键从库里拼出整场记录。"""

    def __init__(self, *, sessions: "SessionRepo", chat: "ChatRepo") -> None:
        self._sessions = sessions
        self._chat = chat

    async def load(self, live_session_id: int) -> Optional[Dict[str, Any]]:
        """返回推给插件的整场记录；场次不存在、未结账、是回放场次或没有任何对话时返回 None。"""

        session_row = await self._sessions.get_live_session(live_session_id=int(live_session_id))
        if session_row is None:
            logger.debug(f"场次 {live_session_id} 不存在，不推整场记录")
            return None
        if str(session_row["source"] or "") == _REPLAY_SOURCE or not session_row["ended_at_ms"]:
            return None

        lines = self._chat_lines(
            await self._chat.list_recent_live_chat(live_session_id=int(live_session_id), limit=None)
        )
        lines += self._super_chat_lines(
            await self._chat.list_session_super_chats(live_session_id=int(live_session_id), limit=_SUPER_CHAT_ROWS)
        )
        if not lines:
            return None
        lines.sort(key=lambda item: item["ts_ms"])
        if len(lines) > MAX_TRANSCRIPT_LINES:
            logger.warning(f"场次 {live_session_id} 有 {len(lines)} 行对话，只推最后 {MAX_TRANSCRIPT_LINES} 行")
            lines = lines[-MAX_TRANSCRIPT_LINES:]

        platform = str(session_row["platform"] or "")
        return {
            "type": "transcript",
            "live_session_id": int(live_session_id),
            # 场次平台未知时留空，由插件用它配置的默认平台
            "platform": "" if platform == "unknown" else platform,
            "room_id": str(session_row["stream_id"] or ""),
            "title": str(session_row["title"] or ""),
            "started_at_ms": int(session_row["started_at_ms"] or 0),
            "ended_at_ms": int(session_row["ended_at_ms"] or 0),
            "lines": lines,
        }

    @staticmethod
    def _chat_lines(rows: List[Any]) -> List[Dict[str, Any]]:
        lines: List[Dict[str, Any]] = []
        for row in rows:
            role = _ROLE_BY_SENDER.get(row.sender_role or "")
            text = (row.content or "").strip()
            if role is None or not text or row.simulated:
                continue
            # 主播不带 ID；观众用内部观众 ID；联动对象沿用原值
            if role == "streamer":
                user_id = ""
            elif role == "viewer":
                user_id = _viewer_user_id(row, "sender_id")
            else:
                user_id = str(row.sender_id or "")
            lines.append(
                {
                    "ts_ms": row.timestamp_ms,
                    "role": role,
                    "user_id": user_id,
                    "user_name": "" if role == "streamer" else str(row.sender_name or ""),
                    "text": text,
                }
            )
        return lines

    @staticmethod
    def _super_chat_lines(rows: List[Any]) -> List[Dict[str, Any]]:
        lines: List[Dict[str, Any]] = []
        for row in rows:
            text = (row.message or "").strip()
            if not text or row.simulated:
                continue
            amount = to_cny(row.total_price or 0, row.currency or "")
            label = f"[醒目留言 ¥{amount:.0f}]" if amount else "[醒目留言]"
            lines.append(
                {
                    "ts_ms": row.timestamp_ms,
                    "role": "viewer",
                    "user_id": _viewer_user_id(row, "user_id"),
                    "user_name": str(row.user_name or ""),
                    "text": f"{label} {text}",
                }
            )
        return lines
