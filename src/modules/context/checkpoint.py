"""检查点：按组切分工作层，以及在后台提前准备检查点摘要。

检查点 = 把工作层最老的一段压成一章追加到纪要层。切分必须守住协议结构：
assistant 的工具调用与紧随其后的工具回执是一组，整组同进退——回执被压而
调用还留着，模型会以为那次调用没有结果。

摘要通常要调一次模型（几十秒）。越过提前整理线时就在后台把下一章算好，
涨到预算时直接替换；后台没算完、又已超预算的少数情况，由调用方决定等待
还是先用兜底摘要。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Dict, Generic, List, Mapping, Optional, Sequence, Tuple, TypeVar

from src.modules.logging import get_logger

__all__ = ["group_messages", "group_spans", "fold_count", "BackgroundCheckpoint"]

logger = get_logger("ContextCheckpoint")

T = TypeVar("T")


def group_spans(messages: Sequence[Mapping[str, Any]]) -> List[Tuple[int, int]]:
    """把消息序列切成不可拆的组，返回每组在原序列里的 ``[起, 止)`` 下标。

    工具调用与紧随其后的回执捆成一组，其余每条单独成组；没有前置调用的连续回执
    （被截断的旧历史）自成一组，可以整体压掉。按下标返回，调用方能就地替换整组。
    """
    spans: List[Tuple[int, int]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        end = index + 1
        if message.get("tool_calls") or message.get("role") == "tool":
            while end < len(messages) and messages[end].get("role") == "tool":
                end += 1
        spans.append((index, end))
        index = end
    return spans


def group_messages(messages: Sequence[Mapping[str, Any]]) -> List[List[Mapping[str, Any]]]:
    """把消息序列切成不可拆的组（切法见 ``group_spans``），每组是一段消息列表。"""
    return [list(messages[start:end]) for start, end in group_spans(messages)]


def fold_count(group_tokens: Sequence[int], *, keep_tokens: int, protect_last: int = 1) -> int:
    """从最老的组开始数，压掉几组才能让剩余部分不超过 ``keep_tokens``。

    最后 ``protect_last`` 组永远保留：它们是模型还没读过或刚读完的最新记录，
    压掉会让模型丢失当前进展。全部可压的组都压掉仍超量时，返回可压的最大组数。
    """
    foldable = max(0, len(group_tokens) - protect_last)
    remaining = sum(group_tokens)
    count = 0
    while count < foldable and remaining > keep_tokens:
        remaining -= group_tokens[count]
        count += 1
    return count


class BackgroundCheckpoint(Generic[T]):
    """后台检查点准备：同一时刻最多一个准备任务，结果取走即清。

    准备任务失败只记日志、不抛到决策链；调用方下次越过提前整理线时会重新发起。
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._task: Optional["asyncio.Task[T]"] = None
        self._meta: Dict[str, Any] = {}

    @property
    def busy(self) -> bool:
        """有准备任务在跑或已算完待取。"""
        return self._task is not None

    @property
    def meta(self) -> Dict[str, Any]:
        """发起时登记的附带信息（如这一章覆盖到哪条记录），随结果一起取走。"""
        return dict(self._meta)

    def start(self, work: Awaitable[T], **meta: Any) -> bool:
        """发起一次后台准备；已有任务在跑或结果未取时不重复发起。"""
        if self._task is not None:
            if asyncio.iscoroutine(work):
                work.close()
            return False
        self._meta = dict(meta)
        self._task = asyncio.ensure_future(work)
        return True

    def take(self) -> Optional[T]:
        """取走已算完的结果；没算完返回 None，失败记日志后清空以便下次重发。"""
        task = self._task
        if task is None or not task.done():
            return None
        self._task = None
        if task.cancelled():
            return None
        error = task.exception()
        if error is not None:
            logger.warning(f"{self._name} 后台检查点准备失败，下次越过提前整理线时重试：{error}", exc=error)
            return None
        return task.result()

    async def wait(self) -> Optional[T]:
        """等当前准备任务算完再取结果；没有任务时立即返回 None。"""
        task = self._task
        if task is None:
            return None
        await asyncio.wait({task})
        return self.take()

    async def cancel(self) -> None:
        """放弃当前准备任务（工作层被整体重置时，旧准备已失去意义）。"""
        task, self._task = self._task, None
        self._meta = {}
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 - 已放弃的准备任务，失败原因只记录不传播
            logger.debug(f"{self._name} 已放弃的检查点准备任务结束时报错：{exc}")
