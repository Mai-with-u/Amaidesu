"""ConfigProxy 单元测试

ConfigProxy 是配置热重载的关键: 把 `self.config = config` 的快照模式替换为
透明代理, 让旧引用也能看到最新的配置。

覆盖场景 (来自 POC poc_proxy_test.py 的 21 条 + 新增):
1. __getitem__ 透明访问
2. __getattr__ 透明访问
3. .get(key, default) 透明访问
4. 替换底层 dict → 代理立即反映
5. len(), "in", iter() 全部可用
6. Pydantic 模型属性访问
7. asyncio 并发访问安全

参考实现:
- MaiBot-v1.0.0/src/config/config.py:688-707 (_ConfigProxy)
- scripts/poc_proxy_test.py (已验证 21/21)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, Field


# 注意: ConfigProxy 模块在 PHASE 2 才会创建 — 这里先 import 报错以确认 RED


# =============================================================================
# Test data: simple dict and Pydantic model
# =============================================================================


class _MockConfig(BaseModel):
    """用于测试 Pydantic 模型属性访问的 mock 配置"""

    voice: str = Field(default="zh-CN-Xiaoxiao", description="语音")
    language: str = Field(default="zh-CN", description="语言")
    volume: str = Field(default="+0%", description="音量")
    rate: str = Field(default="+0%", description="语速")
    max_messages: int = Field(default=100, description="最大消息数")
    show_danmaku: bool = Field(default=True, description="显示弹幕")


# =============================================================================
# __getitem__ forwarding
# =============================================================================


class TestGetitemForwarding:
    """proxy[key] 必须转发到底层 dict"""

    def test_getitem_returns_value(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao", "language": "zh-CN"}
        proxy = ConfigProxy(getter=lambda: holder)
        assert proxy["voice"] == "zh-CN-Xiaoxiao"
        assert proxy["language"] == "zh-CN"

    def test_getitem_missing_raises_key_error(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)
        with pytest.raises(KeyError):
            _ = proxy["missing"]


# =============================================================================
# __getattr__ forwarding
# =============================================================================


class TestGetattrForwarding:
    """proxy.key 必须转发到底层 dict / Pydantic 模型"""

    def test_getattr_returns_value(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)
        assert proxy.voice == "zh-CN-Xiaoxiao"

    def test_getattr_missing_raises_attribute_error(self):
        """访问不存在的属性必须抛 AttributeError (不是 KeyError)"""
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)
        with pytest.raises(AttributeError):
            _ = proxy.missing

    def test_getattr_does_not_expose_private_attributes(self):
        """__getattr__ 不应转发私有属性查找 (避免意外暴露内部状态)

        注意: 这里测试的是 __getattr__ 的转发行为, 不是 _getter 本身的访问性。
        _getter 作为内部 slot, 通过 normal lookup 访问 (允许 swap 操作)。
        __getattr__ 只在 normal lookup 失败时被调用, 此时必须拒绝私有名称,
        防止 inner config 的私有属性意外泄漏。
        """
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao", "_private_inner": "secret"}
        proxy = ConfigProxy(getter=lambda: holder)
        # 访问 inner 上存在的私有属性 — __getattr__ 必须拒绝
        with pytest.raises(AttributeError):
            _ = proxy._private_inner

    def test_getattr_on_pydantic_model(self):
        """__getattr__ 必须能转发到 Pydantic BaseModel 的属性"""
        from src.modules.config.config_proxy import ConfigProxy

        config = _MockConfig(voice="en-US-Jenny", max_messages=200)
        proxy = ConfigProxy(getter=lambda: config)
        assert proxy.voice == "en-US-Jenny"
        assert proxy.max_messages == 200
        assert proxy.show_danmaku is True


# =============================================================================
# .get() forwarding
# =============================================================================


class TestGetForwarding:
    """proxy.get(key, default) 必须转发到底层 dict"""

    def test_get_existing_key(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)
        assert proxy.get("voice") == "zh-CN-Xiaoxiao"

    def test_get_missing_returns_default(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder: dict[str, Any] = {}
        proxy = ConfigProxy(getter=lambda: holder)
        assert proxy.get("missing", "fallback") == "fallback"

    def test_get_missing_returns_none_by_default(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder: dict[str, Any] = {}
        proxy = ConfigProxy(getter=lambda: holder)
        assert proxy.get("missing") is None


# =============================================================================
# Hot-reload: swap underlying dict
# =============================================================================


class TestHotReload:
    """替换底层 dict 后,代理必须立即反映新值"""

    def test_swap_dict_via_clear_and_update(self):
        """模拟最常见的 hot-reload 模式: clear + update"""
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao", "language": "zh-CN", "volume": "+0%"}
        proxy = ConfigProxy(getter=lambda: holder)

        assert proxy["voice"] == "zh-CN-Xiaoxiao"

        # 模拟热重载
        holder.clear()
        holder.update({"voice": "en-US-Jenny", "rate": "+10%"})

        assert proxy["voice"] == "en-US-Jenny"
        assert proxy["rate"] == "+10%"
        assert "language" not in proxy
        assert "volume" not in proxy

    def test_swap_dict_via_assignment(self):
        """直接重新赋值 holder 内的字段"""
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao", "volume": "+0%"}
        proxy = ConfigProxy(getter=lambda: holder)

        holder["voice"] = "ja-JP-Nanami"
        assert proxy.voice == "ja-JP-Nanami"
        assert proxy.get("volume") == "+0%"

    def test_swap_pydantic_instance(self):
        """完全替换 Pydantic 实例后,代理看到新实例"""
        from src.modules.config.config_proxy import ConfigProxy

        config_holder: list[_MockConfig] = [_MockConfig(voice="zh-CN-Xiaoxiao")]
        proxy = ConfigProxy(getter=lambda: config_holder[0])

        assert proxy.voice == "zh-CN-Xiaoxiao"
        assert proxy.max_messages == 100

        # 替换为新的 Pydantic 实例 (模拟热重载)
        config_holder[0] = _MockConfig(voice="en-US-Jenny", max_messages=250)

        assert proxy.voice == "en-US-Jenny"
        assert proxy.max_messages == 250
        assert proxy.show_danmaku is True  # 保留默认值

    def test_proxy_identity_stable(self):
        """代理对象本身是稳定引用 — 这是 hot-reload 的关键"""
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)

        old_ref = proxy  # "导入"的旧引用

        # 模拟热重载
        holder["voice"] = "new-voice"
        holder["x"] = 1

        # 旧引用必须看到新值
        assert old_ref.voice == "new-voice"
        assert old_ref["x"] == 1
        assert old_ref is proxy


# =============================================================================
# len(), "in", iteration
# =============================================================================


class TestContainerDunder:
    """len(), __contains__, __iter__ 必须工作"""

    def test_len_returns_dict_size(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"a": 1, "b": 2, "c": 3}
        proxy = ConfigProxy(getter=lambda: holder)
        assert len(proxy) == 3

    def test_len_reflects_swap(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"a": 1, "b": 2}
        proxy = ConfigProxy(getter=lambda: holder)
        assert len(proxy) == 2

        holder.clear()
        holder.update({"x": 1, "y": 2, "z": 3, "w": 4})
        assert len(proxy) == 4

    def test_contains_true(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)
        assert "voice" in proxy

    def test_contains_false(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)
        assert "missing" not in proxy

    def test_iter_returns_keys(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"a": 1, "b": 2, "c": 3}
        proxy = ConfigProxy(getter=lambda: holder)
        keys = sorted(iter(proxy))
        assert keys == ["a", "b", "c"]

    def test_iter_reflects_swap(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"a": 1, "b": 2}
        proxy = ConfigProxy(getter=lambda: holder)
        assert sorted(iter(proxy)) == ["a", "b"]

        holder.clear()
        holder["new"] = 1
        assert list(proxy) == ["new"]


# =============================================================================
# Pydantic model attribute access
# =============================================================================


class TestPydanticAccess:
    """Pydantic 模型属性访问必须透明"""

    def test_attribute_access_on_pydantic(self):
        from src.modules.config.config_proxy import ConfigProxy

        config = _MockConfig()
        proxy = ConfigProxy(getter=lambda: config)
        assert proxy.voice == "zh-CN-Xiaoxiao"
        assert proxy.max_messages == 100

    def test_item_access_on_pydantic(self):
        """Pydantic 模型本身支持 __getitem__, 代理必须能转发"""
        from src.modules.config.config_proxy import ConfigProxy

        config = _MockConfig(voice="en-US-Jenny")
        proxy = ConfigProxy(getter=lambda: config)
        assert proxy["voice"] == "en-US-Jenny"

    def test_get_method_on_pydantic(self):
        from src.modules.config.config_proxy import ConfigProxy

        config = _MockConfig()
        proxy = ConfigProxy(getter=lambda: config)
        assert proxy.get("voice") == "zh-CN-Xiaoxiao"
        assert proxy.get("missing", "fallback") == "fallback"


# =============================================================================
# Thread-safety / concurrency safety
# =============================================================================


class TestConcurrency:
    """异步并发访问必须安全, 不抛异常"""

    async def test_concurrent_getitem(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {f"key_{i}": i for i in range(100)}
        proxy = ConfigProxy(getter=lambda: holder)

        async def read_keys():
            return [proxy[f"key_{i}"] for i in range(100)]

        # 100 个并发任务读取全部键
        results = await asyncio.gather(*[read_keys() for _ in range(100)])

        assert len(results) == 100
        for result in results:
            assert result == list(range(100))

    async def test_concurrent_getattr(self):
        from src.modules.config.config_proxy import ConfigProxy

        config = _MockConfig(max_messages=42)
        proxy = ConfigProxy(getter=lambda: config)

        async def read_attrs():
            return proxy.voice, proxy.max_messages, proxy.show_danmaku

        results = await asyncio.gather(*[read_attrs() for _ in range(100)])
        assert all(r == ("zh-CN-Xiaoxiao", 42, True) for r in results)

    async def test_concurrent_get_method(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)

        async def read_get():
            return proxy.get("voice"), proxy.get("missing", "default")

        results = await asyncio.gather(*[read_get() for _ in range(100)])
        assert all(r == ("zh-CN-Xiaoxiao", "default") for r in results)

    async def test_concurrent_len_and_contains(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {f"k_{i}": i for i in range(10)}
        proxy = ConfigProxy(getter=lambda: holder)

        async def check():
            return len(proxy), "k_5" in proxy, "missing" not in proxy

        results = await asyncio.gather(*[check() for _ in range(100)])
        for length, has, not_has in results:
            assert length == 10
            assert has is True
            assert not_has is True


# =============================================================================
# __setattr__: only _getter itself can be set
# =============================================================================


class TestSetattrBehavior:
    """__setattr__ 必须只允许设置 _getter, 其他属性转发到底层"""

    def test_set_getter_via_constructor(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)
        # 没有异常 — 构造时设置了 _getter
        assert proxy["voice"] == "zh-CN-Xiaoxiao"

    def test_setattr_to_getter_field_works(self):
        """proxy._getter = new_getter 必须能替换 getter"""
        from src.modules.config.config_proxy import ConfigProxy

        holder_a = {"voice": "voice_a"}
        holder_b = {"voice": "voice_b"}
        proxy = ConfigProxy(getter=lambda: holder_a)

        assert proxy.voice == "voice_a"

        proxy._getter = lambda: holder_b
        assert proxy.voice == "voice_b"

    def test_setattr_to_other_field_forwards_to_inner(self):
        """proxy.foo = 123 应被转发到内部 dict 的 foo 键"""
        from src.modules.config.config_proxy import ConfigProxy

        holder: dict[str, Any] = {}
        proxy = ConfigProxy(getter=lambda: holder)

        proxy.foo = 123
        # 写入应到达 holder
        assert holder.get("foo") == 123
        # 通过代理读回
        assert proxy.foo == 123


# =============================================================================
# Repr / diagnostics
# =============================================================================


class TestRepr:
    """__repr__ 应包含可读信息"""

    def test_repr_returns_string(self):
        from src.modules.config.config_proxy import ConfigProxy

        holder = {"voice": "zh-CN-Xiaoxiao"}
        proxy = ConfigProxy(getter=lambda: holder)
        r = repr(proxy)
        assert isinstance(r, str)
        assert len(r) > 0
