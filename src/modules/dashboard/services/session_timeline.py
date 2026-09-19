"""场次时间线装配

单场时间线回看的条目装配：明细行（消息/发言/礼物/SC）的 kind 映射 +
事件历史（决策/阶段/边界）过滤与合并排序。纯数据变换，不含 HTTP 语义。
"""

from typing import Any, Dict, List, Optional, Protocol

from src.modules.events.names import CoreEvents


class _EventHistory(Protocol):
    """event_history 环形缓冲的最小消费契约。"""

    def get_by_session(self, session_id: int, limit: int) -> List[Any]: ...


# 时间线只消费事件历史中的这些类型——消息/发言已由明细行承载，
# 事件记录仅补充明细表没有的决策与状态事实
TIMELINE_EVENT_TYPES = frozenset(
    {
        CoreEvents.PLANNER_DECISION,
        CoreEvents.STREAMER_STAGE,
        CoreEvents.LIVE_STARTED,
        CoreEvents.LIVE_ENDED,
        CoreEvents.RUNDOWN_CHANGED,
        CoreEvents.GAME_MILESTONE,
        CoreEvents.GAME_REPORT,
    }
)


# 明细行 kind → 前端卡片 kind 的映射（gift_row/super_chat_row 为仓储层的
# 行类型标记，出时间线时折叠为卡片语义）
_KIND_MAP = {"gift_row": "gift", "super_chat_row": "super_chat"}


def build_timeline_items(
    detail_rows: List[Dict[str, Any]],
    event_history: Optional[_EventHistory],
    session_id: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """合并明细行与事件历史为按 ``ts_ms`` 升序的时间线条目（截取最近 ``limit`` 条）。

    礼物/SC 明细行与 room.message 事件同义，统一映射为前端卡片 kind；
    事件历史为内存环形缓冲，重启后事件侧条目不可回看（明细行不受影响）。
    入参 ``detail_rows`` 不被修改——kind 映射落在浅拷贝的新条目上。
    """
    items: List[Dict[str, Any]] = []
    for row in detail_rows:
        entry = dict(row)
        entry["kind"] = _KIND_MAP.get(row["kind"], row["kind"])
        items.append(entry)

    if event_history is not None:
        for record in event_history.get_by_session(session_id, limit=limit):
            if record.type not in TIMELINE_EVENT_TYPES:
                continue
            items.append(
                {
                    "kind": "event",
                    "event_type": record.type,
                    "ts_ms": record.timestamp_ms,
                    "data": record.data,
                }
            )

    items.sort(key=lambda item: item["ts_ms"])
    return items[-limit:]
