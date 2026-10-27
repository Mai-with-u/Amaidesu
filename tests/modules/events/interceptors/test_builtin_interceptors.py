"""内置拦截器：限流（全局/用户滑窗）、相似过滤（跨用户/来源隔离/短文本）、场次盖章（字段驱动、异常放行）"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from src.modules.events.interceptors.chain import InterceptorChain
from src.modules.events.interceptors.lookup import extract_user_id
from src.modules.events.interceptors.rate_limit import RateLimitInterceptor
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.interceptors.similar_filter import SimilarFilterInterceptor
from src.modules.events.payloads.planner import StreamerStagePayload
from src.modules.events.payloads.rundown import RundownChangedPayload

EVENT = "room.message.danmaku"


def _danmaku(text: str = "hello", user_id: str = "u1") -> dict:
    """真实 RoomMessagePayload 形状：用户嵌套在 user，文本在 content。"""
    return {"message_type": "danmaku", "user": {"id": user_id, "name": f"观众{user_id}"}, "content": text}


# 限流


async def test_rate_limit_user_global_and_per_user_buckets() -> None:
    interceptor = RateLimitInterceptor(global_rate_limit=100, user_rate_limit=2, window_size=60)
    passed = [await interceptor.intercept(EVENT, _danmaku(f"m{i}", "alice"), "t") is not None for i in range(3)]
    assert passed == [True, True, False]  # 用户配额用完，第 3 条被丢弃
    assert await interceptor.intercept(EVENT, _danmaku(user_id="bob"), "t") is not None  # 每用户独立计数

    glob = RateLimitInterceptor(global_rate_limit=2, user_rate_limit=100, window_size=60)
    results = [await glob.intercept(EVENT, _danmaku(user_id=u), "t") for u in ("u1", "u2", "u3")]
    assert [r is not None for r in results] == [True, True, False]  # 全局配额不分用户

    await glob.reset()
    assert await glob.intercept(EVENT, _danmaku(user_id="u1"), "t") is not None


def test_extract_user_id_prefers_nested_user_then_top_level_keys() -> None:
    assert extract_user_id(_danmaku(user_id="alice")) == "alice"
    assert extract_user_id({"user_id": "alice"}) == "alice"
    assert extract_user_id({"open_id": "bob"}) == "bob"
    assert extract_user_id({"text": "hi"}) == extract_user_id({}) == "unknown_user"


# 相似过滤


async def test_similar_filter_drops_repeats_and_respects_user_and_source_scope() -> None:
    async def send(ic: SimilarFilterInterceptor, text: str, user_id: str = "u1", source: str = "g") -> bool:
        return await ic.intercept(EVENT, _danmaku(text, user_id), source) is not None

    ic = SimilarFilterInterceptor()
    text = "这是一条测试消息"
    assert await send(ic, text) and not await send(ic, text)  # 完全相同的第二条被过滤
    assert await send(ic, "消息 B 是那样")  # 不同文本放行
    assert await send(ic, text, source="g2")  # 缓存按 source 隔离

    cross = SimilarFilterInterceptor(cross_user_filter=True)
    assert await send(cross, "相同消息内容", "u1") and not await send(cross, "相同消息内容", "u2")
    own = SimilarFilterInterceptor(cross_user_filter=False)
    assert await send(own, "相同消息内容", "u1") and await send(own, "相同消息内容", "u2")

    short = SimilarFilterInterceptor(min_text_length=3)
    assert await send(short, "12") and await send(short, "12")  # 短于 min_text_length 不参与过滤


async def test_similar_filter_contained_text_weighting_and_reset() -> None:
    ic = SimilarFilterInterceptor()
    assert ic._calculate_similarity("666", "6666") >= 0.5  # 包含关系加权
    await ic.intercept(EVENT, _danmaku("测试消息"), "g")
    assert ic._text_cache.get("g")
    await ic.reset()
    assert not ic._text_cache and await ic.intercept(EVENT, _danmaku("测试消息"), "g") is not None


# 场次盖章


class _FakeSessionManager:
    def __init__(self, pk: Optional[int] = 42, *, raise_on_resolve: bool = False) -> None:
        self._pk = pk
        self._raise = raise_on_resolve
        self.calls = 0

    async def resolve_pk(self) -> Optional[int]:
        self.calls += 1
        if self._raise:
            raise RuntimeError("解析失败")
        return self._pk


@pytest.mark.parametrize(
    "event_name",
    # 全部取不在历史盖章域名单内的名字：实证盖章由字段声明驱动、不限事件域
    [
        "example.thing.happened",
        "somegame.tick",
        "whatever.domain.event",
        "room.message.danmaku",
        "planner.verdict",
    ],
)
async def test_stamps_any_domain_when_field_declared(event_name: str) -> None:
    sm = _FakeSessionManager(42)
    chain = InterceptorChain()
    chain.register(SessionStampInterceptor(sm))
    out = await chain.apply(event_name, {"live_session_id": 0}, "test")
    assert out == {"live_session_id": 42} and sm.calls == 1


async def test_stamps_real_payload_shapes() -> None:
    chain = InterceptorChain()
    chain.register(SessionStampInterceptor(_FakeSessionManager(42)))
    for event_name, payload in (
        ("rundown.changed", RundownChangedPayload(rundown_id="default_first_stream")),
        ("streamer.stage", StreamerStagePayload(stage="planning")),
    ):
        data = payload.model_dump()
        assert data["live_session_id"] == 0
        assert (await chain.apply(event_name, data, "test"))["live_session_id"] == 42


async def test_stamp_skips_nonzero_fieldless_and_live_lifecycle_payloads() -> None:
    sm = _FakeSessionManager(42)
    ic = SessionStampInterceptor(sm)
    # 非 0 主键（含 live.* 生命周期事件自带主键的形态）不被覆盖
    assert (await ic.intercept("example.thing.happened", {"live_session_id": 7}, "t"))["live_session_id"] == 7
    assert (await ic.intercept("live.session.started", {"live_session_id": 9}, "t"))["live_session_id"] == 9
    assert sm.calls == 0  # 已带主键不再解析

    # 未声明 live_session_id 字段 → 原样放行、不注入未知键
    assert "live_session_id" not in await ic.intercept("example.thing.happened", {"timeline_summary": "x"}, "t")
    assert sm.calls == 0


@pytest.mark.parametrize("manager", [_FakeSessionManager(raise_on_resolve=True), _FakeSessionManager(pk=None)])
async def test_stamp_passes_payload_through_when_resolve_fails_or_returns_none(manager: _FakeSessionManager) -> None:
    payload: Dict[str, Any] = {"live_session_id": 0, "content": "hi"}
    out = await SessionStampInterceptor(manager).intercept(EVENT, payload, "t")
    assert out is not None and out["live_session_id"] == 0  # 无场次时原样放行，由落库路径跳过
