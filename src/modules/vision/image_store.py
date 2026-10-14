"""截图等图像大载荷的进程内有界暂存（引用进历史的本体保管所）

请求尾部的图像只以 :class:`~src.modules.llm.payload.ImageRefPart` 引用存在，
base64 本体保存在这里。截图是几轮就过期的易腐数据：容量按 LRU 逐出、按 TTL
过期，逐出即引用失效（装配期降级为"截图已过期"提示），不持久化、不入库。

单例经 :func:`get_image_store` 取用（与全局请求历史管理器同款先例）：
消费方有引擎（装配期注水）与视觉调用方 Agent（观察组装时 put），经工厂
两处调用点逐一穿线构造器的管道成本远超收益。测试用 :func:`set_image_store`
换隔离实例。
"""

from __future__ import annotations

import threading
import uuid
import time
from collections import OrderedDict
from typing import Callable, Optional

from src.modules.logging import get_logger

logger = get_logger("VisionImageStore")

# 图在装配请求里保持可见的 assistant 轮数：只认最后一条 assistant 之后的
# 引用（Agent 把图挂在请求尾部，紧随回执的那一次请求看一次）；更早位置的
# 引用说明有图残留在历史里，一律降级为文字，不再每次请求重传
DEFAULT_VISIBLE_TURNS = 1

# 容量与 TTL 的保守默认：16 张已远超单任务并发看屏需求；截图 15 分钟后
# 场景大概率已变，留引用只会让模型拿到过期画面
DEFAULT_CAPACITY = 16
DEFAULT_TTL_MS = 15 * 60 * 1000


class VisionImageStore:
    """data URL 图像的有界暂存：LRU + TTL 逐出，线程安全"""

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        ttl_ms: int = DEFAULT_TTL_MS,
        visible_turns: int = DEFAULT_VISIBLE_TURNS,
        now_fn: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._capacity = capacity
        self._ttl_ms = ttl_ms
        self.visible_turns = visible_turns
        self._now_fn = now_fn
        self._lock = threading.Lock()
        # ref → (data_url, created_ms)；OrderedDict 尾部 = 最近使用
        self._entries: "OrderedDict[str, tuple[str, int]]" = OrderedDict()

    def put(self, data_url: str) -> str:
        """存入一张图，返回引用句柄（``img_<hex>``）。

        非字符串或超长空串拒绝——store 只收观察组装层提取出的 data URL，
        形状不对是上游缺陷，就地硬错。
        """
        if not isinstance(data_url, str) or len(data_url) < 32:
            raise ValueError(f"image_store 只接受 data URL 字符串，收到: {type(data_url).__name__}")
        ref = f"img_{uuid.uuid4().hex[:12]}"
        now = self._now_fn()
        with self._lock:
            self._evict_expired(now)
            while len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
            self._entries[ref] = (data_url, now)
        return ref

    def get(self, ref: str) -> Optional[str]:
        """取回 data URL；逐出或过期返回 None（装配期降级为过期提示）。"""
        with self._lock:
            entry = self._entries.get(ref)
            if entry is None:
                return None
            data_url, created_ms = entry
            if self._now_fn() - created_ms > self._ttl_ms:
                del self._entries[ref]
                return None
            self._entries.move_to_end(ref)
            return data_url

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        with self._lock:
            self._entries.clear()

    def _evict_expired(self, now_ms: int) -> None:
        """过期逐出（put 前调用；持有锁）。"""
        expired = [ref for ref, (_, created) in self._entries.items() if now_ms - created > self._ttl_ms]
        for ref in expired:
            del self._entries[ref]
        if expired:
            logger.debug(f"图像暂存过期逐出 {len(expired)} 张")


_default_store: Optional[VisionImageStore] = None


def get_image_store() -> VisionImageStore:
    """取进程级默认 store（惰性创建）。"""
    global _default_store
    if _default_store is None:
        _default_store = VisionImageStore()
    return _default_store


def set_image_store(store: VisionImageStore) -> None:
    """替换默认 store（测试隔离用）。"""
    global _default_store
    _default_store = store


__all__ = [
    "VisionImageStore",
    "get_image_store",
    "set_image_store",
    "DEFAULT_VISIBLE_TURNS",
    "DEFAULT_CAPACITY",
    "DEFAULT_TTL_MS",
]
