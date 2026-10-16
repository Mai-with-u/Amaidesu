"""v16 → v17：流程单域拆行/建表 + 全域遗留死列一次性清理。

1. **模板拆行**：新建 ``rundown_segments``（环节成行，``seq`` 定序），存量
   ``rundowns.segments_json`` 逐环节解包插行后 DROP 该列。``key_points``
   字段整链删除（与 ``task_description`` 信息重复）：迁移把存量环节要点
   拼进 ``task_description`` 末尾——原描述为空则直接写入要点，非空则另起
   一行"要点：a、b、c"，信息不丢。
2. **实例双表**：新建 ``rundown_instances``（每场一行的时点快照头）与
   ``rundown_instance_segments``（环节快照行）——崩溃恢复 + 防模板漂移
   的时点事实源；接线由后续切片承担。
3. **分析事实表**：新建 ``rundown_transitions``（append-only 变更事实行）。
4. **遗留死列 DROP**（实测全库非空为零）：
   - ``llm_requests`` 五个出库列（request_params / reasoning_content /
     tool_calls / usage_raw_json / breakdown_json）——正文 v14 起出库到
     诊断文件，列留 NULL 的降级回退价值消失；
   - ``live_chat.tool_result``（死列，全库无写入方）；
   - ``timeline_summary.tags``（死列，唯一写入点恒传 None）。

新建库的 DDL 已是最新形态；回调以列/表存在性检查保证对旧库升级与重复
执行安全。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

from src.modules.storage.migrations._common import column_exists, table_exists
from src.modules.storage.schema import (
    _RUNDOWN_INSTANCE_SEGMENTS_SQL,
    _RUNDOWN_INSTANCES_SQL,
    _RUNDOWN_SEGMENTS_SQL,
    _RUNDOWN_TRANSITIONS_SQL,
)


def _merge_key_points(task_description: str, key_points: List[Any]) -> str:
    """存量环节的 key_points 拼进 task_description 末尾（信息不丢）。

    原描述为空 → 直接写入"要点：a、b、c"；非空 → 原描述后另起一行。
    要点列表为空时原样返回。
    """
    points = [str(p).strip() for p in key_points if str(p).strip()]
    if not points:
        return task_description
    points_line = "要点：" + "、".join(points)
    if not task_description.strip():
        return points_line
    return f"{task_description}\n{points_line}"


def _unpack_segments_json(conn: sqlite3.Connection) -> None:
    """存量 ``rundowns.segments_json`` 逐环节插行，随后 DROP 该列。"""
    if not table_exists(conn, "rundowns") or not column_exists(conn, "rundowns", "segments_json"):
        return
    rows = conn.execute("SELECT id, segments_json FROM rundowns").fetchall()
    for rundown_id_raw, segments_json_raw in rows:
        rundown_id = str(rundown_id_raw)
        try:
            segments: List[Dict[str, Any]] = json.loads(str(segments_json_raw))
        except (TypeError, ValueError):
            # 历史脏行无法解析：跳过插行（该流程单环节丢失），不让迁移整体失败
            continue
        for seq, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO rundown_segments ("
                "rundown_id, seq, seg_id, title, task_description, expected_ms, min_duration_ms, notes"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rundown_id,
                    seq,
                    str(seg.get("id", "") or ""),
                    str(seg.get("title", "") or ""),
                    _merge_key_points(str(seg.get("task_description", "") or ""), list(seg.get("key_points") or [])),
                    int(seg.get("expected_ms", 0) or 0),
                    seg.get("min_duration_ms"),
                    seg.get("notes"),
                ),
            )
    conn.execute("ALTER TABLE rundowns DROP COLUMN segments_json")


def _drop_columns(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> None:
    """逐列幂等 DROP（列存在才 drop）。"""
    for column in columns:
        if column_exists(conn, table, column):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")  # noqa: S608 表名/列名为代码内常量


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：四新表 + segments_json 解行 + 七处死列 DROP。"""
    for ddl in (
        _RUNDOWN_SEGMENTS_SQL,
        _RUNDOWN_INSTANCES_SQL,
        _RUNDOWN_INSTANCE_SEGMENTS_SQL,
        _RUNDOWN_TRANSITIONS_SQL,
    ):
        conn.executescript(ddl)
    _unpack_segments_json(conn)
    _drop_columns(
        conn, "llm_requests", ("request_params", "reasoning_content", "tool_calls", "usage_raw_json", "breakdown_json")
    )
    _drop_columns(conn, "live_chat", ("tool_result",))
    _drop_columns(conn, "timeline_summary", ("tags",))


__all__ = ["migrate"]
