"""rundown_tool - 流程单控制工具（Planner ReAct 循环的推进出口）

**注册形态**：provider="rundown"、声明名 control → 全名 ``rundown_control``，
经 ``build_rundown_tool_provider`` 组装后由 StreamerAgent 注册进 ToolRegistry
（可见名单 ``["streamer"]``）。决策面的出现时机仍由 Planner 按
``RundownControlProvider.is_active`` 条件追加（动态工具的已知例外）。
推进权归 Agent：何时切换环节是决策脑自己的决定，本工具只提供能力通道，
并把 ``RundownState`` 的结构化拒绝（未知环节 id / 未达最少停留）原样重新写入
——Agent 读到拒绝原因后自纠，不走异常通道。

工具契约：
- OpenAI function 形态（``build_rundown_control_function_def``）
- ``RundownControlProvider.invoke(args)`` 同步执行（状态机方法非阻塞），
  返回观察 JSON：成功带流程单快照；拒绝带 reason / available_segment_ids /
  remaining_ms
"""

from __future__ import annotations

import json
from typing import Any, Dict

from src.modules.logging import get_logger
from src.modules.tools.models import ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs

from ..rundown.rundown_state import RundownState

__all__ = [
    "build_rundown_control_function_def",
    "build_rundown_tool_provider",
    "RundownControlProvider",
]

_TOOL_NAME = "rundown_control"

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


def build_rundown_control_function_def() -> Dict[str, Any]:
    """构造 rundown_control 的 OpenAI function 定义（Planner ReAct 工具列表）。"""
    return {
        "name": _TOOL_NAME,
        "description": _CONTROL_SPEC.description,
        "parameters": _PARAMETERS_SCHEMA,
    }


def build_rundown_tool_provider(provider: "RundownControlProvider") -> ToolProvider:
    """把 RundownControlProvider 包成注册形态的 ToolProvider（简单工具路径）。

    注册键 = 派生全名 ``rundown_control``（provider="rundown" + 声明名
    control）；执行体复用同一 ``RundownControlProvider.invoke``——注册
    通道与 Planner 直连通道走同一状态机，无第二事实源。
    """

    async def _run(inv):  # type: ignore[no-untyped-def]
        return provider.invoke(dict(inv.arguments or {}))

    return make_provider_from_specs(
        "rundown",
        [(_CONTROL_SPEC, as_tool_impl(_CONTROL_SPEC.full_name, _run))],
    )


class RundownControlProvider:
    """rundown_control 的执行器（Agent 内部件协议，直连 ``RundownState``）。

    非线程安全；仅在 StreamerAgent 单一 asyncio 事件循环内使用。
    """

    def __init__(self, state: RundownState) -> None:
        self._state = state
        self._logger = get_logger("RundownControlTool")

    def is_active(self) -> bool:
        """流程单是否激活（Planner 据此决定是否把工具放进本轮工具列表）。"""
        return self._state.rundown is not None

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
