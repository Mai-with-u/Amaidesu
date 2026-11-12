"""杂谈体检：读一场彩排或正式场的库，输出成对的正指标与副作用指标。

用法：

    uv run python scripts/zatsudan_eval.py --db data/amaidesu.db --session 42
    uv run python scripts/zatsudan_eval.py --db data/amaidesu.db            # 默认取最近一场
    uv run python scripts/zatsudan_eval.py --selftest                      # 用临时库自检口径

只读数据库（``live_chat`` / ``rundown_transitions`` / ``llm_requests``），不改动任何运行状态。
每个正指标都配一个副作用指标，防止只看一边把她调成另一种机器人：

| 正指标 | 配对的副作用指标 |
|------|------|
| 提纲要点覆盖率（环节停留落在预期 75%～130% 的比例） | 提前切穿告警（停留不到预期 75% 就被 Agent 切走的环节） |
| 回弹幕钩子率（她紧接着回某条弹幕时，开头带对方昵称或原问题核心词） | 句首高频起头词集中度（前 5 个起头词占比，过高 = 客服腔模板） |
| 杂谈台词字数中位数 | 超长台词占比（超过杂谈上限的句子） |
| 最长沉默秒数（越短越好） | 沉默期空转决策次数（两句之间超过 3 次决策请求的空档） |
| 复读对数（近逐字重复的台词对，越少越好） | —— |

口径都在本文件的纯函数里，改口径只改这一处；``--json`` 输出机器可读结果。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# 回弹幕判定窗口：弹幕到达后这么久之内她开口，才算在接这条弹幕
_REPLY_WINDOW_MS = 30_000
# 起头词取台词前几个字
_OPENER_CHARS = 2
# 近逐字复读判定：两句相似度达到这个值算一对复读
_REPEAT_RATIO = 0.85
# 杂谈档台词上限（与配置 talk_speech_max_chars 默认值对齐），超过算超长
_TALK_MAX_CHARS = 150
# 两句之间决策请求数超过这个值算一次空转（沉默期间反复调大模型）
_IDLE_SPIN_REQUESTS = 3
# 环节停留合格区间（相对预期时长）
_DWELL_LOW = 0.75
_DWELL_HIGH = 1.30
# 决策请求所用的模型档位名
_PLANNER_PROFILE = "planner"


@dataclass(frozen=True)
class ChatRow:
    """live_chat 一行里体检用得到的字段。"""

    timestamp_ms: int
    sender_role: str
    sender_name: str
    content: str


@dataclass(frozen=True)
class Transition:
    """rundown_transitions 一行：进入某环节的时刻与预期时长。"""

    at_ms: int
    action: str
    seg_id: str
    by: str
    expected_ms: Optional[int]


@dataclass
class Report:
    """一场的体检结果：正指标与副作用指标成对给出。"""

    session_id: int
    speech_count: int = 0
    # 环节节奏
    dwell_in_range_ratio: Optional[float] = None
    early_exit_segments: List[str] = field(default_factory=list)
    # 回弹幕钩子
    hook_ratio: Optional[float] = None
    opener_top5_share: Optional[float] = None
    # 篇幅
    median_chars: Optional[float] = None
    p90_chars: Optional[float] = None
    overlong_ratio: Optional[float] = None
    # 沉默与空转
    longest_silence_s: Optional[float] = None
    idle_spin_gaps: int = 0
    # 复读
    repeat_pairs: int = 0


def speech_rows(rows: Sequence[ChatRow]) -> List[ChatRow]:
    """她自己说出口的台词（按时间正序）。"""
    return [r for r in rows if r.sender_role == "assistant" and r.content.strip()]


def hook_ratio(rows: Sequence[ChatRow]) -> Optional[float]:
    """回弹幕钩子率：弹幕后窗口内她的下一句，开头一截带对方昵称或复述弹幕里的词。

    "开头一截"取台词前 12 个字；复述按弹幕里任一长度 ≥2 的片段出现在开头一截判定。
    没有可判定的回复时返回 None。
    """
    hits = 0
    total = 0
    pending: Optional[ChatRow] = None
    for row in rows:
        if row.sender_role == "viewer" and row.content.strip():
            pending = row
            continue
        if row.sender_role != "assistant" or pending is None:
            continue
        if row.timestamp_ms - pending.timestamp_ms > _REPLY_WINDOW_MS:
            pending = None
            continue
        total += 1
        head = row.content.strip()[:12]
        name = (pending.sender_name or "").strip()
        if (name and name in head) or _shares_fragment(head, pending.content):
            hits += 1
        pending = None
    return hits / total if total else None


def _shares_fragment(head: str, danmaku: str) -> bool:
    """弹幕里有没有长度 ≥2 的片段出现在台词开头一截（顺嘴接住对方问的词）。"""
    text = "".join(danmaku.split())
    for size in range(min(6, len(text)), 1, -1):
        for start in range(0, len(text) - size + 1):
            if text[start : start + size] in head:
                return True
    return False


def opener_top5_share(speeches: Sequence[str]) -> Optional[float]:
    """句首高频起头词集中度：前 5 个最常见起头（台词前两个字）占全部台词的比例。"""
    openers = [s.strip()[:_OPENER_CHARS] for s in speeches if len(s.strip()) >= _OPENER_CHARS]
    if not openers:
        return None
    top = Counter(openers).most_common(5)
    return sum(count for _, count in top) / len(openers)


def length_stats(speeches: Sequence[str]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """台词字数中位数、90 分位与超长占比；没有台词时三项都是 None。"""
    lengths = sorted(len(s.strip()) for s in speeches if s.strip())
    if not lengths:
        return None, None, None
    p90 = lengths[min(len(lengths) - 1, int(round(0.9 * (len(lengths) - 1))))]
    overlong = sum(1 for n in lengths if n > _TALK_MAX_CHARS) / len(lengths)
    return float(statistics.median(lengths)), float(p90), overlong


def longest_silence_s(speech_times: Sequence[int]) -> Optional[float]:
    """相邻两句之间最长的空档（秒）；不到两句返回 None。"""
    if len(speech_times) < 2:
        return None
    return max(b - a for a, b in zip(speech_times, speech_times[1:], strict=False)) / 1000.0


def idle_spin_gaps(speech_times: Sequence[int], planner_times: Sequence[int]) -> int:
    """沉默期空转次数：两句之间决策请求超过阈值的空档数（她没开口却反复调大模型）。"""
    spins = 0
    for start, end in zip(speech_times, speech_times[1:], strict=False):
        between = sum(1 for t in planner_times if start < t < end)
        if between > _IDLE_SPIN_REQUESTS:
            spins += 1
    return spins


def repeat_pairs(speeches: Sequence[str], *, lookback: int = 20) -> int:
    """近逐字复读对数：每句与它前面 ``lookback`` 句比较，相似度达到阈值算一对。"""
    pairs = 0
    clean = [s.strip() for s in speeches if s.strip()]
    for i, current in enumerate(clean):
        for earlier in clean[max(0, i - lookback) : i]:
            if SequenceMatcher(None, earlier, current).ratio() >= _REPEAT_RATIO:
                pairs += 1
                break
    return pairs


def dwell_stats(transitions: Sequence[Transition], end_ms: int) -> Tuple[Optional[float], List[str]]:
    """环节停留：落在预期 75%～130% 的比例，以及被 Agent 提前切穿的环节。

    每次进入环节到下一次进入（或场次结束）算一段停留；没有预期时长的段不计入。
    """
    entries = [t for t in transitions if t.action in ("begin", "next", "goto", "restart", "append")]
    entries.sort(key=lambda t: t.at_ms)
    judged = 0
    in_range = 0
    early: List[str] = []
    for index, entry in enumerate(entries):
        if not entry.expected_ms or not entry.seg_id:
            continue
        leave = entries[index + 1] if index + 1 < len(entries) else None
        leave_ms = leave.at_ms if leave else end_ms
        ratio = (leave_ms - entry.at_ms) / entry.expected_ms
        judged += 1
        if _DWELL_LOW <= ratio <= _DWELL_HIGH:
            in_range += 1
        if ratio < _DWELL_LOW and leave is not None and leave.by == "agent":
            early.append(entry.seg_id)
    return (in_range / judged if judged else None), early


def build_report(
    session_id: int,
    rows: Sequence[ChatRow],
    transitions: Sequence[Transition],
    planner_times: Sequence[int],
) -> Report:
    """把一场的原始行汇总成体检结果。"""
    speeches = speech_rows(rows)
    texts = [r.content for r in speeches]
    times = [r.timestamp_ms for r in speeches]
    end_ms = max((r.timestamp_ms for r in rows), default=0)
    report = Report(session_id=session_id, speech_count=len(speeches))
    report.dwell_in_range_ratio, report.early_exit_segments = dwell_stats(transitions, end_ms)
    report.hook_ratio = hook_ratio(rows)
    report.opener_top5_share = opener_top5_share(texts)
    report.median_chars, report.p90_chars, report.overlong_ratio = length_stats(texts)
    report.longest_silence_s = longest_silence_s(times)
    report.idle_spin_gaps = idle_spin_gaps(times, planner_times)
    report.repeat_pairs = repeat_pairs(texts)
    return report


def load_session(
    conn: sqlite3.Connection, session_id: Optional[int]
) -> Tuple[int, List[ChatRow], List[Transition], List[int]]:
    """从库里读一场的原始行；不给场次取最近一场。"""
    conn.row_factory = sqlite3.Row
    if session_id is None:
        row = conn.execute("SELECT id FROM live_sessions ORDER BY started_at_ms DESC LIMIT 1").fetchone()
        if row is None:
            raise SystemExit("库里没有任何场次")
        session_id = int(row["id"])
    chat = [
        ChatRow(int(r["timestamp_ms"]), str(r["sender_role"]), str(r["sender_name"] or ""), str(r["content"] or ""))
        for r in conn.execute(
            "SELECT timestamp_ms, sender_role, sender_name, content FROM live_chat"
            " WHERE live_session_id=? AND dropped_at_ms IS NULL ORDER BY timestamp_ms",
            (session_id,),
        )
    ]
    transitions = [
        Transition(int(r["at_ms"]), str(r["action"]), str(r["seg_id"]), str(r["by"]), r["expected_ms"])
        for r in conn.execute(
            "SELECT at_ms, action, seg_id, by, expected_ms FROM rundown_transitions"
            " WHERE live_session_id=? ORDER BY at_ms",
            (session_id,),
        )
    ]
    planner_times: List[int] = []
    if chat:
        start, end = chat[0].timestamp_ms, chat[-1].timestamp_ms
        planner_times = [
            int(r["timestamp_ms"])
            for r in conn.execute(
                "SELECT timestamp_ms FROM llm_requests WHERE profile_name=? AND timestamp_ms BETWEEN ? AND ?",
                (_PLANNER_PROFILE, start, end),
            )
        ]
    return session_id, chat, transitions, planner_times


def _fmt(value: Optional[float], unit: str = "", pct: bool = False) -> str:
    if value is None:
        return "（无数据）"
    return f"{value * 100:.0f}%" if pct else f"{value:.1f}{unit}"


def render_table(report: Report) -> str:
    """体检表：每行一对正指标与副作用指标。"""
    lines = [
        f"场次 {report.session_id}：她说了 {report.speech_count} 句",
        "",
        "| 正指标 | 值 | 配对的副作用指标 | 值 |",
        "|------|------|------|------|",
        f"| 环节停留合格率（预期 75%～130%） | {_fmt(report.dwell_in_range_ratio, pct=True)} "
        f"| 提前切穿的环节 | {', '.join(report.early_exit_segments) or '无'} |",
        f"| 回弹幕钩子率 | {_fmt(report.hook_ratio, pct=True)} "
        f"| 前 5 个起头词集中度 | {_fmt(report.opener_top5_share, pct=True)} |",
        f"| 台词字数中位数（90 分位） | {_fmt(report.median_chars, ' 字')}（{_fmt(report.p90_chars, ' 字')}） "
        f"| 超长台词占比 | {_fmt(report.overlong_ratio, pct=True)} |",
        f"| 最长沉默 | {_fmt(report.longest_silence_s, ' 秒')} | 沉默期空转次数 | {report.idle_spin_gaps} |",
        f"| 近逐字复读对数 | {report.repeat_pairs} | —— | —— |",
    ]
    return "\n".join(lines)


def _selftest() -> int:
    """用项目真实建表语句建临时库、写一小场数据，核对每项口径。"""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from src.modules.storage.schema import build_schema_sql  # 自检才需要项目建表语句，按需导入

    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "eval.db")
        conn.executescript(build_schema_sql())
        conn.execute("INSERT INTO live_sessions(id, started_at_ms, kind, updated_at_ms) VALUES (1, 0, 'rehearsal', 0)")
        chat = [
            (1_000, "assistant", "麦麦", "大家好呀，今天第一次正式站上台"),
            (5_000, "viewer", "观众甲", "腰上的旋钮是干嘛的"),
            (8_000, "assistant", "麦麦", "观众甲问旋钮啊，这个可以调我的参数"),
            (60_000, "assistant", "麦麦", "大家好呀，今天第一次正式站上台"),
            (70_000, "viewer", "观众乙", "你是真人吗"),
            (75_000, "assistant", "麦麦", "说起来今天天气不错"),
        ]
        conn.executemany(
            "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, sender_name, content, message_type)"
            " VALUES (1, ?, ?, ?, ?, 'danmaku')",
            chat,
        )
        conn.executemany(
            "INSERT INTO rundown_transitions(live_session_id, rundown_id, action, seg_id, seg_index, by, at_ms,"
            " expected_ms, updated_at_ms) VALUES (1, 'rd', ?, ?, ?, ?, ?, ?, 0)",
            [("begin", "opening", 0, "system", 0, 100_000), ("next", "origin", 1, "agent", 20_000, 60_000)],
        )
        conn.executemany(
            "INSERT INTO llm_requests(request_id, timestamp_ms, profile_name) VALUES (?, ?, 'planner')",
            [
                (
                    f"r{i}",
                    10_000 + i * 1_000,
                )
                for i in range(5)
            ],
        )
        conn.commit()
        session_id, rows, transitions, planner_times = load_session(conn, None)
        conn.close()

    report = build_report(session_id, rows, transitions, planner_times)
    checks = {
        "取到最近一场": report.session_id == 1,
        "四句台词": report.speech_count == 4,
        "钩子率 1/2": report.hook_ratio == 0.5,
        "复读一对": report.repeat_pairs == 1,
        "最长沉默 52 秒": report.longest_silence_s == 52.0,
        "空转一次": report.idle_spin_gaps == 1,
        "开场被提前切穿": report.early_exit_segments == ["opening"],
    }
    failed = [name for name, ok in checks.items() if not ok]
    print(render_table(report))
    if failed:
        print(f"自检失败：{', '.join(failed)}\n{json.dumps(asdict(report), ensure_ascii=False)}")
        return 1
    print("自检通过")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="杂谈体检：成对输出正指标与副作用指标")
    parser.add_argument("--db", type=Path, help="Amaidesu SQLite 库路径")
    parser.add_argument("--session", type=int, help="场次 id（不给取最近一场）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    parser.add_argument("--selftest", action="store_true", help="用临时库核对口径")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.db is None:
        parser.error("需要 --db（或用 --selftest 自检）")
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        session_id, rows, transitions, planner_times = load_session(conn, args.session)
    finally:
        conn.close()
    report = build_report(session_id, rows, transitions, planner_times)
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2) if args.json else render_table(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
