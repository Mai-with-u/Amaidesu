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


def test_latest_slot_overwrites_per_sample_and_resolves_body_via_ref() -> None:
    """latest 槽位覆盖式更新（防内存堆积），本体经 ref 从画面帧池取回；clear 一并清空。"""
    store = _store()
    assert store.latest() is None
    first = store.put_latest(DATA_URL, width=64, height=36, change=1.0, timestamp_ms=1_000)
    second = store.put_latest(DATA_URL, width=32, height=18, change=0.1, timestamp_ms=2_000)
    assert second.ref.startswith("img_") and store.latest() is second
    assert (second.width, second.height, second.change, second.timestamp_ms) == (32, 18, 0.1, 2_000)
    assert store.get(first.ref) == DATA_URL and store.get(second.ref) == DATA_URL
    store.clear()
    assert store.latest() is None


def test_frame_flood_does_not_evict_screenshot_refs() -> None:
    """画面帧高频覆盖写入只走独立帧池，不把已存入、等待组装取用的工具截图挤出 LRU。"""
    store = _store(capacity=2)
    shot_a, shot_b = store.put(DATA_URL), store.put(DATA_URL)
    for i in range(20):  # 帧洪泛量远超截图池容量
        store.put_latest(DATA_URL, width=16, height=9, change=0.5, timestamp_ms=i)
    assert store.get(shot_a) == DATA_URL and store.get(shot_b) == DATA_URL


def test_latest_pool_holds_independently_and_expires_by_its_own_ttl() -> None:
    """帧池容量独立于截图池：两池各自按自己的容量逐出，互不占位。"""
    store = _store(capacity=2, latest_capacity=1)
    shot_a, shot_b = store.put(DATA_URL), store.put(DATA_URL)
    frame1 = store.put_latest(DATA_URL, width=16, height=9, change=0.5, timestamp_ms=1)
    frame2 = store.put_latest(DATA_URL, width=16, height=9, change=0.5, timestamp_ms=2)
    assert store.get(shot_a) == DATA_URL and store.get(shot_b) == DATA_URL  # 两张截图都在
    assert store.get(frame1.ref) is None  # 帧池按自身容量 1 逐出旧帧
    assert store.get(frame2.ref) == DATA_URL


def test_pool_ttls_do_not_interfere_with_each_other() -> None:
    """两池 TTL 各自独立：截图过期不影响帧取回，帧过期不影响截图取回。"""
    clock = {"now": 1_000_000}
    store = VisionImageStore(ttl_ms=100_000, latest_ttl_ms=10_000, now_fn=lambda: clock["now"])
    shot = store.put(DATA_URL)
    frame = store.put_latest(DATA_URL, width=16, height=9, change=0.5, timestamp_ms=1)

    clock["now"] += 50_000  # 帧已超短 TTL，截图未到长 TTL
    assert store.get(frame.ref) is None
    assert store.get(shot) == DATA_URL

    clock["now"] += 60_000  # 截图已越过长 TTL
    assert store.get(shot) is None
    frame2 = store.put_latest(DATA_URL, width=16, height=9, change=0.5, timestamp_ms=2)
    assert store.get(frame2.ref) == DATA_URL  # 新采样帧在自身 TTL 窗口内照常可取
