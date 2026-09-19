"""LLM 用量统计装配

用量趋势的日期对齐 / 缺失日期补零 / 逐日与按模型聚合，以及缓存命中率口径。
纯数据变换与仓储读取编排，不含 HTTP 语义。
"""

from datetime import date, datetime, time, timedelta
from typing import Any, List, Optional

from src.modules.dashboard.schemas.llm import (
    LLMUsageTrendModelPoint,
    LLMUsageTrendPoint,
    LLMUsageTrendsResponse,
)


def cache_hit_rate(cache_hit_tokens: float, cache_miss_tokens: float) -> Optional[float]:
    """由缓存命中/未命中 token 计算命中率（0-1）。

    两者同时为 0 视为上游从未上报缓存用量，返回 None 而非 0——
    落库口径把未上报记 0，0/0 无法与真实零命中区分，交给前端显示"未上报"。
    """
    total = cache_hit_tokens + cache_miss_tokens
    if total <= 0:
        return None
    return cache_hit_tokens / total


async def build_usage_trends(llm_repo: Optional[Any], days: int) -> LLMUsageTrendsResponse:
    """获取近 N 天逐日用量趋势（图表数据源；缺失日期补零对齐时间轴）。

    起点取窗口首日本地零点，与仓储按本地日分组的口径一致。
    """
    points: List[LLMUsageTrendPoint] = []
    model_points: List[LLMUsageTrendModelPoint] = []

    if llm_repo is not None:
        first_day = date.today() - timedelta(days=days - 1)
        start_ms = int(datetime.combine(first_day, time.min).timestamp() * 1000)
        trends = await llm_repo.llm_usage_daily_trends(start_ms=start_ms)

        daily_by_date = {row["day"]: row for row in trends.get("daily", [])}
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            day_str = day.isoformat()
            row = daily_by_date.get(day_str, {})
            hit = int(row.get("cache_hit_tokens", 0))
            miss = int(row.get("cache_miss_tokens", 0))
            points.append(
                LLMUsageTrendPoint(
                    date=day_str,
                    timestamp_ms=int(datetime.combine(day, time.min).timestamp() * 1000),
                    total_calls=int(row.get("total_calls", 0)),
                    prompt_tokens=int(row.get("prompt_tokens", 0)),
                    completion_tokens=int(row.get("completion_tokens", 0)),
                    total_tokens=int(row.get("total_tokens", 0)),
                    cost=float(row.get("cost", 0.0)),
                    cache_hit_tokens=hit,
                    cache_miss_tokens=miss,
                    cache_hit_rate=cache_hit_rate(hit, miss),
                )
            )

        for row in trends.get("by_model", []):
            hit = int(row.get("cache_hit_tokens", 0))
            miss = int(row.get("cache_miss_tokens", 0))
            model_points.append(
                LLMUsageTrendModelPoint(
                    date=str(row.get("day", "")),
                    model_name=str(row.get("model_name") or "unknown"),
                    total_calls=int(row.get("total_calls", 0)),
                    total_tokens=int(row.get("total_tokens", 0)),
                    cost=float(row.get("cost", 0.0)),
                    cache_hit_tokens=hit,
                    cache_miss_tokens=miss,
                    cache_hit_rate=cache_hit_rate(hit, miss),
                )
            )

    return LLMUsageTrendsResponse(days=days, points=points, model_points=model_points)
