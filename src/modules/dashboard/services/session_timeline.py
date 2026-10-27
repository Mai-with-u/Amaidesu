"""场次时间线装配

单场时间线回看的条目装配：明细行（消息/发言/礼物/SC）的 kind 映射 +
发言双源去重（与 ``streamer.speech`` 事件成对的明细行让位）+ 事件记录
（已由 SessionEventReader 收集：环形缓冲 ∪ 诊断文件、去重、白名单过滤）
合并排序与游标分页。纯数据变换，不含 HTTP 语义与 IO。
"""

from typing import Any, Dict, List, Optional, Tuple

from src.modules.dashboard.services.session_events import trim_long_strings


# 明细行 kind → 前端卡片 kind 的映射（gift_row/super_chat_row 为仓储层的
# 行类型标记，出时间线时折叠为卡片语义；guard 明细行 kind 本身即卡片语义，
# 直通不改名）
_KIND_MAP = {"gift_row": "gift", "super_chat_row": "super_chat"}

# 发言双源去重的时刻容差：明细行 ts 即事件载荷 timestamp_ms（同源写两处），
# 正常完全相等，窗口只为容忍落库与落盘的写时刻漂移
_SPEECH_DEDUPE_WINDOW_MS = 5_000


def _drop_duplicated_speech_rows(
    detail_rows: List[Dict[str, Any]],
    event_records: List[Any],
) -> List[Dict[str, Any]]:
    """丢弃与事件侧 ``streamer.speech`` 成对的发言明细行（一对一去重）。

    同一条发言既落 live_chat 明细行又发事件：事件侧是富卡（情绪/统计/轮次
    关联），命中即以事件为准；未命中的明细行（诊断流被清理、事件缺失）保留，
    作为明细行视图的兜底。匹配键：文本相等且时刻在容差窗内，多条同名发言按
    时刻最近一对一消耗。
    """
    event_speeches = [
        (record.timestamp_ms, str((record.data or {}).get("text") or ""))
        for record in event_records
        if record.type == "streamer.speech"
    ]
    if not event_speeches:
        return detail_rows
    consumed: set = set()
    kept: List[Dict[str, Any]] = []
    for row in detail_rows:
        if row.get("kind") == "speech":
            text = str(row.get("text") or "")
            candidates = [
                idx
                for idx, (ts, event_text) in enumerate(event_speeches)
                if idx not in consumed and event_text == text and abs(ts - row["ts_ms"]) <= _SPEECH_DEDUPE_WINDOW_MS
            ]
            if candidates:
                consumed.add(min(candidates, key=lambda idx: abs(event_speeches[idx][0] - row["ts_ms"])))
                continue
        kept.append(row)
    return kept


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

    礼物/SC 明细行与消息事件同义，统一映射为前端卡片 kind；发言明细行与
    ``streamer.speech`` 事件成对的只保留事件（见
    ``_drop_duplicated_speech_rows``）；事件记录的超长字符串在返回侧截断
    （不改落盘文件）。入参均不被修改。
    """
    detail_rows = _drop_duplicated_speech_rows(detail_rows, event_records)
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
