"""场次流程单执行回看服务

把 ``rundown_transitions`` 变更事实行（append-only）重放为"各环节实际停留
vs 预计时长"的结构化视图。纯计算：事实行进、执行视图出，不含 HTTP 语义，
不做任何模板表 JOIN（每行自带的 ``expected_ms`` 就是该环节当时的预计时长）。

重放规则（与 ``rundown.changed`` 载荷语义对齐——``segment_id`` 是变更后的
环节 id，空串 = finish/游标重置无当前环节）：

- 状态变更行（goto/next/load/append/restore/renew…）把当前环节切到
  ``segment_id``；同段重复声明不重开；
- ``pause`` / ``resume`` 不切环节，只在其间累计暂停时长（按段单列）；
- 尾部未闭合段以场次结束时刻封口；结束边界未知（进行中场次）时停留
  不计，``left_at_ms`` 为空由前端标注"仍在该环节"。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.modules.dashboard.schemas.session import (
    RundownSegmentRun,
    SessionRundownExecutionResponse,
)
from src.modules.logging import get_logger
from src.modules.storage.models.rundown import RundownTransition

logger = get_logger("SessionRundownService")

__all__ = ["build_rundown_execution"]


def build_rundown_execution(
    live_session_id: int,
    transitions: List[RundownTransition],
    *,
    ended_at_ms: Optional[int] = None,
) -> SessionRundownExecutionResponse:
    """事实行列表 → 执行视图（``transitions`` 须按 ``at_ms`` 升序）。"""
    runs: List[RundownSegmentRun] = []
    current: Optional[RundownSegmentRun] = None
    pause_started: Optional[int] = None
    expected_by_seg: Dict[str, int] = {}
    rundown_id = ""

    for t in transitions:
        rundown_id = t.rundown_id or rundown_id
        if t.seg_id and t.expected_ms is not None:
            # 每行都携带当前环节的预计时长；模板中途改写以最新一次为准
            expected_by_seg[t.seg_id] = int(t.expected_ms)
        if t.action == "pause":
            if current is not None and pause_started is None:
                pause_started = t.at_ms
            continue
        if t.action == "resume":
            if current is not None and pause_started is not None:
                current.pause_ms += max(0, t.at_ms - pause_started)
                pause_started = None
            continue

        new_seg = t.seg_id or None
        if current is not None and new_seg == current.seg_id:
            continue
        if current is not None:
            current.left_at_ms = t.at_ms
            current.dwell_ms += max(0, t.at_ms - current.entered_at_ms)
        if new_seg is None:
            current = None
            pause_started = None
            continue
        current = RundownSegmentRun(
            seg_id=t.seg_id,
            seg_title=t.seg_title,
            seg_index=t.seg_index,
            entered_at_ms=t.at_ms,
            expected_ms=expected_by_seg.get(t.seg_id),
        )
        runs.append(current)

    if current is not None and ended_at_ms is not None:
        current.left_at_ms = ended_at_ms
        current.dwell_ms += max(0, ended_at_ms - current.entered_at_ms)
        if pause_started is not None:
            # 收播时仍处暂停：这段墙钟时间计入该段暂停单列
            current.pause_ms += max(0, ended_at_ms - pause_started)

    return SessionRundownExecutionResponse(
        live_session_id=live_session_id,
        rundown_id=rundown_id,
        segments=runs,
        pause_total_ms=sum(r.pause_ms for r in runs),
        transitions_count=len(transitions),
    )
