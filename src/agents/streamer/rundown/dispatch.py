"""流程单控制动作的统一分发器（human / agent 两条通道共享）。

Dashboard 手动控制与主播 Agent 的 LLM 工具两条通道共用本模块的
"动作名 + by + 参数 → 状态机方法"分发表与参数校验，任何通道对同一动作
得到同一强度的校验（``insert`` 的 payload 校验、``append`` 的 ``expected_ms``
下界对两侧一致生效）。动作分发、参数校验与结构化拒绝集中在这一份
事实源：各动作的参数要求与状态机白名单契约
对齐（``append`` 仅 Agent、``insert`` / ``restart`` 仅 human/system）。

通道适配器（``presentation.apply_rundown_control`` 出三元组、
``tools/rundown_tool`` 出 JSON 观察）只做结果形状翻译，不再各自分发。

``by`` 白名单语义、推进权归 Agent 语义都由 ``RundownState`` 自身契约承载，
本模块按通道传入的 ``by`` 原样透传，不做二次判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import uuid4

from src.modules.storage.models.rundown import RundownSegment

from .rundown_state import RundownReject, RundownState

__all__ = [
    "APPEND_MIN_EXPECTED_MS",
    "INSERT_DEFAULT_EXPECTED_MS",
    "AppendParamError",
    "DispatchOutcome",
    "dispatch_rundown_control",
]

#: 插入的一次性应急环节的默认预期时长（5 分钟；用户契约面不收时长字段，
#: 只为进度显示与超时提醒给一个合理量）
INSERT_DEFAULT_EXPECTED_MS: int = 300_000

#: append 动作的预期时长下界：追加环节至少值 1 分钟的直播时间，
#: 挡掉"随口起意 30 秒"这类碎片动作（超短内容不值得占用环节位）
APPEND_MIN_EXPECTED_MS: int = 60_000


class AppendParamError(ValueError):
    """append 环节参数形态不合法（通道无关的参数问题，非状态机拒绝）。

    Attributes:
        code: 稳定原因码（segment_not_object / expected_ms_below_min / segment_invalid）
        detail: 补充信息（如非法输入原值的 repr、模型校验报错原文）
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """控制动作的统一分发结果（通道无关）。

    Attributes:
        success: 动作是否被状态机接受
        reject: 状态机的结构化拒绝（成功或参数问题时为 ``None``）
        error_code: 参数校验失败的稳定原因码（成功与状态机拒绝时为空串）；
            通道适配器据码渲染各自的文案
        error_detail: 原因码的补充信息（如非法输入原值、模型校验报错原文）
    """

    success: bool
    reject: Optional[RundownReject] = None
    error_code: str = ""
    error_detail: str = ""


def _rejected(reject: RundownReject) -> DispatchOutcome:
    return DispatchOutcome(success=False, reject=reject)


def _param_error(error_code: str, error_detail: str = "") -> DispatchOutcome:
    return DispatchOutcome(success=False, error_code=error_code, error_detail=error_detail)


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


def _build_append_segment(payload: Any) -> RundownSegment:
    """把 append 的 ``segment`` dict 装成环节对象；参数形态不合法抛 ``AppendParamError``。

    ``seg_id`` 是 LLM 工具契约的字段名，模型层字段叫 ``id``，在此换装；
    ``expected_ms`` 下界在此统一拦（human / agent 两侧通道同强度生效）。
    """
    if not isinstance(payload, dict):
        raise AppendParamError("segment_not_object")
    expected_ms = payload.get("expected_ms")
    if not isinstance(expected_ms, int) or isinstance(expected_ms, bool) or expected_ms < APPEND_MIN_EXPECTED_MS:
        raise AppendParamError("expected_ms_below_min", repr(expected_ms))
    seg_dict = dict(payload)
    seg_dict["id"] = seg_dict.pop("seg_id", "")
    try:
        return RundownSegment.model_validate(seg_dict)
    except Exception as exc:
        raise AppendParamError("segment_invalid", str(exc)) from exc


def dispatch_rundown_control(
    state: RundownState,
    action: str,
    *,
    by: str,
    segment_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    now_ms: Optional[int] = None,
) -> DispatchOutcome:
    """执行流程单控制动作并统一翻译状态机拒绝（所有通道的唯一分发入口）。

    Args:
        state: ``RundownState`` 实例（鸭子使用：仅调其公开方法）
        action: 控制动作名（pause/resume/next/renew/restart/goto/insert/append）
        by: 触发主体（``"human"`` / ``"agent"`` / ``"system"``），原样透传给
            状态机——各动作的 by 白名单由状态机契约执行，非法值抛 ``ValueError``
        segment_id: goto 必填；其它动作忽略
        payload: insert 为 ``{title, content?}``；append 为环节定义 dict
            （``seg_id/title/task_description/expected_ms`` 必填）；其它动作忽略
        now_ms: 可选时间戳（默认走 RundownState 注入时钟或真实时钟）

    Returns:
        :class:`DispatchOutcome`——成功 ``success=True``；状态机拒绝带
        ``reject``；参数问题带 ``error_code``（unknown_action /
        missing_segment_id / missing_insert_title / append_segment_not_object /
        append_expected_ms_below_min / append_segment_invalid）。
        状态机抛出的异常（非法 by 等程序员错误、内部错误）原样上抛，由
        通道适配器按各自策略兜底。
    """
    if action == "pause":
        reject = state.pause(by=by, now_ms=now_ms)
    elif action == "resume":
        reject = state.resume(by=by, now_ms=now_ms)
    elif action == "renew":
        reject = state.renew(by=by, now_ms=now_ms)
    elif action == "next":
        reject = state.next(by=by, now_ms=now_ms)
    elif action == "restart":
        # 整场重开是导演通道（Agent 工具不暴露 restart）
        reject = state.restart(by=by, now_ms=now_ms)
    elif action == "goto":
        if not segment_id:
            return _param_error("missing_segment_id")
        reject = state.goto(segment_id, by=by, now_ms=now_ms)
    elif action == "insert":
        segment = _build_insert_segment(payload)
        if segment is None:
            return _param_error("missing_insert_title")
        reject = state.insert_after_current(segment, by=by, now_ms=now_ms)
    elif action == "append":
        # append 是 Agent 的计划扩展权；参数整形与下界校验统一在此完成
        # （先拦通道传参错误，再交给模型层校验与状态机结构化拒绝）
        try:
            segment = _build_append_segment(payload)
        except AppendParamError as exc:
            return _param_error(f"append_{exc.code}", exc.detail)
        reject = state.append_segment(segment, by=by, now_ms=now_ms)
    else:
        return _param_error("unknown_action", action)

    if reject is not None:
        return _rejected(reject)
    return DispatchOutcome(success=True)
