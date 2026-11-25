#!/usr/bin/env python
"""场次素材导出：按场次号把一场直播的运行记录整理成复盘要用的标准文件。

复盘开工时先跑一次，后面的时间线、审计派工、固定指标都从导出目录取数，各场不再各自手写一次性脚本。

    uv run python .agents/skills/livestream-review/scripts/session_export.py --session 78

场次边界取场次记录表（live_sessions）；事件取窗口内各小时文件里带该场次号的事件，跨零点的场次不受影响。
已知事件类型的载荷缺字段时直接报错并附样本行，不静默返回空。只依赖标准库。

产出目录（默认 .omo/livestream-analysis/tmp/s<场次号>）：

- summary.md             数据范围、运行锚点线索、固定指标、基础设施表、覆盖缺口
- timeline.txt           合并时间线素材（发言、解说、目标终态、里程碑、游戏公屏、身体事件、房间消息）
- speech_commentary.txt  发言与解说逐条对照，发言带出声时刻
- goals.txt              目标清单：受理与终态时刻、结果摘要、终态后有没有紧接的解说
- alignment.txt          言行对齐表：已出口的解说与它之前提交的目标，四个时刻并排，只列要核对的两类
- chat_all.txt           游戏公屏：收到的消息、自发消息、玩家的肢体互动
- milestones.txt         里程碑与需要注意的游戏事件
- body_events.txt        身体事件与挂起唤醒
- room.txt               直播间房间消息（进房、弹幕、礼物、超级留言、舰长）
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import re
import sqlite3
import statistics
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 场次结束后仍计入的时长：收播话通常落在结束事件之后几秒
DEFAULT_PAD_MS = 60_000
# 负面事件之后这么久之内有发言，记为"有后续发言"；说的是不是这件事由复盘判断
NEGATIVE_FOLLOWUP_MS = 30_000
# 逐字重复：与之前多少条发言比（字符重合率的算法与历史统计同口径）
REPETITION_WINDOW = 10
REPETITION_LINES = (0.3, 0.5)
# 目标从受理到终态的时延，按这两条线分别统计占比
SHORT_GOAL_LINES_MS = (1_000, 3_000)
# 静默分段统计的门槛
SILENCE_LINES_MS = (60_000, 120_000, 180_000, 300_000)
# 解说事件与它的提交回执、发言与解说的时刻配对允许的最大偏差
PAIR_TOLERANCE_MS = 5_000
# 同一玩家短时间内的多条系统播报合并成一起（对端会把一次死亡播成两行）
BROADCAST_MERGE_MS = 2_000
# 达不到这个时长的已结束场次按测试场处理，不进覆盖缺口
MIN_LIVE_MS = 5 * 60_000
# 往回找进程启动的天数
STARTUP_LOOKBACK_DAYS = 3

SPEECH_EVENT = "streamer.speech"
COMMENTARY_EVENT = "agent.lead.commentary"
COMMENTARY_RESULT_EVENT = "tool.result.framework_commentary"
GOAL_SUBMIT_RESULT_EVENT = "tool.result.maicraft_execute"
TASK_EVENT = "task.changed"
MILESTONE_EVENTS = ("game.milestone", "game.attention_required", "game.place.noted")
SUSPEND_EVENT = "game.suspend.no_progress"
TTS_EVENT_SUFFIXES = ("started", "playback_started", "finished", "failed", "dropped")
NEGATIVE_EVENT_NAMES = ("game.body.attacked", "game.body.died", "game.body.gear_broken", "game.body.need_unhandled")
# 游戏公屏里进退、进度、传送类的系统播报不是负面事件；其余的系统播报按他人状态播报计
BENIGN_BROADCAST_PATTERN = re.compile(
    r"加入了游戏|退出了游戏|取得了进度|完成了挑战|达成了目标|已设置重生点|传送请求|点击下列选项|请求已发送"
)
# 进程启动后说明运行配置的应用日志行；只是线索，定性仍由复盘写
ANCHOR_LOG_PATTERN = re.compile(r"\[tts\]|LLMManager 初始化完成|Collector '")
APP_START_MESSAGE = "启动 Amaidesu"
MCP_ERROR_PATTERN = re.compile(r"MCP 业务错误:\s*(\{.*\})\s*$", re.S)
CONNECT_EVENT_PATTERN = re.compile(r"connect|reconnect|disconnect", re.I)
# 台词里的英文词：多为玩家账号名、物品或工具标识
ASCII_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_:.]{2,}")
# 数值归一与出处引注剥除，口径与历史重合率统计一致
NUMBER_PATTERN = re.compile(r"[0-9０-９]+(?:[.．][0-9０-９]+)*|[零一二两三四五六七八九十百千万亿]+")
CITATION_PAREN_PATTERN = re.compile(r"（[^（）]*）|\([^()]*\)")
CITATION_MARKERS = ("见", "记录", "确认", "回顾", "参考", "来自", "出处")
CITATION_ASCII = re.compile(r"[A-Za-z0-9_]")
ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z_]+")
COVERED_PATTERN = re.compile(r"场次(\d+)(?:-(\d+))?")


class ExportError(RuntimeError):
    """数据形状与预期不符：带着样本报错，不静默跳过。"""


@dataclass
class Speech:
    utterance_id: str
    ts: int
    text: str
    emotion: str
    source: str
    reply_to: str
    playback_ms: int | None = None
    finished_ms: int | None = None
    failed: bool = False
    dropped: bool = False
    commentary: Commentary | None = None

    @property
    def heard_ms(self) -> int:
        """观众实际听到的时刻；没有出声记录时退回发言时刻。"""
        return self.playback_ms if self.playback_ms is not None else self.ts

    @property
    def heard_text(self) -> str:
        if self.playback_ms is None:
            return "未出声"
        state = "【合成失败】" if self.failed else ("【丢弃】" if self.dropped else "")
        return f"出声 {_hhms(self.playback_ms)}{state}"


@dataclass
class Goal:
    goal_id: int
    ability: str
    purpose: str
    submitted_ms: int | None = None
    round_id: str = ""
    accepted_ms: int | None = None
    final_ms: int | None = None
    status: str = ""
    summary: str = ""

    @property
    def final_text(self) -> str:
        if self.final_ms is None:
            return "本场内未到终态"
        return f"{_hhms(self.final_ms)} {self.status}"


@dataclass
class Commentary:
    ts: int
    summary: str
    mood: str
    about_player: str
    correction: bool
    reason: str
    round_id: str
    speech: Speech | None = None
    # 同一轮次里，这条解说之前提交的目标（解说可能是在说它们）与之后提交的目标（解说不可能依赖其结果）
    earlier_goals: list[Goal] = field(default_factory=list)
    later_goals: list[Goal] = field(default_factory=list)


@dataclass
class Session:
    session_id: int
    title: str
    kind: str
    start_ms: int
    end_ms: int | None
    settled: bool


@dataclass
class Bundle:
    """按类型分好组的本场事件。"""

    events: list[dict[str, Any]] = field(default_factory=list)
    speeches: list[dict[str, Any]] = field(default_factory=list)
    commentaries: list[dict[str, Any]] = field(default_factory=list)
    commentary_results: list[dict[str, Any]] = field(default_factory=list)
    goal_submit_results: list[dict[str, Any]] = field(default_factory=list)
    task_events: list[dict[str, Any]] = field(default_factory=list)
    tts: dict[str, dict[str, int]] = field(default_factory=dict)
    chats: list[dict[str, Any]] = field(default_factory=list)
    self_chats: list[dict[str, Any]] = field(default_factory=list)
    interacts: list[dict[str, Any]] = field(default_factory=list)
    milestones: list[dict[str, Any]] = field(default_factory=list)
    body_events: list[dict[str, Any]] = field(default_factory=list)
    suspends: list[dict[str, Any]] = field(default_factory=list)
    room_messages: list[dict[str, Any]] = field(default_factory=list)
    lifecycle: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


def _hhms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _short(ms: int) -> str:
    """毫秒时长写成秒；超过一分钟写成"X 分 Y 秒"。"""
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} 分 {rest} 秒"


def _one_line(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """最近秩分位数：取排序后位于 fraction 位置的值；空列表返回 0。"""
    if not sorted_values:
        return 0.0
    return sorted_values[min(int(len(sorted_values) * fraction), len(sorted_values) - 1)]


def _field(data: dict[str, Any], key: str, event_name: str) -> Any:
    if key not in data:
        sample = json.dumps(data, ensure_ascii=False)[:300]
        raise ExportError(f"{event_name} 的载荷缺少字段 {key!r}；样本载荷：{sample}")
    return data[key]


def _row_time(row: dict[str, Any]) -> int:
    ts = row.get("timestamp_ms")
    if not isinstance(ts, int):
        raise ExportError(f"事件行缺少整数 timestamp_ms：{json.dumps(row, ensure_ascii=False)[:200]}")
    return ts


def _data(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data")
    return dict(data) if isinstance(data, dict) else {}


def _read_jsonl(path: Path, needle: str | None = None) -> tuple[list[dict[str, Any]], int]:
    """读 JSONL，返回行与坏行数；坏行跳过（复盘对象是事后日志，容忍个别残行）。

    给了 needle 时只解析含这个片段的行，用来在整天日志里只取某一种事件。
    """
    rows: list[dict[str, Any]] = []
    bad = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if needle is not None and needle not in line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return rows, bad


def _hourly_files(root: Path, first_ms: int, last_ms: int) -> tuple[list[Path], list[str]]:
    """窗口覆盖的各小时对应的按小时切分的日志文件；没有文件的小时记进返回的第二项。

    文件名是补零的两位小时（00.jsonl 到 23.jsonl）。
    """
    hour = datetime.fromtimestamp(first_ms / 1000).replace(minute=0, second=0, microsecond=0)
    last = datetime.fromtimestamp(last_ms / 1000)
    files: list[Path] = []
    missing: list[str] = []
    while hour <= last:
        path = root / hour.strftime("%Y-%m-%d") / f"{hour:%H}.jsonl"
        if path.exists():
            files.append(path)
        else:
            missing.append(hour.strftime("%Y-%m-%d %H时"))
        hour += timedelta(hours=1)
    return files, missing


def _event_files(events_dir: Path, first_ms: int, last_ms: int) -> tuple[list[Path], list[str]]:
    """窗口覆盖的各小时的事件文件；缺文件的时段记进返回的第二项，提醒确认日志没被清理。"""
    return _hourly_files(events_dir, first_ms, last_ms)


def _load_session(db_path: Path, session_id: int) -> Session:
    if not db_path.exists():
        raise ExportError(f"场次记录库不存在：{db_path}")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id, title, kind, started_at_ms, ended_at_ms, settled_at_ms FROM live_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ExportError(f"场次记录表里没有 id={session_id} 这一场")
    if row[3] is None:
        raise ExportError(f"场次 {session_id} 没有开播时刻，无法圈定范围")
    return Session(int(row[0]), str(row[1] or ""), str(row[2] or ""), int(row[3]), row[4], row[5] is not None)


def _find_startups(events_dir: Path, start_ms: int) -> list[tuple[int, str]]:
    """开播之前的全部进程启动（时刻、摘要），按时刻升序；最多往回找三天。"""
    found: list[tuple[int, str]] = []
    day = datetime.fromtimestamp(start_ms / 1000).date()
    for _ in range(STARTUP_LOOKBACK_DAYS + 1):
        folder = events_dir / day.isoformat()
        if folder.is_dir():
            for path in sorted(folder.glob("*.jsonl")):
                rows, _ = _read_jsonl(path, needle='"core.startup"')
                for row in rows:
                    ts = _row_time(row)
                    if row.get("event_name") == "core.startup" and ts <= start_ms:
                        found.append((ts, str(row.get("summary") or "")))
        day -= timedelta(days=1)
    return sorted(found)


def _in_scope(row: dict[str, Any], session_id: int, start_ms: int, end_ms: int) -> bool:
    """先看时刻：落在窗口里的事件才算本场。

    残留场次会在之后的进程里继续产生带旧场次号的事件，所以带场次号也要落在窗口里；
    不带场次号的（进程级事件）只看时刻。
    """
    if not start_ms - PAIR_TOLERANCE_MS <= _row_time(row) <= end_ms:
        return False
    tagged = _data(row).get("live_session_id")
    return tagged == session_id if tagged is not None else True


def _collect(events: list[dict[str, Any]]) -> Bundle:
    bundle = Bundle(events=events)
    for row in events:
        name = str(row.get("event_name") or "")
        data = _data(row)
        if name == SPEECH_EVENT:
            bundle.speeches.append(row)
        elif name == COMMENTARY_EVENT:
            bundle.commentaries.append(row)
        elif name == COMMENTARY_RESULT_EVENT:
            bundle.commentary_results.append(row)
        elif name == GOAL_SUBMIT_RESULT_EVENT:
            bundle.goal_submit_results.append(row)
        elif name == TASK_EVENT:
            bundle.task_events.append(row)
        elif name == "game.chat.received":
            bundle.chats.append(row)
        elif name == "game.self.chatted":
            bundle.self_chats.append(row)
        elif name == "game.player.interacted":
            bundle.interacts.append(row)
        elif name in MILESTONE_EVENTS:
            bundle.milestones.append(row)
        elif name == SUSPEND_EVENT:
            bundle.suspends.append(row)
        elif name.startswith("game.body."):
            bundle.body_events.append(row)
        elif name.startswith("room.message."):
            bundle.room_messages.append(row)
        elif name in (
            "core.startup",
            "core.shutdown",
            "live.started",
            "live.ended",
            "agent.lead.changed",
            "rundown.changed",
        ):
            bundle.lifecycle.append(row)
        if name.startswith("tool.result."):
            bundle.tool_results.append(row)
        if name.startswith("tts.utterance."):
            suffix = name.removeprefix("tts.utterance.")
            if suffix in TTS_EVENT_SUFFIXES:
                uid = str(_field(data, "utterance_id", name))
                bundle.tts.setdefault(uid, {})[suffix] = _row_time(row)
    return bundle


def _build_goals(
    bundle: Bundle,
) -> tuple[dict[int, Goal], collections.Counter[tuple[str, str]], collections.Counter[str]]:
    """目标清单来自两处：提交回执给出提交时刻与所在轮次，task.changed 给出受理与终态。"""
    goals: dict[int, Goal] = {}
    for row in bundle.goal_submit_results:
        data = _data(row)
        status = str(data.get("status") or "")
        result = data.get("result")
        if status == "success" and not isinstance(result, dict):
            sample = json.dumps(data, ensure_ascii=False)[:300]
            raise ExportError(f"{GOAL_SUBMIT_RESULT_EVENT} 成功回执的 result 不是对象；样本载荷：{sample}")
        inner = result.get("data") if isinstance(result, dict) else None
        goal_id = inner.get("goal_id") if isinstance(inner, dict) else None
        if not isinstance(goal_id, int):
            continue
        arguments = data.get("arguments")
        spec = arguments.get("goal") if isinstance(arguments, dict) else None
        spec = spec if isinstance(spec, dict) else {}
        goals[goal_id] = Goal(
            goal_id=goal_id,
            ability=str(inner.get("ability") or ""),
            purpose=str(spec.get("purpose") or ""),
            submitted_ms=_row_time(row),
            round_id=str(data.get("round_id") or ""),
        )
    other_tasks: collections.Counter[tuple[str, str]] = collections.Counter()
    unknown_status: collections.Counter[str] = collections.Counter()
    for row in sorted(bundle.task_events, key=_row_time):
        data = _data(row)
        status = str(_field(data, "status", TASK_EVENT))
        snapshot = data.get("snapshot")
        goal_id = snapshot.get("goal_id") if isinstance(snapshot, dict) else None
        if not isinstance(goal_id, int):
            other_tasks[(str(data.get("source") or ""), status)] += 1
            continue
        if goal_id not in goals:
            goals[goal_id] = Goal(goal_id, str(snapshot.get("ability") or ""), str(snapshot.get("purpose") or ""))
        goal = goals[goal_id]
        ts = _row_time(row)
        if status == "accepted":
            goal.accepted_ms = ts
        elif status in ("succeeded", "failed", "cancelled") and goal.final_ms is None:
            goal.final_ms = ts
            goal.status = status
            goal.summary = str(data.get("summary") or "")
        elif status not in ("succeeded", "failed", "cancelled"):
            unknown_status[status] += 1
    return goals, other_tasks, unknown_status


def _build_commentaries(bundle: Bundle, goals: dict[int, Goal]) -> tuple[list[Commentary], int]:
    """解说事件与它的提交回执按（时刻、文本）对上，取到所在的模型轮次。"""
    results: dict[tuple[int, str], str] = {}
    by_summary: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    for row in bundle.commentary_results:
        data = _data(row)
        ts = _row_time(row)
        arguments = data.get("arguments")
        summary = str(arguments.get("summary") or "") if isinstance(arguments, dict) else ""
        round_id = str(data.get("round_id") or "")
        results[(ts, summary)] = round_id
        by_summary[summary].append((ts, round_id))
    commentaries: list[Commentary] = []
    unpaired = 0
    for row in bundle.commentaries:
        data = _data(row)
        ts = _row_time(row)
        summary = str(_field(data, "summary", COMMENTARY_EVENT))
        round_id = results.get((ts, summary))
        if round_id is None:
            near = [item for item in by_summary.get(summary, []) if abs(item[0] - ts) <= PAIR_TOLERANCE_MS]
            round_id = near[0][1] if near else ""
            unpaired += 0 if near else 1
        commentaries.append(
            Commentary(
                ts=ts,
                summary=summary,
                mood=str(data.get("mood") or ""),
                about_player=str(data.get("about_player") or ""),
                correction=bool(data.get("correction")),
                reason=str(_field(data, "reason", COMMENTARY_EVENT)),
                round_id=round_id,
            )
        )
    commentaries.sort(key=lambda c: c.ts)
    goals_by_round: dict[str, list[Goal]] = collections.defaultdict(list)
    for goal in goals.values():
        if goal.round_id:
            goals_by_round[goal.round_id].append(goal)
    for commentary in commentaries:
        for goal in goals_by_round.get(commentary.round_id, []):
            submitted = goal.submitted_ms if goal.submitted_ms is not None else goal.accepted_ms
            if submitted is None:
                continue
            target = commentary.earlier_goals if submitted <= commentary.ts else commentary.later_goals
            target.append(goal)
    return commentaries, unpaired


def _build_speeches(bundle: Bundle, commentaries: list[Commentary]) -> list[Speech]:
    speeches: list[Speech] = []
    for row in bundle.speeches:
        data = _data(row)
        uid = str(_field(data, "utterance_id", SPEECH_EVENT))
        text = data.get("text") or data.get("speech")
        if not isinstance(text, str):
            sample = json.dumps(data, ensure_ascii=False)[:300]
            raise ExportError(f"{SPEECH_EVENT} 的载荷没有文本；样本载荷：{sample}")
        speeches.append(
            Speech(
                utterance_id=uid,
                ts=_row_time(row),
                text=text,
                emotion=str(data.get("emotion") or ""),
                source=str(data.get("source") or ""),
                reply_to=str(data.get("reply_to_user_name") or data.get("reply_player_game") or ""),
            )
        )
    speeches.sort(key=lambda s: s.ts)
    for speech in speeches:
        marks = bundle.tts.get(speech.utterance_id, {})
        speech.playback_ms = marks.get("playback_started")
        speech.finished_ms = marks.get("finished")
        speech.failed = "failed" in marks
        speech.dropped = "dropped" in marks
    # 已出口的解说与发言按时刻一一配对
    pending = [c for c in commentaries if c.reason == "dispatched"]
    for speech in speeches:
        candidates = [c for c in pending if c.speech is None and abs(c.ts - speech.ts) <= PAIR_TOLERANCE_MS]
        if candidates:
            nearest = min(candidates, key=lambda c: abs(c.ts - speech.ts))
            nearest.speech = speech
            speech.commentary = nearest
    return speeches


def _compare_text(text: str) -> str:
    """比对语料：出处引注剥除后做数值归一。"""
    cleaned = text or ""
    for paren in CITATION_PAREN_PATTERN.findall(cleaned):
        inner = paren[1:-1]
        if any(marker in inner for marker in CITATION_MARKERS) or CITATION_ASCII.search(inner):
            cleaned = cleaned.replace(paren, " ")
    return NUMBER_PATTERN.sub("N", ASCII_TOKEN_PATTERN.sub(" ", cleaned))


def _char_bigrams(text: str) -> set[str]:
    chars = [c for c in text if not c.isspace()]
    return {"".join(chars[i : i + 2]) for i in range(len(chars) - 1)}


def _repetition(speeches: list[Speech]) -> list[float]:
    """每条发言对之前若干条发言的字符重合率（0.0~1.0）；只认得出逐字重复，换词的模板句认不出。"""
    texts = [_compare_text(s.text) for s in speeches]
    ratios: list[float] = []
    for index, text in enumerate(texts):
        candidate = _char_bigrams(text)
        known: set[str] = set()
        for previous in texts[max(0, index - REPETITION_WINDOW) : index]:
            known |= _char_bigrams(previous)
        ratios.append(len(candidate & known) / len(candidate) if candidate and known else 0.0)
    return ratios


def _negative_events(bundle: Bundle, speeches: list[Speech]) -> list[tuple[int, int | None, str]]:
    """受击、死亡、装备损坏、需求未处理，以及游戏公屏里的他人状态播报；每起配一条窗口内紧随其后的发言时刻。"""
    events: list[tuple[int, str]] = []
    for row in bundle.body_events:
        if row["event_name"] in NEGATIVE_EVENT_NAMES:
            events.append((_row_time(row), f"{row['event_name']} {_one_line(_data(row).get('message'), 80)}"))
    own_death_times = [_row_time(row) for row in bundle.body_events if row["event_name"] == "game.body.died"]
    broadcasts: list[list[Any]] = []
    for row in sorted(bundle.chats, key=_row_time):
        content = str(_data(row).get("content") or "")
        if _data(row).get("kind") == "system" and content and not BENIGN_BROADCAST_PATTERN.search(content):
            ts = _row_time(row)
            # 主播自己的死亡在游戏公屏里也有一条播报，身体事件已经记过，不重复计
            if any(abs(ts - death) <= BROADCAST_MERGE_MS for death in own_death_times):
                continue
            if broadcasts and ts - broadcasts[-1][0] <= BROADCAST_MERGE_MS:
                broadcasts[-1][1].append(content)
            else:
                broadcasts.append([ts, [content]])
    events.extend((ts, f"他人状态播报 {'／'.join(contents)}") for ts, contents in broadcasts)
    speech_times = sorted(s.ts for s in speeches)
    result: list[tuple[int, int | None, str]] = []
    for ts, label in sorted(events):
        index = bisect.bisect_left(speech_times, ts)
        after = speech_times[index] if index < len(speech_times) else None
        result.append((ts, after if after is not None and after - ts <= NEGATIVE_FOLLOWUP_MS else None, label))
    return result


def _gap_stats(speeches: list[Speech]) -> dict[str, Any]:
    """按出声时刻算相邻间隔、各档静默、最长的几段、出声时长占比。"""
    heard = sorted(s.heard_ms for s in speeches)
    gaps = sorted(b - a for a, b in zip(heard, heard[1:], strict=False))
    span = heard[-1] - heard[0] if len(heard) > 1 else 0
    spoken = sum(s.finished_ms - s.heard_ms for s in speeches if s.finished_ms is not None)
    return {
        "median": int(statistics.median(gaps)) if gaps else 0,
        "max": gaps[-1] if gaps else 0,
        "buckets": [
            (line, sum(1 for gap in gaps if gap > line), sum(gap for gap in gaps if gap > line))
            for line in SILENCE_LINES_MS
        ],
        "longest": sorted(zip(heard, heard[1:], strict=False), key=lambda pair: pair[1] - pair[0], reverse=True)[:5],
        "spoken_seconds": spoken / 1000,
        "span_seconds": span / 1000,
    }


def _error_code(data: dict[str, Any]) -> str:
    match = MCP_ERROR_PATTERN.search(str(data.get("error_message") or ""))
    if not match:
        return "非 MCP 业务错误"
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return "业务错误内容解析失败"
    error = payload.get("error") if isinstance(payload, dict) else None
    return str(error.get("code") or "未带错误码") if isinstance(error, dict) else "未带错误码"


def _write_lines(out: Path, name: str, lines: list[str]) -> None:
    (out / name).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _stamp(lines: list[tuple[int, str]]) -> list[str]:
    return [f"[{_hhms(ts)}] {line}" for ts, line in sorted(lines, key=lambda item: item[0])]


def _write_timeline(
    out: Path, bundle: Bundle, speeches: list[Speech], commentaries: list[Commentary], goals: dict[int, Goal]
) -> None:
    """时间线素材：发言、未出口的解说、目标终态、里程碑、游戏公屏、身体事件、房间消息、生命周期，按时刻合并。"""
    rows: list[tuple[int, str]] = []
    for speech in speeches:
        reply = f" 回复{speech.reply_to}" if speech.reply_to else ""
        rows.append(
            (speech.ts, f"SPEECH {speech.utterance_id} [{speech.emotion}]{reply} {speech.heard_text}｜{speech.text}")
        )
    rows.extend(
        (c.ts, f"COMMENTARY[{c.reason}] {c.summary}")
        for c in commentaries
        if not (c.reason == "dispatched" and c.speech)
    )
    rows.extend(
        (g.final_ms, f"GOAL #{g.goal_id} {g.status} {g.ability}｜{_one_line(g.summary, 160)}")
        for g in goals.values()
        if g.final_ms is not None
    )
    rows.extend(
        (
            _row_time(row),
            f"{row['event_name']}{'【值得注意】' if _data(row).get('notable') else ''} {_one_line(_data(row).get('message'), 300)}",
        )
        for row in bundle.milestones
    )
    rows.extend(
        (
            _row_time(row),
            f"CHAT[{_data(row).get('kind')}] {_data(row).get('sender')}: {_one_line(_data(row).get('content'), 300)}",
        )
        for row in bundle.chats
    )
    rows.extend((_row_time(row), f"SELF-CHAT {_one_line(_data(row).get('message'), 300)}") for row in bundle.self_chats)
    rows.extend(
        (
            _row_time(row),
            f"INTERACT {_data(row).get('player_name')} {_data(row).get('kind')} {'、'.join(_data(row).get('actions') or [])}",
        )
        for row in bundle.interacts
    )
    rows.extend(
        (_row_time(row), f"{row['event_name']} {_one_line(_data(row).get('message'), 200)}")
        for row in bundle.body_events + bundle.suspends
        if row["event_name"] != "game.body.sky_changed"
    )
    rows.extend(
        (
            _row_time(row),
            f"ROOM {_data(row).get('message_type')} {_room_name(row)} {_one_line(_data(row).get('content'), 200)}",
        )
        for row in bundle.room_messages
    )
    rows.extend((_row_time(row), f"{row['event_name']} {_lifecycle_detail(row)}") for row in bundle.lifecycle)
    _write_lines(out, "timeline.txt", _stamp(rows))


def _room_name(row: dict[str, Any]) -> str:
    user = _data(row).get("user")
    return str(user.get("name", "")) if isinstance(user, dict) else ""


def _lifecycle_detail(row: dict[str, Any]) -> str:
    data = _data(row)
    if row["event_name"] == "rundown.changed":
        return f"{data.get('action')} {data.get('segment_title')}（第 {data.get('index')} 段，共 {data.get('total')} 段，by={data.get('by')}）"
    if row["event_name"] == "agent.lead.changed":
        return f"主控 {data.get('previous_lead')} → {data.get('lead_agent')}（{data.get('reason')}）"
    return str(row.get("summary") or "")


def _write_speech_commentary(out: Path, speeches: list[Speech], commentaries: list[Commentary]) -> None:
    rows: list[tuple[int, str]] = [
        (
            s.ts,
            f"SPEECH {s.utterance_id} [{s.emotion}]（{s.source}{f' 回复{s.reply_to}' if s.reply_to else ''}）{s.heard_text}｜{s.text}",
        )
        for s in speeches
    ]
    rows.extend(
        (c.ts, f"COMMENTARY[{c.reason}] mood={c.mood}{' 纠偏' if c.correction else ''}｜{c.summary}")
        for c in commentaries
    )
    _write_lines(out, "speech_commentary.txt", _stamp(rows))


def _write_goals(out: Path, goals: dict[int, Goal], commentaries: list[Commentary]) -> None:
    """目标清单；"终态后的下一条解说"给"失败了有没有向观众交代"提供起点，说的是不是这件事由复盘判断。"""
    followup_ms = sorted(c.ts for c in commentaries if c.speech is not None)
    lines: list[str] = []
    for goal in sorted(goals.values(), key=lambda g: (g.accepted_ms or g.submitted_ms or 0, g.goal_id)):
        submitted = _hhms(goal.submitted_ms) if goal.submitted_ms is not None else "无提交回执"
        accepted = _hhms(goal.accepted_ms) if goal.accepted_ms is not None else "无受理事件"
        final = goal.final_text
        base = goal.accepted_ms if goal.accepted_ms is not None else goal.submitted_ms
        if goal.final_ms is not None:
            if base is not None:
                final += f"（受理到终态 {_short(goal.final_ms - base)}）"
            index = bisect.bisect_left(followup_ms, goal.final_ms)
            after = followup_ms[index] if index < len(followup_ms) else None
            follow = (
                _hhms(after) if after is not None and after - goal.final_ms <= NEGATIVE_FOLLOWUP_MS else "30 秒内没有"
            )
        else:
            follow = "—"
        lines.append(
            f"#{goal.goal_id} {goal.ability}｜意图：{_one_line(goal.purpose, 120)}｜提交 {submitted}｜受理 {accepted}｜终态 {final}"
            f"｜终态后的下一条解说 {follow}｜结果：{_one_line(goal.summary, 200)}"
        )
    _write_lines(out, "goals.txt", lines)


def _write_alignment(out: Path, commentaries: list[Commentary], goals: dict[int, Goal]) -> dict[str, int]:
    """言行对齐表：已出口的解说与同一轮次里它之前提交的目标，四个时刻并排。

    只列两类要核对的：目标已以失败或取消结束、解说紧随其后（看解说有没有把它说成在进行或成功）；
    解说出口时目标还没有结果（看解说有没有提前下判断）。成功之后的解说只计数。
    """
    stats = {
        "rounds": 0,
        "rounds_with_goal": 0,
        "silent_rounds": 0,
        "spoken_before_goal": 0,
        "failed": 0,
        "pending": 0,
        "succeeded": 0,
    }
    commentary_rounds = {c.round_id for c in commentaries if c.round_id}
    goal_rounds = {g.round_id for g in goals.values() if g.round_id}
    stats["rounds"] = len(commentary_rounds)
    stats["rounds_with_goal"] = len(commentary_rounds & goal_rounds)
    stats["silent_rounds"] = len(goal_rounds - commentary_rounds)
    blocks: list[tuple[int, str]] = []
    for commentary in commentaries:
        speech = commentary.speech
        if speech is None or not commentary.earlier_goals:
            continue
        stats["spoken_before_goal"] += 1
        head = f"轮次 {commentary.round_id or '（未知）'}｜解说 {_hhms(commentary.ts)}：{_one_line(commentary.summary, 200)}"
        head += f"｜发言 {_hhms(speech.ts)} → {speech.heard_text}"
        lines: list[str] = []
        for goal in commentary.earlier_goals:
            note = ""
            if goal.final_ms is None or goal.final_ms > commentary.ts:
                stats["pending"] += 1
                later = (
                    "本场内未到终态"
                    if goal.final_ms is None
                    else f"{_short(goal.final_ms - commentary.ts)}后才以 {goal.status} 结束"
                )
                note = f"解说出口时目标还没有结果（{later}）——核对解说有没有提前下判断"
            elif goal.status in ("failed", "cancelled"):
                stats["failed"] += 1
                note = f"目标在解说出口前 {_short(commentary.ts - goal.final_ms)}已以 {goal.status} 结束"
                if speech.playback_ms is not None:
                    note += f"，观众听到时已结束 {_short(speech.heard_ms - goal.final_ms)}"
                note += "——核对解说有没有把它说成在进行或成功，失败有没有向观众交代"
            else:
                stats["succeeded"] += 1
            line = (
                f"  目标 #{goal.goal_id} {goal.ability}｜提交 {_hhms(goal.submitted_ms) if goal.submitted_ms is not None else '—'}"
                f" → {goal.final_text}｜意图：{_one_line(goal.purpose, 100)}｜结果：{_one_line(goal.summary, 120)}"
            )
            lines.append(line + (f"\n  核对：{note}" if note else ""))
        if commentary.later_goals:
            lines.append(
                f"  解说之后才提交的目标 {'、'.join(f'#{g.goal_id}' for g in commentary.later_goals)}（解说不可能依赖它们的结果）"
            )
        if any("核对：" in line for line in lines):
            blocks.append((commentary.ts, "\n".join([head, *lines])))
    header = [
        "言行对齐表：已出口的解说，与同一模型轮次里它之前提交的目标。",
        "每个目标一行：提交时刻、终态时刻与状态。带“核对”的行是两类要人工看原文的情形；其余成功之后的解说只计数（见 summary.md）。",
        "时刻先后只说明框架层的顺序；模型写解说时有没有看到终态回执，要回请求全文核对。",
        "",
    ]
    _write_lines(out, "alignment.txt", header + [block for _, block in sorted(blocks, key=lambda item: item[0])])
    return stats


def _write_simple_files(out: Path, bundle: Bundle) -> None:
    rows: list[tuple[int, str]] = [
        (
            _row_time(row),
            f"RECEIVED[{_data(row).get('kind')}] {_data(row).get('sender')}: {_one_line(_data(row).get('content'), 400)}",
        )
        for row in bundle.chats
    ]
    rows.extend((_row_time(row), f"SELF-CHAT {_one_line(_data(row).get('message'), 400)}") for row in bundle.self_chats)
    rows.extend(
        (
            _row_time(row),
            f"INTERACT {_data(row).get('player_name')} {_data(row).get('kind')} {'、'.join(_data(row).get('actions') or [])}"
            f" {json.dumps(_data(row).get('fact'), ensure_ascii=False) if _data(row).get('fact') else ''}",
        )
        for row in bundle.interacts
    )
    _write_lines(out, "chat_all.txt", _stamp(rows))

    _write_lines(
        out,
        "milestones.txt",
        _stamp(
            (
                _row_time(row),
                f"{row['event_name']}{'【值得注意】' if _data(row).get('notable') else ''} {_one_line(_data(row).get('message'), 400)}",
            )
            for row in bundle.milestones
        ),
    )
    _write_lines(
        out,
        "body_events.txt",
        _stamp(
            (_row_time(row), f"{row['event_name']} {_one_line(_data(row).get('message'), 300)}")
            for row in bundle.body_events + bundle.suspends
        ),
    )
    _write_lines(
        out,
        "room.txt",
        _stamp(
            (
                _row_time(row),
                f"{_data(row).get('message_type')} {_room_name(row)}{'（模拟）' if _data(row).get('simulated') else ''} "
                f"{_one_line(_data(row).get('content'), 300)}",
            )
            for row in bundle.room_messages
        ),
    )


def _read_app_log(app_dir: Path, first_ms: int, last_ms: int) -> list[tuple[int, str, str, str]]:
    """应用日志行：时刻、级别、模块、消息。只读窗口覆盖的日期。"""
    rows: list[tuple[int, str, str, str]] = []
    day = datetime.fromtimestamp(first_ms / 1000).date()
    last_day = datetime.fromtimestamp(last_ms / 1000).date()
    while day <= last_day:
        path = app_dir / f"amaidesu_{day.isoformat()}.jsonl"
        if path.exists():
            loaded, _ = _read_jsonl(path)
            for raw in loaded:
                try:
                    ts = int(datetime.fromisoformat(str(raw.get("timestamp"))).timestamp() * 1000)
                except ValueError:
                    continue
                rows.append(
                    (ts, str(raw.get("level") or ""), str(raw.get("module") or ""), str(raw.get("message") or ""))
                )
        day += timedelta(days=1)
    return rows


def _grouped_warnings(
    app_rows: list[tuple[int, str, str, str]], start_ms: int, end_ms: int
) -> list[tuple[tuple[str, str, str], int]]:
    """窗口内的 WARNING/ERROR 按（级别、模块、数字归一后的消息）归并计数，多的在前。"""
    counts: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for ts, level, module, message in app_rows:
        if start_ms <= ts <= end_ms and level in ("WARNING", "ERROR"):
            counts[(level, module, NUMBER_PATTERN.sub("N", message))] += 1
    return counts.most_common(15)


def _git_commit_before(repo_root: Path, ms: int) -> str:
    """进程启动时刻之前最近的一笔提交；历史被改写后提交时刻会变，只是线索。"""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%h %s", f"--before={_iso(ms)}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"无法取得（{_one_line(exc, 120)}）"
    if done.returncode != 0:
        return f"无法取得（git 返回 {done.returncode}）"
    return done.stdout.strip() or "无"


def _uncovered_sessions(db_path: Path, reviews_dir: Path, session_id: int) -> list[tuple[int, str, int]]:
    """已结束、达到正场时长、复盘目录的文件名里却找不到的场次。"""
    covered: set[int] = set()
    if reviews_dir.is_dir():
        for path in reviews_dir.glob("*.md"):
            for match in COVERED_PATTERN.finditer(path.name):
                first = int(match.group(1))
                covered.update(range(first, (int(match.group(2)) if match.group(2) else first) + 1))
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, started_at_ms, ended_at_ms - started_at_ms FROM live_sessions "
            "WHERE ended_at_ms IS NOT NULL AND kind = 'live' AND ended_at_ms - started_at_ms >= ?",
            (MIN_LIVE_MS,),
        ).fetchall()
    finally:
        conn.close()
    return [(int(row[0]), _iso(row[1]), int(row[2])) for row in rows if row[0] not in covered and row[0] != session_id]


def _summary_scope(session: Session, bundle: Bundle, context: dict[str, Any]) -> list[str]:
    end_text = _iso(session.end_ms) if session.end_ms is not None else "尚未结束（中间态，结论要等最终状态）"
    missing = f"；窗口内没有事件文件的时段：{'、'.join(context['missing_hours'])}" if context["missing_hours"] else ""
    return [
        "# 场次 " + str(session.session_id) + " 导出摘要",
        "",
        "## 数据范围",
        "",
        f"- 场次：{session.title}（{session.kind}），{_iso(session.start_ms)} → {end_text}"
        f"{'，已结算' if session.settled else '，未结算'}",
        f"- 导出时刻：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 事件文件 {len(context['event_files'])} 个，窗口内 {len(bundle.events)} 行，无法解析 {context['bad_lines']} 行{missing}",
        f"- 结束后仍计入 {_short(context['pad'])}（收播话通常落在结束事件之后）",
        "",
    ]


def _summary_anchors(session: Session, bundle: Bundle, context: dict[str, Any]) -> list[str]:
    lines = ["## 运行锚点（线索，定性由复盘写）", ""]
    startups = context["startups"]
    if startups:
        recent = [ts for ts, _summary in startups if ts >= session.start_ms - 24 * 60 * 60 * 1000]
        lines.append(
            f"- 开播前 {STARTUP_LOOKBACK_DAYS} 天内共 {len(startups)} 次进程启动，其中开播前 24 小时内的："
            f"{'、'.join(_iso(ts) for ts in recent) or '无'}"
        )
        latest_ms = startups[-1][0]
        lines.append(
            f"- 跑这一场的进程启动于 {_iso(latest_ms)}（{startups[-1][1]}），距开播 {_short(session.start_ms - latest_ms)}"
        )
        lines.append(f"- 启动前最近一笔提交：{context['git_commit']}（提交时刻会随历史改写而变）")
        for ts, module, message in context["anchor_log"]:
            lines.append(f"- {_hhms(ts)} {module}：{_one_line(message, 160)}")
    else:
        lines.append(f"- 开播前 {STARTUP_LOOKBACK_DAYS} 天内的事件日志里没有 core.startup，运行版本与配置要另行锚定")
    for row in sorted(bundle.lifecycle, key=_row_time):
        lines.append(f"- {_hhms(_row_time(row))} {row['event_name']}：{_one_line(_lifecycle_detail(row), 160)}")
    if context["unpaired_commentaries"]:
        lines.append(f"- 有 {context['unpaired_commentaries']} 条解说事件找不到对应的提交回执，所在轮次记为未知")
    return [*lines, ""]


def _summary_metrics(
    bundle: Bundle,
    speeches: list[Speech],
    commentaries: list[Commentary],
    goals: dict[int, Goal],
    context: dict[str, Any],
) -> list[str]:
    lines = ["## 固定指标（只量不判）", ""]
    reasons = collections.Counter(c.reason for c in commentaries)
    sources = collections.Counter(s.source for s in speeches)
    emotions = collections.Counter(s.emotion for s in speeches)
    lines.append(
        f"- 发言 {len(speeches)} 条（按来源 {dict(sources.most_common())}）；情绪分布 {dict(emotions.most_common())}"
    )
    lines.append(
        f"- 解说 {len(commentaries)} 条：已出口 {reasons.get('dispatched', 0)}、被节流仅记录 {reasons.get('throttled_recorded', 0)}、"
        f"同轮去重 {reasons.get('same_round_deduped', 0)}；纠偏 {sum(1 for c in commentaries if c.correction)} 条"
    )
    gaps = _gap_stats(speeches)
    buckets = "；".join(
        f"超过 {_short(line)} {count} 段（合计 {_short(total)}）" for line, count, total in gaps["buckets"]
    )
    lines.append(f"- 相邻出声间隔：中位 {_short(gaps['median'])}，最大 {_short(gaps['max'])}；{buckets}")
    if gaps["longest"]:
        lines.append(
            "- 最长的几段静默：" + "；".join(f"{_hhms(a)} → {_hhms(b)}（{_short(b - a)}）" for a, b in gaps["longest"])
        )
    if gaps["span_seconds"]:
        share = gaps["spoken_seconds"] / gaps["span_seconds"]
        lines.append(
            f"- 出声时长合计 {gaps['spoken_seconds']:.0f} 秒，占首末出声跨度 {gaps['span_seconds']:.0f} 秒的 {share:.0%}"
        )
    latencies = sorted(s.playback_ms - s.ts for s in speeches if s.playback_ms is not None)
    unheard = sum(1 for s in speeches if s.playback_ms is None)
    lines.append(
        f"- 发言到出声：中位 {_short(int(_percentile(latencies, 0.5)))}，p90 {_short(int(_percentile(latencies, 0.9)))}，"
        f"最大 {_short(int(latencies[-1])) if latencies else '—'}；没有出声记录的发言 {unheard} 条"
    )

    statuses = collections.Counter(g.status for g in goals.values())
    lines.append(
        f"- 目标 {len(goals)} 个：成功 {statuses.get('succeeded', 0)}、失败 {statuses.get('failed', 0)}、"
        f"取消 {statuses.get('cancelled', 0)}、本场内未到终态 {sum(1 for g in goals.values() if g.final_ms is None)}"
    )
    durations = sorted(
        g.final_ms - g.accepted_ms for g in goals.values() if g.final_ms is not None and g.accepted_ms is not None
    )
    if durations:
        shares = "、".join(
            f"{_short(line)} 内 {sum(1 for value in durations if value <= line) / len(durations):.0%}"
            for line in SHORT_GOAL_LINES_MS
        )
        lines.append(f"- 目标受理到终态：中位 {_short(int(statistics.median(durations)))}；{shares}")
    if context["other_tasks"]:
        lines.append(f"- 不带目标编号的其他任务事件（来源、状态）：{dict(context['other_tasks'])}")
    if context["unknown_status"]:
        lines.append(
            f"- 【注意】task.changed 出现未知的 status：{dict(context['unknown_status'])}，目标终态统计可能不全"
        )

    alignment = context["alignment"]
    rounds = alignment["rounds"]
    if not rounds:
        lines.append(
            "- 本场没有解说事件，发言不是由游戏 Agent 的解说驱动：言行对齐两类指标不适用，发言与行动的对应从时间线核对"
        )
    else:
        round_share = f"（{alignment['rounds_with_goal'] / rounds:.0%}）"
        lines.append(
            f"- 含解说的模型轮次 {rounds} 个，其中同一轮还提交了新目标 {alignment['rounds_with_goal']} 个{round_share}"
        )
        lines.append(
            f"- 只提交目标、整轮没有任何解说的轮次 {alignment['silent_rounds']} 个（该说的没说，从 goals.txt 里找重要的目标）"
        )
        lines.append(
            f"- 已出口的解说里，之前同轮已提交目标的有 {alignment['spoken_before_goal']} 条：目标已成功 {alignment['succeeded']} 个；"
            f"目标已失败或取消 {alignment['failed']} 个；解说出口时目标还没有结果 {alignment['pending']} 个。"
            f"后两类共 {alignment['failed'] + alignment['pending']} 个列在 alignment.txt，逐条核对"
        )

    negative = context["negative_events"]
    if negative:
        followed = sum(1 for _ts, after, _label in negative if after is not None)
        lines.append(
            f"- 负面事件 {len(negative)} 起，{_short(NEGATIVE_FOLLOWUP_MS)} 内有发言的 {followed} 起（说的是不是这件事由复盘判断）"
        )
        lines.extend(
            f"  - {_hhms(ts)} {label} → {_hhms(after) if after is not None else '窗口内没有发言'}"
            for ts, after, label in negative
        )

    ratios = _repetition(speeches)
    if ratios:
        median = statistics.median(ratios)
        counts = "、".join(f"≥{line:.0%} {sum(1 for value in ratios if value >= line)} 条" for line in REPETITION_LINES)
        lines.append(
            f"- 逐字重复：与之前 {REPETITION_WINDOW} 条发言的字符重合率中位 {median:.0%}；{counts}"
            "（换词的模板句测不出来，按判据表人工判）"
        )
    words = context["ascii_words"]
    lines.append(
        f"- 台词里的英文词（多为玩家账号名、物品或工具标识）共 {sum(words.values())} 处："
        + ("、".join(f"{word} ×{count}" for word, count in words.most_common(6)) or "无")
    )
    return [*lines, ""]


def _summary_infra(bundle: Bundle, context: dict[str, Any]) -> list[str]:
    lines = ["## 基础设施表", ""]
    marks: collections.Counter[str] = collections.Counter()
    unpaired = 0
    for mark in bundle.tts.values():
        if "started" not in mark and "playback_started" not in mark:
            unpaired += 1
        marks.update(mark.keys())
    lines.append(
        f"- 语音合成：started {marks['started']}、出声 {marks['playback_started']}、结束 {marks['finished']}、"
        f"失败 {marks['failed']}、丢弃 {marks['dropped']}；对不上的编号 {unpaired} 个"
    )
    lines.append(
        f"- 挂起唤醒 {len(bundle.suspends)} 次；身体事件 {len(bundle.body_events)} 条（明细见 body_events.txt）"
    )
    by_tool: collections.Counter[tuple[str, str]] = collections.Counter()
    by_code: collections.Counter[tuple[str, str]] = collections.Counter()
    for row in bundle.tool_results:
        data = _data(row)
        tool = str(data.get("tool_name") or row["event_name"].removeprefix("tool.result."))
        by_tool[(tool, str(data.get("status") or ""))] += 1
        if data.get("status") == "error":
            by_code[(tool, _error_code(data))] += 1
    lines.append(
        "- 工具回执：" + "；".join(f"{tool} {status} {count}" for (tool, status), count in sorted(by_tool.items()))
    )
    codes = "；".join(f"{tool} {code} {count}" for (tool, code), count in by_code.most_common())
    lines.append("- 工具回执的错误码：" + (codes or "无"))
    connects: collections.Counter[str] = collections.Counter(
        str(row.get("event_name"))
        for row in bundle.events
        if CONNECT_EVENT_PATTERN.search(str(row.get("event_name") or ""))
    )
    lines.append("- 事件日志里的连接类事件：" + (str(dict(connects)) if connects else "无（连接告警在应用日志，见下）"))
    warnings = context["warnings"]
    total = sum(count for _, count in warnings)
    lines.append(f"- 应用日志告警归并（列出的 {len(warnings)} 种共 {total} 条，数字已归一）：")
    lines.extend(
        f"  - {count} 条 {level} {module}：{_one_line(message, 110)}" for (level, module, message), count in warnings
    )
    return [*lines, ""]


def _write_summary(
    out: Path,
    session: Session,
    bundle: Bundle,
    speeches: list[Speech],
    commentaries: list[Commentary],
    goals: dict[int, Goal],
    context: dict[str, Any],
) -> None:
    uncovered = context["uncovered"]
    lines = [
        *_summary_scope(session, bundle, context),
        *_summary_anchors(session, bundle, context),
        *_summary_metrics(bundle, speeches, commentaries, goals, context),
        *_summary_infra(bundle, context),
        "## 覆盖缺口（只提示，不主动扩工）",
        "",
    ]
    if uncovered:
        lines.extend(
            f"- 场次 {sid}（{started} 开播，{_short(duration)}）已结束，复盘目录的文件名里找不到对应文档"
            for sid, started, duration in uncovered
        )
        lines.append("  （合篇或只在索引备注里登记过的测试场不算缺口，核对目录里的 README）")
    else:
        lines.append("- 已结束的正场都有复盘文档")
    window_end = (session.end_ms if session.end_ms is not None else session.start_ms) + int(context["pad"])
    since = datetime.fromtimestamp(session.start_ms / 1000).strftime("%Y-%m-%dT%H:%M:%S")
    until = datetime.fromtimestamp(window_end / 1000).strftime("%Y-%m-%dT%H:%M:%S")
    lines += [
        "",
        "## 下一步",
        "",
        "事件分布、生命周期、模型用量与费用用 overview_stats.py，窗口用完整日期时间：",
        "",
        "```",
        "uv run python .agents/skills/livestream-review/scripts/overview_stats.py \\",
        *(f"    --events {path.as_posix()} \\" for path in context["event_files"]),
        *(f"    --llm {path.as_posix()} \\" for path in context["llm_files"]),
        f"    --since {since} --until {until} --config config/model.toml",
        "```",
        "",
    ]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def export(
    session_id: int,
    out: Path,
    db_path: Path,
    events_dir: Path,
    llm_dir: Path,
    app_dir: Path,
    reviews_dir: Path,
    pad: int,
    use_git: bool,
) -> None:
    session = _load_session(db_path, session_id)
    window_end = (session.end_ms if session.end_ms is not None else session.start_ms) + pad
    event_files, missing_days = _event_files(events_dir, session.start_ms, window_end)
    if not event_files:
        raise ExportError(f"窗口 {_iso(session.start_ms)}–{_iso(window_end)} 覆盖的日期在 {events_dir} 下没有事件文件")
    rows: list[dict[str, Any]] = []
    bad_lines = 0
    for path in event_files:
        loaded, bad = _read_jsonl(path)
        bad_lines += bad
        rows.extend(row for row in loaded if isinstance(row.get("timestamp_ms"), int))
    events = sorted((row for row in rows if _in_scope(row, session_id, session.start_ms, window_end)), key=_row_time)
    if not events:
        raise ExportError(f"场次 {session_id} 在窗口内没有事件——核对场次号与事件目录")

    bundle = _collect(events)
    goals, other_tasks, unknown_status = _build_goals(bundle)
    commentaries, unpaired_commentaries = _build_commentaries(bundle, goals)
    speeches = _build_speeches(bundle, commentaries)

    out.mkdir(parents=True, exist_ok=True)
    _write_timeline(out, bundle, speeches, commentaries, goals)
    _write_speech_commentary(out, speeches, commentaries)
    _write_goals(out, goals, commentaries)
    alignment = _write_alignment(out, commentaries, goals)
    _write_simple_files(out, bundle)

    startups = _find_startups(events_dir, session.start_ms)
    day_before = session.start_ms - STARTUP_LOOKBACK_DAYS * 24 * 60 * 60 * 1000
    app_rows = _read_app_log(app_dir, day_before, window_end)
    anchor_since = startups[-1][0] if startups else session.start_ms
    starts = [
        ts
        for ts, _, _, message in app_rows
        if message.startswith(APP_START_MESSAGE) and ts <= anchor_since + PAIR_TOLERANCE_MS
    ]
    anchor_log = [
        (ts, module, message)
        for ts, _, module, message in app_rows
        if (starts[-1] if starts else anchor_since - 600_000) <= ts <= session.start_ms
        and ANCHOR_LOG_PATTERN.search(message)
    ][-12:]
    repo_root = Path(__file__).resolve().parents[4]
    ascii_words: collections.Counter[str] = collections.Counter(
        match.group(0) for speech in speeches for match in ASCII_WORD_PATTERN.finditer(speech.text)
    )
    context: dict[str, Any] = {
        "pad": pad,
        "bad_lines": bad_lines,
        "event_files": event_files,
        "llm_files": _hourly_files(llm_dir, session.start_ms, window_end)[0],
        "missing_hours": missing_days,
        "startups": startups,
        "git_commit": _git_commit_before(repo_root, startups[-1][0]) if (startups and use_git) else "未取",
        "anchor_log": anchor_log,
        "unpaired_commentaries": unpaired_commentaries,
        "other_tasks": other_tasks,
        "unknown_status": unknown_status,
        "alignment": alignment,
        "negative_events": _negative_events(bundle, speeches),
        "ascii_words": ascii_words,
        "warnings": _grouped_warnings(app_rows, session.start_ms, window_end),
        "uncovered": _uncovered_sessions(db_path, reviews_dir, session_id),
    }
    _write_summary(out, session, bundle, speeches, commentaries, goals, context)
    print(
        f"场次 {session_id} 的素材已导出到 {out}（窗口内事件 {len(events)} 行，发言 {len(speeches)}，"
        f"解说 {len(commentaries)}，目标 {len(goals)}）"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按场次号导出复盘用的标准文件（时间线素材、言行对齐表、固定指标、基础设施表）"
    )
    parser.add_argument("--session", type=int, required=True, help="场次号（场次记录表 live_sessions 的 id）")
    parser.add_argument(
        "--out", type=Path, default=None, help="导出目录（默认 .omo/livestream-analysis/tmp/s<场次号>）"
    )
    parser.add_argument("--db", type=Path, default=Path("data/amaidesu.db"), help="场次记录库（只读打开）")
    parser.add_argument("--events-dir", type=Path, default=Path("logs/events"), help="事件日志根目录（按日期分子目录）")
    parser.add_argument(
        "--llm-dir", type=Path, default=Path("logs/llm_requests"), help="模型请求全文目录（只用来列出下一步要读的文件）"
    )
    parser.add_argument("--app-dir", type=Path, default=Path("logs/app"), help="应用日志目录")
    parser.add_argument(
        "--reviews-dir", type=Path, default=Path(".omo/livestream-analysis"), help="复盘文档目录（用来盘点覆盖缺口）"
    )
    parser.add_argument("--pad-seconds", type=int, default=DEFAULT_PAD_MS // 1000, help="场次结束后仍计入的秒数")
    parser.add_argument("--skip-git", action="store_true", help="不查启动前最近的一笔提交")
    args = parser.parse_args()
    out = args.out if args.out is not None else Path(f".omo/livestream-analysis/tmp/s{args.session}")
    try:
        export(
            args.session,
            out,
            args.db,
            args.events_dir,
            args.llm_dir,
            args.app_dir,
            args.reviews_dir,
            args.pad_seconds * 1000,
            not args.skip_git,
        )
    except ExportError as exc:
        raise SystemExit(f"导出失败：{exc}") from exc


if __name__ == "__main__":
    main()
