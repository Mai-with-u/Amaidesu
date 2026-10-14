"""前缀稳定性自检：相邻两次请求的稳定层被意外改写时记告警。

服务端前缀缓存按 token 前缀命中，稳定层（系统提示词、纪要、工作层）中段
任何一个字节变化，之后全部重算。约定"只追加"只靠人守容易破，这里把它变成
可测量的：Agent 每次请求前把稳定层交给自检，上一次的稳定层不再是这一次的
前缀、又不是预告过的检查点时，记一条带位置的告警。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.modules.logging import get_logger

__all__ = ["PrefixGuard"]

logger = get_logger("ContextPrefix")


class PrefixGuard:
    """记住上一次请求的稳定层指纹，判断这一次是否只在尾部追加。"""

    def __init__(self, name: str) -> None:
        self._name = name
        self._previous: List[Tuple[Any, ...]] = []
        self._expected_reason = ""
        # 非字符串字段（工具调用、分段正文）按对象身份缓存哈希：历史条目写入后不再改动
        self._value_hashes: Dict[int, Tuple[Any, int]] = {}

    def expect_rewrite(self, reason: str) -> None:
        """预告下一次请求会改写稳定层（检查点、开新任务），届时不告警。"""
        self._expected_reason = reason

    def observe(self, stable: Sequence[Mapping[str, Any]]) -> Optional[str]:
        """登记这一次的稳定层；被意外改写时记告警并返回说明，否则返回 None。"""
        seen: Dict[int, Tuple[Any, int]] = {}
        current = [self._fingerprint(message, seen) for message in stable]
        # 只保留本次仍在稳定层里的对象，缓存大小随历史长度有界
        self._value_hashes = seen
        previous, self._previous = self._previous, current
        expected, self._expected_reason = self._expected_reason, ""
        common = 0
        # 两次稳定层长度本就不同（追加了新记录），只比较重叠部分
        for old, new in zip(previous, current, strict=False):
            if old != new:
                break
            common += 1
        if common == len(previous) or expected:
            return None
        detail = (
            f"{self._name} 上下文稳定层在第 {common + 1} 条被改写"
            f"（上次 {len(previous)} 条，本次 {len(current)} 条），之后的前缀缓存全部失效"
        )
        logger.warning(detail)
        return detail

    def _fingerprint(self, message: Mapping[str, Any], seen: Dict[int, Tuple[Any, int]]) -> Tuple[Any, ...]:
        """消息指纹：只看模型可见字段，下划线开头的内部标记不参与。

        每次请求都要对整段历史算一遍：正文字符串直接取 Python 自带哈希（字符串对象
        缓存自己的哈希，第二次起是常数开销）；工具调用、分段正文按对象身份复用上次
        的序列化哈希，字段换成新对象时重算。指纹只在本进程内相邻两次请求间比较。
        """
        parts: List[Any] = []
        for key in sorted(key for key in message if not str(key).startswith("_")):
            value = message[key]
            if isinstance(value, str):
                parts.append((key, hash(value)))
                continue
            cached = self._value_hashes.get(id(value))
            if cached is None or cached[0] is not value:
                cached = (value, hash(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)))
            seen[id(value)] = cached
            parts.append((key, cached[1]))
        return tuple(parts)
