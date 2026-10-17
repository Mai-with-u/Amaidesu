#!/usr/bin/env python
"""话题查重回放：用一场的真实发言与话题摘要序列，量化查重机制"本应拦多少、实际拦多少"。

复盘第二步（内容分析）里核查重复话题检测机制是否生效的工具。对窗口内每一轮，按与线上一致的标准调用
``check_topic``（含引注剥除、CJK 过滤、新词门槛），并附"不做豁免"的重合率对照：

    uv run python .agents/skills/livestream-replay/scripts/gate_replay.py \
        --events logs/events/2026-10-15/21.jsonl --window 6 --fresh-minutes 15

fresh 语料（新实体豁免的证据来源）取发言前 N 分钟内的游戏事件文本，近似线上
注入的叙事窗口；需要 import 本仓库的 topic_gate，因此须在仓库根用 uv run 执行。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 脚本位于 <仓库根>/.agents/skills/livestream-replay/scripts/，向上四级到仓库根
_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

from src.agents.streamer.topic_gate import (  # noqa: E402
    DUPLICATE_RATIO,
    _normalize_numbers,
    char_bigrams,
    check_topic,
    strip_citations,
)

# fresh 语料的事件类型：与线上游戏叙事的来源一致（进展/遭遇/任务变化）
_FRESH_EVENTS = {
    "game.milestone",
    "game.attention_required",
    "game.body.need_unhandled",
    "game.body.attacked",
    "game.body.reflex_started",
    "game.body.reflex_finished",
    "task.changed",
}


def _hhms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda row: int(row.get("timestamp_ms") or 0))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="话题查重回放（真实发言序列过 check_topic）")
    parser.add_argument("--events", type=Path, required=True, help="事件日志 JSONL")
    parser.add_argument(
        "--window", type=int, default=6, help="已聊话题回看窗（轮），与线上 planner_recent_intents_max 对齐"
    )
    parser.add_argument("--fresh-minutes", type=int, default=15, help="豁免语料的游戏事件回看分钟数")
    args = parser.parse_args()

    verdict_topics: dict[str, str] = {}
    speeches: list[tuple[int, str, str]] = []
    fresh_events: list[tuple[int, str]] = []
    for row in read_jsonl(args.events):
        name = str(row.get("event_name"))
        ts = int(row.get("timestamp_ms") or 0)
        data = row.get("data") or {}
        if name == "planner.verdict" and data.get("topic_summary"):
            verdict_topics[str(data.get("round_id"))] = str(data["topic_summary"])
        elif name == "streamer.speech":
            speeches.append((ts, str(data.get("round_id")), str(data.get("text") or "")))
        elif name in _FRESH_EVENTS:
            fresh_events.append((ts, str(data.get("message") or data.get("summary") or "")))

    speeches.sort()
    rounds = [(ts, rid, verdict_topics[rid], text) for ts, rid, text in speeches if rid in verdict_topics]
    if len(rounds) < 2:
        raise SystemExit("可回放的轮次不足（需要至少 2 轮既有话题摘要又有发言）")

    blocked: list[tuple[int, str]] = []
    exempted = 0
    plain_allowed = 0
    ratios: list[float] = []
    over_line = 0
    for i, (ts, _rid, topic, _text) in enumerate(rounds):
        fresh_text = " ".join(msg for ets, msg in fresh_events if ts - args.fresh_minutes * 60000 <= ets <= ts)
        known_topics = [rounds[j][2] for j in range(max(0, i - args.window), i)]
        known_speeches = [rounds[j][3] for j in range(max(0, i - args.window), i)]
        outcome = check_topic(
            topic,
            recent_topics=known_topics,
            recent_speeches=known_speeches,
            fresh_texts=[fresh_text] if fresh_text else (),
        )
        ratios.append(outcome.overlap_ratio)
        candidate = char_bigrams(_normalize_numbers(strip_citations(topic)))
        known: set[str] = set()
        for j in range(max(0, i - args.window), i):
            for text in (rounds[j][2], rounds[j][3]):
                known |= char_bigrams(_normalize_numbers(strip_citations(text)))
        raw_ratio = len(candidate & known) / len(candidate) if candidate and known else 0.0
        if raw_ratio >= DUPLICATE_RATIO:
            over_line += 1
        if not outcome.allowed:
            blocked.append((ts, topic))
        elif outcome.duplicate:
            exempted += 1
        else:
            plain_allowed += 1
        print(
            f"{_hhms(ts)} 重合率={outcome.overlap_ratio:4.0%} 未豁免重合率={raw_ratio:4.0%} "
            f"{'拦截' if not outcome.allowed else ('豁免放行' if outcome.duplicate else '放行')}  {topic[:40]}"
        )

    print()
    print(
        f"== 汇总（{len(rounds)} 轮，回看窗 {args.window} 轮，豁免语料 {args.fresh_minutes} 分钟）==\n"
        f"未豁免重合率 ≥ {DUPLICATE_RATIO:.0%}（规则上应拦）：{over_line} 轮\n"
        f"实际拦截 {len(blocked)} 轮 / 豁免放行 {exempted} 轮 / 无重复直接放行 {plain_allowed} 轮\n"
        f"重合率中位 {statistics.median(ratios):.0%} 最大 {max(ratios):.0%}"
    )
    if blocked:
        print("被拦轮次：")
        for ts, topic in blocked:
            print(f"  {_hhms(ts)}  {topic[:50]}")


if __name__ == "__main__":
    main()
