"""流程单视图拼装与手动控制翻译（Dashboard 消费面）。

视图要知道 ``Rundown``/环节数据形状、控制要翻译状态机的结构化拒绝——
这两块知识属于流程单域，集中在流程单子包内，改动流程单只看本目录。
控制动作的分发、参数校验与结构化拒绝在 :mod:`.dispatch`（两条通道共享），
本模块只做 human 通道的形状翻译：出 ``(success, message, snapshot)`` 三元组
供 Dashboard 渲染。``state`` 即 ``RundownState`` 实例（鸭子使用：仅调其公开方法）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.modules.logging import get_logger

from .dispatch import INSERT_DEFAULT_EXPECTED_MS, dispatch_rundown_control
from .rundown_state import SEGMENTS_MAX, RundownState

__all__ = ["INSERT_DEFAULT_EXPECTED_MS", "apply_rundown_control", "build_rundown_view"]

_logger = get_logger("StreamerAgent.rundown.presentation")


def build_rundown_view(state: RundownState, *, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """聚合 Dashboard 编排页所需的流程单视图数据。

    Returns:
        ``None`` 当流程单未加载；否则返回::

            {
                "snapshot": dict,  # RundownState.get_snapshot() 原样
                "transitions": list[dict],  # 最近 50 条变更历史
                "segments": list[dict],  # 环节清单（id/title/目标/时长/备注）
            }
    """
    rundown = state.rundown
    if rundown is None:
        return None

    segments_view: List[Dict[str, Any]] = [
        {
            "id": seg.id,
            "title": seg.title,
            "task_description": seg.task_description,
            "expected_ms": seg.expected_ms,
            "min_duration_ms": seg.min_duration_ms,
            "notes": seg.notes,
        }
        for seg in rundown.segments
    ]

    return {
        "snapshot": state.get_snapshot(now_ms=now_ms),
        "transitions": state.get_transitions(),
        "segments": segments_view,
    }


def _human_message(outcome_result: Any) -> str:
    """把分发结果翻译成 Dashboard 文案（参数问题与状态机拒绝共用一张表）。"""
    if outcome_result.error_code != "":
        # 未知动作原样带上动作名（与历史文案一致）；其余按原因码查表
        if outcome_result.error_code == "unknown_action":
            return f"未知 action: {outcome_result.error_detail!r}"
        if outcome_result.error_code == "missing_segment_id":
            return "goto 必须提供 segment_id"
        if outcome_result.error_code == "missing_insert_title":
            return "insert 必须提供非空 title（payload: {title, content?}）"
        return outcome_result.error_code

    reject = outcome_result.reject
    if reject.reason == "min_duration_not_met":
        return f"当前环节最少停留未到（还需约 {max(1, round(reject.remaining_ms / 1000))} 秒）"
    if reject.reason == "unknown_segment_id":
        return f"环节不存在（可用: {', '.join(reject.available_ids)}）"
    static_messages = {
        "blank_title_target": "目标环节标题为空（数据问题），请在流程单编辑中补全标题后再切换",
        "already_done": "流程单已完成",
        "not_started": "流程单尚未开始",
        "already_begun": "流程单已开始",
        "not_running": "流程单未在运行",
        "not_paused": "流程单未在暂停",
        "no_rundown_loaded": "未加载流程单",
        "duplicate_segment_id": "环节 id 重复（数据问题），请重试",
        "limit_reached": f"环节已达上限（{SEGMENTS_MAX} 个），请先合并或删减环节",
    }
    return static_messages.get(reject.reason, reject.reason)


def apply_rundown_control(
    state: RundownState,
    action: str,
    *,
    segment_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    now_ms: Optional[int] = None,
) -> tuple[bool, str, Optional[Dict[str, Any]]]:
    """执行 Dashboard 手动控制动作（pause/resume/next/goto/renew/restart/insert，by="human"）。

    仅做分发转发与结构化拒绝翻译；调用方可凭 ``success`` / ``message`` /
    ``snapshot`` 三元组渲染 UI。

    Args:
        action: 控制动作名（pause/resume/next/goto/renew/restart/insert）
        segment_id: goto 必填；其它动作忽略
        payload: insert 必填（``{title, content?}``，title 非空）；其它动作忽略
        now_ms: 可选时间戳（默认走 RundownState 注入时钟或真实时钟）

    Returns:
        ``(success, message, snapshot)``：失败时 ``snapshot`` 为 ``None``
        （参数问题）或当前快照（状态机拒绝 / 异常）。
    """

    def _snap() -> Optional[Dict[str, Any]]:
        try:
            return state.get_snapshot(now_ms=now_ms)
        except Exception as exc:  # pragma: no cover - 防御
            _logger.warning(f"rundown_control 取快照失败: {exc}")
            return None

    try:
        outcome = dispatch_rundown_control(
            state,
            action,
            by="human",
            segment_id=segment_id,
            payload=payload,
            now_ms=now_ms,
        )
    except ValueError as exc:
        # 契约级错误（非法 by 等程序员错误）
        return False, str(exc), _snap()
    except Exception as exc:
        # 状态机内部错误统一兜底，避免 dashboard 500
        _logger.warning(f"rundown_control {action!r} 异常: {exc}", exc=True)
        return False, f"控制失败: {exc}", _snap()

    if outcome.success:
        return True, "已执行", _snap()
    return False, _human_message(outcome), (None if outcome.reject is None else _snap())
