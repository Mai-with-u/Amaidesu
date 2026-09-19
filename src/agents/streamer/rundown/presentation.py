"""流程单视图拼装与手动控制翻译（Dashboard 消费面）。

视图要知道 ``Rundown``/环节数据形状、控制要翻译状态机的结构化拒绝——
这两块知识属于流程单域，集中在流程单子包内，改动流程单只看本目录。
两个函数都只读/翻译，不持状态；``state`` 即 ``RundownState`` 实例
（鸭子使用：仅调其公开方法）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.modules.logging import get_logger

from .rundown_state import RundownState

__all__ = ["apply_rundown_control", "build_rundown_view"]

_logger = get_logger("StreamerAgent.rundown.presentation")


def build_rundown_view(state: RundownState, *, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """聚合 Dashboard 编排页所需的流程单视图数据。

    Returns:
        ``None`` 当流程单未加载；否则返回::

            {
                "snapshot": dict,  # RundownState.get_snapshot() 原样
                "transitions": list[dict],  # 最近 50 条变更历史
                "segments": list[dict],  # 环节清单（id/title/目标/要点/时长/备注）
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
            "key_points": list(seg.key_points),
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


def apply_rundown_control(
    state: RundownState,
    action: str,
    *,
    segment_id: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> tuple[bool, str, Optional[Dict[str, Any]]]:
    """执行 Dashboard 手动控制动作（pause/resume/next/goto，by="human"）。

    仅做转发与结构化拒绝翻译；调用方可凭 ``success`` / ``message`` /
    ``snapshot`` 三元组渲染 UI。

    Args:
        action: 控制动作名（pause/resume/next/goto）
        segment_id: goto 必填；其它动作忽略
        now_ms: 可选时间戳（默认走 RundownState 注入时钟或真实时钟）

    Returns:
        ``(success, message, snapshot)``：失败时 ``snapshot`` 为 ``None``。
    """

    def _snap() -> Optional[Dict[str, Any]]:
        try:
            return state.get_snapshot(now_ms=now_ms)
        except Exception as exc:  # pragma: no cover - 防御
            _logger.warning(f"rundown_control 取快照失败: {exc}")
            return None

    try:
        if action == "pause":
            reject = state.pause(by="human", now_ms=now_ms)
        elif action == "resume":
            reject = state.resume(by="human", now_ms=now_ms)
        elif action == "next":
            reject = state.next(by="human", now_ms=now_ms)
        elif action == "goto":
            if not segment_id:
                return False, "goto 必须提供 segment_id", None
            reject = state.goto(segment_id, by="human", now_ms=now_ms)
        else:
            return False, f"未知 action: {action!r}", None

        if reject is not None:
            messages = {
                "min_duration_not_met": (
                    f"当前环节最少停留未到（还需约 {max(1, round(reject.remaining_ms / 1000))} 秒）"
                ),
                "unknown_segment_id": f"环节不存在（可用: {', '.join(reject.available_ids)}）",
                "already_done": "流程单已完成",
                "not_running": "流程单未在运行",
                "not_paused": "流程单未在暂停",
                "no_rundown_loaded": "未加载流程单",
            }
            return False, messages.get(reject.reason, reject.reason), _snap()
        return True, "已执行", _snap()
    except ValueError as exc:
        # 契约级错误（非法 by 等程序员错误）
        return False, str(exc), _snap()
    except Exception as exc:
        # 状态机内部错误统一兜底，避免 dashboard 500
        _logger.warning(f"rundown_control {action!r} 异常: {exc}", exc=True)
        return False, f"控制失败: {exc}", _snap()
