"""场次概览：下播后给这一场写一段"发生了什么"（给人看，不进记忆）。

与下播整理（``memory/settle.py``）是两回事：

- 整理写的是跨场记忆——承诺表与场次档案行，那是要进主播记忆、要在下次开播被
  想起的，模型写错要有人纠错；
- 这里只写 ``live_sessions.overview`` 一列：给人快速回看与查找这场的内容。
  不进任何记忆表，不进回忆索引，主播说话时看不到它。

原料是后台循环本来就在写的窗口摘要（``timeline_summary``，每分钟一段"观众在聊
什么"）与游戏里程碑事件（``game_events``）。这两样都不含观众昵称，因此不依赖
遮名集合，可以排在下播清理之后跑。

失败只记日志：概览缺了不影响任何账本，也不挡下一场的开场。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from src.modules.background import BackgroundTaskRegistry
from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.storage.models.sessions import LiveSession
from src.modules.storage.models.topics import TimelineSummaryEntry
from src.modules.storage.repos.events import OVERVIEW_HIGH_SIGNAL_EVENT_TYPES, TASK_SUCCEEDED_EVENT, sample_game_events
from src.modules.utils.time_utils import now_ms

logger = get_logger("SessionOverviewWriter")

# 用途 profile：与后台摘要同一个（短文本生成；不新增 profile，不动配置里的封闭集合）
_OVERVIEW_PROFILE = "summary"

# 系统提示词模板名（src/agents/streamer/prompts/session_overview_system.md）
_OVERVIEW_TEMPLATE = "session_overview_system"

# 窗口摘要取多少段（每段一分钟，一场三小时约 180 段）
_SUMMARY_SEGMENTS_MAX = 600

# 场次概览注入的游戏事件上限（跨全时段分阶段抽样后的条数，兼顾前中后段主线与输入预算）
_GAME_EVENTS_MAX = 120
# 兼容未实现分桶采样读口的仓储桩时，按较大上限读取后再本地分阶段采样
_GAME_EVENTS_FALLBACK_SCAN = 500

# 任务面板里的任务名（与整理任务区分开）
_TASK_ID_PREFIX = "session-overview"


def _task_id(live_session_id: int) -> str:
    return f"{_TASK_ID_PREFIX}-{live_session_id}"


def _format_clock(ms: int) -> str:
    """毫秒 → HH:MM（本地时区）。"""
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _format_day_clock(ms: int) -> str:
    """毫秒 → MM-DD HH:MM（本地时区）。"""
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


class SessionOverviewWriter:
    """下播后给一场写一句概览：取料 → 一次模型调用 → 写 ``live_sessions.overview``。"""

    def __init__(
        self,
        *,
        sessions_repo: Any,
        topic_repo: Any,
        event_repo: Optional[Any] = None,
        llm_service: Any,
        prompt_manager: PromptManager,
        task_registry: Optional[BackgroundTaskRegistry] = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        """仓储、模型与提示词由主播 Agent 构造时注入。

        event_repo 缺失时原料里没有游戏这一路，概览只按话题摘要写；task_registry
        是后台任务登记处（缺省 None 时概览不在任务面板里显示，其余行为一致）。
        """
        self._sessions = sessions_repo
        self._topics = topic_repo
        self._events = event_repo
        self._llm = llm_service
        self._prompt_manager = prompt_manager
        self._task_registry = task_registry
        self._clock = clock

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    async def write_overview(self, live_session_id: int) -> bool:
        """给这一场写概览；成功返回 True（没料可写或失败返回 False）。"""
        session = await self._sessions.get_live_session(live_session_id=live_session_id)
        if session is None:
            return False
        summaries = await self._topics.list_session_summaries(
            live_session_id=live_session_id, limit=_SUMMARY_SEGMENTS_MAX
        )
        events = await self._load_game_events(live_session_id)
        delegations = await self._load_succeeded_delegations(live_session_id)
        if not summaries and not events and not delegations:
            # 没有话题摘要也没有游戏事实记录：这场没留下可概括的东西，不白花一次模型调用
            return False

        if self._task_registry is not None:
            await self._task_registry.begin(
                _task_id(live_session_id), f"场次概览 #{live_session_id}", progress_text="取料中"
            )
        try:
            prompt = self._build_prompt(session, summaries, events, delegations)
            system = self._prompt_manager.render(_OVERVIEW_TEMPLATE)
            response = await self._llm.generate(prompt, profile=_OVERVIEW_PROFILE, system=system)
            content = getattr(response, "content", None)
            if not getattr(response, "ok", False) or not content:
                raise RuntimeError("模型调用失败或返回空内容")
            overview = str(content).strip()
            if not overview:
                raise RuntimeError("模型返回空概要")
            written = await self._sessions.update_live_session_overview(
                live_session_id=live_session_id, overview=overview, updated_at_ms=int(self._clock())
            )
            if not written:
                raise RuntimeError("场次行已不在，写概要没落处")
        except Exception as exc:  # noqa: BLE001 - 概览失败只记日志，不牵连任何账本
            if self._task_registry is not None:
                await self._task_registry.finish(_task_id(live_session_id), success=False, failure_reason=str(exc))
            raise
        if self._task_registry is not None:
            await self._task_registry.finish(_task_id(live_session_id), success=True)
        return True

    # ------------------------------------------------------------------
    # 取料与拼装
    # ------------------------------------------------------------------

    async def _load_game_events(self, live_session_id: int) -> List[Any]:
        """跨全时段分阶段读取游戏事件，避免长场次前半段主线被只取末尾事件截断。"""
        if self._events is None:
            return []
        try:
            sampled_fn = getattr(self._events, "list_session_game_events_sampled", None)
            if callable(sampled_fn):
                return list(
                    await sampled_fn(
                        live_session_id=live_session_id,
                        limit=_GAME_EVENTS_MAX,
                        high_signal_types=OVERVIEW_HIGH_SIGNAL_EVENT_TYPES,
                    )
                )
            rows = list(
                await self._events.list_session_game_events(
                    live_session_id=live_session_id,
                    limit=_GAME_EVENTS_FALLBACK_SCAN,
                )
            )
            return sample_game_events(
                rows,
                limit=_GAME_EVENTS_MAX,
                high_signal_types=OVERVIEW_HIGH_SIGNAL_EVENT_TYPES,
            )
        except Exception as exc:  # noqa: BLE001 - 少了游戏这一路就只按话题摘要写
            logger.warning(f"场次 #{live_session_id} 读取游戏事件失败，概览将只按话题摘要生成: {exc}", exc=True)
            return []

    async def _load_succeeded_delegations(self, live_session_id: int) -> List[Any]:
        """读取本场成功交付的委派终态记录，补足概览事实源中缺失的完成结果。"""
        if self._events is None:
            return []
        list_fn = getattr(self._events, "list_session_delegations", None)
        if not callable(list_fn):
            return []
        try:
            records = await list_fn(live_session_id=live_session_id)
        except Exception as exc:  # noqa: BLE001 - 委派读不到时只按游戏事件与话题摘要生成概览
            logger.warning(f"场次 #{live_session_id} 读取委派交付记录失败，概览将跳过委派项: {exc}", exc=True)
            return []
        return [
            item
            for item in records
            if str(getattr(item, "status", "") or "") == "succeeded" and int(getattr(item, "ended_ms", 0) or 0) > 0
        ]

    def _build_prompt(
        self,
        session: LiveSession,
        summaries: List[TimelineSummaryEntry],
        events: List[Any],
        delegations: Optional[List[Any]] = None,
    ) -> str:
        """把这一场的原料拼成给模型的一段话（不含观众昵称）。"""
        parts: List[str] = []
        started = _format_day_clock(int(session.started_at_ms))
        if session.ended_at_ms is not None:
            ended = _format_day_clock(int(session.ended_at_ms))
            duration_ms = int(session.ended_at_ms) - int(session.started_at_ms)
            parts.append(f"【这场的基本情况】\n开播 {started}，下播 {ended}，时长约 {duration_ms // 60000} 分钟。")
        else:
            parts.append(f"【这场的基本情况】\n开播 {started}，还没下播。")

        if summaries:
            lines = [f"{_format_clock(int(item.end_ms))} {item.summary}" for item in summaries if item.summary]
            parts.append("【按时间顺序的观众话题摘要】\n" + "\n".join(lines))

        # 合并游戏进展事件与成功交付的委派终态按时间正序排列，避免只看到失败里程碑而把已跑通的成果写反
        game_items: List[Tuple[int, int, str]] = []
        report_messages: List[str] = []
        for idx, event in enumerate(events):
            ts = int(getattr(event, "timestamp_ms", 0) or 0)
            event_type = str(getattr(event, "event_type", "") or "")
            msg = " ".join(str(getattr(event, "message", "") or "").split()) or event_type
            if event_type == "report":
                report_messages.append(msg)
            if event_type == TASK_SUCCEEDED_EVENT and not msg.startswith(("完成", "交付", "委派")):
                line_text = f"完成：{msg}"
            else:
                line_text = msg
            game_items.append((ts, idx, f"{_format_clock(ts)} {line_text}"))

        base_order = len(game_items)
        for offset, item in enumerate(delegations or ()):
            ended_ms = int(getattr(item, "ended_ms", 0) or 0)
            instruction = " ".join(str(getattr(item, "instruction", "") or "").split())
            summary = " ".join(str(getattr(item, "summary", "") or "").split()).removeprefix("delivery:").strip()
            # 过短的委派摘要容易被无关 report 误子串命中：仅完全相等或长度至少 6 字时才按子串去重
            if summary and any(summary == rep or (len(summary) >= 6 and summary in rep) for rep in report_messages):
                continue
            if instruction and summary:
                text = f"委派完成「{instruction}」：{summary}"
            elif summary:
                text = f"委派完成：{summary}"
            elif instruction:
                text = f"委派完成「{instruction}」"
            else:
                continue
            game_items.append((ended_ms, base_order + offset, f"{_format_clock(ended_ms)} {text}"))

        if game_items:
            game_items.sort(key=lambda entry: (entry[0], entry[1]))
            parts.append("【游戏里发生的事】\n" + "\n".join(line for _, _, line in game_items))
        return "\n\n".join(parts)


__all__ = ["SessionOverviewWriter"]
