"""rundown_tool - 流程单控制工具（Planner ReAct 循环的推进出口）

**注册形态**：provider="rundown"、声明名 control → 全名 ``rundown_control``，
经 ``build_rundown_tool_provider`` 组装后由 StreamerAgent 注册进 ToolRegistry
（可见名单 ``["streamer"]``）。Planner 与其他工具同路径经 ``registry.invoke``
调用；可见性随 Provider 注册/摘除进出名单。推进权归 Agent：何时切换环节是
决策脑自己的决定，本工具只提供能力通道，并把 ``RundownState`` 的结构化拒绝
（未知环节 id / 未达最少停留）原样重新写入——Agent 读到拒绝原因后自纠，
不走异常通道。

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
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs

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
            "enum": ["next", "goto", "pause", "resume"],
            "description": (
                "next=结束当前环节切到下一环节（当前是末段时结束整场流程单）；"
                "goto=跳到指定环节（不限方向）；pause=暂停环节计时；resume=恢复计时"
            ),
        },
        "segment_id": {
            "type": "string",
            "description": "goto 的目标环节 id（action=goto 时必填；可从情境注入的后续环节列表取得）",
        },
    },
    "required": ["action"],
}

# 注册形态的 spec：provider="rundown"、声明名 control（全名 rundown_control 派生）
_CONTROL_SPEC = ToolSpec(
    name="control",
    description=(
        "流程单控制：切换直播环节或暂停/恢复环节计时。"
        "当本环节目标已达成、或剩余时间不多且话题自然收束时，用 next/goto 推进；"
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

    async def _run(inv):  # type: ignore[no-untyped-def]
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
            if action == "next":
                reject = self._state.next(by="agent")
            elif action == "goto":
                segment_id = str(args.get("segment_id", "") or "")
                if not segment_id:
                    return json.dumps(
                        {"ok": False, "error": "goto 需要 segment_id（可从情境注入的后续环节列表取得）"},
                        ensure_ascii=False,
                    )
                reject = self._state.goto(segment_id, by="agent")
            elif action == "pause":
                reject = self._state.pause(by="agent")
            elif action == "resume":
                reject = self._state.resume(by="agent")
            else:
                return json.dumps({"ok": False, "error": f"未知 action={action!r}"}, ensure_ascii=False)
        except Exception as exc:
            self._logger.warning(f"rundown_control 执行异常: {exc}", exc_info=True)
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

        if reject is not None:
            data: Dict[str, Any] = {"ok": False, "reason": reject.reason}
            if reject.available_ids:
                data["available_segment_ids"] = reject.available_ids
            if reject.remaining_ms:
                data["remaining_ms"] = reject.remaining_ms
            return json.dumps(data, ensure_ascii=False)

        return json.dumps({"ok": True, "rundown": self._state.get_snapshot()}, ensure_ascii=False)
