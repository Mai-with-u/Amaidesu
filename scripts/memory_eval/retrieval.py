"""被动想起语义一路的阈值核对：在真实直播库存下的向量上，看余弦门槛（VECTOR_MIN_COSINE）卡在哪。

生产索引任务把档案行、承诺、她的台词、游戏事件都算好向量存进库里（``memory_vectors_*``），弹幕的
查询向量不存。所以这里能在真实数据上量的是两件事，都不用再调嵌入服务：

- **原话命中**：档案行、承诺各有一句作证据的台词（原话从那句里逐字截出）。拿那句台词的向量去对
  全部条目，看它对应的条目余弦多少、排第几——这相当于"完整原话"这一类说法，是最容易的一类，
  量出来的是上限。
- **她自己台词的误推**：其余台词（不是任何条目的证据）对全部条目的最高余弦，按门槛算会有多少句
  "推出去"。这些台词里有一部分本来就在聊同一件事，所以这是误推率的上界；她的台词也不走语义一路，
  这里只拿它当"同一个直播间里的话"的分布来定门槛。游戏事件同样算一遍作参照。

只读打开直播库，输出只有分数与比例，不带原文，报告不入库。换说法的查询（半截引用、谐音、同义
改写等）要真调嵌入服务，见 README 的说明。

用法：
    uv run python scripts/memory_eval/retrieval.py --real <直播库路径>
    uv run python scripts/memory_eval/retrieval.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents.streamer.memory.passive import VECTOR_MIN_COSINE, VECTOR_TOP  # noqa: E402

#: 扫的门槛
THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85)
#: 两档误推目标：无关文本逐条 0.2%（方案定的校准口径）、她自己台词 2%（方案门槛表）
TARGET_RATES = (0.002, 0.02)


def _vectors(conn: sqlite3.Connection, table: str, key: str) -> Tuple[List[int], np.ndarray]:
    """一张向量表：主键列表与按行归一化后的矩阵（没有这张表或是空表时矩阵为空）。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return [], np.zeros((0, 0), dtype=np.float32)
    rows = conn.execute(f"SELECT {key}, vector FROM {table} ORDER BY {key}").fetchall()  # noqa: S608 常量表名
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    matrix = np.stack([np.frombuffer(row[1], dtype="<f4") for row in rows]).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return [int(row[0]) for row in rows], matrix / norms


def _evidence(conn: sqlite3.Connection, table: str) -> Dict[int, int]:
    """条目主键 → 作证据的那句台词（live_chat 行）；没有证据列或证据为空的不算。"""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608 常量表名
    if "evidence_chat_id" not in cols:
        return {}
    return {
        int(row[0]): int(row[1])
        for row in conn.execute(f"SELECT id, evidence_chat_id FROM {table} WHERE evidence_chat_id IS NOT NULL")  # noqa: S608
    }


def _quantiles(values: np.ndarray) -> Dict[str, float]:
    if values.size == 0:
        return {}
    return {
        name: round(float(np.quantile(values, q)), 3) for name, q in (("p50", 0.5), ("p90", 0.9), ("p99", 0.99))
    } | {"max": round(float(values.max()), 3)}


def _rates(max_cos: np.ndarray) -> Dict[str, float]:
    return {f"{t:.2f}": round(float((max_cos >= t).mean()), 4) for t in THRESHOLDS} if max_cos.size else {}


def _threshold_for(max_cos: np.ndarray, rate: float) -> Optional[float]:
    """误推率不超过 rate 的最低门槛（取那个分位上的余弦再往上一点）。"""
    if max_cos.size == 0:
        return None
    return round(float(np.quantile(max_cos, 1.0 - rate)) + 0.001, 3)


def evaluate(conn: sqlite3.Connection) -> Dict[str, Any]:
    archive_ids, archive = _vectors(conn, "memory_vectors_archive", "archive_line_id")
    commitment_ids, commitments = _vectors(conn, "memory_vectors_commitment", "commitment_id")
    speech_ids, speech = _vectors(conn, "memory_vectors_speech", "chat_id")
    _event_ids, events = _vectors(conn, "memory_vectors_event", "event_id")
    if not archive_ids and not commitment_ids:
        return {"error": "库里没有档案行或承诺的向量（语义检索没开过）"}

    parts = [m for m in (archive, commitments) if m.size]
    items = np.concatenate(parts, axis=0)
    labels = [("archive", i) for i in archive_ids] + [("commitment", i) for i in commitment_ids]
    evidence = {("archive", k): v for k, v in _evidence(conn, "archive_lines").items()}
    evidence.update({("commitment", k): v for k, v in _evidence(conn, "commitments").items()})
    speech_row = {chat_id: row for row, chat_id in enumerate(speech_ids)}

    # 原话命中：作证据的那句台词对它自己的条目
    hit_cos: List[float] = []
    ranks: List[int] = []
    for column, label in enumerate(labels):
        row = speech_row.get(evidence.get(label, -1))
        if row is None:
            continue
        sims = items @ speech[row]
        hit_cos.append(float(sims[column]))
        ranks.append(int((sims > sims[column]).sum()) + 1)
    hits = np.array(hit_cos, dtype=np.float32)

    # 她自己台词的误推：不是任何条目证据的台词，对全部条目的最高余弦
    evidence_rows = {speech_row[chat] for chat in evidence.values() if chat in speech_row}
    others = np.array([row for row in range(len(speech_ids)) if row not in evidence_rows], dtype=int)
    speech_max = (speech[others] @ items.T).max(axis=1) if others.size else np.zeros(0, dtype=np.float32)
    event_max = (events @ items.T).max(axis=1) if events.size else np.zeros(0, dtype=np.float32)

    return {
        "current_threshold": VECTOR_MIN_COSINE,
        "items": {"archive": len(archive_ids), "commitment": len(commitment_ids)},
        "verbatim_hits": {
            "pairs": len(hit_cos),
            "cosine": _quantiles(hits) | ({"min": round(float(hits.min()), 3)} if hits.size else {}),
            f"recall@{VECTOR_TOP}_by_rank": round(sum(1 for r in ranks if r <= VECTOR_TOP) / len(ranks), 3)
            if ranks
            else None,
            "passes_threshold": _rates(hits),
        },
        "her_lines": {
            "lines": int(others.size),
            "max_cosine": _quantiles(speech_max),
            "push_rate": _rates(speech_max),
            "threshold_for_rate": {f"{r:g}": _threshold_for(speech_max, r) for r in TARGET_RATES},
        },
        "game_events": {
            "events": int(event_max.size),
            "max_cosine": _quantiles(event_max),
            "push_rate": _rates(event_max),
        },
    }


def run_real(db_path: Path) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        report = evaluate(conn)
        report["semantic_cards"] = (
            [
                json.loads(row[0]).get("cosine")
                for row in conn.execute(
                    "SELECT detail FROM memory_cue_log WHERE card_kind='vector' ORDER BY pushed_at_ms"
                )
            ]
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='memory_cue_log'").fetchone()
            else []
        )
        return report
    finally:
        conn.close()


def selftest() -> int:
    """四维假向量：两条条目、各一句证据台词、两句无关台词，核对配对、排名和门槛口径。"""

    def blob(values: Sequence[float]) -> bytes:
        return np.array(values, dtype="<f4").tobytes()

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "selftest.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE archive_lines(id INTEGER PRIMARY KEY, evidence_chat_id INT);"
            "CREATE TABLE commitments(id INTEGER PRIMARY KEY, evidence_chat_id INT);"
            "CREATE TABLE memory_vectors_archive(archive_line_id INT, vector BLOB);"
            "CREATE TABLE memory_vectors_commitment(commitment_id INT, vector BLOB);"
            "CREATE TABLE memory_vectors_speech(chat_id INT, vector BLOB);"
            "INSERT INTO archive_lines VALUES (1, 10); INSERT INTO commitments VALUES (2, 20);"
        )
        conn.executemany("INSERT INTO memory_vectors_archive VALUES (?, ?)", [(1, blob([1, 0, 0, 0]))])
        conn.executemany("INSERT INTO memory_vectors_commitment VALUES (?, ?)", [(2, blob([0, 1, 0, 0]))])
        conn.executemany(
            "INSERT INTO memory_vectors_speech VALUES (?, ?)",
            [
                (10, blob([0.9, 0.1, 0, 0])),
                (20, blob([0.2, 0.8, 0, 0])),
                (30, blob([0, 0, 1, 0])),
                (40, blob([0.7, 0, 0.7, 0])),
            ],
        )
        conn.commit()
        report = evaluate(conn)
        conn.close()
    hits = report["verbatim_hits"]
    her = report["her_lines"]
    ok = (
        hits["pairs"] == 2
        and hits[f"recall@{VECTOR_TOP}_by_rank"] == 1.0
        and her["lines"] == 2
        # 第 40 句对档案行 1 的余弦约 0.707：0.70 推、0.75 不推；第 30 句与两条都正交
        and her["push_rate"]["0.70"] == 0.5
        and her["push_rate"]["0.75"] == 0.0
    )
    print("selftest", "PASS" if ok else f"FAIL {json.dumps(report, ensure_ascii=False)}")
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="语义一路门槛核对（真实库只读，只出分数与比例）")
    parser.add_argument("--real", type=Path, default=None, help="直播库路径（只读）")
    parser.add_argument("--selftest", action="store_true", help="在临时库上用假向量自检")
    args = parser.parse_args(argv)
    if args.selftest or args.real is None:
        return selftest()
    print(json.dumps(run_real(args.real), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
