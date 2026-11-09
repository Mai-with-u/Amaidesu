#!/usr/bin/env python
"""重合度回放：用一场的真实序列算台词之间的字面重合率，量化"哪一层在复读"。

复盘内容分析步里核查重复治理是否生效的工具。两条序列各跑一遍：

- 台词序列（streamer.speech）：成品发言对最近 N 条已说发言的 bigram 包含率——
  线下台词层自身的复读程度；
- 指导语序列（planner.decision.reply_guidance）：planner 写给台词生成环节的指导语互相之间的
  重合率——这一层不在台词层，比较两层的重合率就能看出复读源头在决策环节还是表达环节。

    uv run python .agents/skills/livestream-review/scripts/gate_replay.py \
        --events logs/events/2026-11-02/21.jsonl logs/events/2026-11-02/22.jsonl --since 21:36 --until 22:15

窗口与阈值默认取线上曾用过的口径，可用 --window/--ratio 做参数敏感性对照。

包含率计算（字符 bigram 集合运算）此前住在 ``src/agents/streamer/topic_gate.py``——
那个模块随台词重合度闸一并退役，本脚本内联一份纯函数拷贝，判定口径不变。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

# 贴线带：重合率落在 [阈值 - NEAR_BAND, 阈值) 视为"差一点被拦"，单独计数
NEAR_BAND = 0.15

#: 回看窗（参与比对的最近发言条数）
DEFAULT_WINDOW = 10
#: 判重阈值（重合率达到即视为重复）
DEFAULT_RATIO = 0.5

#: 字符 n-gram 的 n（bigram）
NGRAM_SIZE = 2

#: 数值归一模式：阿拉伯数字串（含全宽与小数）或中文数字串，统一替换为占位符
_NUMBER_PATTERN = re.compile(r"[0-9０-９]+(?:[.．][0-9０-９]+)*|[零一二两三四五六七八九十百千万亿]+")
#: 数值归一后的占位符
_NUMBER_PLACEHOLDER = "N"
#: 括号引注段：中英文括号包住的一段（不含嵌套）
_CITATION_PAREN_PATTERN = re.compile(r"（[^（）]*）|\([^()]*\)")
#: 引注段判定标记：括号内含这些字样或含 ASCII 字母数字（内部标识/坐标）即视为出处引注
_CITATION_MARKERS = ("见", "记录", "确认", "回顾", "参考", "来自", "出处")
_CITATION_ASCII = re.compile(r"[A-Za-z0-9_]")
#: 残余的 ASCII 字母词元（内部标识）：数字不在其列，留给数值归一统一压成占位符
_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z_]+")


def strip_citations(text: str) -> str:
    """剥除文本里的出处引注，返回只含自然语言的比对语料。

    依次去掉：含引注标记或 ASCII 字母数字的括号段（如"（见 reflex_finished）"
    "（-86,66,50）"），以及残余的 ASCII 字母词元。在数值归一之前调用——
    占位符"N"本身是 ASCII 字母，顺序颠倒会被误删。
    """
    cleaned = text or ""
    for paren in _CITATION_PAREN_PATTERN.findall(cleaned):
        inner = paren[1:-1]
        if any(marker in inner for marker in _CITATION_MARKERS) or _CITATION_ASCII.search(inner):
            cleaned = cleaned.replace(paren, " ")
    return _ASCII_TOKEN_PATTERN.sub(" ", cleaned)


def _normalize_numbers(text: str) -> str:
    """把文本中的数值串替换为统一占位符（数值变化不构成新内容）。"""
    return _NUMBER_PATTERN.sub(_NUMBER_PLACEHOLDER, text or "")


def char_bigrams(text: str) -> set[str]:
    """文本的字符 bigram 集合（去空白；长度不足 n 时返回空集——无可比对成分）。"""
    chars = [c for c in (text or "") if not c.isspace()]
    return {"".join(chars[i : i + NGRAM_SIZE]) for i in range(len(chars) - NGRAM_SIZE + 1)}


def _gate_bigrams(text: str) -> set[str]:
    """比对语料 bigram：引注剥除 → 数值归一 → 字符 bigram。"""
    return char_bigrams(_normalize_numbers(strip_citations(text)))


def speech_overlap_ratio(speech: str, recent_speeches: Sequence[str]) -> float:
    """成品台词对最近发言语料的 bigram 包含率（0.0~1.0）。

    台词或语料任一侧没有可比对成分（过短/全空白）时返回 0.0——无从比对即放行。
    """
    candidate = _gate_bigrams(speech)
    if not candidate:
        return 0.0
    known: set[str] = set()
    for text in recent_speeches:
        known |= _gate_bigrams(text)
    if not known:
        return 0.0
    return len(candidate & known) / len(candidate)


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


def replay(label: str, sequence: list[tuple[int, str]], window: int, ratio_line: float) -> None:
    """对一条文本序列逐轮算重合率，打印超阈与贴线轮次，末尾汇总。"""
    if len(sequence) < 2:
        print(f"== {label}：可回放轮次不足（{len(sequence)}）==")
        return
    ratios: list[float] = []
    over: list[tuple[int, float, str]] = []
    near: list[tuple[int, float, str]] = []
    for i, (ts, text) in enumerate(sequence):
        recent = [t for _, t in sequence[max(0, i - window) : i]]
        ratio = speech_overlap_ratio(text, recent)
        ratios.append(ratio)
        if ratio >= ratio_line:
            over.append((ts, ratio, text))
        elif ratio >= ratio_line - NEAR_BAND:
            near.append((ts, ratio, text))
    print(f"== {label}（{len(sequence)} 轮）==")
    print(
        f"重合率中位 {statistics.median(ratios):.0%} 最大 {max(ratios):.0%}；"
        f"按阈值 {ratio_line:.0%} 超阈 {len(over)} 轮；贴线（≥{ratio_line - NEAR_BAND:.0%}）{len(near)} 轮"
    )
    for ts, r, text in over:
        print(f"  超 {_hhms(ts)} {r:4.0%}  {text[:48]}")
    for ts, r, text in near:
        print(f"  贴 {_hhms(ts)} {r:4.0%}  {text[:48]}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="重合度回放（台词序列 + 指导语序列各算一遍）")
    parser.add_argument("--events", type=Path, required=True, nargs="+", help="事件日志 JSONL（可多个文件）")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="回看窗（轮）")
    parser.add_argument("--ratio", type=float, default=DEFAULT_RATIO, help="判重阈值")
    parser.add_argument("--since", help="窗口起点 HH:MM（本地时钟）")
    parser.add_argument("--until", help="窗口终点 HH:MM（本地时钟）")
    args = parser.parse_args()

    speeches: list[tuple[int, str]] = []
    guidances: list[tuple[int, str]] = []
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
    speeches.sort()
    guidances.sort()

    print(f"参数：回看窗 {args.window} 轮，阈值 {args.ratio:.0%}\n")
    replay("台词序列（台词层自身的复读）", speeches, args.window, args.ratio)
    replay("指导语序列（决策层的复读）", guidances, args.window, args.ratio)


if __name__ == "__main__":
    main()
