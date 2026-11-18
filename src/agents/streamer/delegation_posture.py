"""委派处境判定：一件委派此刻处在哪种处境的唯一判定点。

"处境"是把两套账面状态词归纳成主播侧真正关心的几类：
- 任务账本状态词表（``src/modules/tools/tasks.py`` 的 ``TaskStatus``，权威定义）；
- 存储委派账（``DelegationRecord``）的状态词：受理/排队写 ``accepted`` / ``queued``，
  终态沿用任务账本终态，另有一个任务账本没有的 ``interrupted``（进程重启打断）。

判定点此前散在续播恢复、续播认领、下播收尾、停滞兜底等处，各自与状态字符串
直接比较，词表漂移无感知；本模块集中后，各处按处境分支、不再摸原始字符串。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

from src.modules.storage.models.events import DelegationRecord
from src.modules.tools.tasks import (
    DELIVERED_TASK_STATES,
    TASK_QUEUED,
    TASK_WAITING_FOR_DECISION,
    TERMINAL_TASK_STATES,
)

__all__ = [
    "DelegationPosture",
    "posture_of_delegation_record",
    "posture_of_task_status",
    "snapshot_awaits_decision",
    "was_queued",
]

# 存储委派账独有的状态：进程重启时还没走到终态的委派由落库器记成被打断
RECORD_INTERRUPTED = "interrupted"


class DelegationPosture(Enum):
    """委派此刻的处境（按主播侧关心方式归纳）。"""

    IN_HAND = "in_hand"  # 已送达执行方、正在做
    QUEUED = "queued"  # 在待办里排队（还没开工或先搁下）
    AWAITING_DECISION = "awaiting_decision"  # 停着等发起方定夺
    SETTLED = "settled"  # 已走到终态（做成、撤单、失败等）
    INTERRUPTED = "interrupted"  # 重启打断，做到哪不确定
    UNRECOGNIZED = "unrecognized"  # 词表外的值，不假设


def posture_of_task_status(status: str) -> DelegationPosture:
    """按任务账本状态词表判处境；存储委派账的状态词与之兼容，共用本判定。"""
    normalized = (status or "").strip()
    if normalized == TASK_WAITING_FOR_DECISION:
        return DelegationPosture.AWAITING_DECISION
    if normalized == TASK_QUEUED:
        return DelegationPosture.QUEUED
    if normalized in DELIVERED_TASK_STATES:
        return DelegationPosture.IN_HAND
    if normalized in TERMINAL_TASK_STATES:
        return DelegationPosture.SETTLED
    if normalized == RECORD_INTERRUPTED:
        return DelegationPosture.INTERRUPTED
    return DelegationPosture.UNRECOGNIZED


def posture_of_delegation_record(record: DelegationRecord) -> DelegationPosture:
    """存储委派账一条记录的处境。"""
    return posture_of_task_status(record.status)


def snapshot_awaits_decision(record: Optional[Mapping[str, Any]]) -> bool:
    """任务账本快照（status_provider 返回的 mapping）里的委派是否停着等发起方定夺。"""
    if record is None:
        return False
    return posture_of_task_status(str(record.get("status") or "")) is DelegationPosture.AWAITING_DECISION


def was_queued(record: DelegationRecord) -> bool:
    """重启前的委派当时是否在待办排队（而非在执行方手上做）。

    排队事实看 ``prior_status``；更早的账面行没有记 delegation_queued 受理行、
    ``prior_status`` 判不出时，退回看终态摘要文字里的"待办"（旧行排队事实只写在
    摘要里）。只用于续播恢复/下播收尾读库侧记录；运行期以任务账本为准。
    """
    if posture_of_task_status(record.prior_status) is DelegationPosture.QUEUED:
        return True
    return "待办" in (record.summary or "")
