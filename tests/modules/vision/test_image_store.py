"""VisionImageStore：有界暂存的逐出、过期与取回契约。"""

from __future__ import annotations

import pytest

from src.modules.vision.image_store import (
    DEFAULT_VISIBLE_TURNS,
    VisionImageStore,
    get_image_store,
    set_image_store,
)

_DATA_URL = "data:image/png;base64," + "A" * 64


def _store(**kwargs) -> VisionImageStore:
    """默认时钟可控行：测试注入假时钟避免真实等待。"""
    kwargs.setdefault("now_fn", lambda: 1_000_000)
    return VisionImageStore(**kwargs)


def test_put_get_roundtrip() -> None:
    store = _store()
    ref = store.put(_DATA_URL)
    assert ref.startswith("img_")
    assert store.get(ref) == _DATA_URL


def test_put_rejects_non_payload() -> None:
    """store 只收 data URL 形态；短串/非字符串就地拒绝（上游缺陷不进库）。"""
    store = _store()
    with pytest.raises(ValueError):
        store.put("太短")
    with pytest.raises(ValueError):
        store.put(12345)  # type: ignore[arg-type]


def test_capacity_evicts_oldest() -> None:
    """容量满后 LRU 逐出最老条目，逐出即引用失效。"""
    store = _store(capacity=2)
    r1 = store.put(_DATA_URL)
    r2 = store.put(_DATA_URL)
    r3 = store.put(_DATA_URL)
    assert store.get(r1) is None
    assert store.get(r2) == _DATA_URL
    assert store.get(r3) == _DATA_URL


def test_get_refreshes_lru_order() -> None:
    """读取刷新最近使用序：被读的旧条目比未读的新条目活得久。"""
    store = _store(capacity=2)
    r1 = store.put(_DATA_URL)
    r2 = store.put(_DATA_URL)
    assert store.get(r1) == _DATA_URL
    store.put(_DATA_URL)
    assert store.get(r1) == _DATA_URL
    assert store.get(r2) is None


def test_ttl_expiry() -> None:
    """超 TTL 的图取回返回 None（引用失效，装配期降级为过期提示）。"""
    clock = {"now": 1_000_000}
    store = VisionImageStore(ttl_ms=60_000, now_fn=lambda: clock["now"])
    ref = store.put(_DATA_URL)
    clock["now"] += 61_000
    assert store.get(ref) is None


def test_visible_turns_default() -> None:
    """窗口默认 1 轮：只认最后一条 assistant 之后（请求尾部）的引用；引擎注水判定读此值。"""
    assert DEFAULT_VISIBLE_TURNS == 1
    assert _store().visible_turns == 1


def test_singleton_get_set() -> None:
    """get 返回进程级单例；set 可替换（测试隔离用）。"""
    store = VisionImageStore()
    try:
        set_image_store(store)
        assert get_image_store() is store
    finally:
        set_image_store(VisionImageStore())
        assert get_image_store() is not store
