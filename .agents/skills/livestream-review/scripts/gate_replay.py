#!/usr/bin/env python
"""重合度闸回放：用一场的真实序列过线上同款 SpeechGate，量化"本应拦多少、实际拦多少、源头在哪"。

复盘内容分析步里核查重复治理是否生效的工具。两条序列各跑一遍：

- 台词序列（streamer.speech）：成品发言对最近 N 条已说发言的 bigram 重合率——线上闸就作用在这一层，
  回放结果 = 闸放行后的残余重合（贴线轮次多说明闸阈值偏松或话题供给单一）；
- 指导语序列（planner.decision.reply_guidance）：planner 写给台词生成环节的指导语互相之间的重合率——
  线上闸不检查这一层，这里是诊断：指导语自己在复读，闸只能在下游反复打回。

另统计线上实际打回次数（tool.result.streamer_reply 的 speech_gate_bounces），与回放对照。

    uv run python .agents/skills/livestream-review/scripts/gate_replay.py \
        --events logs/events/2026-11-02/21.jsonl logs/events/2026-11-02/22.jsonl --since 21:36 --until 22:15

窗口与阈值默认取线上常量，可用 --window/--ratio 做参数敏感性对照。
需要 import 本仓库的 topic_gate，因此须在仓库根用 uv run 执行。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 脚本位于 <仓库根>/.agents/skills/livestream-review/scripts/，向上四级到仓库根
_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

from src.agents.streamer.topic_gate import (  # noqa: E402
    SPEECH_DUPLICATE_RATIO,
    SPEECH_GATE_WINDOW,
    SpeechGate,
)

# 贴线带：重合率落在 [阈值 - NEAR_BAND, 阈值) 视为"差一点被拦"，单独计数
NEAR_BAND = 0.15


def _hhms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def _in_window(ms: int, since: str | None, until: str | None) -> bool:
    hhmm = datetime.fromtimestamp(ms / 1000).strftime("%H:%M")
    if since and hhmm < since:
        return False
    if until and hhmm > until:
        return False
    return True


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


def replay(label: str, sequence: list[tuple[int, str]], gate: SpeechGate, ratio_line: float) -> None:
    """对一条文本序列逐轮跑闸，打印被拦与贴线轮次，末尾汇总。"""
    if len(sequence) < 2:
        print(f"== {label}：可回放轮次不足（{len(sequence)}）==")
        return
    ratios: list[float] = []
    bounced: list[tuple[int, float, str]] = []
    near: list[tuple[int, float, str]] = []
    for i, (ts, text) in enumerate(sequence):
        recent = [t for _, t in sequence[:i]]
        verdict = gate.verdict(text, recent)
        ratios.append(verdict.ratio)
        if not verdict.passed:
            bounced.append((ts, verdict.ratio, text))
        elif verdict.ratio >= ratio_line - NEAR_BAND:
            near.append((ts, verdict.ratio, text))
    print(f"== {label}（{len(sequence)} 轮）==")
    print(
        f"重合率中位 {statistics.median(ratios):.0%} 最大 {max(ratios):.0%}；"
        f"按阈值 {ratio_line:.0%} 应拦 {len(bounced)} 轮；贴线（≥{ratio_line - NEAR_BAND:.0%}）{len(near)} 轮"
    )
    for ts, r, text in bounced:
        print(f"  拦 {_hhms(ts)} {r:4.0%}  {text[:48]}")
    for ts, r, text in near:
        print(f"  贴 {_hhms(ts)} {r:4.0%}  {text[:48]}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="重合度闸回放（台词序列 + 指导语序列各过一遍 SpeechGate）")
    parser.add_argument("--events", type=Path, required=True, nargs="+", help="事件日志 JSONL（可多个文件）")
    parser.add_argument("--window", type=int, default=SPEECH_GATE_WINDOW, help="回看窗（轮），默认线上常量")
    parser.add_argument("--ratio", type=float, default=SPEECH_DUPLICATE_RATIO, help="拦截阈值，默认线上常量")
    parser.add_argument("--since", help="窗口起点 HH:MM（本地时钟）")
    parser.add_argument("--until", help="窗口终点 HH:MM（本地时钟）")
    args = parser.parse_args()

    speeches: list[tuple[int, str]] = []
    guidances: list[tuple[int, str]] = []
    live_bounces = 0
    for path in args.events:
        for row in read_jsonl(path):
            ts = int(row.get("timestamp_ms") or 0)
            if not _in_window(ts, args.since, args.until):
                continue
            name = str(row.get("event_name"))
            data = row.get("data") or {}
            if name == "streamer.speech" and data.get("text"):
                speeches.append((ts, str(data["text"])))
            elif name == "planner.decision" and data.get("reply_guidance"):
                guidances.append((ts, str(data["reply_guidance"])))
            elif name == "tool.result.streamer_reply":
                result = data.get("result")
                if isinstance(result, dict) and result.get("speech_gate_bounces"):
                    live_bounces += int(result["speech_gate_bounces"])
    speeches.sort()
    guidances.sort()

    gate = SpeechGate(window=args.window, ratio=args.ratio)
    print(f"参数：回看窗 {args.window} 轮，阈值 {args.ratio:.0%}；线上实际打回 {live_bounces} 次\n")
    replay("台词序列（闸作用层，回放=闸后残余）", speeches, gate, args.ratio)
    replay("指导语序列（闸未覆盖层，诊断源头复读）", guidances, gate, args.ratio)


if __name__ == "__main__":
    main()
