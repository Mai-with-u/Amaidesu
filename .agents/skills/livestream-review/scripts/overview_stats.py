#!/usr/bin/env python
"""直播会话概览统计：事件分布、生命周期时间线、场次数据、LLM 用量一次出齐。

复盘第一步（概览统计）的机械统计都在这里，统计标准与 SKILL.md 一致：

    uv run python .agents/skills/livestream-replay/scripts/overview_stats.py \
        --events logs/events/2026-10-15/21.jsonl \
        --llm logs/llm_requests/2026-10-15/21.jsonl \
        --since 21:20 --until 21:49

时间过滤按本地时钟的 HH:MM 圈窗口（跨零点的场次请拆两次跑）。只依赖标准库。
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 生命周期时间线要单列的事件；warn/error 无论类型都单列
_LIFECYCLE_EVENTS = {
    "core.startup",
    "core.shutdown",
    "live.started",
    "live.ended",
    "rundown.changed",
}
# 场次数据统计涉及的事件
_SPEECH_EVENT = "streamer.speech"
_DECISION_EVENT = "planner.decision"
_ENTER_EVENT = "room.message.enter"
_CHAT_EVENT = "game.chat.received"
_LLM_EVENT = "llm.context.used"


def _local_hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _local_hhms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读 JSONL，坏行跳过（复盘对象是事后日志，容忍个别残行）。"""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda row: int(row.get("timestamp_ms") or 0))
    return rows


def _in_window(ms: int, since: Optional[str], until: Optional[str]) -> bool:
    hhmm = _local_hhmm(ms)
    if since is not None and hhmm < since:
        return False
    if until is not None and hhmm > until:
        return False
    return True


def _print_lifecycle(events: list[dict[str, Any]]) -> None:
    print("== 生命周期时间线 ==")
    for event in events:
        name = str(event.get("event_name"))
        ts = int(event.get("timestamp_ms") or 0)
        level = str(event.get("level"))
        data = event.get("data") or {}
        if level in ("warn", "error"):
            detail = str(data.get("message") or event.get("summary") or "")[:100]
            print(f"{_local_hhms(ts)} [{level}] {event.get('source')}: {detail}")
        elif name in _LIFECYCLE_EVENTS:
            if name == "rundown.changed":
                segment = str(data.get("segment_title") or data.get("segment_id") or "")
                print(
                    f"{_local_hhms(ts)} {name}  seg={segment!r} idx={data.get('index')}/{data.get('total')} by={data.get('by')}"
                )
            else:
                print(f"{_local_hhms(ts)} {name}  {str(event.get('summary') or '')[:80]}")
    print()


def _print_session_stats(events: list[dict[str, Any]]) -> None:
    print("== 本场数据 ==")
    speeches = [e for e in events if e.get("event_name") == _SPEECH_EVENT]
    decisions = [e for e in events if e.get("event_name") == _DECISION_EVENT]
    enters = [e for e in events if e.get("event_name") == _ENTER_EVENT]
    chats = [e for e in events if e.get("event_name") == _CHAT_EVENT and (e.get("data") or {}).get("kind") != "system"]
    if speeches:
        first, last = int(speeches[0]["timestamp_ms"]), int(speeches[-1]["timestamp_ms"])
        minutes = max((last - first) / 60000, 1e-6)
        # 相邻配对两列长度天然差一，strict 只能取 False（B905 的显式要求）
        gaps = sorted(
            (int(b["timestamp_ms"]) - int(a["timestamp_ms"])) / 1000
            for a, b in zip(speeches, speeches[1:], strict=False)
        )
        if gaps:
            gap_text = f"间隔中位 {statistics.median(gaps):.0f}s 最大 {gaps[-1]:.0f}s"
        else:
            gap_text = "仅 1 条无间隔"
        print(
            f"发言 {len(speeches)} 条 ≈ {len(speeches) / minutes:.1f} 条/分钟（{_local_hhms(first)}–{_local_hhms(last)}）；{gap_text}"
        )
    else:
        print("发言 0 条")
    triggers: collections.Counter[str] = collections.Counter()
    replied = 0
    for decision in decisions:
        data = decision.get("data") or {}
        triggers[str(data.get("trigger_reason"))] += 1
        if data.get("should_reply"):
            replied += 1
    print(f"决策 {len(decisions)} 轮：{dict(triggers)}；其中开口 {replied} 轮 / 沉默 {len(decisions) - replied} 轮")
    print(f"观众弹幕 {len(chats)} 条；进房 {len(enters)} 次：", end="")
    names = [str((e.get("data") or {}).get("user", {}).get("name", "?")) for e in enters]
    print("、".join(names) if names else "无")
    print()


def _print_llm_usage(path: Optional[Path]) -> None:
    print("== LLM 用量（按请求全文统计）==")
    if path is None:
        print("（未提供 --llm，跳过；缓存命中率只有请求全文里有）")
        return
    stats: dict[str, dict[str, int]] = {}
    for row in read_jsonl(path):
        profile = str(row.get("profile_name") or "?")
        bucket = stats.setdefault(profile, collections.defaultdict(int))
        bucket["calls"] += 1
        if not row.get("success", True):
            bucket["fail"] += 1
        try:
            usage = json.loads(str(row.get("usage_raw_json") or "{}"))
        except json.JSONDecodeError:
            usage = {}
        prompt = int(usage.get("prompt_tokens") or 0)
        bucket["prompt"] += prompt
        bucket["max_prompt"] = max(bucket["max_prompt"], prompt)
        details = usage.get("prompt_tokens_details") or {}
        bucket["cached"] += int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
        bucket["completion"] += int(usage.get("completion_tokens") or 0)
        out_details = usage.get("completion_tokens_details") or {}
        bucket["reasoning"] += int(out_details.get("reasoning_tokens") or 0) if isinstance(out_details, dict) else 0
    for profile, bucket in sorted(stats.items()):
        hit = bucket["cached"] / bucket["prompt"] * 100 if bucket["prompt"] else 0.0
        print(
            f"{profile:10s} calls={bucket['calls']:4d} fail={bucket['fail']} "
            f"prompt={bucket['prompt'] / 10000:8.1f}万 cached={bucket['cached'] / 10000:8.1f}万({hit:.0f}%) "
            f"out={bucket['completion'] / 10000:.1f}万(思考{bucket['reasoning'] / 10000:.1f}万) "
            f"max_prompt={bucket['max_prompt']:,}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="直播会话概览统计（事件分布/生命周期/场次数据/LLM 用量）")
    parser.add_argument("--events", type=Path, required=True, help="事件日志 JSONL（可多次给以合并跨小时场次）")
    parser.add_argument("--llm", type=Path, help="LLM 请求全文 JSONL（可选；给出来才统计缓存命中率）")
    parser.add_argument("--since", help="窗口起点，本地 HH:MM")
    parser.add_argument("--until", help="窗口终点，本地 HH:MM")
    args = parser.parse_args()

    event_rows = read_jsonl(args.events)
    if args.since or args.until:
        event_rows = [e for e in event_rows if _in_window(int(e.get("timestamp_ms") or 0), args.since, args.until)]
    if not event_rows:
        raise SystemExit("窗口内没有事件——检查 --events 路径与 --since/--until")

    types: collections.Counter[str] = collections.Counter(str(e.get("event_name")) for e in event_rows)
    print(
        f"== 事件类型分布（窗口内共 {len(event_rows)} 条，{_local_hhmm(int(event_rows[0]['timestamp_ms']))}–{_local_hhmm(int(event_rows[-1]['timestamp_ms']))}）=="
    )
    for name, count in types.most_common():
        print(f"{count:5d}  {name}")
    print()

    _print_lifecycle(event_rows)
    _print_session_stats(event_rows)
    _print_llm_usage(args.llm)


if __name__ == "__main__":
    main()
