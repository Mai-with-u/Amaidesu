"""形象动作工具：主播有意发起身体动作的唯一入口（``avatar_list_actions`` / ``avatar_act``）

平时的表情、眨眼、眼神、说话时的点头都由面捕合成器自己完成——``reply`` 的情绪就决定
这句话的表情。只有主播想"做一个动作"时才用这组工具：点头同意、摇头否定、歪头疑惑、
看一眼弹幕，触发模型作者做好的动画（平台登记的片段），或者摆一个表情保持一会儿。

工具名不带平台前缀：面捕合成器是唯一持有身体状态的部件，多个平台同时启用时也是同一具
身体，一次调用所有平台一起动。名字写错时把可用目录随失败结果一起返回（失败即发现）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.modules.avatar.tool_common import dispatch_tool, unknown_action_fail
from src.modules.avatar.tracking.gestures import HOLD_EXPRESSION
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
    tool_result_fail,
    tool_result_ok,
)
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

PROVIDER_NAME = "avatar"
LIST_TOOL = "list_actions"
ACT_TOOL = "act"
HOLD_DEFAULT_S = 8.0

GUIDE = (
    "形象（虚拟形象）的表情、眨眼、眼神和说话时的点头都由形象自己完成：reply 的 emotion 就决定这句话的表情，"
    "不需要另外调用工具。只在想做一个有意的动作时用 avatar_act——点头同意、摇头否定、歪头疑惑、看一眼弹幕，"
    "或触发模型做好的动作（目录用 avatar_list_actions 查）；要演“假装生气一会儿”这类梗时，"
    "用 avatar_act 的“保持表情”并给出 emotion 与 seconds。不要每句话都调用。"
)

_ACT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": f"动作名（取自 avatar_list_actions 返回的目录）；摆一个表情并保持写“{HOLD_EXPRESSION}”",
        },
        "emotion": {
            "type": "string",
            "enum": [e.value for e in Emotion],
            "description": f"仅“{HOLD_EXPRESSION}”时使用：要保持的情绪",
        },
        "intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.7,
            "description": f"仅“{HOLD_EXPRESSION}”时使用：情绪强度（0~1）",
        },
        "seconds": {
            "type": "number",
            "minimum": 1,
            "maximum": 60,
            "default": HOLD_DEFAULT_S,
            "description": f"仅“{HOLD_EXPRESSION}”时使用：保持多少秒",
        },
    },
    "required": ["action"],
}

_EMOTIONS = frozenset(e.value for e in Emotion)


class AvatarActProvider(BaseToolProvider):
    """形象动作工具的 Provider：委托面捕合成器运行时执行（无外部连接）。"""

    category = "avatar"

    def __init__(self, tracking: Any) -> None:
        """Args:
        tracking: 面捕合成器运行时（提供 ``action_catalog`` / ``act`` / ``hold_expression``）。
        """
        self._tracking = tracking
        self.logger = get_logger(self.__class__.__name__)

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        return [
            ToolSpec(
                name=LIST_TOOL,
                description="列出形象可以主动做的动作（有意的动作、保持表情、模型做好的动作片段）",
                kind="sync",
                provider=PROVIDER_NAME,
            ),
            ToolSpec(
                name=ACT_TOOL,
                description="让形象做一个有意的动作，或摆一个表情保持一会儿（平时的表情与小动作不需要调用）",
                kind="sync",
                provider=PROVIDER_NAME,
                parameters_schema=_ACT_SCHEMA,
            ),
        ]

    def guide_for(self, agent_name: str) -> str:
        """工具组说明：读者固定是主播，不按读者区分。"""
        del agent_name
        return GUIDE

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        return await dispatch_tool(
            invocation,
            PROVIDER_NAME,
            {
                f"{PROVIDER_NAME}_{LIST_TOOL}": self._list_actions,
                f"{PROVIDER_NAME}_{ACT_TOOL}": lambda: self._act(args),
            },
            self.logger,
        )

    async def _list_actions(self) -> ToolExecutionResult:
        return tool_result_ok(f"{PROVIDER_NAME}_{LIST_TOOL}", True, {"actions": self._tracking.action_catalog()})

    def _catalog_names(self) -> List[str]:
        return [entry["name"] for entry in self._tracking.action_catalog()]

    async def _act(self, args: Dict[str, Any]) -> ToolExecutionResult:
        tool = f"{PROVIDER_NAME}_{ACT_TOOL}"
        action = str(args.get("action", "")).strip()
        if action == HOLD_EXPRESSION:
            emotion = str(args.get("emotion", "")).strip()
            if emotion not in _EMOTIONS:
                return tool_result_fail(
                    tool, f"“{HOLD_EXPRESSION}”需要 emotion（17 个情绪枚举值之一），得到 {emotion!r}"
                )
            intensity = min(1.0, max(0.0, float(args.get("intensity", 0.7))))
            seconds = min(60.0, max(1.0, float(args.get("seconds", HOLD_DEFAULT_S))))
            outcome = self._tracking.hold_expression(emotion, intensity, seconds)
            return tool_result_ok(
                tool, True, {"action": action, "emotion": emotion, "seconds": seconds, "outcome": outcome}
            )
        done = await self._tracking.act(action) if action else None
        if done is None:
            return unknown_action_fail(tool, action, self._catalog_names())
        return tool_result_ok(tool, bool(done), {"action": action})


__all__ = ["GUIDE", "PROVIDER_NAME", "AvatarActProvider"]
