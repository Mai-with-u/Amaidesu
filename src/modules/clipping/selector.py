"""AI 选段：基于本场事件时间线提议值得切片的时段。

取料全部来自库里已有的带时间戳数据（主播发言、弹幕高峰、礼物/SC、游戏
事件、话题摘要），不做视频/音频分析——主播是 AI，说的每句话本来就有
精确文本与时间戳。LLM 输出相对开播秒数的 JSON 数组，解析后换算回墙钟
毫秒并按时长约束收敛。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Sequence

from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.logging import get_logger
from src.modules.storage.models.chat import GiftRecord, SuperChatRecord
from src.modules.storage.models.sessions import LiveSession
from src.modules.storage.repos.chat import ChatRepo
from src.modules.storage.repos.events import EventRepo
from src.modules.storage.repos.sessions import SessionRepo
from src.modules.storage.repos.topics import TopicRepo

if TYPE_CHECKING:
    from src.modules.llm.engine import LLMManager

logger = get_logger("ClipSelector")

_PROMPT_PATH = Path(__file__).parent / "prompts" / "segment_selection_system.md"

# 提议段数上限
_MAX_SEGMENTS = 10
# 弹幕高峰取每分钟桶计数的前列（分钟桶计数达到该阈值才报，避免冷场刷屏）
_DANMAKU_PEAK_MIN_COUNT = 3
_DANMAKU_PEAK_TOP_RATIO = 0.5

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


class ClipSelectionError(Exception):
    """AI 选段失败（模型输出无法解析等）。"""


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


def _render_material(
    *,
    session: LiveSession,
    speech_lines: Sequence,
    viewer_message_times: Sequence[int],
    gifts: Sequence[GiftRecord],
    super_chats: Sequence[SuperChatRecord],
    game_event_lines: Sequence[str],
    topic_lines: Sequence[str],
) -> str:
    """把取料结果渲染成给 LLM 的正文（每行带 [分:秒] 相对时刻）。"""
    parts: List[str] = [f"本场直播时长约 {_fmt_rel(_rel_ms(session.started_at_ms, session.updated_at_ms))}。"]

    speech_section = "\n".join(
        f"[{_fmt_rel(_rel_ms(session.started_at_ms, line.timestamp_ms))}] 她说：{line.content}"
        + (f"（↩ 弹幕：{line.reply_content}）" if line.reply_content else "")
        for line in speech_lines
    )
    parts.append(f"【她说的话】\n{speech_section if speech_section else '（本场没有发言记录）'}")

    # 弹幕高峰：观众消息按分钟分桶，取计数前列（≥ 半个峰值且 ≥ 3 条）的桶
    buckets: dict[int, int] = {}
    for ts in viewer_message_times:
        minute = _rel_ms(session.started_at_ms, ts) // 60_000
        buckets[minute] = buckets.get(minute, 0) + 1
    if buckets:
        peak = max(buckets.values())
        threshold = max(_DANMAKU_PEAK_MIN_COUNT, int(peak * _DANMAKU_PEAK_TOP_RATIO))
        hot = sorted(minute for minute, count in buckets.items() if count >= threshold)
        if hot:
            hot_text = "、".join(f"[{_fmt_rel(minute * 60_000)}]（{buckets[minute]}条）" for minute in hot[:60])
            parts.append(f"【弹幕高峰】\n{hot_text}")

    def _pay_line(item: object) -> str:
        ts = _fmt_rel(_rel_ms(session.started_at_ms, item.timestamp_ms))  # type: ignore[attr-defined]
        if isinstance(item, GiftRecord):
            return f"[{ts}] {item.user_name} 送 {item.gift_name}×{item.quantity}"
        if isinstance(item, SuperChatRecord):
            return f"[{ts}] {item.user_name} SC：{item.message}"
        return ""

    pay_section = "\n".join(filter(None, (_pay_line(item) for item in list(gifts) + list(super_chats))))
    parts.append(f"【礼物/SC】\n{pay_section if pay_section else '（本场没有付费事件）'}")

    parts.append(f"【游戏事件】\n{chr(10).join(game_event_lines) if game_event_lines else '（无）'}")
    parts.append(f"【话题段】\n{chr(10).join(topic_lines) if topic_lines else '（无）'}")
    return "\n\n".join(parts)


def _extract_json_array(text: str) -> str:
    """从模型输出里抠出 JSON 数组文本（容忍代码块围栏与前后闲话）。"""
    match = _JSON_ARRAY_RE.search(text)
    if match is None:
        raise ClipSelectionError("模型输出里找不到 JSON 数组")
    return match.group(0)


def _suggestions_of(
    payload: str,
    *,
    session: LiveSession,
    min_ms: int,
    max_ms: int,
) -> List[ClipSuggestion]:
    """解析模型输出并换算成墙钟毫秒提议；时长与边界按时长约束收敛。"""
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
        try:
            start_s = float(item["start_s"])
            end_s = float(item["end_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ClipSelectionError(f"提议元素缺 start_s/end_s 数值字段：{item!r}") from exc
        title = str(item.get("title", "")).strip()
        reason = str(item.get("reason", "")).strip()
        start_ms = session.started_at_ms + max(0, int(start_s * 1000))
        end_ms = session.started_at_ms + max(0, int(end_s * 1000))
        start_ms = min(start_ms, session_end_ms)
        end_ms = min(max(end_ms, start_ms), session_end_ms)
        if end_ms - start_ms > max_ms:
            end_ms = start_ms + max_ms
        if end_ms - start_ms < min_ms:
            continue
        suggestions.append(ClipSuggestion(start_ms=start_ms, end_ms=end_ms, title=title, reason=reason))
    if not suggestions:
        raise ClipSelectionError("模型没有给出任何满足时长约束的提议")
    return suggestions


class ClipSelector:
    """AI 选段器：取料 → 渲染 → 非流式 LLM 调用 → 解析收敛 → 建议列表。"""

    def __init__(
        self,
        llm: "LLMManager",
        *,
        chat_repo: ChatRepo,
        event_repo: EventRepo,
        session_repo: SessionRepo,
        topic_repo: TopicRepo,
        config: ClippingConfigSchema,
    ) -> None:
        self._llm = llm
        self._chat_repo = chat_repo
        self._event_repo = event_repo
        self._session_repo = session_repo
        self._topic_repo = topic_repo
        self._config = config
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    async def select(self, *, live_session_id: int) -> List[ClipSuggestion]:
        """对一场直播跑一次 AI 选段，返回墙钟毫秒提议列表。"""
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
        game_events = await self._event_repo.list_session_game_events(live_session_id=live_session_id)
        summaries = await self._topic_repo.list_session_summaries(live_session_id=live_session_id)

        material = _render_material(
            session=session,
            speech_lines=delivered,
            viewer_message_times=[m.timestamp_ms for m in viewer_messages],
            gifts=gifts,
            super_chats=super_chats,
            game_event_lines=[
                f"[{_fmt_rel(_rel_ms(session.started_at_ms, e.timestamp_ms))}] {e.game}：{e.message}"
                for e in game_events
            ],
            topic_lines=[
                f"[{_fmt_rel(_rel_ms(session.started_at_ms, s.start_ms))} ~ "
                f"{_fmt_rel(_rel_ms(session.started_at_ms, s.end_ms))}] {s.summary}"
                for s in summaries
            ],
        )
        system = self._system_prompt.replace("{min_s}", str(self._config.min_segment_seconds)).replace(
            "{max_s}", str(self._config.max_segment_seconds)
        )

        payload = await self._ask(material, system=system, profile=self._config.llm_profile)
        return _suggestions_of(
            payload,
            session=session,
            min_ms=self._config.min_segment_seconds * 1000,
            max_ms=self._config.max_segment_seconds * 1000,
        )

    async def _ask(self, material: str, *, system: str, profile: str) -> str:
        """跑一次非流式 LLM 调用；输出解析失败重试一次，仍失败抛 ClipSelectionError。"""
        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                response = await self._llm.generate(material, profile=profile, system=system)
                _extract_json_array(response.content)  # 只验可解析性；换算收敛留给调用方
                return response.content
            except ClipSelectionError as exc:
                last_error = exc
                logger.warning(f"AI 选段输出第 {attempt} 次解析失败：{exc}")
        raise ClipSelectionError(f"AI 选段输出两次都无法解析：{last_error}")
