#!/usr/bin/env python
"""挂起站桩统计：游戏 Agent 挂起等目标事件的无进展唤醒（game.suspend.no_progress）。

发呆治理的验收指标——每场复盘统计"长挂次数与合计站桩时长"：

    uv run python .agents/skills/livestream-review/scripts/suspend_stats.py \
        --events logs/events/2026-11-08/20.jsonl --since 20:00 --until 22:00

无进展唤醒为 0 = 挂起等待都在上限内自然醒；次数多、时长高 = 有条件挂住了不推进
（配合场次文档里的时间线看是哪类条件）。时间过滤按本地时钟 HH:MM；只依赖标准库。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

EVENT_NAME = "game.suspend.no_progress"


def _hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _in_window(ms: int, since: str | None, until: str | None) -> bool:
    hhmm = _hhmm(ms)
    if since and hhmm < since:
        return False
    if until and hhmm > until:
        return False
    return True


def _stalled_seconds(message: str) -> float:
    m = re.search(r"已 (\d+) 秒没有任何进展", message)
    return float(m.group(1)) if m else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="挂起站桩统计（无进展唤醒事件）")
    parser.add_argument("--events", type=Path, required=True, action="append", help="事件 JSONL（可多次给）")
    parser.add_argument("--since", help="窗口起点 HH:MM")
    parser.add_argument("--until", help="窗口终点 HH:MM")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for path in args.events:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    rows = sorted(
        (r for r in rows if r.get("event_name") == EVENT_NAME),
        key=lambda row: int(row.get("timestamp_ms") or 0),
    )
    rows = [r for r in rows if _in_window(int(r.get("timestamp_ms") or 0), args.since, args.until)]
    if not rows:
        print("无无进展唤醒事件（挂起等待都在上限内自然醒，或本场没有挂起站桩）")
        return

    total_standing = 0.0
    print(f"== 无进展唤醒 {len(rows)} 次 ==")
    for row in rows:
        message = str((row.get("data") or {}).get("message") or "")
        total_standing += _stalled_seconds(message)
        print(f"  {_hhmm(int(row.get('timestamp_ms') or 0))}  {message[:90]}")
    print(f"合计站桩约 {total_standing / 60:.0f} 分钟（按各次唤醒时的无进展秒数累加，下限口径）")


if __name__ == "__main__":
    main()
