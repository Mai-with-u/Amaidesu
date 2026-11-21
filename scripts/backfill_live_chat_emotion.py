"""一次性回填工具：从事件日志把历史台词的情绪写回 ``live_chat``。

数据来源与去向：

- 源：``logs/events/<日期>/<小时>.jsonl``（事件查看器的文件存储）里
  ``streamer.speech`` 事件载荷——它一直携带 ``utterance_id`` /
  ``emotion`` / ``emotion_intensity``（虚拟形象侧一直在消费），只是
  v49 之前落库时被丢弃。
- 去向：``live_chat`` 表按 ``utterance_id`` 回填 ``emotion`` /
  ``emotion_intensity`` 两列；**只填 NULL 行**（幂等，可重复执行，
  不覆盖任何已落库值）。

用法（实例运行中可安全执行：短事务 + 只写 NULL 行；先跑 ``--dry-run``
看匹配统计再真写）：

    python scripts/backfill_live_chat_emotion.py --dry-run
    python scripts/backfill_live_chat_emotion.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Emotion 枚举 17 值（与 src/modules/types/emotion_vocab.py 同源；词表外不回填）
_VALID_EMOTIONS = {
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprised",
    "scared",
    "disgusted",
    "shy",
    "embarrassed",
    "confused",
    "love",
    "excited",
    "smug",
    "serious",
    "tired",
    "crying",
    "speechless",
}


def collect_event_emotions(logs_dir: Path) -> tuple[dict[str, tuple[str, float]], int]:
    """扫全部事件日志，收集 ``utterance_id → (emotion, intensity)``；返回 (映射, 总事件数)。"""
    mapping: dict[str, tuple[str, float]] = {}
    seen = 0
    for path in sorted(logs_dir.glob("*/*.jsonl")):
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or "streamer.speech" not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "streamer.speech":
                    continue
                seen += 1
                data = record.get("data") or {}
                utterance_id = data.get("utterance_id")
                emotion = data.get("emotion")
                intensity = data.get("emotion_intensity")
                if not utterance_id or emotion not in _VALID_EMOTIONS:
                    continue
                # 同一 utterance_id 重复事件以最新为准（理论不会发生，防御性保留）
                mapping[utterance_id] = (str(emotion), float(intensity) if intensity is not None else 0.5)
    return mapping, seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--db-path", type=Path, default=REPO_ROOT / "data" / "amaidesu.db")
    parser.add_argument("--logs-dir", type=Path, default=REPO_ROOT / "logs" / "events")
    parser.add_argument("--dry-run", action="store_true", help="只统计匹配情况，不写库")
    args = parser.parse_args()

    if not args.db_path.exists():
        print(f"数据库不存在: {args.db_path}")
        return 1
    if not args.logs_dir.exists():
        print(f"事件日志目录不存在: {args.logs_dir}")
        return 1

    mapping, seen = collect_event_emotions(args.logs_dir)
    print(f"扫描完成：speech 事件 {seen} 条，词表内可回填映射 {len(mapping)} 条")

    conn = sqlite3.connect(args.db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(live_chat)").fetchall()}
        if "emotion" not in cols:
            print("live_chat 缺 emotion 列：先启动一次实例让 v49 迁移生效，再回填")
            return 1

        pending = conn.execute(
            "SELECT utterance_id FROM live_chat"
            " WHERE sender_role='assistant' AND message_type='speak'"
            " AND utterance_id IS NOT NULL AND emotion IS NULL"
        ).fetchall()
        pending_ids = {row[0] for row in pending}
        matched = pending_ids & mapping.keys()
        print(f"待回填台词行 {len(pending_ids)}，其中可从事件日志匹配 {len(matched)}")

        # 事件日志里根本没出现过的台词 utterance_id（可诊断缺口）
        unmatched_sample = sorted(pending_ids - mapping.keys())[:5]
        if unmatched_sample:
            print(f"未匹配样例: {unmatched_sample}")

        if args.dry_run or not matched:
            print("dry-run 或无可回填项，未写库")
            return 0

        updated = 0
        with conn:  # 单事务：要么全成要么全滚
            for utterance_id, (emotion, intensity) in mapping.items():
                if utterance_id not in pending_ids:
                    continue
                cur = conn.execute(
                    "UPDATE live_chat SET emotion=?, emotion_intensity=? WHERE utterance_id=? AND emotion IS NULL",
                    (emotion, intensity, utterance_id),
                )
                updated += cur.rowcount
        print(f"回填完成：更新 {updated} 行")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
