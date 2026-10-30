"""场次回顾：收播时把一场直播总结成主播下次开播还记得的一段话。

触发：``live.ended``（主播 Agent 收播钩子）；进程异常退出没跑到钩子、或停止
Agent 时整理被打断的场次，下次启动时按 ``list_unsettled`` 的待办补完（回顾已在库里
就不重写，经 ``ensure_recap``）。两条路径共用同一份原料与同一次 LLM 调用，原料全部
来自库表，不依赖进程内状态。

原料（全部按场次主键取；备忘录由主播 Agent 每次写入时落进场次行，经按场次主键
读取的回调递入）：
- 主播备忘录全文（``live_sessions.notebook_text``）——模型自己沉淀的结论账，排在
  最前、不参与截断
- 主播自己说过的话（``live_chat`` 的 assistant 行）——承诺、梗、进展都在嘴里
- 话题摘要轨迹（``timeline_summary``）——观众这场聊了什么
- 游戏里程碑（``game_events``）——游戏里做到哪
- 本场最活跃的观众与付费（``live_chat`` 聚合 + 付费三表）——谁来得多、谁撑场
- 观众指出主播说错、记错的弹幕（连同主播前一句）——长期笔记据此改正

发言、话题、游戏三类按全场时间均匀取样（首尾都在），不是只取结尾：一场几小时的
直播，开头答应的事和结尾一样要进回顾。整体超长时再把发言隔行抽稀，仍超才从尾部
截断；备忘录不受影响。

产出写 ``session_recaps``；开播注入与 ``memory_recall_session`` 工具都从那张表读。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, List, Optional

from src.modules.events.names import CoreEvents
from src.modules.storage.models.memory import SessionRecap
from src.modules.storage.models.sessions import LiveSession
from src.modules.events.payloads.recap import StreamerRecapWrittenPayload
from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.types.currency import to_cny
from src.modules.types.guard_levels import GUARD_LEVEL_NAMES
from src.modules.types.session_kind import SessionKind

from ..background import SUMMARY_PROFILE

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

__all__ = ["SessionRecapWriter", "clip_whole_sentences", "format_recap"]

logger = get_logger("SessionRecapWriter")

# 系统提示词模板键（正文见 prompts/session_recap_system.md）
_RECAP_SYSTEM_TEMPLATE = "session_recap_system"

# 原料封顶：取样条数与单行长度（整场按时间均匀取样，控 prompt 体量）
_SPEECH_ROWS = 80
_SPEECH_LINE_CHARS = 120
_SUMMARY_ROWS = 60
_SUMMARY_SCAN_ROWS = 1000
_GAME_EVENT_ROWS = 40
_GAME_EVENT_SCAN_ROWS = 500
_SPEAKER_ROWS = 8
_PAYMENT_ROWS = 15
_MATERIAL_MAX_CHARS = 12_000
# 发言抽稀的下限：再少就看不出这场的脉络了，宁可从尾部截断
_SPEECH_MIN_ROWS = 10
# 观众纠错：命中这些说法的弹幕算"指出主播说错/记错"，最多收几条
_CORRECTION_MARKERS = ("记错", "说错", "不对", "错了", "说反", "不是这样", "没答应", "瞎说", "乱说")
_CORRECTION_FALSE_MARKERS = ("不对劲",)
_CORRECTION_ROWS = 8
# 截短时句末标点之后才下刀；最后一个完整句子太靠前（不到上限一半）就直接按字截
_SENTENCE_ENDS = "。！？!?\n"


def clip_whole_sentences(text: str, limit: int) -> str:
    """把文本截到 ``limit`` 字以内，尽量停在完整句子之后，不把一句话截成半截。

    上限内最后一个句末标点不到上限一半时直接按字截（整段只有一两句很长的话）。
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(mark) for mark in _SENTENCE_ENDS)
    if cut + 1 >= limit // 2:
        return head[: cut + 1].rstrip()
    return head


def _sample_evenly(rows: List[Any], count: int) -> List[Any]:
    """从时间正序的行里均匀取 ``count`` 条（首尾都保留）；不够就全给。"""
    if count <= 0:
        return []
    if len(rows) <= count:
        return list(rows)
    if count == 1:
        return [rows[-1]]
    step = (len(rows) - 1) / (count - 1)
    return [rows[round(i * step)] for i in range(count)]


def _is_correction(text: str) -> bool:
    """这条弹幕是不是在指出主播说错、记错（"不对劲"这类不算）。"""
    cleaned = text
    for false_marker in _CORRECTION_FALSE_MARKERS:
        cleaned = cleaned.replace(false_marker, "")
    return any(marker in cleaned for marker in _CORRECTION_MARKERS)


def _duration_text(ms: int) -> str:
    """毫秒时长 → "X 小时 Y 分钟"（回顾只需要分钟粒度，中文写法与参考段一致）。"""
    total_minutes = max(0, int(ms)) // 60_000
    if total_minutes < 1:
        return "不足 1 分钟"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours} 小时 {minutes} 分钟" if hours else f"{minutes} 分钟"


def _clock(ts_ms: int) -> str:
    """毫秒时刻 → 本地 HH:MM（回顾原料里只需要相对先后）。"""
    return datetime.fromtimestamp(max(0, int(ts_ms)) / 1000).strftime("%H:%M")


def format_recap(recap: SessionRecap) -> str:
    """把一场回顾渲染成"【日期的直播，时长】正文"——注入段与工具观察共用同一形态。"""
    started = int(recap.started_at_ms or 0)
    ended = int(recap.ended_at_ms or 0)
    date_text = datetime.fromtimestamp(started / 1000).strftime("%Y-%m-%d") if started > 0 else "日期不详"
    duration = _duration_text(ended - started) if started > 0 and ended > started else ""
    head = f"【{date_text} 的直播" + (f"，时长 {duration}" if duration else "") + "】"
    return f"{head}\n{recap.recap_text.strip()}"


class SessionRecapWriter:
    """场次回顾的生成器：取原料 → 一次 LLM 总结 → 写回 ``session_recaps``。"""

    def __init__(
        self,
        *,
        sessions_repo: Any,
        chat_repo: Any,
        recap_repo: Any,
        llm_service: Any,
        prompt_manager: PromptManager,
        topic_repo: Any = None,
        event_repo: Any = None,
        notebook_text_provider: Optional[Callable[[int], Awaitable[str]]] = None,
        max_length: int = 500,
        event_bus: Optional["EventBus"] = None,
    ) -> None:
        """全部仓储与服务由主播 Agent 构造时注入；topic/event 仓储缺失只让原料少一类。

        ``notebook_text_provider`` 按场次主键返回这场落盘的备忘录全文（直播中途
        关掉应用、下次启动补整理时同样读得到）；None 或空串 = 本场没写过备忘录，原料少这一类。
        ``event_bus`` 用于回顾落库后发 ``streamer.recap.written``；None 时只写库不通知。
        """
        self._sessions = sessions_repo
        self._chat = chat_repo
        self._recaps = recap_repo
        self._topics = topic_repo
        self._events = event_repo
        self._notebook_text_provider = notebook_text_provider
        self._llm = llm_service
        self._prompt_manager = prompt_manager
        self._max_length = max(100, int(max_length))
        self._event_bus = event_bus
        self._system_prompt: str = ""

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def write_recap(self, live_session_id: int) -> Optional[str]:
        """为一场已结束的直播写回顾；原料为空（空场次）或 LLM 失败时返回 None、不落库。

        流程：读场次行 → 收集原料 → LLM 总结 → 截断到上限 → upsert。
        """
        session_row = await self._sessions.get_live_session(live_session_id=int(live_session_id))
        if session_row is None:
            logger.debug(f"场次 {live_session_id} 不存在，跳过回顾")
            return None
        started_at = int(session_row.started_at_ms or 0)
        ended_at = int(session_row.ended_at_ms or 0)
        if ended_at <= 0:
            logger.debug(f"场次 {live_session_id} 尚未结账，跳过回顾")
            return None

        material = await self._collect_material(int(live_session_id), session_row)
        if not material:
            logger.info(f"场次 {live_session_id} 没有可总结的记录，不写回顾")
            return None

        prompt = f"以下是这一场直播留下的记录，请写出这场的回顾：\n\n{material}"
        try:
            response = await self._llm.generate(prompt, profile=SUMMARY_PROFILE, system=self._get_system_prompt())
        except Exception as exc:
            logger.warning(f"场次回顾 LLM 调用异常（场次 {live_session_id}）: {exc}")
            return None
        if not (response.ok and response.content):
            logger.warning(f"场次回顾 LLM 返回失败（场次 {live_session_id}）")
            return None
        text = clip_whole_sentences(str(response.content), self._max_length)
        if not text:
            return None
        await self._recaps.upsert_recap(
            live_session_id=int(live_session_id),
            started_at_ms=started_at,
            ended_at_ms=ended_at,
            recap_text=text,
        )
        logger.info(f"场次回顾已写入（场次 {live_session_id}）: {text[:60]}")
        await self._announce_written(int(live_session_id), session_row.kind, started_at, ended_at, text)
        return text

    async def _announce_written(
        self, live_session_id: int, kind: SessionKind, started_at_ms: int, ended_at_ms: int, text: str
    ) -> None:
        """回顾落库后发事实通知；收播整理与启动补写都经 ``write_recap``，两条路径都会发。

        通知失败只告警：回顾已经在库里，开播注入与回忆工具不受影响。
        """
        if self._event_bus is None:
            return
        try:
            await self._event_bus.emit(
                CoreEvents.STREAMER_RECAP_WRITTEN,
                StreamerRecapWrittenPayload(
                    live_session_id=live_session_id,
                    kind=kind,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                    recap_text=text,
                ),
                source="SessionRecapWriter",
            )
        except Exception as exc:  # noqa: BLE001 - 通知失败不推翻已落库的回顾
            logger.warning(f"streamer.recap.written 发布失败（场次 {live_session_id}，已忽略）: {exc}")

    async def ensure_recap(self, live_session_id: int) -> bool:
        """保证这一场有回顾：库里已有就直接用（不调模型、不再发通知），没有才写；返回本场是否有回顾。

        启动补写的场次可能在回顾写完后才被打断，重写既浪费一次调用，也会让下游再收一次通知。
        """
        if await self._recaps.get_recap(live_session_id=int(live_session_id)) is not None:
            logger.debug(f"场次 {live_session_id} 已有回顾，不重写")
            return True
        return await self.write_recap(live_session_id) is not None

    async def list_unsettled(self, *, limit: int = 1) -> List[int]:
        """最近 ``limit`` 场"已结束却没整理完"的场次主键（结束时刻倒序），启动补写按它补完收播整理。

        没整理完包括没有回顾的，和回顾写完后整理被打断的；查询失败按没有待办处理。
        """
        try:
            pending = await self._recaps.list_unsettled_sessions(limit=limit)
        except Exception as exc:
            logger.warning(f"查询待补完整理的场次失败: {exc}", exc=True)
            return []
        return [row.live_session_id for row in pending]

    async def mark_settled(self, live_session_id: int) -> bool:
        """记下这一场的收播整理已走完；本场没有回顾时记不上（返回 False），留给下次启动再试回顾。"""
        return await self._recaps.mark_settled(live_session_id=int(live_session_id))

    # ------------------------------------------------------------------
    # 读取（注入段）
    # ------------------------------------------------------------------

    async def render_recent(self, *, count: int, kind: SessionKind, exclude_session_id: Optional[int] = None) -> str:
        """渲染最近 ``count`` 场同类型场次的回顾（最近一场在前），供开播后注入"上场回顾"段；没有返回空串。

        ``kind`` 是本场的类型：正式场只接正式场的回顾，彩排场只接彩排场的——
        昨晚的调试场不会被当成今晚正式直播的"上一场"。
        """
        if count <= 0:
            return ""
        try:
            rows = await self._recaps.list_recent_recaps(limit=count, exclude_session_id=exclude_session_id, kind=kind)
        except Exception as exc:
            logger.warning(f"读取最近场次回顾失败: {exc}")
            return ""
        return "\n\n".join(format_recap(row) for row in rows)

    # ------------------------------------------------------------------
    # 原料
    # ------------------------------------------------------------------

    async def collect_material(self, live_session_id: int) -> str:
        """这场的整理原料（回顾与长期笔记共用同一份）；场次不存在或空场次时为空串。"""
        session_row = await self._sessions.get_live_session(live_session_id=int(live_session_id))
        if session_row is None:
            return ""
        return await self._collect_material(int(live_session_id), session_row)

    async def _collect_material(self, live_session_id: int, session_row: LiveSession) -> str:
        """按场次主键把各表记录渲染成原料文本。

        顺序：场次头 → 备忘录 → 发言 → 话题 → 游戏 → 观众与付费 → 观众纠错。整体超过上限时
        先把发言隔行抽稀（仍覆盖全场），抽到下限还超才从尾部截断；场次头与备忘录不动。
        """
        head = self._render_head(session_row)
        notebook = await self._render_notebook(live_session_id)
        speech_rows = await self._speech_rows(live_session_id)
        speech_lines = [self._speech_line(row) for row in _sample_evenly(speech_rows, _SPEECH_ROWS)]
        rest: List[str] = []
        topics = await self._render_topics(live_session_id)
        if topics:
            rest.append("【观众话题轨迹】\n" + topics)
        games = await self._render_game_events(live_session_id)
        if games:
            rest.append("【游戏里的进展】\n" + games)
        audience = await self._render_audience(live_session_id)
        if audience:
            rest.append("【活跃观众与付费】\n" + audience)
        corrections = await self._render_corrections(live_session_id, speech_rows)
        if corrections:
            rest.append("【观众指出主播说错、记错的地方（连同主播前一句）】\n" + corrections)

        fixed = [head] if head else []
        if notebook:
            fixed.append("【主播备忘录（本场结束时的工作笔记全文）】\n" + notebook)
        # 只有场次头没有任何内容 = 空场次，不值得写回顾
        if not (notebook or speech_lines or rest):
            return ""
        budget = max(0, _MATERIAL_MAX_CHARS - len("\n\n".join(fixed)))

        def _body(lines: List[str]) -> str:
            sections = (["【主播自己说过的话（全场按时间取样）】\n" + "\n".join(lines)] if lines else []) + rest
            return "\n\n".join(sections)

        body = _body(speech_lines)
        while len(body) > budget and len(speech_lines) > _SPEECH_MIN_ROWS:
            speech_lines = speech_lines[::2]
            body = _body(speech_lines)
        return "\n\n".join([*fixed, body[:budget]] if body else fixed)

    async def _render_notebook(self, live_session_id: int) -> str:
        """这场落盘的备忘录全文（空/回调缺失 = 本场没写，整段省略）。"""
        if self._notebook_text_provider is None:
            return ""
        try:
            return (await self._notebook_text_provider(live_session_id) or "").strip()
        except Exception as exc:
            logger.warning(f"读取备忘录快照失败，回顾原料省略该类: {exc}")
            return ""

    def _render_head(self, session_row: LiveSession) -> str:
        started = int(session_row.started_at_ms or 0)
        ended = int(session_row.ended_at_ms or 0)
        parts: List[str] = []
        if started > 0:
            parts.append("日期 " + datetime.fromtimestamp(started / 1000).strftime("%Y-%m-%d %H:%M"))
        if started > 0 and ended > started:
            parts.append("时长 " + _duration_text(ended - started))
        title = session_row.title
        if title:
            parts.append(f"标题「{title}」")
        return ("【场次】" + "，".join(parts)) if parts else ""

    async def _speech_rows(self, live_session_id: int) -> List[Any]:
        """这场主播说过的全部话（时间正序，去掉空行）；取样与纠错素材都从这里取。"""
        try:
            rows = await self._chat.list_recent_live_chat(
                live_session_id=live_session_id, limit=None, sender_role="assistant"
            )
        except Exception as exc:
            logger.warning(f"读取主播发言失败（场次 {live_session_id}）: {exc}")
            return []
        return [row for row in rows if (row.content or "").strip()]

    @staticmethod
    def _speech_line(row: Any) -> str:
        content = (row.content or "").strip().replace("\n", " ")
        return f"{_clock(row.timestamp_ms)} 主播: {content[:_SPEECH_LINE_CHARS]}"

    async def _render_corrections(self, live_session_id: int, speech_rows: List[Any]) -> str:
        """观众指出主播说错、记错的弹幕，每条带上主播在那之前说的最后一句（对照着看错在哪）。"""
        try:
            rows = await self._chat.list_recent_live_chat(
                live_session_id=live_session_id, limit=None, sender_role="viewer"
            )
        except Exception as exc:
            logger.warning(f"读取观众弹幕失败（场次 {live_session_id}），原料省略观众纠错: {exc}")
            return ""
        lines: List[str] = []
        for row in rows:
            content = (row.content or "").strip().replace("\n", " ")
            if not content or not _is_correction(content):
                continue
            before = [speech for speech in speech_rows if speech.timestamp_ms <= row.timestamp_ms]
            line = f"{_clock(row.timestamp_ms)} {row.sender_name or '观众'}: {content[:_SPEECH_LINE_CHARS]}"
            if before:
                line += f"（前一句主播说：{(before[-1].content or '').strip()[:_SPEECH_LINE_CHARS]}）"
            lines.append(line)
        return "\n".join(_sample_evenly(lines, _CORRECTION_ROWS))

    async def _render_topics(self, live_session_id: int) -> str:
        if self._topics is None:
            return ""
        try:
            rows = await self._topics.list_session_summaries(live_session_id=live_session_id, limit=_SUMMARY_SCAN_ROWS)
        except Exception as exc:
            logger.warning(f"读取话题轨迹失败（场次 {live_session_id}）: {exc}")
            return ""
        lines = []
        last = ""
        for row in rows:
            summary = (row.summary or "").strip()
            # 相邻重复的摘要句只留一条（弹幕没变时后台会原样再摘一次）
            if summary and summary != last:
                lines.append(f"{_clock(row.end_ms)} {summary}")
                last = summary
        return "\n".join(_sample_evenly(lines, _SUMMARY_ROWS))

    async def _render_game_events(self, live_session_id: int) -> str:
        if self._events is None:
            return ""
        try:
            rows = await self._events.list_session_game_events(
                live_session_id=live_session_id, limit=_GAME_EVENT_SCAN_ROWS
            )
        except Exception as exc:
            logger.warning(f"读取游戏事件失败（场次 {live_session_id}）: {exc}")
            return ""
        lines = []
        for row in rows:
            message = (row.message or "").strip().replace("\n", " ")
            if message:
                lines.append(f"{_clock(row.timestamp_ms)} [{row.game}] {message[:_SPEECH_LINE_CHARS]}")
        return "\n".join(_sample_evenly(lines, _GAME_EVENT_ROWS))

    async def _render_audience(self, live_session_id: int) -> str:
        lines: List[str] = []
        try:
            speakers = await self._chat.list_session_speakers(live_session_id=live_session_id, limit=_SPEAKER_ROWS)
            if speakers:
                names = "、".join(
                    f"{speaker.user_name or '观众 #' + str(speaker.viewer_id)}（{speaker.message_count} 条）"
                    for speaker in speakers
                )
                lines.append(f"弹幕最多的观众：{names}")
        except Exception as exc:
            logger.warning(f"读取活跃观众失败（场次 {live_session_id}）: {exc}")
        try:
            for gift in await self._chat.list_session_gifts(live_session_id=live_session_id, limit=_PAYMENT_ROWS):
                lines.append(f"{_clock(gift.timestamp_ms)} {gift.user_name} 送出 {gift.gift_name}×{gift.quantity or 1}")
            for sc in await self._chat.list_session_super_chats(live_session_id=live_session_id, limit=_PAYMENT_ROWS):
                amount = to_cny(sc.total_price or 0, sc.currency or "")
                amount_text = f"（¥{amount:.0f}）" if amount else ""
                lines.append(f"{_clock(sc.timestamp_ms)} {sc.user_name} SC{amount_text}：{(sc.message or '')[:60]}")
            for guard in await self._chat.list_session_guards(live_session_id=live_session_id, limit=_PAYMENT_ROWS):
                guard_name = GUARD_LEVEL_NAMES.get(guard.guard_level or 0, "大航海")
                lines.append(f"{_clock(guard.timestamp_ms)} {guard.user_name} 开通{guard_name}")
        except Exception as exc:
            logger.warning(f"读取付费记录失败（场次 {live_session_id}）: {exc}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------

    def _get_system_prompt(self) -> str:
        """渲染回顾系统提示词（含长度变量，结果缓存复用）。"""
        if not self._system_prompt:
            self._system_prompt = self._prompt_manager.render(_RECAP_SYSTEM_TEMPLATE, recap_max_length=self._max_length)
        return self._system_prompt
