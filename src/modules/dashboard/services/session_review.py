"""场次复盘聚合服务

把复盘"场次概况"节的机械聚合代码化：库表计数（live_chat / gifts /
super_chats / game_events）、LLM 请求统计窗口切片（llm_requests 统计列）、
诊断事件流按事件名常量过滤后的决策分布与链路时延。纯计算 + 仓储读取编排，
不含 HTTP 语义；全部指标来自结构化数据，不做日志文本匹配。

多场合并口径：计数与用量跨场累加；发言节奏/最长沉默逐场计算后取最值；
水位图时间序列按场拼接（点带 session_id，前端在场边界断线留空隙）。
观众构成、游戏明细、流程单执行属单场语义，合并视图不产出。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from src.modules.dashboard.schemas.review import (
    DecisionLatency,
    GameEventItem,
    LLMDomainStats,
    ReviewChips,
    ReviewDecisions,
    ReviewGame,
    ReviewLLM,
    ReviewSessionBrief,
    ReviewSpeech,
    ReviewViewers,
    SessionReviewResponse,
    ViewerSpeakerItem,
    WaterlinePoint,
    WaterlineSeries,
)
from src.modules.dashboard.services.llm_stats import cache_hit_rate
from src.modules.dashboard.services.session_events import SessionEventReader
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.types.currency import to_cny

if TYPE_CHECKING:
    from src.modules.events.event_history import EventRecord

logger = get_logger("SessionReviewService")

# 水位图单 profile 采样点上限：超限按等步长抽样保形（一场数千笔请求时
# 折线形状不变，载荷可控）
_MAX_WATERLINE_POINTS = 2_000

# 游戏明细单场返回上限（超出截断并以 truncated 标记）
_MAX_GAME_ITEMS = 100


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


def longest_silence(speech_ts: Sequence[int], start_ms: int, end_ms: int) -> Tuple[int, Optional[int]]:
    """最长沉默（毫秒）与其起点：相邻发言时刻的最大间隔，场次首尾计入边界。

    场内零发言时整场时长即沉默（起点=场次开始）；单点发言时取两端到边界的
    较大者。沉默起点 = 区间左端（上一条发言或场次开始），供复盘页跳转回看。
    """
    if end_ms <= start_ms:
        return 0, None
    bounds = [start_ms, *sorted(speech_ts), end_ms]
    best_gap = 0
    best_start: Optional[int] = None
    for a, b in zip(bounds, bounds[1:], strict=False):
        if b - a > best_gap:
            best_gap = b - a
            best_start = a
    return best_gap, best_start


def longest_silence_ms(speech_ts: Sequence[int], start_ms: int, end_ms: int) -> int:
    """最长沉默（毫秒）：相邻发言时刻的最大间隔，场次首尾计入边界。"""
    return longest_silence(speech_ts, start_ms, end_ms)[0]


def cny_total(by_currency: Dict[str, int]) -> Optional[float]:
    """按币种分组的金额原值 → 可折算人民币合计（元）。

    银瓜子等免费/未知币种 ``to_cny`` 返回 None，自然不计入；全部不可折算时
    返回 None（与"未发生付费"区分开，避免 0 元误导）。
    """
    total: Optional[float] = None
    for currency, amount in by_currency.items():
        cny = to_cny(int(amount), currency)
        if cny is None:
            continue
        total = (total or 0.0) + cny
    return total


def _to_brief(row: Dict[str, object], now_ms: Optional[int] = None) -> ReviewSessionBrief:
    """live_sessions 行 → 基本信息。

    进行中场次（ended 缺失）以当前时刻为聚合窗口右端：时长取
    ``now_ms - started``，保证聚合窗与派生指标（速率/沉默）不为零宽。
    now_ms 由调用方注入以便测试；缺省取系统当前毫秒。
    """
    started = int(row["started_at_ms"])  # type: ignore[index]
    ended_raw = row.get("ended_at_ms")
    ended = int(ended_raw) if ended_raw is not None else None
    right = ended if ended is not None else (now_ms if now_ms is not None else int(time.time() * 1000))
    return ReviewSessionBrief(
        live_session_id=int(row["id"]),  # type: ignore[index]
        title=row.get("title"),  # type: ignore[arg-type]
        source=str(row.get("source") or "manual"),
        platform=str(row.get("platform") or ""),
        started_at_ms=started,
        ended_at_ms=ended,
        duration_ms=max(0, right - started),
        heat=int(row.get("heat") or 0),  # type: ignore[arg-type]
        viewer_count=int(row.get("viewer_count") or 0),  # type: ignore[arg-type]
        audience_total=int(row.get("audience_total") or 0),  # type: ignore[arg-type]
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
        recap_repo: Optional[object] = None,
        slow_thresholds: Optional[Dict[str, int]] = None,
        reviews_dir: Optional[Path] = None,
    ) -> None:
        self.chat_repo = chat_repo
        self.llm_repo = llm_repo
        self.event_repo = event_repo
        self.event_reader = event_reader
        self.recap_repo = recap_repo
        # 每 profile 慢调用阈值（毫秒；来自 [llm_profiles.<name>].slow_threshold_ms，
        # 与运行期慢调用告警同源同值），未配置的 profile 落全局缺省
        self.slow_thresholds = slow_thresholds or {}
        # 复盘可视化页目录（logs/reviews）；未装配时报告字段恒为 None
        self.reviews_dir = reviews_dir
        self.default_slow_threshold_ms = 15_000

    # ------------------------------------------------------------------ #
    # 对外入口                                                            #
    # ------------------------------------------------------------------ #

    async def build(self, session_rows: List[Dict[str, object]]) -> SessionReviewResponse:
        """按场次行列表聚合（多行即多选合并视图）。"""
        # 进行中场次的窗口右端统一取同一时刻，保证各场次口径一致
        now_ms = int(time.time() * 1000)
        briefs = [_to_brief(row, now_ms=now_ms) for row in session_rows]
        windows = [
            (b.started_at_ms, b.ended_at_ms if b.ended_at_ms is not None else b.started_at_ms + b.duration_ms)
            for b in briefs
        ]
        session_ids = [b.live_session_id for b in briefs]

        window_start = min((b.started_at_ms for b in briefs), default=0)
        window_end = max(
            (b.ended_at_ms if b.ended_at_ms is not None else b.started_at_ms + b.duration_ms for b in briefs),
            default=0,
        )

        chips, speech = await self._chat_section(session_ids, briefs, window_start, window_end)
        llm, waterline = await self._llm_section(windows, session_ids)
        decisions, degraded = await self._decision_section(session_ids, windows)
        game = await self._game_section(session_ids)
        await self._fill_recaps(briefs)
        # 观众构成/游戏明细属单场语义，合并视图不产出
        viewers = await self._viewers_section(briefs[0]) if len(briefs) == 1 else None

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
            viewers=viewers,
            report_file=self._find_report_file(briefs),
            degraded=degraded,
        )

    def _find_report_file(self, briefs: List[ReviewSessionBrief]) -> Optional[str]:
        """单场视图的复盘可视化页文件名（logs/reviews 下 session-{id}- 前缀）。

        多选合并视图或目录未装配/报告未生成时 None；同前缀多文件按文件名取首个。
        """
        if self.reviews_dir is None or len(briefs) != 1:
            return None
        pattern = f"session-{briefs[0].live_session_id}-*.html"
        try:
            matches = sorted(self.reviews_dir.glob(pattern))
        except OSError:
            return None
        return matches[0].name if matches else None

    async def _fill_recaps(self, briefs: List[ReviewSessionBrief]) -> None:
        """把收播总结按场次主键回填到 brief（缺回顾的场次保持 None）。"""
        if self.recap_repo is None or not briefs:
            return
        try:
            rows = await self.recap_repo.list_recaps_by_session_ids(  # type: ignore[attr-defined]
                live_session_ids=[b.live_session_id for b in briefs]
            )
        except Exception as exc:  # noqa: BLE001 - 回顾缺失不阻断聚合，按无数据降级
            logger.warning(f"读取场次回顾失败，复盘页总结面板降级: {exc}", exc=True)
            return
        by_id = {int(row["live_session_id"]): row for row in rows}
        for brief in briefs:
            row = by_id.get(brief.live_session_id)
            if row is None:
                continue
            brief.recap_text = str(row["recap_text"] or "")
            brief.recap_created_at_ms = int(row["created_at_ms"]) if row["created_at_ms"] else None

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
            gift_total_cny=cny_total(dict(summary.get("gift_total_by_currency") or {})),
            super_chat_total_cny=cny_total(dict(summary.get("super_chat_total_by_currency") or {})),
        )

        speech_rows = await self.chat_repo.list_assistant_speech_by_session(  # type: ignore[attr-defined]
            live_session_ids=session_ids
        )
        # 节奏/沉默逐场计算后取最值合并（跨场拼接会把场间空窗误算成沉默）
        total_speech = 0
        worst_silence: Optional[int] = None
        worst_silence_start: Optional[int] = None
        best_rate: Optional[float] = None
        by_session: Dict[int, List[int]] = {}
        for row in speech_rows:
            by_session.setdefault(int(row["live_session_id"]), []).append(int(row["timestamp_ms"]))
        for brief in briefs:
            ts_list = sorted(by_session.get(brief.live_session_id, []))
            total_speech += len(ts_list)
            s_start = brief.started_at_ms
            s_end = brief.ended_at_ms if brief.ended_at_ms is not None else window_end
            silence, silence_start = longest_silence(ts_list, s_start, s_end)
            if worst_silence is None or silence > worst_silence:
                worst_silence = silence
                worst_silence_start = silence_start
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
            longest_silence_start_ms=worst_silence_start,
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
        # 时延样本带开口时刻，最慢一次的时刻供复盘页锚定跳转回看
        latencies: List[Tuple[int, int]] = []
        for rid, spoken_at in speech_at.items():
            decided_at = verdict_at.get(rid)
            if decided_at is not None and spoken_at >= decided_at:
                latencies.append((spoken_at - decided_at, spoken_at))
        latencies.sort()
        decisions.latency = DecisionLatency(
            sample_count=len(latencies),
            p50_ms=int(percentile([v for v, _ in latencies], 0.5)) if latencies else None,
            p90_ms=int(percentile([v for v, _ in latencies], 0.9)) if latencies else None,
            max_ms=latencies[-1][0] if latencies else None,
            max_at_ms=latencies[-1][1] if latencies else None,
        )
        return decisions, degraded

    async def _game_section(self, session_ids: List[int]) -> ReviewGame:
        if self.event_repo is None or not session_ids:
            return ReviewGame()
        rows = await self.event_repo.count_game_events_by_type(live_session_ids=session_ids)  # type: ignore[attr-defined]
        by_type = {str(row["event_type"]): int(row["n"]) for row in rows}
        game = ReviewGame(total=sum(by_type.values()), by_type=by_type)
        # 明细列表只服务单场视图（逐条锚定跳转回看）；合并视图保持空
        if len(session_ids) == 1:
            detail_rows = await self.event_repo.list_session_game_events(  # type: ignore[attr-defined]
                live_session_id=session_ids[0], limit=_MAX_GAME_ITEMS
            )
            game.items = [
                GameEventItem(
                    game=str(row["game"]),
                    event_type=str(row["event_type"]),
                    message=str(row["message"] or ""),
                    timestamp_ms=int(row["timestamp_ms"]),
                )
                for row in detail_rows
            ]
            game.truncated = game.total > len(game.items)
        return game

    async def _viewers_section(self, brief: ReviewSessionBrief) -> Optional[ReviewViewers]:
        """单场观众构成：发言观众数 / 新·回头观众 / 发言条数 top。"""
        if self.chat_repo is None:
            return None
        try:
            cohorts = await self.chat_repo.count_session_viewer_cohorts(  # type: ignore[attr-defined]
                live_session_id=brief.live_session_id, started_at_ms=brief.started_at_ms
            )
            speaker_rows = await self.chat_repo.list_session_speakers(  # type: ignore[attr-defined]
                live_session_id=brief.live_session_id, limit=5
            )
        except Exception as exc:  # noqa: BLE001 - 观众侧缺失不阻断聚合，按无数据降级
            logger.warning(f"读取观众构成失败（场次 {brief.live_session_id}）: {exc}", exc=True)
            return None
        active_total = int(cohorts.get("active_total") or 0)
        new_viewers = int(cohorts.get("new_viewers") or 0)
        return ReviewViewers(
            active_total=active_total,
            new_viewers=new_viewers,
            returning_viewers=max(0, active_total - new_viewers),
            top_speakers=[
                ViewerSpeakerItem(
                    viewer_id=int(row["viewer_id"]),
                    platform=str(row["platform"] or ""),
                    user_name=str(row["user_name"] or ""),
                    message_count=int(row["message_count"] or 0),
                )
                for row in speaker_rows
            ],
        )


__all__ = [
    "SessionReviewAggregator",
    "cny_total",
    "longest_silence",
    "longest_silence_ms",
    "percentile",
]
