"""一次性运维脚本：llm_requests 存量正文出库 + 库瘦身。

背景（2026-10-11 正文出库改造的存量处置）：
- v14 起 ``llm_requests`` 正文大字段出库到 ``logs/llm_requests/`` 诊断文件，
  新行库列一律 NULL；本脚本把**存量行**的正文按新文件形态导出后置空，
  使读侧（详情 / 水位懒取）对存量行与新增行行为一致。
- 导出按每行 ``timestamp_ms`` 归位到对应小时流文件（与新写入同形态，
  读侧无差别回查）。

流程：备份 → 逐行导出（追加小时流）→ 置空五字段 → VACUUM。
幂等性：置空后重复执行零导出零更新；中断重跑只补未处理行。

用法：
    uv run python scripts/export_llm_payloads_to_files.py            # 实际执行
    uv run python scripts/export_llm_payloads_to_files.py --dry-run  # 只统计不动库
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "amaidesu.db"
LOGS_DIR = PROJECT_ROOT / "logs"
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"

# 出库的正文列（与写侧 _persist_request / _persist_llm_call 置空清单一致）
PAYLOAD_COLUMNS = ("request_params", "reasoning_content", "tool_calls", "usage_raw_json", "breakdown_json")


def _parse_json_column(raw: object) -> object:
    """JSON 文本列 → 对象（解析失败原样返回字符串）。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _export_row_to_file(row: sqlite3.Row, logs_dir: Path) -> None:
    """单行正文按新形态追加到对应小时流文件。"""
    ts_ms = int(row["timestamp_ms"] or 0)
    dt = datetime.fromtimestamp(ts_ms / 1000)
    day_dir = logs_dir / "llm_requests" / dt.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "request_id": row["request_id"],
        "timestamp_ms": ts_ms,
        "profile_name": row["profile_name"] or "",
        "model_name": row["model_name"] or "",
        "success": bool(row["success"]),
        "request_params": _parse_json_column(row["request_params"]) or {},
        "response_content": row["response_content"],
        "reasoning_content": row["reasoning_content"],
        "tool_calls": _parse_json_column(row["tool_calls"]) or [],
        "usage_raw_json": _parse_json_column(row["usage_raw_json"]),
        "breakdown": _parse_json_column(row["breakdown_json"]),
    }
    stream = day_dir / f"{dt.strftime('%H')}.jsonl"
    with open(stream, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只统计待处理行数，不写文件不动库")
    args = parser.parse_args()

    if not DB_PATH.is_file():
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    predicate = " OR ".join(f"({col} IS NOT NULL AND {col} != '')" for col in PAYLOAD_COLUMNS)
    pending = conn.execute(f"SELECT COUNT(*) FROM llm_requests WHERE {predicate}").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM llm_requests").fetchone()[0]
    print(f"总行数 {total}，含存量正文待导出 {pending} 行")

    if args.dry_run:
        conn.close()
        return 0
    if pending == 0:
        print("无存量正文需要导出")
        conn.close()
        return 0

    # 备份（导出+置空不可逆，先落备份）
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"amaidesu-pre-payload-export-{time.strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(DB_PATH, backup_path)
    print(f"已备份: {backup_path}")

    rows = conn.execute(f"SELECT * FROM llm_requests WHERE {predicate} ORDER BY timestamp_ms").fetchall()
    exported = 0
    for row in rows:
        try:
            _export_row_to_file(row, LOGS_DIR)
        except OSError as exc:
            print(f"导出失败（跳过置空）request_id={row['request_id']}: {exc}", file=sys.stderr)
            continue
        exported += 1
    print(f"已导出 {exported} 行到 {LOGS_DIR / 'llm_requests'}")

    # 置空五字段（导出成功的才置空；失败行保留库内正文，重跑可再导出）
    cleared_pred = " OR ".join(f"({col} IS NOT NULL AND {col} != '')" for col in PAYLOAD_COLUMNS)
    updates = ", ".join(f"{col} = NULL" for col in PAYLOAD_COLUMNS)
    with conn:
        cur = conn.execute(f"UPDATE llm_requests SET {updates} WHERE {cleared_pred}")
    print(f"已置空 {cur.rowcount} 行的正文列")

    print("VACUUM 中（回收页空间，期间锁库）...")
    conn.execute("VACUUM")
    size_mb = DB_PATH.stat().st_size / 1048576
    print(f"完成。库文件现大小: {size_mb:.1f}MB")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
