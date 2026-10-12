"""场次时间线装配

单场时间线回看的条目装配：明细行（消息/发言/礼物/SC）的 kind 映射 +
事件记录（已由 SessionEventReader 收集：环形缓冲 ∪ 诊断文件、去重、
白名单过滤）合并排序与游标分页。纯数据变换，不含 HTTP 语义与 IO。
"""

from typing import Any, Dict, List, Optional, Tuple

from src.modules.dashboard.services.session_events import trim_long_strings


# 明细行 kind → 前端卡片 kind 的映射（gift_row/super_chat_row 为仓储层的
# 行类型标记，出时间线时折叠为卡片语义）
_KIND_MAP = {"gift_row": "gift", "super_chat_row": "super_chat"}


def build_timeline_items(
    detail_rows: List[Dict[str, Any]],
    event_records: List[Any],
    limit: int,
    *,
    before_timestamp_ms: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """合并明细行与事件记录为按 ``ts_ms`` 升序的时间线条目。

    返回 ``(items, has_more)``：``before_timestamp_ms`` 游标只保留严格早于
    该时刻的条目；合并后超出 ``limit`` 时保留最近一段并把 ``has_more`` 置真
    （调用方以返回条目中最旧的 ``ts_ms`` 作为下一页游标续翻）。

    礼物/SC 明细行与消息事件同义，统一映射为前端卡片 kind；事件记录的
    超长字符串在返回侧截断（不改落盘文件）。入参均不被修改。
    """
    items: List[Dict[str, Any]] = []
    for row in detail_rows:
        entry = dict(row)
        entry["kind"] = _KIND_MAP.get(row["kind"], row["kind"])
        items.append(entry)

    for record in event_records:
        items.append(
            {
                "kind": "event",
                "event_type": record.type,
                "ts_ms": record.timestamp_ms,
                "data": trim_long_strings(record.data),
            }
        )

    if before_timestamp_ms is not None:
        items = [item for item in items if item["ts_ms"] < before_timestamp_ms]

    items.sort(key=lambda item: item["ts_ms"])
    has_more = len(items) > limit
    return items[-limit:], has_more


__all__ = ["build_timeline_items"]
