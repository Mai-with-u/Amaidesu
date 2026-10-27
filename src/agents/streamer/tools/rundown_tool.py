"""rundown_tool - 流程单控制工具（Planner ReAct 循环的推进出口）

**注册形态**：provider="rundown"、声明名 control → 全名 ``rundown_control``,
经 ``build_rundown_tool_provider`` 组装后由 StreamerAgent 注册进 ToolRegistry
（可见名单 ``["streamer"]``）。Planner 与其他工具同路径经 ``registry.invoke``
调用；可见性随 Provider 注册/摘除进出名单。推进权归 Agent：何时切换环节是
决策脑自己的决定，本工具只提供能力通道，并把 ``RundownState`` 的结构化拒绝
（未知环节 id / 未达最少停留）原样重新写入——Agent 读到拒绝原因后自纠，
不走异常通道。

动作分发、参数校验与结构化拒绝在 ``../rundown/dispatch.py``（与 Dashboard
手动控制共享同一份事实源），本模块只做 agent 通道的形状翻译：出 JSON 观察。

工具契约：
- OpenAI function 形态（``_CONTROL_SPEC`` 经 ToolSpec → function def 转换）
- ``RundownControlProvider.invoke(args)`` 同步执行（状态机方法非阻塞），
  返回观察 JSON：成功带流程单快照；拒绝带 reason / available_segment_ids /
  remaining_ms
"""

from __future__ import annotations

import json
from typing import Any, Dict

from src.modules.logging import get_logger
from src.modules.tools import ToolInvocation
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs

from ..rundown.dispatch import APPEND_MIN_EXPECTED_MS, dispatch_rundown_control
from ..rundown.rundown_state import RundownState

__all__ = [
    "build_rundown_tool_provider",
    "RundownControlProvider",
]

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["next", "goto", "pause", "resume", "renew", "append"],
            "description": (
                "next=结束当前环节切到下一环节（当前是末段时结束整场流程单）；"
                "goto=跳到指定环节（不限方向）；pause=暂停环节计时；resume=恢复计时；"
                "renew=续期当前环节（计时重新起算，本环节再给一个完整预期周期）；"
                "append=在流程单尾部追加新环节（需 segment 参数）"
            ),
        },
        "segment_id": {
            "type": "string",
            "description": "goto 的目标环节 id（action=goto 时必填；可从情境注入的后续环节列表取得）",
        },
        "segment": {
            "type": "object",
            "description": "action=append 时必填：新环节定义（seg_id / title / task_description /"
            " expected_ms 必填，min_duration_ms / notes 可选）",
            "properties": {
                "seg_id": {"type": "string", "description": "新环节唯一标识（与现有环节 id 不得重复）"},
                "title": {"type": "string", "description": "环节标题（非空）"},
                "task_description": {"type": "string", "description": "给 AI 的目标指引"},
                "expected_ms": {"type": "integer", "description": "预期停留时长（毫秒，至少 60000）"},
                "min_duration_ms": {"type": "integer", "description": "最少停留时长（毫秒，可选）"},
                "notes": {"type": "string", "description": "备注（可选）"},
            },
            "required": ["seg_id", "title", "task_description", "expected_ms"],
        },
    },
    "required": ["action"],
}

# 注册形态的 spec：provider="rundown"、声明名 control（全名 rundown_control 派生）
_CONTROL_SPEC = ToolSpec(
    name="control",
    description=(
        "流程单控制：切换直播环节或暂停/恢复/续期环节计时。"
        "当本环节目标已达成、或剩余时间不多且话题自然收束时，用 next/goto 推进；"
        "环节已超时但话题仍值得继续时，用 renew 为本环节续一个周期；"
        "流程单走完但直播仍要继续、或临时起意要做一件值得持续几分钟的事时，"
        "用 append 在流程单尾部追加环节（新环节自带预期时长与超时闹钟）；"
        "被拒绝时读取原因（如最少停留未到），不要盲目重试同一调用。"
    ),
    parameters_schema=_PARAMETERS_SCHEMA,
    kind="sync",
    provider="rundown",
)


def build_rundown_tool_provider(provider: "RundownControlProvider") -> ToolProvider:
    """把 RundownControlProvider 包成注册形态的 ToolProvider（简单工具路径）。

    注册键 = 派生全名 ``rundown_control``（provider="rundown" + 声明名
    control）；执行体即 ``RundownControlProvider.invoke``——Planner 的调用
    经注册表落到这唯一状态机入口，无第二事实源。

    返回值带 ``structured_content``：快照/结构化拒绝（reason、remaining_ms 等）
    完整进入观察文本与 tool.result 事件，消费方拿到的信息与直连返回一致。
    """

    async def _run(inv: ToolInvocation) -> ToolExecutionResult:
        text = provider.invoke(dict(inv.arguments or {}))
        return ToolExecutionResult(
            tool_name=_CONTROL_SPEC.full_name,
            success=True,
            content=text,
            structured_content=json.loads(text),
        )

    return make_provider_from_specs(
        "rundown",
        [(_CONTROL_SPEC, as_tool_impl(_CONTROL_SPEC.full_name, _run))],
        category="framework",
    )


class RundownControlProvider:
    """rundown_control 的执行器（Agent 内部件协议，直连 ``RundownState``）。

    非线程安全；仅在 StreamerAgent 单一 asyncio 事件循环内使用。
    """

    def __init__(self, state: RundownState) -> None:
        self._state = state
        self._logger = get_logger("RundownControlTool")

    def invoke(self, args: Dict[str, Any]) -> str:
        """执行控制动作，返回观察 JSON 文本（成功与结构化拒绝都重新写入给 LLM）。"""
        action = str(args.get("action", "") or "")
        try:
            outcome = dispatch_rundown_control(
                self._state,
                action,
                by="agent",
                segment_id=str(args.get("segment_id", "") or "") or None,
                payload=args.get("segment"),
            )
        except Exception as exc:
            self._logger.warning(f"rundown_control 执行异常: {exc}", exc=True)
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

        if outcome.success:
            return json.dumps({"ok": True, "rundown": self._state.get_snapshot()}, ensure_ascii=False)

        # 参数问题的原因码 → 面向 LLM 的纠错提示（状态机拒绝走结构化通道）
        if outcome.error_code != "":
            if outcome.error_code == "unknown_action":
                return json.dumps({"ok": False, "error": f"未知 action={outcome.error_detail!r}"}, ensure_ascii=False)
            if outcome.error_code == "missing_segment_id":
                return json.dumps(
                    {"ok": False, "error": "goto 需要 segment_id（可从情境注入的后续环节列表取得）"},
                    ensure_ascii=False,
                )
            if outcome.error_code == "append_segment_not_object":
                return json.dumps(
                    {
                        "ok": False,
                        "error": "append 需要 segment 参数（对象，含 seg_id/title/task_description/expected_ms）",
                    },
                    ensure_ascii=False,
                )
            if outcome.error_code == "append_expected_ms_below_min":
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"追加环节预期时长至少 1 分钟（expected_ms >= {APPEND_MIN_EXPECTED_MS}，"
                        f"当前 {outcome.error_detail}）",
                    },
                    ensure_ascii=False,
                )
            if outcome.error_code == "append_segment_invalid":
                return json.dumps(
                    {"ok": False, "error": f"segment 参数校验失败: {outcome.error_detail}"},
                    ensure_ascii=False,
                )
            return json.dumps({"ok": False, "error": outcome.error_code}, ensure_ascii=False)

        reject = outcome.reject
        data: Dict[str, Any] = {"ok": False, "reason": reject.reason}
        if reject.available_ids:
            data["available_segment_ids"] = reject.available_ids
        if reject.remaining_ms:
            data["remaining_ms"] = reject.remaining_ms
        return json.dumps(data, ensure_ascii=False)
