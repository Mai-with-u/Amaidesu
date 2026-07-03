"""ConfigProxy — 透明配置代理, 实现热重载友好的配置访问

设计目标
--------
替换 Amaidesu 中 `self.config = config` 的快照模式, 让:

    from somewhere import config
    config.voice  # 总能拿到最新值

即使 `config` 是很久以前 import 的, 也仍然能看到最新配置。

参考实现
--------
- MaiBot-v1.0.0/src/config/config.py:688-707 `_ConfigProxy`
- scripts/poc_proxy_test.py (Task 3 POC, 已验证 21/21 行为契约)

支持的内部配置类型
------------------
1. ``dict`` —— 键值访问, 支持 ``[]``, ``.get()``, ``len()``, ``in``, ``iter``
2. ``pydantic.BaseModel`` —— 属性访问, 支持 ``.field`` 风格

线程/异步并发安全
----------------
代理内部持有 ``asyncio.Lock`` 用于保护 ``_getter`` 替换时的状态一致性。
同步访问方法 (``__getattr__``, ``__getitem__`` 等) 在 CPython 单线程事件循环
下天然原子, 因为它们不包含 ``await`` 点。

使用示例
--------
::

    from src.modules.config.config_proxy import ConfigProxy


    def get_current_config() -> dict:
        return config_service.get_active_config()


    config = ConfigProxy(getter=get_current_config)

    # 字典风格
    voice = config["voice"]
    voice = config.get("voice", "default")

    # 属性风格 (Pydantic 模型或支持 __getattr__ 的对象)
    voice = config.voice

    # 容器协议
    if "voice" in config:
        ...
    for key in config:
        ...
    n = len(config)
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


# 用于识别内部属性的名称集合。访问这些属性走 object.__getattribute__ 路径,
# 不会被 __getattr__ 拦截。
_INTERNAL_ATTRS: frozenset[str] = frozenset({"_getter", "_lock"})


class ConfigProxy:
    """透明配置代理 — 通过 getter 函数实时拉取最新配置"""

    __slots__ = ("_getter", "_lock")

    def __init__(self, getter: Callable[[], Any]) -> None:
        """初始化代理。

        Args:
            getter: 每次访问时被调用的无参可调用对象, 返回当前的内部配置
                    (可以是 dict 或 Pydantic BaseModel 等支持属性访问的对象)。

        Note:
            构造时直接调用 ``object.__setattr__`` 是因为本类重写了 ``__setattr__``,
            默认行为会将属性写入内部配置, 这不是我们想要的。
        """
        object.__setattr__(self, "_getter", getter)
        object.__setattr__(self, "_lock", asyncio.Lock())

    # ------------------------------------------------------------------
    # Read operations — 转发到内部配置
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """属性访问转发。

        只在普通属性查找失败时被调用。私有属性名 (``_xxx``) 会被显式拒绝,
        防止意外把内部配置的私有属性暴露给调用方。

        支持的内部配置类型:
        - Pydantic BaseModel: 走 ``getattr(inner, name)``
        - dict: 走 ``inner[name]`` (KeyError 转为 AttributeError 保持语义一致)
        """
        if name.startswith("_"):
            raise AttributeError(f"ConfigProxy 不转发私有属性 {name!r} (避免暴露内部状态)")
        inner = object.__getattribute__(self, "_getter")()
        try:
            return getattr(inner, name)
        except AttributeError:
            # dict 不支持 ``.name`` 属性访问, 退回到 ``[name]``
            try:
                return inner[name]
            except (KeyError, TypeError):
                raise AttributeError(f"ConfigProxy 内部配置没有属性 {name!r}") from None

    def __getitem__(self, key: str) -> Any:
        """``proxy[key]`` — 转发 ``[]`` 访问。

        优先尝试 ``inner[key]`` (dict 风格, KeyError 自然传播),
        失败时退回到 ``getattr(inner, key)`` (Pydantic 风格)。
        """
        inner = object.__getattribute__(self, "_getter")()
        try:
            return inner[key]
        except TypeError:
            # Pydantic 不支持 subscript, 用属性访问代替
            return getattr(inner, key)
        # KeyError 自然传播 — 符合 dict 语义

    def get(self, key: str, default: Any = None) -> Any:
        """``proxy.get(key, default)`` — dict 风格的安全访问。

        优先调用 ``inner.get(key, default)`` (dict / 支持 .get 的对象),
        不支持时退回到 ``getattr(inner, key, default)`` (Pydantic 风格)。
        """
        inner = object.__getattribute__(self, "_getter")()
        get_method = getattr(inner, "get", None)
        if callable(get_method):
            try:
                return get_method(key, default)
            except TypeError:
                # Pydantic 的 .get 不是 dict.get 风格, 退回到 getattr
                pass
        return getattr(inner, key, default)

    def __len__(self) -> int:
        """``len(proxy)`` — 容器长度"""
        return len(object.__getattribute__(self, "_getter")())

    def __contains__(self, key: Any) -> bool:
        """``key in proxy`` — 成员检查"""
        return key in object.__getattribute__(self, "_getter")()

    def __iter__(self):
        """``iter(proxy)`` — 迭代内部配置的键"""
        return iter(object.__getattribute__(self, "_getter")())

    # ------------------------------------------------------------------
    # Write operations — _getter/_lock 自身, 其他转发到内部配置
    # ------------------------------------------------------------------

    def __setattr__(self, name: str, value: Any) -> None:
        """属性赋值。

        - ``_getter`` / ``_lock``: 写入代理自身的内部 slot
        - 其他名称: 转发到内部配置 (dict 用 ``[]``, Pydantic 用 ``setattr``)
        """
        if name in _INTERNAL_ATTRS:
            object.__setattr__(self, name, value)
            return

        inner = object.__getattribute__(self, "_getter")()
        try:
            setattr(inner, name, value)
        except (AttributeError, TypeError):
            # AttributeError: Pydantic 模型 extra="forbid" 时
            # TypeError: dict 不支持 setattr (有时表现为 TypeError)
            inner[name] = value

    def __repr__(self) -> str:
        """代理的可读表示"""
        try:
            inner = object.__getattribute__(self, "_getter")()
            return f"ConfigProxy({inner!r})"
        except Exception as exc:
            return f"ConfigProxy(<unavailable: {exc!r})>"

    # ------------------------------------------------------------------
    # 异步安全的状态交换
    # ------------------------------------------------------------------

    async def swap_getter(self, new_getter: Callable[[], Any]) -> None:
        """异步场景下原子地替换内部 getter。

        使用 ``asyncio.Lock`` 保护, 防止并发读取期间出现 _getter 引用不一致。
        同步场景下可以直接 ``proxy._getter = new_getter``, 无须此方法。
        """
        async with object.__getattribute__(self, "_lock"):
            object.__setattr__(self, "_getter", new_getter)


__all__ = ["ConfigProxy"]
