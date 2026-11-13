"""记忆档案行人工复核命令行工具：走 MemoryReviewRepo 正规路径查询或作废档案行并记入 memory_edits。

用法示例（在开播机器仓库根执行）：
  1. 核对指定档案行内容与所在快照：
     uv run python scripts/review_archive_cli.py inspect --db data/amaidesu.db --line-id 47
  2. 走 memory_edits 人工改动记录路径作废删除该行（自动从所有快照级联撤出并清检索索引）：
     uv run python scripts/review_archive_cli.py delete --db data/amaidesu.db --line-id 47
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from src.modules.storage.database import SQLiteDatabase
from src.modules.utils.time_utils import now_ms


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过 MemoryReviewRepo 查看或作废指定档案行（写 memory_edits）")
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd_name, help_text in (
        ("inspect", "查看指定档案行的当前内容与快照归属"),
        ("delete", "走 memory_edits 人工改动记录路径删除作废指定档案行"),
    ):
        p = sub.add_parser(cmd_name, help=help_text)
        p.add_argument("--db", default="data/amaidesu.db", help="SQLite 数据库文件路径")
        p.add_argument("--line-id", type=int, required=True, help="档案行主键（即简报中的 档#N）")
    return parser.parse_args(argv)


async def run_cli(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库文件不存在: {db_path}", file=sys.stderr)
        return 2

    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        rows = await db.archive.list_lines_by_ids(line_ids=[int(args.line_id)])
        if not rows:
            print(f"未找到 档#{args.line_id}（可能已删除）")
            return 1
        line = rows[0]
        print(f"[档#{line.id}] 场次=#{line.live_session_id} 游戏={line.game or '无'} 标题={line.text!r}")
        if args.command == "inspect":
            return 0

        deleted = await db.memory_review.delete_line(line_id=int(args.line_id), at_ms=now_ms())
        if not deleted:
            print(f"档#{args.line_id} 删除未命中", file=sys.stderr)
            return 1
        edits = await db.memory_review.count_edit_records(live_session_id=int(line.live_session_id))
        print(f"已作废 档#{args.line_id} 并写入 memory_edits（场次 #{line.live_session_id} 累计人工改动 {edits} 笔）")
        return 0
    finally:
        await db.close()


def main() -> None:
    raise SystemExit(asyncio.run(run_cli()))


if __name__ == "__main__":
    main()
