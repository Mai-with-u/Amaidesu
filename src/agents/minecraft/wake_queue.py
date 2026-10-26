"""Minecraft 消息队列：队列项协议与开批判定的单一归属。

队列里流通的项共用 ``(task_id, content)`` 二元组形状，语义差异全部收在本模块一处：

- ``MinecraftInstruction``（state.py）：玩家指令（委派或递话）——永远能开批，按任务号可撤。
- ``WakeEvenIdle``（state.py）：闲着也要叫醒的通知（计时到点、Mod 挂出的决策）——能开批、
  不可按任务号撤（task_id 恒为空串）；轮到读时才按任务状态分流，见 ``opens_task``。
- ``SkyNotice``：天色通知——不叫醒模型，随下一次唤醒捎带；新的天色来了就地替换，队列里只留最新一条。
- 普通通知元组：后台事件的结果说明（目标结束、提问、暂停、取消通知等）——不能独立开批，
  已交付或挂起时在队列里等着，直到有指令或闲着也叫醒的通知把批开起来一起读。

``WakeQueue`` 只管队列协议（投递、消费、撤单、天色替换、能否开批）；任务状态
（已交付/挂起）归 Agent 所有，判定函数以参数接收，不反向依赖 Agent。
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Iterator, NamedTuple, Optional, Tuple, Union

from src.agents.minecraft.state import MinecraftInstruction, WakeEvenIdle

__all__ = ["SkyNotice", "WakeQueue", "can_wake_idle", "opens_task"]

# 队列项的统一形状：具名元组或普通二元组，末位总是给模型读的正文
QueueItem = Union[MinecraftInstruction, WakeEvenIdle, "SkyNotice", Tuple[str, str]]


class SkyNotice(NamedTuple):
    """天色通知：不叫醒模型，挂在队列里随下一次唤醒捎带给模型。

    task_id 恒为空串——天色不属于任何任务，也不参与按任务号撤单。
    """

    task_id: str
    content: str


def opens_task(item: QueueItem, *, task_finished: bool) -> bool:
    """轮到读这条队列项时，是否要开（新）任务，而不是当普通通知读。

    玩家指令永远开任务；闲着也叫醒的通知排队时不定处理方式，轮到读的那一刻才看任务状态：
    已交付就和一条指令一样开新任务（计时的备注、要回答的决策就是这次要做的事），
    挂起或进行中只是接着原任务读的一条通知。
    """
    if isinstance(item, MinecraftInstruction):
        return True
    return isinstance(item, WakeEvenIdle) and task_finished


def can_wake_idle(item: QueueItem) -> bool:
    """闲着（已交付或挂起）时这条能不能独自开批。

    与 ``opens_task`` 同源：把两种闲着状态各代入一次，任一成立即能叫醒——
    玩家指令与闲着也叫醒的通知是仅有的两种。
    """
    return opens_task(item, task_finished=True) or opens_task(item, task_finished=False)


class WakeQueue:
    """Minecraft 消息队列：投递、消费、按任务号撤单、天色只留最新、能否开批，协议一处可查。"""

    def __init__(self) -> None:
        self._items: Deque[QueueItem] = deque()
        # 还排在队列里、没被模型读到的最新天色通知；用它持有引用，替换按对象身份撤下
        self._sky_notice: Optional[SkyNotice] = None

    # ---- 只读面 ----

    @property
    def items(self) -> Deque[QueueItem]:
        """队列本体（既有调用面与测试直接读；写入请走本类方法，天色替换的簿记在方法里）。"""
        return self._items

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self) -> Iterator[QueueItem]:
        return iter(self._items)

    def can_wake(self) -> bool:
        """队列里有没有闲着也能开批的项（玩家指令或闲着也叫醒的通知）。"""
        return any(can_wake_idle(item) for item in self._items)

    # ---- 投递 ----

    def push_instruction(self, task_id: str, content: str) -> None:
        """排一条玩家指令（委派带任务号，递话任务号为空串）。"""
        self._items.append(MinecraftInstruction(task_id, content))

    def push_notice(self, content: str) -> None:
        """排一条普通通知（后台事件的结果说明，不能独立开批）。"""
        self._items.append(("", content))

    def push_wake_even_idle(self, content: str) -> None:
        """排一条闲着也要叫醒模型的通知（计时到点、Mod 挂出的决策）。"""
        self._items.append(WakeEvenIdle("", content))

    # ---- 消费 ----

    def pop(self) -> QueueItem:
        """出队最早的一条（消费方只在本类判定函数的配合下分流）。"""
        return self._items.popleft()

    def clear(self) -> None:
        """清空全部未处理消息（收工即停时用）。"""
        self._items.clear()
        self._sky_notice = None

    # ---- 撤单与天色替换 ----

    def drop_task(self, task_id: str) -> bool:
        """把还排在队列里、没被任何批次吸收的某任务条目整批出队。

        只有玩家指令带任务号，命中的只可能是委派单；空任务号（普通通知、闲着也叫醒的通知、
        天色）不参与撤单。返回是否有撤下的条目。
        """
        if not task_id:
            return False
        remaining = [item for item in self._items if item[0] != task_id]
        if len(remaining) == len(self._items):
            return False
        self._items.clear()
        self._items.extend(remaining)
        return True

    def replace_sky_notice(self, content: str) -> None:
        """挂最新一条天色通知，还没被读到的上一条就地撤下，队列里只留最新一条。

        撤下按对象身份判定：天色内容可能逐字重复，按值相等会误伤同文的其他条目。
        """
        self._drop_pending_sky()
        self._sky_notice = SkyNotice("", content)
        self._items.append(self._sky_notice)

    def drop_sky_notice(self) -> None:
        """撤下还没被读到的天色通知（任务已交付时不再挂，过时的天色留着只会误导）。"""
        self._drop_pending_sky()

    def _drop_pending_sky(self) -> None:
        stale, self._sky_notice = self._sky_notice, None
        if stale is None:
            return
        for index, item in enumerate(self._items):
            if item is stale:
                del self._items[index]
                return
