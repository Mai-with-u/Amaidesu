"""直播库上复算"别唠叨"指标：同一件旧事一场提几次、没人问时主动提几次、推的卡用上几张。

和下播整理写进报告的是同一个函数（``compute_side_effects``），这里只是换个取料口：直接只读打开
一份导出的直播库，按场算一遍，用来给还没有这一节报告的旧场次补基线、或核对报告。

只读打开（不跑迁移、不写任何东西）；输出只有编号和数字，不带台词原文与观众身份，报告不入库。

用法：
    uv run python scripts/memory_eval/live_side_effects.py <直播库路径> [--session N ...] [--last N]
    uv run python scripts/memory_eval/live_side_effects.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents.streamer.memory.side_effects import compute_side_effects  # noqa: E402
from src.modules.storage.models.chat import SettleSpeechLine  # noqa: E402
from src.modules.storage.models.memory import CueLogEntry, SideEffectStats  # noqa: E402

#: 建议阈值（方案里拍的值，前几场测试直播只报告、不拦发版）：超过的项在输出里点名
THRESHOLDS = {
    "max_per_item": 3,
    "p95_per_item": 2,
    "unprompted_per_hour": 4.0,
    "circling_items": 0,
    "max_cards_per_player": 2,
}


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))  # noqa: S608 常量表名


def _speech(conn: sqlite3.Connection, session_id: int) -> List[SettleSpeechLine]:
    """本场她说过的台词：只取算指标要的几列（旧库没有回应玩家那一列时当空）。"""
    player_col = "reply_player_id" if _has_column(conn, "live_chat", "reply_player_id") else "NULL"
    rows = conn.execute(
        f"SELECT id, timestamp_ms, '' AS content, dropped_at_ms, reply_to_message_id, memory_refs,"  # noqa: S608
        f" {player_col} AS reply_player_id FROM live_chat"
        " WHERE live_session_id=? AND sender_role='assistant' ORDER BY timestamp_ms, id",
        (session_id,),
    ).fetchall()
    return [SettleSpeechLine.model_validate(dict(row)) for row in rows]


def _json(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if isinstance(raw, str) and raw else default
    except json.JSONDecodeError:
        return default


def _cards(conn: sqlite3.Connection, session_id: int) -> List[CueLogEntry]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_cue_log'").fetchone():
        return []
    rows = conn.execute(
        "SELECT id, live_session_id, pushed_at_ms, card_kind, archive_line_id, commitment_id, sources, detail,"
        " self_triggered, used_at_ms FROM memory_cue_log WHERE live_session_id=? ORDER BY pushed_at_ms, id",
        (session_id,),
    ).fetchall()
    out = []
    for row in rows:
        data: Dict[str, Any] = dict(row)
        data["sources"] = [str(item) for item in _json(data["sources"], [])]
        data["detail"] = _json(data["detail"], {})
        data["self_triggered"] = bool(data["self_triggered"])
        out.append(CueLogEntry.model_validate(data))
    return out


def over_thresholds(stats: SideEffectStats) -> List[str]:
    """超过建议阈值的项（名字 = 实测值）。"""
    return [f"{name}={getattr(stats, name)}" for name, limit in THRESHOLDS.items() if getattr(stats, name) > limit]


def run(db_path: Path, *, sessions: Sequence[int] = (), last: int = 0) -> List[Dict[str, Any]]:
    """按场算指标；不指定场次时算全部已结束的正式场（--last 只取最近几场）。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT id, started_at_ms, ended_at_ms FROM live_sessions WHERE ended_at_ms IS NOT NULL"
        params: List[Any] = []
        if sessions:
            query += f" AND id IN ({','.join('?' for _ in sessions)})"
            params.extend(int(item) for item in sessions)
        elif _has_column(conn, "live_sessions", "kind"):
            query += " AND kind='live'"
        rows = conn.execute(query + " ORDER BY id", params).fetchall()
        if last > 0:
            rows = rows[-last:]
        report = []
        for row in rows:
            session_id = int(row["id"])
            duration_ms = int(row["ended_at_ms"]) - int(row["started_at_ms"])
            stats = compute_side_effects(_speech(conn, session_id), _cards(conn, session_id), duration_ms=duration_ms)
            entry: Dict[str, Any] = {"session": session_id, "minutes": round(duration_ms / 60_000)}
            if stats is None:
                entry["stats"] = None
            else:
                entry["stats"] = stats.model_dump()
                entry["over"] = over_thresholds(stats)
            report.append(entry)
        return report
    finally:
        conn.close()


def selftest() -> int:
    """在临时库上建一场最小数据，核对取料口和整理报告用的是同一套口径。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "selftest.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE live_sessions(id INTEGER PRIMARY KEY, started_at_ms INT, ended_at_ms INT, kind TEXT);"
            "CREATE TABLE live_chat(id INTEGER PRIMARY KEY, live_session_id INT, timestamp_ms INT, sender_role TEXT,"
            " dropped_at_ms INT, reply_to_message_id TEXT, memory_refs TEXT);"
            "CREATE TABLE memory_cue_log(id INTEGER PRIMARY KEY, live_session_id INT, pushed_at_ms INT, card_kind TEXT,"
            " archive_line_id INT, commitment_id INT, sources TEXT, detail TEXT, self_triggered INT, used_at_ms INT);"
            "INSERT INTO live_sessions VALUES (1, 0, 3600000, 'live'), (2, 0, 60000, 'rehearsal');"
            "INSERT INTO live_chat VALUES (1, 1, 1000, 'assistant', NULL, NULL, '[\"档#3\"]'),"
            " (2, 1, 2000, 'assistant', NULL, 'm1', '[\"档#3\"]'), (3, 1, 3000, 'assistant', 3000, NULL, '[\"档#3\"]');"
            "INSERT INTO memory_cue_log VALUES (1, 1, 500, 'rule', 3, NULL, '[\"self\"]', '{}', 1, 1000);"
        )
        conn.commit()
        conn.close()
        report = run(path)
    stats = report[0]["stats"] if len(report) == 1 else None
    ok = (
        stats is not None
        and stats["mentions"] == 2
        and stats["unprompted"] == 1
        and stats["cards"] == {"rule": {"pushed": 1, "used": 1}}
        and stats["self_triggered_cards"] == 1
    )
    print("selftest", "PASS" if ok else f"FAIL {report}")
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="直播库上复算唠叨指标（只读，只出编号和数字）")
    parser.add_argument("db", nargs="?", type=Path, help="直播库路径")
    parser.add_argument("--session", type=int, action="append", default=[], help="只算这几场（可重复）")
    parser.add_argument("--last", type=int, default=0, help="只算最近几场正式场")
    parser.add_argument("--selftest", action="store_true", help="在临时库上自检")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.db is None:
        parser.error("要给直播库路径，或用 --selftest")
    for entry in run(args.db, sessions=args.session, last=args.last):
        print(json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
