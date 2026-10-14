"""ConfigProxy：把"快照式 self.config = config"换成透明代理，旧引用在热重载后也能读到最新配置"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from src.modules.config.config_proxy import ConfigProxy


class _MockConfig(BaseModel):
    voice: str = "zh-CN-Xiaoxiao"
    max_messages: int = 100
    show_danmaku: bool = True


def test_dict_access_forms_forward_to_the_underlying_dict() -> None:
    holder = {"voice": "zh-CN-Xiaoxiao", "language": "zh-CN", "_private_inner": "secret"}
    proxy = ConfigProxy(getter=lambda: holder)

    assert proxy["voice"] == proxy.voice == proxy.get("voice") == "zh-CN-Xiaoxiao"
    assert (proxy.get("missing", "fallback"), proxy.get("missing")) == ("fallback", None)
    with pytest.raises(KeyError):
        _ = proxy["missing"]
    with pytest.raises(AttributeError):  # 属性缺失是 AttributeError 而非 KeyError
        _ = proxy.missing
    with pytest.raises(AttributeError):  # __getattr__ 不转发私有名，防内部状态意外泄漏
        _ = proxy._private_inner
    assert (len(proxy), "voice" in proxy, "missing" in proxy, sorted(proxy)) == (3, True, False, sorted(holder))


def test_pydantic_model_is_transparent() -> None:
    config = _MockConfig(voice="en-US-Jenny", max_messages=200)
    proxy = ConfigProxy(getter=lambda: config)
    assert (proxy.voice, proxy.max_messages, proxy.show_danmaku) == ("en-US-Jenny", 200, True)
    assert proxy["voice"] == "en-US-Jenny"
    assert (proxy.get("voice"), proxy.get("missing", "fallback")) == ("en-US-Jenny", "fallback")


def test_hot_reload_is_visible_through_stable_proxy_identity() -> None:
    holder = {"voice": "zh-CN-Xiaoxiao", "language": "zh-CN"}
    proxy = ConfigProxy(getter=lambda: holder)
    imported_earlier = proxy  # 旧引用

    holder.clear()  # 最常见的热重载模式：clear + update
    holder.update({"voice": "en-US-Jenny", "rate": "+10%"})
    assert (imported_earlier.voice, imported_earlier["rate"], len(imported_earlier)) == ("en-US-Jenny", "+10%", 2)
    assert "language" not in imported_earlier and list(imported_earlier) == ["voice", "rate"]

    holder["voice"] = "ja-JP-Nanami"
    assert proxy.voice == "ja-JP-Nanami"

    instances = [_MockConfig()]
    model_proxy = ConfigProxy(getter=lambda: instances[0])
    instances[0] = _MockConfig(voice="en-US-Jenny", max_messages=250)  # 整体替换 Pydantic 实例
    assert (model_proxy.voice, model_proxy.max_messages, model_proxy.show_danmaku) == ("en-US-Jenny", 250, True)


def test_setattr_only_swaps_getter_and_forwards_other_names_to_inner() -> None:
    holder_a, holder_b = {"voice": "voice_a"}, {"voice": "voice_b"}
    proxy = ConfigProxy(getter=lambda: holder_a)
    proxy._getter = lambda: holder_b
    assert proxy.voice == "voice_b"

    inner: dict[str, Any] = {}
    other = ConfigProxy(getter=lambda: inner)
    other.foo = 123
    assert inner["foo"] == other.foo == 123
    assert repr(other)


async def test_concurrent_reads_are_safe() -> None:
    holder = {f"key_{i}": i for i in range(100)}
    proxy = ConfigProxy(getter=lambda: holder)
    config = ConfigProxy(getter=lambda: _MockConfig(max_messages=42))

    async def read() -> tuple:
        return (
            [proxy[f"key_{i}"] for i in range(100)],
            proxy.get("missing", "d"),
            len(proxy),
            "key_5" in proxy,
            config.max_messages,
        )

    results = await asyncio.gather(*[read() for _ in range(100)])
    assert all(r == (list(range(100)), "d", 100, True, 42) for r in results)
