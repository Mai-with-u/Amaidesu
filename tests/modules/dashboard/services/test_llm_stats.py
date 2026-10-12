"""llm_stats 服务层边界单测。

覆盖用量趋势的缺失日期补零 / 首尾日期边界，与缓存命中率口径（零除 / 全命中 / 部分）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import pytest

from src.modules.dashboard.services.llm_stats import build_usage_trends, cache_hit_rate


# ---------------------------------------------------------------------------
# cache_hit_rate
# ---------------------------------------------------------------------------


class TestCacheHitRate:
    """命中率口径：未上报返回 None，全命中为 1，部分命中按比例。"""

    def test_双零视为未上报返回None(self) -> None:
        assert cache_hit_rate(0, 0) is None

    def test_全命中返回1(self) -> None:
        assert cache_hit_rate(100, 0) == 1.0

    def test_全未命中返回0(self) -> None:
        assert cache_hit_rate(0, 100) == 0.0

    def test_部分命中按比例(self) -> None:
        assert cache_hit_rate(30, 70) == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# build_usage_trends
# ---------------------------------------------------------------------------


class _FakeRepo:
    """仓储桩：按调用记录 start_ms，返回预置趋势数据。"""

    def __init__(self, trends: dict[str, Any]) -> None:
        self.trends = trends
        self.calls: list[dict[str, Any]] = []

    async def llm_usage_daily_trends(self, start_ms: int) -> dict[str, Any]:
        self.calls.append({"start_ms": start_ms})
        return self.trends


def _expected_first_day(days: int) -> date:
    return date.today() - timedelta(days=days - 1)


def _midnight_ms(day: date) -> int:
    return int(datetime.combine(day, time.min).timestamp() * 1000)


class TestBuildUsageTrends:
    """趋势装配：逐日补零对齐时间轴、窗口起点、逐模型透传。"""

    @pytest.mark.asyncio
    async def test_缺失日期补零且首尾对齐窗口(self) -> None:
        days = 4
        first_day = _expected_first_day(days)
        middle_day = first_day + timedelta(days=2)
        repo = _FakeRepo(
            {
                "daily": [
                    {
                        "day": middle_day.isoformat(),
                        "total_calls": 7,
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                        "cost": 1.5,
                        "cache_hit_tokens": 40,
                        "cache_miss_tokens": 60,
                    }
                ],
                "by_model": [],
            }
        )
        result = await build_usage_trends(repo, days)

        assert len(result.points) == days
        assert result.days == days
        # 首尾日期边界：窗口首日 = today - (days-1)，末日 = today
        assert result.points[0].date == first_day.isoformat()
        assert result.points[-1].date == date.today().isoformat()
        # 缺失日期补零
        empty = result.points[0]
        assert empty.total_calls == 0
        assert empty.total_tokens == 0
        assert empty.cost == 0.0
        assert empty.cache_hit_rate is None
        # 有数据日期透传 + 命中率装配
        hit_point = result.points[2]
        assert hit_point.date == middle_day.isoformat()
        assert hit_point.total_calls == 7
        assert hit_point.total_tokens == 150
        assert hit_point.cache_hit_rate == pytest.approx(0.4)
        # 起点时间戳为窗口首日本地零点
        assert repo.calls[0]["start_ms"] == _midnight_ms(first_day)
        # 每个点携带本地零点时间戳
        assert result.points[1].timestamp_ms == _midnight_ms(first_day + timedelta(days=1))

    @pytest.mark.asyncio
    async def test_by_model_逐行透传且空模型名归unknown(self) -> None:
        repo = _FakeRepo(
            {
                "daily": [],
                "by_model": [
                    {
                        "day": "2026-09-18",
                        "model_name": "glm-5",
                        "total_calls": 3,
                        "total_tokens": 300,
                        "cost": 0.3,
                        "cache_hit_tokens": 100,
                        "cache_miss_tokens": 100,
                    },
                    {"day": "2026-09-19", "model_name": "", "total_calls": 1, "total_tokens": 10},
                ],
            }
        )
        result = await build_usage_trends(repo, 1)
        assert [p.model_name for p in result.model_points] == ["glm-5", "unknown"]
        assert result.model_points[0].cache_hit_rate == pytest.approx(0.5)
        # 缺省字段补零
        assert result.model_points[1].cache_hit_tokens == 0
        assert result.model_points[1].cache_hit_rate is None

    @pytest.mark.asyncio
    async def test_仓储为None时返回空趋势(self) -> None:
        result = await build_usage_trends(None, 7)
        assert result.days == 7
        assert result.points == []
        assert result.model_points == []
