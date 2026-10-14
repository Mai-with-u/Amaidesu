"""前缀稳定性自检：相邻两次请求的稳定层被意外改写时记告警。

服务端前缀缓存按 token 前缀命中，稳定层（系统提示词、纪要、工作层）中段
任何一个字节变化，之后全部重算。约定"只追加"只靠人守容易破，这里把它变成
可测量的：Agent 每次请求前把稳定层交给自检，上一次的稳定层不再是这一次的
前缀、又不是预告过的检查点时，记一条带位置的告警。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, List, Mapping, Optional, Sequence

from src.modules.logging import get_logger

__all__ = ["PrefixGuard"]

logger = get_logger("ContextPrefix")


def _fingerprint(message: Mapping[str, Any]) -> str:
    """消息指纹：只看模型可见字段，下划线开头的内部标记不参与。"""
    visible = {key: value for key, value in message.items() if not str(key).startswith("_")}
    raw = json.dumps(visible, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=8).hexdigest()


class PrefixGuard:
    """记住上一次请求的稳定层指纹，判断这一次是否只在尾部追加。"""

    def __init__(self, name: str) -> None:
        self._name = name
        self._previous: List[str] = []
        self._expected_reason = ""

    def expect_rewrite(self, reason: str) -> None:
        """预告下一次请求会改写稳定层（检查点、开新任务），届时不告警。"""
        self._expected_reason = reason

    def observe(self, stable: Sequence[Mapping[str, Any]]) -> Optional[str]:
        """登记这一次的稳定层；被意外改写时记告警并返回说明，否则返回 None。"""
        current = [_fingerprint(message) for message in stable]
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
