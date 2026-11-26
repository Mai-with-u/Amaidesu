"""AI 选段：基于本场完整记录提议值得剪成视频的完整事件。

取料全部来自库里已有的带时间戳数据，不做视频/音频分析——她的台词、游戏
公屏（她在游戏内聊天栏打出的字与玩家说的话）、全程均匀抽样的游戏事件、
礼物与醒目留言。这些按时间先后合并成一份带行号的记录交给 LLM；LLM 用行号
指出每件事的第一行和最后一行，代码据此定起止：有 TTS 诊断记录的台词按真实
的出声与播完时刻，其余按校准过的延迟与语速估算；起点不带着上一行的尾音，
终点让末行播完再留余量、不越进下一行观众看得到的内容，不超时长上限（超了
就保住结果、把起点往后挪到一行上），互相不重叠。用行号而不是秒数作锚点，
起止就对得上记录里真实的一行。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Set, Tuple, Union

from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.diagnostics.tts_reader import TtsDiagnosticsReader, TtsPlaybackWindow
from src.modules.logging import get_logger
from src.modules.storage.models.chat import GiftRecord, SettleSpeechLine, SuperChatRecord
from src.modules.storage.models.events import GameEventRecord
from src.modules.storage.models.game_players import GAME_CHAT_PLAYER, GAME_CHAT_STREAMER, GameChatLine
from src.modules.storage.models.sessions import LiveSession
from src.modules.storage.models.topics import TimelineSummaryEntry
from src.modules.storage.repos.chat import ChatRepo
from src.modules.storage.repos.events import EventRepo
from src.modules.storage.repos.game_players import GamePlayerRepo
from src.modules.storage.repos.sessions import SessionRepo
from src.modules.storage.repos.topics import TopicRepo
from src.modules.types.emotion_vocab import EMOTION_CN_ALIASES, Emotion

if TYPE_CHECKING:
    from src.modules.llm.engine import LLMManager

logger = get_logger("ClipSelector")

_PROMPT_PATH = Path(__file__).parent / "prompts" / "segment_selection_system.md"

# 一场最多提议的事件数：值得单独成片的完整事件一场里通常只有一两件，宁缺毋滥
_MAX_SEGMENTS = 3
# 进记录的游戏事件条数上限（全程均匀抽样后的条数）与单条正文的截断长度
_GAME_EVENT_LINES = 150
_GAME_EVENT_CHARS = 100
# 不进记录的游戏事件类型：生存需求打断与底层调用出错，不是她做事的内容
_GAME_EVENT_EXCLUDED_TYPES = frozenset({"attention_required", "error"})
# 弹幕高峰取每分钟桶计数的前列（分钟桶计数达到该阈值才报，避免冷场刷屏）
_DANMAKU_PEAK_MIN_COUNT = 3
_DANMAKU_PEAK_TOP_RATIO = 0.5

# 收边：出现时刻已知的行，起点提前 2 秒；只能估算的台词可能早得多就出声，起点只提前 1 秒
_LEAD_MS = 2000
_ESTIMATED_LEAD_MS = 1000
# 末行播完之后留的余量
_TAIL_MS = 2000
# 没有 TTS 诊断记录时的估算：出声延迟取实测的九成分位，语速取实测偏慢的一成（字/秒），说话时长夹在上下限内
_VOICE_ONSET_MS = 6500
_SPEECH_CHARS_PER_SECOND = 4.0
_SPEECH_MIN_MS = 3000
_SPEECH_MAX_MS = 60_000
# 游戏公屏、礼物这类文字在画面上停留时长的估计
_TEXT_ONSCREEN_MS = 10_000
# 情绪强度达到这个数就在记录里标"情绪很强"（整场里情绪最重的一档台词）
_STRONG_EMOTION_INTENSITY = 0.7

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

# 情绪的展示用中文叫法：取别名表里每个枚举值登记的第一个叫法，别名表是唯一事实源
_EMOTION_NAMES: Dict[str, str] = {emotion.value: name for name, emotion in reversed(list(EMOTION_CN_ALIASES.items()))}

_PaidRecord = Union[GiftRecord, SuperChatRecord]
# 编行号之前的一行：(落库时刻, 正文, 出现前的延迟, 停留时长, 延迟是否来自真实记录, 观众是否看得到)
_RawLine = Tuple[int, str, int, int, bool, bool]


class ClipSelectionError(Exception):
    """AI 选段失败（模型输出无法采用等）。"""


@dataclass(frozen=True)
class MaterialLine:
    """合并记录里的一行：行号、落库时刻，以及它在观众面前的出现与持续（定起止用）。

    ``onset_ms`` 是从落库到观众看到或听到的延迟（有真实播放记录时是精确值），
    ``hold_ms`` 是出现之后持续多久；``onset_known`` 标明延迟来自真实记录还是估算，
    ``visible`` 标明观众是否看得到或听得到（游戏事件行看不到）。
    """

    index: int
    timestamp_ms: int
    text: str
    onset_ms: int
    hold_ms: int
    onset_known: bool
    visible: bool


@dataclass
class ClipSuggestion:
    """一段 AI 提议（墙钟毫秒，写入 clip_segments 前的中间形态）。"""

    start_ms: int
    end_ms: int
    title: str
    reason: str


def _rel_ms(started_at_ms: int, timestamp_ms: int) -> int:
    """墙钟毫秒 → 自开播毫秒（负值截到 0）。"""
    return max(0, int(timestamp_ms) - int(started_at_ms))


def _fmt_rel(timestamp_ms: int) -> str:
    seconds = int(timestamp_ms // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _speech_hold_ms(content: str) -> int:
    """一句台词说多久：按字数与语速估，夹在合理上下限内。"""
    estimated = int(len(content) / _SPEECH_CHARS_PER_SECOND * 1000)
    return min(_SPEECH_MAX_MS, max(_SPEECH_MIN_MS, estimated))


def _speech_line(line: SettleSpeechLine, window: Optional[TtsPlaybackWindow]) -> _RawLine:
    """她的台词：有真实播放时段就按它，没有就估算；情绪不是平静时标出情绪，回应了弹幕时带上那条弹幕。"""
    if window is None:
        onset_ms, hold_ms, onset_known = _VOICE_ONSET_MS, _speech_hold_ms(line.content), False
    else:
        onset_ms = max(0, window.started_ms - line.timestamp_ms)
        hold_ms = max(0, window.ended_ms - window.started_ms)
        onset_known = True
    name = _EMOTION_NAMES.get(line.emotion or "")
    speaker = "她说"
    if name and line.emotion != Emotion.NEUTRAL.value:
        strong = "，情绪很强" if (line.emotion_intensity or 0.0) >= _STRONG_EMOTION_INTENSITY else ""
        speaker = f"她说（{name}{strong}）"
    text = f"{speaker}：{line.content}"
    if line.reply_content:
        text += f"（↩ 弹幕：{line.reply_content}）"
    return (line.timestamp_ms, text, onset_ms, hold_ms, onset_known, True)


def _chat_line(line: GameChatLine, player_name: Optional[str]) -> Optional[_RawLine]:
    """游戏公屏的一行：她打字的与玩家说的；认不出是谁说的行不存原话，不进记录。"""
    if not line.content:
        return None
    if line.direction == GAME_CHAT_STREAMER:
        text = f"她打字：{line.content}"
    elif line.direction == GAME_CHAT_PLAYER:
        text = f"玩家 {player_name or f'编号{line.player_id}'}：{line.content}"
    else:
        return None
    return (line.at_ms, text, 0, _TEXT_ONSCREEN_MS, True, True)


def _paid_line(item: _PaidRecord) -> _RawLine:
    if isinstance(item, GiftRecord):
        text = f"观众 {item.user_name} 送出 {item.gift_name}×{item.quantity}"
    else:
        text = f"观众 {item.user_name} 的醒目留言：{item.message}"
    return (item.timestamp_ms, text, 0, _TEXT_ONSCREEN_MS, True, True)


def _event_line(event: GameEventRecord) -> _RawLine:
    return (event.timestamp_ms, f"游戏事件：{event.message[:_GAME_EVENT_CHARS]}", 0, 0, True, False)


def _number_lines(raw_lines: Sequence[_RawLine]) -> List[MaterialLine]:
    """按时刻排好并从 1 起编行号；同一时刻按取料先后。"""
    ordered = sorted(raw_lines, key=lambda raw: raw[0])
    return [
        MaterialLine(
            index=index,
            timestamp_ms=timestamp_ms,
            text=text,
            onset_ms=onset_ms,
            hold_ms=hold_ms,
            onset_known=onset_known,
            visible=visible,
        )
        for index, (timestamp_ms, text, onset_ms, hold_ms, onset_known, visible) in enumerate(ordered, start=1)
    ]


def _danmaku_peaks(started_at_ms: int, viewer_message_times: Sequence[int]) -> str:
    """弹幕高峰：观众消息按分钟分桶，取计数前列（不低于半个峰值且不少于最低条数）的桶。"""
    buckets: Dict[int, int] = {}
    for timestamp_ms in viewer_message_times:
        minute = _rel_ms(started_at_ms, timestamp_ms) // 60_000
        buckets[minute] = buckets.get(minute, 0) + 1
    if not buckets:
        return ""
    peak = max(buckets.values())
    threshold = max(_DANMAKU_PEAK_MIN_COUNT, int(peak * _DANMAKU_PEAK_TOP_RATIO))
    hot = sorted(minute for minute, count in buckets.items() if count >= threshold)
    return "、".join(f"[{_fmt_rel(minute * 60_000)}]（{buckets[minute]}条）" for minute in hot[:60])


def _render_material(
    *,
    session: LiveSession,
    lines: Sequence[MaterialLine],
    viewer_message_times: Sequence[int],
    summaries: Sequence[TimelineSummaryEntry],
) -> str:
    """把合并记录渲染成给 LLM 的正文：每行带行号与 `[分:秒]` 相对时刻。"""
    started_at_ms = session.started_at_ms
    ended_at_ms = session.ended_at_ms if session.ended_at_ms is not None else session.updated_at_ms
    parts: List[str] = [f"本场直播时长约 {_fmt_rel(_rel_ms(started_at_ms, ended_at_ms))}。"]

    record = "\n".join(
        f"#{line.index} [{_fmt_rel(_rel_ms(started_at_ms, line.timestamp_ms))}] {line.text}" for line in lines
    )
    parts.append(f"【完整记录】\n{record}")

    hot_text = _danmaku_peaks(started_at_ms, viewer_message_times)
    if hot_text:
        parts.append(f"【弹幕高峰】\n{hot_text}")

    topic_lines = [
        f"[{_fmt_rel(_rel_ms(started_at_ms, summary.start_ms))} ~ {_fmt_rel(_rel_ms(started_at_ms, summary.end_ms))}]"
        f" {summary.summary}"
        for summary in summaries
    ]
    if topic_lines:
        parts.append(f"【话题段】\n{chr(10).join(topic_lines)}")
    return "\n\n".join(parts)


def _extract_json_array(text: str) -> str:
    """从模型输出里抠出 JSON 数组文本（容忍代码块围栏与前后闲话）。"""
    match = _JSON_ARRAY_RE.search(text)
    if match is None:
        raise ClipSelectionError("模型输出里找不到 JSON 数组")
    return match.group(0)


def _anchor_of(item: Dict[str, object], key: str, line_count: int) -> int:
    """取模型给的起止行号并校验在记录行号范围内。"""
    if key not in item:
        raise ClipSelectionError(f"提议缺 {key}：{item!r}")
    try:
        index = int(item[key])  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ClipSelectionError(f"提议的 {key} 不是行号：{item!r}") from exc
    if not 1 <= index <= line_count:
        raise ClipSelectionError(f"提议的 {key}={index} 超出记录行号范围 1~{line_count}")
    return index


def _appears_at(line: MaterialLine) -> int:
    """观众看到或听到这一行的时刻；出声延迟只有估算时按落库那一刻算（宁早不晚）。"""
    return line.timestamp_ms + (line.onset_ms if line.onset_known else 0)


def _ends_at(line: MaterialLine) -> int:
    return line.timestamp_ms + line.onset_ms + line.hold_ms


def _start_of(lines: Sequence[MaterialLine], first: int) -> int:
    """片段起点：首行出现前留一点提前量，但不早到上一行还没播完。"""
    line = lines[first - 1]
    start_ms = _appears_at(line) - (_LEAD_MS if line.onset_known else _ESTIMATED_LEAD_MS)
    if first > 1:
        start_ms = max(start_ms, _ends_at(lines[first - 2]))
    return start_ms


def _end_of(lines: Sequence[MaterialLine], last: int, session_end_ms: int) -> int:
    """片段终点：末行播完再留余量；下一行观众看得到的内容出现之前让位，但末行必须播完。"""
    line = lines[last - 1]
    finished_ms = _ends_at(line)
    end_ms = finished_ms + _TAIL_MS
    following = next((candidate for candidate in lines[last:] if candidate.visible), None)
    if following is not None:
        end_ms = max(min(end_ms, _appears_at(following)), finished_ms)
    return min(end_ms, session_end_ms)


def _first_line_that_fits(lines: Sequence[MaterialLine], first: int, last: int, earliest_start_ms: int) -> int:
    """从头往后找第一行：它作为起点能让片段装进时长上限；找不到就退到末行。"""
    for index in range(first, last + 1):
        if _start_of(lines, index) >= earliest_start_ms:
            return index
    return last


def _suggestions_of(
    payload: str,
    *,
    session: LiveSession,
    lines: Sequence[MaterialLine],
    min_ms: int,
    max_ms: int,
) -> List[ClipSuggestion]:
    """解析模型输出：行号锚点 → 墙钟毫秒起止，并按时长约束与互不重叠收敛；模型明确给空数组即没有值得剪的。"""
    try:
        data = json.loads(_extract_json_array(payload))
    except json.JSONDecodeError as exc:
        raise ClipSelectionError(f"模型输出不是合法 JSON：{exc}") from exc
    if not isinstance(data, list):
        raise ClipSelectionError("模型输出不是 JSON 数组")

    session_end_ms = session.ended_at_ms if session.ended_at_ms is not None else session.updated_at_ms
    suggestions: List[ClipSuggestion] = []
    for item in data[:_MAX_SEGMENTS]:
        if not isinstance(item, dict):
            continue
        first = _anchor_of(item, "first_line", len(lines))
        last = _anchor_of(item, "last_line", len(lines))
        if first > last:
            raise ClipSelectionError(f"提议的 first_line 晚于 last_line：{item!r}")
        title = str(item.get("title", "")).strip()
        reason = str(item.get("reason", "")).strip()

        start_ms = max(_start_of(lines, first), session.started_at_ms)
        end_ms = min(_end_of(lines, last, session_end_ms), session_end_ms)
        if end_ms - start_ms > max_ms:
            # 整件事超过时长上限：结果（终点）不动，起点往后挪到仍放得下的那一行
            earliest_start_ms = end_ms - max_ms
            start_ms = max(
                _start_of(lines, _first_line_that_fits(lines, first, last, earliest_start_ms)),
                earliest_start_ms,
                session.started_at_ms,
            )
        if end_ms - start_ms < min_ms:
            logger.info(f"AI 选段提议「{title}」只有 {(end_ms - start_ms) / 1000:.1f} 秒，不足最短时长，放弃")
            continue
        # 与已采纳的重叠：模型把前者排在前面，前者优先，后者挪到前者结束后
        for kept in suggestions:
            if start_ms < kept.end_ms and end_ms > kept.start_ms:
                start_ms = kept.end_ms
        if end_ms - start_ms < min_ms:
            logger.info(f"AI 选段提议「{title}」与更值得剪的事件重叠，挪开后不足最短时长，放弃")
            continue
        suggestions.append(ClipSuggestion(start_ms=start_ms, end_ms=end_ms, title=title, reason=reason))
    return sorted(suggestions, key=lambda suggestion: suggestion.start_ms)


class ClipSelector:
    """AI 选段器：取料合并 → 渲染 → 非流式 LLM 调用 → 行号锚点解析与收边 → 建议列表。"""

    def __init__(
        self,
        llm: "LLMManager",
        *,
        chat_repo: ChatRepo,
        event_repo: EventRepo,
        session_repo: SessionRepo,
        topic_repo: TopicRepo,
        config: ClippingConfigSchema,
        game_player_repo: Optional[GamePlayerRepo] = None,
        voice_log: Optional[TtsDiagnosticsReader] = None,
    ) -> None:
        self._llm = llm
        self._chat_repo = chat_repo
        self._event_repo = event_repo
        self._session_repo = session_repo
        self._topic_repo = topic_repo
        self._game_player_repo = game_player_repo
        self._voice_log = voice_log
        self._config = config
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    async def select(self, *, live_session_id: int) -> List[ClipSuggestion]:
        """对一场直播跑一次 AI 选段，返回墙钟毫秒提议列表；没有值得剪的返回空列表。"""
        session = await self._session_repo.get_live_session(live_session_id=live_session_id)
        if session is None:
            raise ClipSelectionError(f"场次 {live_session_id} 不存在")

        speech_lines = await self._chat_repo.list_session_speech_for_settle(live_session_id=live_session_id)
        delivered = [line for line in speech_lines if line.dropped_at_ms is None]
        viewer_messages = await self._chat_repo.list_recent_live_chat(
            live_session_id=live_session_id, limit=None, sender_role="viewer"
        )
        gifts = await self._chat_repo.list_session_gifts(live_session_id=live_session_id, limit=100)
        super_chats = await self._chat_repo.list_session_super_chats(live_session_id=live_session_id, limit=100)
        game_events = await self._event_repo.list_session_game_events_sampled(
            live_session_id=live_session_id, limit=_GAME_EVENT_LINES
        )
        summaries = await self._topic_repo.list_session_summaries(live_session_id=live_session_id)
        chat_lines, player_names = await self._game_chat(live_session_id=live_session_id)
        windows = self._voice_windows(session)
        utterance_ids = (
            await self._chat_repo.list_session_speech_utterance_ids(live_session_id=live_session_id) if windows else {}
        )

        raw_lines: List[_RawLine] = [
            _speech_line(line, windows.get(utterance_ids.get(line.id, ""))) for line in delivered
        ]
        for chat_line in chat_lines:
            player_name = player_names.get(chat_line.player_id) if chat_line.player_id is not None else None
            raw = _chat_line(chat_line, player_name)
            if raw is not None:
                raw_lines.append(raw)
        raw_lines.extend(_paid_line(item) for item in list(gifts) + list(super_chats))
        raw_lines.extend(
            _event_line(event) for event in game_events if event.event_type not in _GAME_EVENT_EXCLUDED_TYPES
        )
        lines = _number_lines(raw_lines)
        if not lines:
            logger.info(f"场次 {live_session_id} 没有任何带时刻的记录，没有可选的事件")
            return []

        material = _render_material(
            session=session,
            lines=lines,
            viewer_message_times=[message.timestamp_ms for message in viewer_messages],
            summaries=summaries,
        )
        system = (
            self._system_prompt.replace("{min_s}", str(self._config.min_segment_seconds))
            .replace("{max_s}", str(self._config.max_segment_seconds))
            .replace("{max_segments}", str(_MAX_SEGMENTS))
        )
        return await self._ask(
            material,
            system=system,
            profile=self._config.llm_profile,
            session=session,
            lines=lines,
            min_ms=self._config.min_segment_seconds * 1000,
            max_ms=self._config.max_segment_seconds * 1000,
        )

    def _voice_windows(self, session: LiveSession) -> Dict[str, TtsPlaybackWindow]:
        """本场每句台词的真实播放时段（按发声编号索引）；没装配读取口或场次还没结束时为空。"""
        if self._voice_log is None or session.ended_at_ms is None:
            return {}
        return self._voice_log.read_session(
            live_session_id=session.id, started_at_ms=session.started_at_ms, ended_at_ms=session.ended_at_ms
        )

    async def _game_chat(self, *, live_session_id: int) -> Tuple[List[GameChatLine], Dict[int, str]]:
        """本场游戏公屏，以及说了话的玩家的名字；没装配玩家仓储时按没有游戏公屏处理。"""
        repo = self._game_player_repo
        if repo is None:
            return [], {}
        chat_lines = await repo.list_session_game_chat(live_session_id=live_session_id)
        names: Dict[int, str] = {}
        looked_up: Set[int] = set()
        for chat_line in chat_lines:
            player_id = chat_line.player_id
            if chat_line.direction != GAME_CHAT_PLAYER or player_id is None or player_id in looked_up:
                continue
            looked_up.add(player_id)
            player = await repo.get_player(player_id=player_id)
            if player is not None:
                names[player_id] = player.name
        return chat_lines, names

    async def _ask(
        self,
        material: str,
        *,
        system: str,
        profile: str,
        session: LiveSession,
        lines: Sequence[MaterialLine],
        min_ms: int,
        max_ms: int,
    ) -> List[ClipSuggestion]:
        """跑一次非流式 LLM 调用并把输出解析成提议；输出无法采用（不是 JSON、行号对不上）重试一次，仍失败抛错。"""
        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                response = await self._llm.generate(material, profile=profile, system=system)
                return _suggestions_of(response.content, session=session, lines=lines, min_ms=min_ms, max_ms=max_ms)
            except ClipSelectionError as exc:
                last_error = exc
                logger.warning(f"AI 选段输出第 {attempt} 次无法采用：{exc}")
        raise ClipSelectionError(f"AI 选段输出两次都无法采用：{last_error}")
