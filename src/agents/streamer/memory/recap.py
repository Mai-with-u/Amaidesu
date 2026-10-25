"""场次回顾：收播时把一场直播总结成主播下次开播还记得的一段话。

触发：``live.ended``（主播 Agent 收播钩子）；进程异常退出没跑到钩子的场次，
下次启动时经 ``backfill_missing`` 补写（只看已结账且无回顾的场次）。
两条路径共用同一份原料与同一次 LLM 调用，原料全部来自库表，不依赖进程内状态。

原料（全部按场次主键取，各自封顶控制 prompt 体量；备忘录是唯一例外——
进程内会话状态，收播时由主播 Agent 先取走全文快照再清空，经只读回调递入）：
- 主播自己说过的话（``live_chat`` 的 assistant 行）——承诺、梗、进展都在嘴里
- 话题摘要轨迹（``timeline_summary``）——观众这场聊了什么
- 游戏里程碑（``game_events``）——游戏里做到哪
- 本场最活跃的观众与付费（``live_chat`` 聚合 + 付费三表）——谁来得多、谁撑场
- 主播备忘录全文（``streamer_notebook`` 的会话内状态）——已翻篇/已兑现的结论账

产出写 ``session_recaps``；开播注入与 ``memory_recall_session`` 工具都从那张表读。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, List, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.recap import StreamerRecapWrittenPayload
from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.types.currency import to_cny
from src.modules.types.guard_levels import GUARD_LEVEL_NAMES

from ..background import SUMMARY_PROFILE

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

__all__ = ["SessionRecapWriter", "format_recap"]

logger = get_logger("SessionRecapWriter")

# 系统提示词模板键（正文见 prompts/session_recap_system.md）
_RECAP_SYSTEM_TEMPLATE = "session_recap_system"

# 原料封顶：条数与单行长度（整场几小时的记录只取最近的一批，控 prompt 体量）
_SPEECH_ROWS = 80
_SPEECH_LINE_CHARS = 120
_SUMMARY_ROWS = 60
_GAME_EVENT_ROWS = 40
_SPEAKER_ROWS = 8
_PAYMENT_ROWS = 15
_MATERIAL_MAX_CHARS = 12_000


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


def format_recap(row: Any) -> str:
    """把一行回顾渲染成"【日期的直播，时长】正文"——注入段与工具观察共用同一形态。"""
    started = int(row["started_at_ms"] or 0)
    ended = int(row["ended_at_ms"] or 0)
    date_text = datetime.fromtimestamp(started / 1000).strftime("%Y-%m-%d") if started > 0 else "日期不详"
    duration = _duration_text(ended - started) if started > 0 and ended > started else ""
    head = f"【{date_text} 的直播" + (f"，时长 {duration}" if duration else "") + "】"
    return f"{head}\n{str(row['recap_text']).strip()}"


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
        notebook_text_provider: Optional[Callable[[], str]] = None,
        max_length: int = 500,
        event_bus: Optional["EventBus"] = None,
    ) -> None:
        """全部仓储与服务由主播 Agent 构造时注入；topic/event 仓储缺失只让原料少一类。

        ``notebook_text_provider`` 返回收播时刻的备忘录全文快照（主播 Agent 在
        清空备忘录前取走）；None 或空串 = 本场没写过备忘录，原料少这一类。
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
        started_at = int(session_row["started_at_ms"] or 0)
        ended_at = int(session_row["ended_at_ms"] or 0)
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
        if not (getattr(response, "success", False) and getattr(response, "content", None)):
            logger.warning(f"场次回顾 LLM 返回失败（场次 {live_session_id}）")
            return None
        text = str(response.content).strip()[: self._max_length]
        if not text:
            return None
        await self._recaps.upsert_recap(
            live_session_id=int(live_session_id),
            started_at_ms=started_at,
            ended_at_ms=ended_at,
            recap_text=text,
        )
        logger.info(f"场次回顾已写入（场次 {live_session_id}）: {text[:60]}")
        await self._announce_written(int(live_session_id), started_at, ended_at, text)
        return text

    async def _announce_written(self, live_session_id: int, started_at_ms: int, ended_at_ms: int, text: str) -> None:
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
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                    recap_text=text,
                ),
                source="SessionRecapWriter",
            )
        except Exception as exc:  # noqa: BLE001 - 通知失败不推翻已落库的回顾
            logger.warning(f"streamer.recap.written 发布失败（场次 {live_session_id}，已忽略）: {exc}")

    async def backfill_missing(self, *, limit: int = 1) -> List[int]:
        """补写最近 ``limit`` 场"已结束却没有回顾"的场次（进程异常退出的善后），返回补写成功的场次主键。

        调用方拿到主键后可顺带补跑观众画像——收播整理没跑到回顾，画像多半也没跑到。
        """
        try:
            pending = await self._recaps.list_ended_sessions_without_recap(limit=limit)
        except Exception as exc:
            logger.warning(f"查询待补写场次失败: {exc}")
            return []
        written: List[int] = []
        for row in pending:
            live_session_id = int(row["live_session_id"])
            try:
                if await self.write_recap(live_session_id):
                    written.append(live_session_id)
            except Exception as exc:
                logger.warning(f"补写场次回顾失败（场次 {live_session_id}）: {exc}")
        return written

    # ------------------------------------------------------------------
    # 读取（注入段）
    # ------------------------------------------------------------------

    async def render_recent(self, *, count: int, exclude_session_id: Optional[int] = None) -> str:
        """渲染最近 ``count`` 场的回顾（最近一场在前），供开播后注入"上场回顾"段；没有返回空串。"""
        if count <= 0:
            return ""
        try:
            rows = await self._recaps.list_recent_recaps(limit=count, exclude_session_id=exclude_session_id)
        except Exception as exc:
            logger.warning(f"读取最近场次回顾失败: {exc}")
            return ""
        return "\n\n".join(format_recap(row) for row in rows)

    # ------------------------------------------------------------------
    # 原料
    # ------------------------------------------------------------------

    async def _collect_material(self, live_session_id: int, session_row: sqlite3.Row) -> str:
        """按场次主键把各表记录渲染成原料文本；每类各自封顶，整体超限时从尾部截断。"""
        sections: List[str] = []
        head = self._render_head(session_row)
        if head:
            sections.append(head)
        speech = await self._render_speech(live_session_id)
        if speech:
            sections.append("【主播自己说过的话（时间正序，节选）】\n" + speech)
        topics = await self._render_topics(live_session_id)
        if topics:
            sections.append("【观众话题轨迹】\n" + topics)
        games = await self._render_game_events(live_session_id)
        if games:
            sections.append("【游戏里的进展】\n" + games)
        audience = await self._render_audience(live_session_id)
        if audience:
            sections.append("【活跃观众与付费】\n" + audience)
        notebook = self._render_notebook()
        if notebook:
            sections.append("【主播备忘录（本场结束时的工作笔记全文）】\n" + notebook)
        # 只有场次头没有任何内容 = 空场次，不值得写回顾
        if len(sections) <= 1:
            return ""
        text = "\n\n".join(sections)
        return text[:_MATERIAL_MAX_CHARS]

    def _render_notebook(self) -> str:
        """收播时刻的备忘录全文快照（空/回调缺失 = 本场没写，整段省略）。"""
        if self._notebook_text_provider is None:
            return ""
        try:
            return (self._notebook_text_provider() or "").strip()
        except Exception as exc:
            logger.warning(f"读取备忘录快照失败，回顾原料省略该类: {exc}")
            return ""

    def _render_head(self, session_row: sqlite3.Row) -> str:
        started = int(session_row["started_at_ms"] or 0)
        ended = int(session_row["ended_at_ms"] or 0)
        parts: List[str] = []
        if started > 0:
            parts.append("日期 " + datetime.fromtimestamp(started / 1000).strftime("%Y-%m-%d %H:%M"))
        if started > 0 and ended > started:
            parts.append("时长 " + _duration_text(ended - started))
        title = session_row["title"] if "title" in session_row.keys() else None
        if title:
            parts.append(f"标题「{title}」")
        return ("【场次】" + "，".join(parts)) if parts else ""

    async def _render_speech(self, live_session_id: int) -> str:
        try:
            rows = await self._chat.list_recent_live_chat(
                live_session_id=live_session_id, limit=_SPEECH_ROWS, sender_role="assistant"
            )
        except Exception as exc:
            logger.warning(f"读取主播发言失败（场次 {live_session_id}）: {exc}")
            return ""
        lines = []
        for row in rows:
            content = str(row["content"] or "").strip().replace("\n", " ")
            if content:
                lines.append(f"{_clock(int(row['timestamp_ms']))} 主播: {content[:_SPEECH_LINE_CHARS]}")
        return "\n".join(lines)

    async def _render_topics(self, live_session_id: int) -> str:
        if self._topics is None:
            return ""
        try:
            rows = await self._topics.list_session_summaries(live_session_id=live_session_id, limit=_SUMMARY_ROWS)
        except Exception as exc:
            logger.warning(f"读取话题轨迹失败（场次 {live_session_id}）: {exc}")
            return ""
        lines = []
        last = ""
        for row in rows:
            summary = str(row["summary"] or "").strip()
            # 相邻重复的摘要句只留一条（弹幕没变时后台会原样再摘一次）
            if summary and summary != last:
                lines.append(f"{_clock(int(row['end_ms']))} {summary}")
                last = summary
        return "\n".join(lines)

    async def _render_game_events(self, live_session_id: int) -> str:
        if self._events is None:
            return ""
        try:
            rows = await self._events.list_session_game_events(live_session_id=live_session_id, limit=_GAME_EVENT_ROWS)
        except Exception as exc:
            logger.warning(f"读取游戏事件失败（场次 {live_session_id}）: {exc}")
            return ""
        lines = []
        for row in rows:
            message = str(row["message"] or "").strip().replace("\n", " ")
            if message:
                lines.append(f"{_clock(int(row['timestamp_ms']))} [{row['game']}] {message[:_SPEECH_LINE_CHARS]}")
        return "\n".join(lines)

    async def _render_audience(self, live_session_id: int) -> str:
        lines: List[str] = []
        try:
            speakers = await self._chat.list_session_speakers(live_session_id=live_session_id, limit=_SPEAKER_ROWS)
            if speakers:
                names = "、".join(
                    f"{row['user_name'] or '观众 #' + str(row['viewer_id'])}（{int(row['message_count'])} 条）"
                    for row in speakers
                )
                lines.append(f"弹幕最多的观众：{names}")
        except Exception as exc:
            logger.warning(f"读取活跃观众失败（场次 {live_session_id}）: {exc}")
        try:
            for row in await self._chat.list_session_gifts(live_session_id=live_session_id, limit=_PAYMENT_ROWS):
                lines.append(
                    f"{_clock(int(row['timestamp_ms']))} {row['user_name']} 送出 {row['gift_name']}×{int(row['quantity'] or 1)}"
                )
            for row in await self._chat.list_session_super_chats(live_session_id=live_session_id, limit=_PAYMENT_ROWS):
                amount = to_cny(int(row["total_price"] or 0), str(row["currency"] or ""))
                amount_text = f"（¥{amount:.0f}）" if amount else ""
                lines.append(
                    f"{_clock(int(row['timestamp_ms']))} {row['user_name']} SC{amount_text}：{str(row['message'] or '')[:60]}"
                )
            for row in await self._chat.list_session_guards(live_session_id=live_session_id, limit=_PAYMENT_ROWS):
                guard_name = GUARD_LEVEL_NAMES.get(int(row["guard_level"] or 0), "大航海")
                lines.append(f"{_clock(int(row['timestamp_ms']))} {row['user_name']} 开通{guard_name}")
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
