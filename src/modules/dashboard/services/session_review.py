"""场次复盘聚合服务

把复盘"场次概况"节的机械聚合代码化：库表计数（live_chat / gifts /
super_chats / game_events）、LLM 请求统计窗口切片（llm_requests 统计列）、
诊断事件流按事件名常量过滤后的决策分布与链路时延。纯计算 + 仓储读取编排，
不含 HTTP 语义；全部指标来自结构化数据，不做日志文本匹配。

多场合并口径：计数与用量跨场累加；发言节奏/最长沉默逐场计算后取最值；
水位图时间序列按场拼接（点带 session_id，前端在场边界断线留空隙）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from src.modules.dashboard.schemas.review import (
    DecisionLatency,
    LLMDomainStats,
    ReviewChips,
    ReviewDecisions,
    ReviewGame,
    ReviewLLM,
    ReviewSessionBrief,
    ReviewSpeech,
    SessionReviewResponse,
    WaterlinePoint,
    WaterlineSeries,
)
from src.modules.dashboard.services.llm_stats import cache_hit_rate
from src.modules.dashboard.services.session_events import SessionEventReader
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.events.event_history import EventRecord

logger = get_logger("SessionReviewService")

# 水位图单 profile 采样点上限：超限按等步长抽样保形（一场数千笔请求时
# 折线形状不变，载荷可控）
_MAX_WATERLINE_POINTS = 2_000


def percentile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    """升序样本的 q 分位（0<=q<=1，线性插值）；空样本返回 None。"""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return float(sorted_values[low]) * (1 - frac) + float(sorted_values[high]) * frac


def longest_silence_ms(speech_ts: Sequence[int], start_ms: int, end_ms: int) -> int:
    """最长沉默（毫秒）：相邻发言时刻的最大间隔，场次首尾计入边界。

    场内零发言时整场时长即沉默；单点发言时取两端到边界的较大者。
    """
    bounds = [start_ms, *sorted(speech_ts), end_ms]
    return max((b - a) for a, b in zip(bounds, bounds[1:], strict=False)) if end_ms > start_ms else 0


def _to_brief(row: Dict[str, object]) -> ReviewSessionBrief:
    """live_sessions 行 → 基本信息（ended 缺失按进行中处理，时长截到现在由调用方保证）。"""
    started = int(row["started_at_ms"])  # type: ignore[index]
    ended_raw = row.get("ended_at_ms")
    ended = int(ended_raw) if ended_raw is not None else None
    return ReviewSessionBrief(
        live_session_id=int(row["id"]),  # type: ignore[index]
        title=row.get("title"),  # type: ignore[arg-type]
        source=str(row.get("source") or "manual"),
        platform=str(row.get("platform") or ""),
        started_at_ms=started,
        ended_at_ms=ended,
        duration_ms=max(0, (ended if ended is not None else started) - started),
        heat=int(row.get("heat") or 0),  # type: ignore[arg-type]
        viewer_count=int(row.get("viewer_count") or 0),  # type: ignore[arg-type]
    )


class SessionReviewAggregator:
    """复盘聚合编排：依赖仓储注入，逐数据源取数后拼一份聚合响应。"""

    def __init__(
        self,
        *,
        chat_repo: Optional[object] = None,
        llm_repo: Optional[object] = None,
        event_repo: Optional[object] = None,
        event_reader: Optional[SessionEventReader] = None,
        slow_thresholds: Optional[Dict[str, int]] = None,
    ) -> None:
        self.chat_repo = chat_repo
        self.llm_repo = llm_repo
        self.event_repo = event_repo
        self.event_reader = event_reader
        # 每 profile 慢调用阈值（毫秒；来自 [llm_profiles.<name>].slow_threshold_ms，
        # 与运行期慢调用告警同源同值），未配置的 profile 落全局缺省
        self.slow_thresholds = slow_thresholds or {}
        self.default_slow_threshold_ms = 15_000

    # ------------------------------------------------------------------ #
    # 对外入口                                                            #
    # ------------------------------------------------------------------ #

    async def build(self, session_rows: List[Dict[str, object]]) -> SessionReviewResponse:
        """按场次行列表聚合（多行即多选合并视图）。"""
        briefs = [_to_brief(row) for row in session_rows]
        windows = [
            (b.started_at_ms, b.ended_at_ms if b.ended_at_ms is not None else b.started_at_ms + b.duration_ms)
            for b in briefs
        ]
        session_ids = [b.live_session_id for b in briefs]

        window_start = min((b.started_at_ms for b in briefs), default=0)
        window_end = max((b.ended_at_ms if b.ended_at_ms is not None else b.started_at_ms for b in briefs), default=0)

        chips, speech = await self._chat_section(session_ids, briefs, window_start, window_end)
        llm, waterline = await self._llm_section(windows, session_ids)
        decisions, degraded = await self._decision_section(session_ids, windows)
        game = await self._game_section(session_ids)

        return SessionReviewResponse(
            sessions=briefs,
            window_start_ms=window_start,
            window_end_ms=window_end,
            chips=chips,
            speech=speech,
            decisions=decisions,
            llm=llm,
            waterline=waterline,
            game=game,
            degraded=degraded,
        )

    # ------------------------------------------------------------------ #
    # 各数据源分节                                                        #
    # ------------------------------------------------------------------ #

    async def _chat_section(
        self,
        session_ids: List[int],
        briefs: List[ReviewSessionBrief],
        window_start: int,
        window_end: int,
    ) -> Tuple[ReviewChips, ReviewSpeech]:
        chips = ReviewChips()
        speech = ReviewSpeech()
        if self.chat_repo is None or not session_ids:
            return chips, speech

        summary = await self.chat_repo.summarize_sessions(live_session_ids=session_ids)  # type: ignore[attr-defined]
        chips = ReviewChips(
            danmaku_count=int(summary.get("danmaku_count") or 0),
            enter_count=int(summary.get("enter_count") or 0),
            gift_count=int(summary.get("gift_count") or 0),
            super_chat_count=int(summary.get("super_chat_count") or 0),
            gift_total_price=int(summary.get("gift_total_price") or 0),
            super_chat_total_price=int(summary.get("super_chat_total_price") or 0),
        )

        speech_rows = await self.chat_repo.list_assistant_speech_by_session(  # type: ignore[attr-defined]
            live_session_ids=session_ids
        )
        # 节奏/沉默逐场计算后取最值合并（跨场拼接会把场间空窗误算成沉默）
        total_speech = 0
        worst_silence: Optional[int] = None
        best_rate: Optional[float] = None
        by_session: Dict[int, List[int]] = {}
        for row in speech_rows:
            by_session.setdefault(int(row["live_session_id"]), []).append(int(row["timestamp_ms"]))
        for brief in briefs:
            ts_list = sorted(by_session.get(brief.live_session_id, []))
            total_speech += len(ts_list)
            s_start = brief.started_at_ms
            s_end = brief.ended_at_ms if brief.ended_at_ms is not None else window_end
            silence = longest_silence_ms(ts_list, s_start, s_end)
            if worst_silence is None or silence > worst_silence:
                worst_silence = silence
            minutes = max(s_end - s_start, 1) / 60_000
            rate = len(ts_list) / minutes
            if best_rate is None or rate > best_rate:
                best_rate = rate
        speech = ReviewSpeech(
            count=total_speech,
            rate_per_min=round(total_speech / (max(window_end - window_start, 1) / 60_000), 2)
            if window_end > window_start
            else None,
            longest_silence_ms=worst_silence,
        )
        return chips, speech

    async def _llm_section(
        self, windows: Sequence[Tuple[int, int]], session_ids: List[int]
    ) -> Tuple[ReviewLLM, List[WaterlineSeries]]:
        review_llm = ReviewLLM()
        series: List[WaterlineSeries] = []
        if self.llm_repo is None or not windows:
            return review_llm, series

        stats = await self.llm_repo.review_window_stats(  # type: ignore[attr-defined,union-attr]
            windows=list(windows), slow_thresholds=self.slow_thresholds
        )
        overall_row, profile_rows, series_rows = stats
        review_llm.overall = self._domain_from_row(overall_row, profile_name="")
        review_llm.by_profile = [
            self._domain_from_row(row, profile_name=str(row.get("profile_name") or "")) for row in profile_rows
        ]

        points_by_profile: Dict[str, List[WaterlinePoint]] = {}
        for row in series_rows:
            profile = str(row.get("profile_name") or "")
            # repo 返回的 session_id 是窗口序号，映射回真实场次主键
            window_idx = int(row.get("session_id") or 0)
            points_by_profile.setdefault(profile, []).append(
                WaterlinePoint(
                    timestamp_ms=int(row["timestamp_ms"]),
                    prompt_tokens=int(row["prompt_tokens"] or 0),
                    session_id=session_ids[window_idx] if 0 <= window_idx < len(session_ids) else 0,
                )
            )
        for profile in sorted(points_by_profile):
            points = points_by_profile[profile]
            if len(points) > _MAX_WATERLINE_POINTS:
                stride = len(points) / _MAX_WATERLINE_POINTS
                points = [points[int(i * stride)] for i in range(_MAX_WATERLINE_POINTS)]
            series.append(WaterlineSeries(profile_name=profile, points=points))
        return review_llm, series

    @staticmethod
    def _domain_from_row(row: Dict[str, object], profile_name: str) -> LLMDomainStats:
        hit = int(row.get("cache_hit_tokens") or 0)  # type: ignore[arg-type]
        miss = int(row.get("cache_miss_tokens") or 0)  # type: ignore[arg-type]
        return LLMDomainStats(
            profile_name=profile_name,
            calls=int(row.get("calls") or 0),  # type: ignore[arg-type]
            failed_calls=int(row.get("failed_calls") or 0),  # type: ignore[arg-type]
            slow_calls=int(row.get("slow_calls") or 0),  # type: ignore[arg-type]
            prompt_tokens=int(row.get("prompt_tokens") or 0),  # type: ignore[arg-type]
            completion_tokens=int(row.get("completion_tokens") or 0),  # type: ignore[arg-type]
            total_tokens=int(row.get("total_tokens") or 0),  # type: ignore[arg-type]
            cache_hit_tokens=hit,
            cache_miss_tokens=miss,
            cache_hit_rate=cache_hit_rate(hit, miss),
            cost=float(row.get("cost") or 0.0),  # type: ignore[arg-type]
            max_prompt_tokens=int(row.get("max_prompt_tokens") or 0),  # type: ignore[arg-type]
        )

    async def _decision_section(
        self, session_ids: List[int], windows: Sequence[Tuple[int, int]]
    ) -> Tuple[ReviewDecisions, bool]:
        decisions = ReviewDecisions()
        if self.event_reader is None or not session_ids:
            return decisions, False

        collected = await asyncio.to_thread(
            lambda: [
                self.event_reader.collect(  # type: ignore[union-attr]
                    live_session_id=sid,
                    started_at_ms=start,
                    ended_at_ms=end,
                )
                for sid, (start, end) in zip(session_ids, windows, strict=True)
            ]
        )
        records: List["EventRecord"] = []
        degraded = False
        for window in collected:
            records.extend(window.records)
            degraded = degraded or window.days_pruned

        distribution: Dict[str, int] = {}
        verdict_at: Dict[str, int] = {}
        speech_at: Dict[str, int] = {}
        for record in records:
            data = record.data if isinstance(record.data, dict) else {}
            if record.type == CoreEvents.PLANNER_DECISION:
                trigger = str(data.get("trigger_reason") or "unknown")
                distribution[trigger] = distribution.get(trigger, 0) + 1
                if data.get("silent_reason"):
                    decisions.silent_count += 1
                if data.get("error"):
                    decisions.failed_count += 1
                continue
            # 决策定稿→开口：verdict 在表达生成之前发出，与 speech 按 round_id
            # 关联（轮内事件共用关联键，不依赖事件流先后顺序）
            round_id = str(data.get("round_id") or "")
            if not round_id:
                continue
            if record.type == CoreEvents.PLANNER_VERDICT:
                verdict_at[round_id] = int(record.timestamp_ms)
            elif record.type == CoreEvents.STREAMER_SPEECH:
                speech_at[round_id] = int(record.timestamp_ms)
        decisions.total = sum(distribution.values())
        decisions.distribution = dict(sorted(distribution.items(), key=lambda kv: -kv[1]))
        latencies: List[int] = []
        for rid, spoken_at in speech_at.items():
            decided_at = verdict_at.get(rid)
            if decided_at is not None and spoken_at >= decided_at:
                latencies.append(spoken_at - decided_at)
        latencies.sort()
        decisions.latency = DecisionLatency(
            sample_count=len(latencies),
            p50_ms=int(percentile(latencies, 0.5)) if latencies else None,
            p90_ms=int(percentile(latencies, 0.9)) if latencies else None,
            max_ms=latencies[-1] if latencies else None,
        )
        return decisions, degraded

    async def _game_section(self, session_ids: List[int]) -> ReviewGame:
        if self.event_repo is None or not session_ids:
            return ReviewGame()
        rows = await self.event_repo.count_game_events_by_type(live_session_ids=session_ids)  # type: ignore[attr-defined]
        by_type = {str(row["event_type"]): int(row["n"]) for row in rows}
        return ReviewGame(total=sum(by_type.values()), by_type=by_type)


__all__ = [
    "SessionReviewAggregator",
    "longest_silence_ms",
    "percentile",
]
