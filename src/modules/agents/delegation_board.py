"""委派看板：一个执行者名下的委派单此刻各在什么处境，读法与叫法只在这里定一次。

委派单在任务账本里只有两种没了结的处境：

- **在做**：已经送到执行者手上、还没了结（账本状态 accepted / running / waiting_for_decision）。
  每个执行者任何时刻至多一件。
- **待办**：还没轮到（账本状态 queued）。分两种：从没送达过的（还没开工）；做过一段后被搁下、
  放回来的（带一句做到哪了）。

任务账本是唯一事实源：受理核心、发起方的参考段、看板都从这里现读，不各自另存一份队列——
以前受理核心、执行方、发起方工作台各记一份，三份对不上时，被搁下的那件会从发起方眼前消失。

短编号是给发起方的模型用的：它看不到内部任务号，凭"在做""待办3"就能点名结单。待办编号在一件事
第一次进待办时分配，之后不变也不重排，所以同一个编号始终指同一件事；直派后从没进过待办的那件
没有编号，只叫"在做"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.modules.agents.delegation_text import own_words_of
from src.modules.tools.tasks import DELIVERED_TASK_STATES, TaskLedger, TaskRecord

__all__ = [
    "BACKLOG_LABEL_PREFIX",
    "BACKLOG_NO_KEY",
    "BACKLOG_SEQ_KEY",
    "Board",
    "BoardItem",
    "DELEGATE_TOOL",
    "DELIVERED_AT_KEY",
    "DELIVERIES_KEY",
    "HELD_KEY",
    "IN_HAND_LABEL",
    "QUEUED_AT_KEY",
    "SHELVED_AT_KEY",
    "SHELVED_PROGRESS_KEY",
    "find_item",
    "read_board",
    "read_boards",
]

#: 委派单在账本里的受理工具名（据此从账本里认出委派单）
DELEGATE_TOOL = "framework_delegate"

#: 在做那一件的叫法、待办件叫法的前缀（后面接待办编号）
IN_HAND_LABEL = "在做"
BACKLOG_LABEL_PREFIX = "待办"

# 委派单快照里由受理核心写的字段（执行方写的进展备注等不在此列）
BACKLOG_NO_KEY = "backlog_no"  # 待办编号：第一次进待办时分配，之后不变
BACKLOG_SEQ_KEY = "backlog_seq"  # 待办里的先后：小的先轮到
QUEUED_AT_KEY = "queued_at_ms"  # 最近一次进待办的时刻
DELIVERIES_KEY = "deliveries"  # 送达过几次；0 = 还没开工
DELIVERED_AT_KEY = "delivered_at_ms"  # 最近一次送达的时刻
SHELVED_AT_KEY = "shelved_at_ms"  # 最近一次被搁下的时刻；0 = 没被搁下过
SHELVED_PROGRESS_KEY = "shelved_progress"  # 搁下时记的一句做到哪了
HELD_KEY = "held"  # 搁下时没有别的事接手：先别自动送回去，等有别的事送达过再轮到它


@dataclass(frozen=True)
class BoardItem:
    """看板上的一件委派：账本条目加上按处境读出来的几样（叫法、搁下与否、做到哪）。"""

    record: TaskRecord
    in_hand: bool

    @property
    def task_id(self) -> str:
        return self.record.task_id

    @property
    def snapshot(self) -> Dict[str, Any]:
        return self.record.snapshot

    @property
    def backlog_no(self) -> int:
        """待办编号；从没进过待办的为 0。"""
        return int(self.snapshot.get(BACKLOG_NO_KEY) or 0)

    @property
    def label(self) -> str:
        """给发起方看的短编号：在做的那件叫"在做"，待办件叫"待办N"。"""
        return IN_HAND_LABEL if self.in_hand else f"{BACKLOG_LABEL_PREFIX}{self.backlog_no}"

    @property
    def own_words(self) -> str:
        """发起方自己的原话（附带的来源对话不算）。"""
        return str(self.snapshot.get("own_words") or own_words_of(str(self.snapshot.get("instruction") or "")))

    @property
    def instruction(self) -> str:
        """指令全文（送达执行方用）。"""
        return str(self.snapshot.get("instruction") or "")

    @property
    def deliveries(self) -> int:
        return int(self.snapshot.get(DELIVERIES_KEY) or 0)

    @property
    def delivered_at_ms(self) -> int:
        """最近一次送达的时刻；没记过时退回受理时刻。"""
        return int(self.snapshot.get(DELIVERED_AT_KEY) or self.record.created_at_ms)

    @property
    def queued_at_ms(self) -> int:
        return int(self.snapshot.get(QUEUED_AT_KEY) or self.record.created_at_ms)

    @property
    def shelved(self) -> bool:
        """是不是做过一段后被搁下、放回待办的。"""
        return not self.in_hand and int(self.snapshot.get(SHELVED_AT_KEY) or 0) > 0

    @property
    def shelved_at_ms(self) -> int:
        return int(self.snapshot.get(SHELVED_AT_KEY) or 0)

    @property
    def shelved_progress(self) -> str:
        return str(self.snapshot.get(SHELVED_PROGRESS_KEY) or "")

    @property
    def held(self) -> bool:
        """搁下后先别自动送回去（见 ``HELD_KEY``）。"""
        return bool(self.snapshot.get(HELD_KEY))


@dataclass(frozen=True)
class Board:
    """一个执行者名下的委派：在做的那件（没有为 None）与按先后排好的待办。"""

    in_hand: Optional[BoardItem] = None
    backlog: Tuple[BoardItem, ...] = ()

    @property
    def empty(self) -> bool:
        return self.in_hand is None and not self.backlog

    def next_ready(self) -> Optional[BoardItem]:
        """待办里下一件可以自动送达的：最靠前、没被标成先别送的那件。"""
        return next((item for item in self.backlog if not item.held), None)


def _is_delegation(record: Optional[TaskRecord]) -> bool:
    return record is not None and record.source == "agent" and record.tool == DELEGATE_TOOL


def read_boards(ledger: Optional[TaskLedger]) -> Dict[str, Board]:
    """从任务账本读出每个执行者的看板（名下没有未了结委派的执行者不出现）。

    在做的若因故不止一件（不变量被破坏时），取最早送达的那件当在做，其余不丢、排到待办最前，
    让发起方看得见、能点名处置。
    """
    if ledger is None:
        return {}
    in_hand: Dict[str, list[TaskRecord]] = {}
    backlog: Dict[str, list[TaskRecord]] = {}
    for task_id in ledger.active_task_ids():
        record = ledger.get(task_id)
        if not _is_delegation(record):
            continue
        assert record is not None  # _is_delegation 已排除 None（类型收窄）
        if record.status in DELIVERED_TASK_STATES:
            in_hand.setdefault(record.executor, []).append(record)
        elif record.status == "queued":
            backlog.setdefault(record.executor, []).append(record)
    boards: Dict[str, Board] = {}
    for executor in sorted(set(in_hand) | set(backlog)):
        held = sorted(in_hand.get(executor, []), key=lambda r: (r.snapshot.get(DELIVERED_AT_KEY) or r.created_at_ms))
        waiting = sorted(
            backlog.get(executor, []),
            key=lambda r: (int(r.snapshot.get(BACKLOG_SEQ_KEY) or 0), r.created_at_ms, r.task_id),
        )
        current = BoardItem(held[0], in_hand=True) if held else None
        extra = [BoardItem(record, in_hand=False) for record in held[1:]]
        boards[executor] = Board(
            in_hand=current, backlog=tuple(extra + [BoardItem(record, in_hand=False) for record in waiting])
        )
    return boards


def read_board(ledger: Optional[TaskLedger], executor: str) -> Board:
    """从任务账本读出 ``executor`` 的看板；名下没有未了结的委派时是一张空看板。"""
    return read_boards(ledger).get(executor, Board())


def find_item(board: Board, ref: str) -> Optional[BoardItem]:
    """按短编号（"在做" / "待办3" / "3"）或内部任务号在看板上找一件；找不到返回 None。

    待办编号跟着这件事走：它轮到、成了在做之后，原来的"待办3"仍然指它——发起方这一步里
    先后发两个动作时，前一个动作让队列往前走了，后一个动作点的还是它看到的那一件。
    """
    text = ref.strip()
    if not text:
        return None
    items = ([board.in_hand] if board.in_hand is not None else []) + list(board.backlog)
    for item in items:
        if item.task_id == text:
            return item
    if text == IN_HAND_LABEL:
        return board.in_hand
    digits = text.removeprefix(BACKLOG_LABEL_PREFIX).strip()
    if not digits.isdigit():
        return None
    number = int(digits)
    return next((item for item in items if number > 0 and item.backlog_no == number), None)
