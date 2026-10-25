"""流程单视图拼装与手动控制翻译（Dashboard 消费面）。

视图要知道 ``Rundown``/环节数据形状、控制要翻译状态机的结构化拒绝——
这两块知识属于流程单域，集中在流程单子包内，改动流程单只看本目录。
两个函数都只读/翻译，不持状态；``state`` 即 ``RundownState`` 实例
（鸭子使用：仅调其公开方法）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.modules.logging import get_logger
from src.modules.storage.models.rundown import RundownSegment

from .rundown_state import SEGMENTS_MAX, RundownState

__all__ = ["INSERT_DEFAULT_EXPECTED_MS", "apply_rundown_control", "build_rundown_view"]

_logger = get_logger("StreamerAgent.rundown.presentation")

#: 插入的一次性应急环节的默认预期时长（5 分钟；用户契约面不收时长字段，
#: 只为进度显示与超时提醒给一个合理量）
INSERT_DEFAULT_EXPECTED_MS: int = 300_000


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


def _build_insert_segment(payload: Any) -> Optional[RundownSegment]:
    """把 insert 的 ``payload`` dict 装成一次性环节；缺非空标题返回 ``None``。

    环节 id 由服务端生成（随机短 id，用户契约面只给 title/content）；内容映射到
    ``task_description``（注入 Agent 情境的目标指引）。
    """
    if not isinstance(payload, dict):
        return None
    title = str(payload.get("title") or "").strip()
    if not title:
        return None
    content = str(payload.get("content") or "").strip()
    return RundownSegment(
        id=f"insert-{uuid4().hex[:8]}",
        title=title,
        task_description=content,
        expected_ms=INSERT_DEFAULT_EXPECTED_MS,
    )


def apply_rundown_control(
    state: RundownState,
    action: str,
    *,
    segment_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    now_ms: Optional[int] = None,
) -> tuple[bool, str, Optional[Dict[str, Any]]]:
    """执行 Dashboard 手动控制动作（pause/resume/next/goto/renew/restart/insert，by="human"）。

    仅做转发与结构化拒绝翻译；调用方可凭 ``success`` / ``message`` /
    ``snapshot`` 三元组渲染 UI。

    Args:
        action: 控制动作名（pause/resume/next/goto/renew/restart/insert）
        segment_id: goto 必填；其它动作忽略
        payload: insert 必填（``{title, content?}``，title 非空）；其它动作忽略
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
        elif action == "renew":
            reject = state.renew(by="human", now_ms=now_ms)
        elif action == "next":
            reject = state.next(by="human", now_ms=now_ms)
        elif action == "restart":
            # 整场重开是导演通道（Agent 工具不暴露 restart）
            reject = state.restart(by="human", now_ms=now_ms)
        elif action == "goto":
            if not segment_id:
                return False, "goto 必须提供 segment_id", None
            reject = state.goto(segment_id, by="human", now_ms=now_ms)
        elif action == "insert":
            segment = _build_insert_segment(payload)
            if segment is None:
                return False, "insert 必须提供非空 title（payload: {title, content?}）", None
            reject = state.insert_after_current(segment, by="human", now_ms=now_ms)
        else:
            return False, f"未知 action: {action!r}", None

        if reject is not None:
            messages = {
                "min_duration_not_met": (
                    f"当前环节最少停留未到（还需约 {max(1, round(reject.remaining_ms / 1000))} 秒）"
                ),
                "unknown_segment_id": f"环节不存在（可用: {', '.join(reject.available_ids)}）",
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
            return False, messages.get(reject.reason, reject.reason), _snap()
        return True, "已执行", _snap()
    except ValueError as exc:
        # 契约级错误（非法 by 等程序员错误）
        return False, str(exc), _snap()
    except Exception as exc:
        # 状态机内部错误统一兜底，避免 dashboard 500
        _logger.warning(f"rundown_control {action!r} 异常: {exc}", exc=True)
        return False, f"控制失败: {exc}", _snap()
