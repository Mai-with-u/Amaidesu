"""人工改动统计：整理写的档案，运营者在记忆页改了多少、改在哪。

每次审核操作都记一行 ``memory_edits``。整理自动生效、不等人审，所以人改过的地方就是整理写得
不合人意的地方，拿来当档案提示词迭代的指标：

- 标题被改的比例（改过标题的行 / 整理写的行）；
- 被删的行（整理写了、人觉得不该记）；
- 被放回的行（整理去掉或挤出、人觉得该留）；
- 人新增的行（整理漏写的事）；
- 稳定标记被人改了几次、观众候选批准与驳回各几条。

只读打开直播库（不跑迁移、不写任何东西）。默认只出数字；``--show-text`` 在本机屏幕上列出
标题改前改后，供手工转写成虚构样本并进 archive 系列评测集——原文可能带观众昵称与存档名，
不落盘、不入库。

用法：
    uv run python scripts/memory_eval/review_diff.py <直播库路径> [--session N ...] [--show-text]
    uv run python scripts/memory_eval/review_diff.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

# 人在记忆页能做的操作（与 MemoryReviewRepo、观众候选仓储写进 memory_edits 的 action 同名）
_TITLE = "title"
_LASTING = "lasting"
_PIN = "pin"
_ADD = "add"
_DELETE = "delete"
_APPROVE = "approve"
_REJECT = "reject"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _ratio(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 3) if whole else None


def session_diff(conn: sqlite3.Connection, session_id: int, *, show_text: bool = False) -> Dict[str, Any]:
    """一场的改动统计。整理写的行 = 现存的非观众来源行 + 被人删掉的行，再去掉人新增的那些。"""
    edits = conn.execute(
        "SELECT archive_line_id, action, before_text, after_text FROM memory_edits"
        " WHERE live_session_id=? ORDER BY at_ms, id",
        (session_id,),
    ).fetchall()
    by_action: Dict[str, Set[int]] = defaultdict(set)
    counts: Counter[str] = Counter()
    titles: List[Dict[str, str]] = []
    for line_id, action, before, after in edits:
        counts[action] += 1
        if line_id is not None:
            by_action[action].add(int(line_id))
        if action == _TITLE and show_text:
            titles.append({"before": before, "after": after})

    alive = {
        int(row[0])
        for row in conn.execute(
            "SELECT id FROM archive_lines WHERE live_session_id=? AND source <> 'viewer'", (session_id,)
        )
    }
    added = by_action[_ADD]
    # 被删的行可能是人自己新增后又删的：那样的不算整理写的
    written = (alive | by_action[_DELETE]) - added
    retitled = by_action[_TITLE] & written
    deleted = by_action[_DELETE] & written
    out: Dict[str, Any] = {
        "session": session_id,
        "written_by_settle": len(written),
        "retitled": len(retitled),
        "retitled_ratio": _ratio(len(retitled), len(written)),
        "deleted": len(deleted),
        "deleted_ratio": _ratio(len(deleted), len(written)),
        "pinned_back": len(by_action[_PIN]),
        "added_by_human": len(added),
        "lasting_flips": counts[_LASTING],
        "candidates_approved": counts[_APPROVE],
        "candidates_rejected": counts[_REJECT],
        "reviewed": counts["reviewed"] > 0,
    }
    if show_text:
        out["titles"] = titles
    return out


def run(db_path: Path, *, sessions: Sequence[int] = (), show_text: bool = False) -> List[Dict[str, Any]]:
    """按场统计；不指定场次时取有改动记录、或整理写过档案的正式场。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        if not _table_exists(conn, "memory_edits"):
            return []
        if sessions:
            ids = [int(item) for item in sessions]
        else:
            ids = sorted(
                {int(row[0]) for row in conn.execute("SELECT DISTINCT live_session_id FROM memory_edits")}
                | {int(row[0]) for row in conn.execute("SELECT DISTINCT live_session_id FROM archive_lines")}
            )
        return [session_diff(conn, session_id, show_text=show_text) for session_id in ids]
    finally:
        conn.close()


def totals(report: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """几场合起来的比例：提示词迭代看的是这一行。"""
    keys = ("written_by_settle", "retitled", "deleted", "pinned_back", "added_by_human", "lasting_flips")
    summed = {key: sum(int(item[key]) for item in report) for key in keys}
    summed["retitled_ratio"] = _ratio(summed["retitled"], summed["written_by_settle"])
    summed["deleted_ratio"] = _ratio(summed["deleted"], summed["written_by_settle"])
    summed["reviewed_sessions"] = sum(1 for item in report if item["reviewed"])
    return summed


def selftest() -> int:
    """临时库上一场：整理写 4 行，人改了 1 行标题、删 1 行、新增 1 行后又删掉、放回 1 行。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "selftest.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE archive_lines(id INTEGER PRIMARY KEY, live_session_id INT, source TEXT);"
            "CREATE TABLE memory_edits(id INTEGER PRIMARY KEY, live_session_id INT, target TEXT, archive_line_id INT,"
            " action TEXT, before_text TEXT, after_text TEXT, at_ms INT, candidate_id INT);"
            "INSERT INTO archive_lines VALUES (1, 1, 'self'), (2, 1, 'self'), (3, 1, 'self'), (6, 1, 'viewer');"
            "INSERT INTO memory_edits(live_session_id, target, archive_line_id, action, before_text, after_text, at_ms)"
            " VALUES (1, 'archive_line', 1, 'title', '旧标题', '新标题', 1),"
            " (1, 'archive_line', 4, 'delete', '', '', 2),"
            " (1, 'archive_line', 5, 'add', '', '人补的一行', 3),"
            " (1, 'archive_line', 5, 'delete', '', '', 4),"
            " (1, 'snapshot_line', 2, 'pin', '', '', 5),"
            " (1, 'session', NULL, 'reviewed', '', '', 6);"
        )
        conn.commit()
        conn.close()
        report = run(path)
    item = report[0] if len(report) == 1 else {}
    ok = (
        item.get("written_by_settle") == 4
        and item.get("retitled") == 1
        and item.get("deleted") == 1
        and item.get("pinned_back") == 1
        and item.get("added_by_human") == 1
        and item.get("reviewed") is True
        and totals(report)["retitled_ratio"] == 0.25
    )
    print("selftest", "PASS" if ok else f"FAIL {report}")
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="人工改动统计（只读；默认只出数字）")
    parser.add_argument("db", nargs="?", type=Path, help="直播库路径")
    parser.add_argument("--session", type=int, action="append", default=[], help="只算这几场（可重复）")
    parser.add_argument("--show-text", action="store_true", help="在屏幕上列出标题改前改后（不落盘）")
    parser.add_argument("--selftest", action="store_true", help="在临时库上自检")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.db is None:
        parser.error("要给直播库路径，或用 --selftest")
    report = run(args.db, sessions=args.session, show_text=args.show_text)
    for entry in report:
        print(json.dumps(entry, ensure_ascii=False))
    print(json.dumps({"total": totals(report)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
