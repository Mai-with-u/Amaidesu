"""VisionImageStore：有界暂存的取回、LRU 逐出、TTL 过期与进程级单例"""

from __future__ import annotations

import pytest

from src.modules.vision.image_store import DEFAULT_VISIBLE_TURNS, VisionImageStore, get_image_store, set_image_store

DATA_URL = "data:image/png;base64," + "A" * 64


def _store(**kwargs) -> VisionImageStore:
    kwargs.setdefault("now_fn", lambda: 1_000_000)  # 假时钟，避免真实等待
    return VisionImageStore(**kwargs)


def test_put_get_roundtrip_and_payload_validation() -> None:
    store = _store()
    ref = store.put(DATA_URL)
    assert ref.startswith("img_") and store.get(ref) == DATA_URL
    with pytest.raises(ValueError):  # 只收 data URL 形态：上游缺陷不进库
        store.put("太短")
    with pytest.raises(ValueError):
        store.put(12345)  # type: ignore[arg-type]


def test_capacity_evicts_the_least_recently_used_and_reads_refresh_recency() -> None:
    store = _store(capacity=2)
    first, second, third = store.put(DATA_URL), store.put(DATA_URL), store.put(DATA_URL)
    assert store.get(first) is None and store.get(second) == DATA_URL and store.get(third) == DATA_URL  # 逐出即引用失效

    fresh = _store(capacity=2)
    old, newer = fresh.put(DATA_URL), fresh.put(DATA_URL)
    assert fresh.get(old) == DATA_URL  # 被读的旧条目比未读的新条目活得久
    fresh.put(DATA_URL)
    assert fresh.get(old) == DATA_URL and fresh.get(newer) is None


def test_ttl_expiry_visible_window_default_and_singleton_swap() -> None:
    clock = {"now": 1_000_000}
    store = VisionImageStore(ttl_ms=60_000, now_fn=lambda: clock["now"])
    ref = store.put(DATA_URL)
    clock["now"] += 61_000
    assert store.get(ref) is None  # 超 TTL 取回返回 None，装配期降级为过期提示

    assert DEFAULT_VISIBLE_TURNS == 1 and _store().visible_turns == 1  # 只认最后一条 assistant 之后的引用

    replacement = VisionImageStore()
    try:
        set_image_store(replacement)
        assert get_image_store() is replacement
    finally:
        set_image_store(VisionImageStore())  # 测试隔离
    assert get_image_store() is not replacement
