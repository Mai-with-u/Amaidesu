"""should_speak_proactively - 主播主动发言触发工具（Wave 6 / §1.5）

**真工具**——通过 ``@tool`` 装饰器注册到 ToolRegistry，供 LLM 调用。
调用入口由 StreamerAgent 设置（提供 invoke 桥接）；底层判定器是
``ProactiveTrigger`` 纯规则组件（**不**注册为工具）。

Wave 6 迁移：
- 原 ``stages/decision/deciders/amaidesu/proactive_trigger.py`` 纯规则组件保留
  并复用（Stage 2 内脏，不注册工具）。
- ``should_speak_proactively`` 工具 = LLM 调用入口，底层调 ``ProactiveTrigger.should_trigger``。

§1.5 工具契约：
- kind: ``"sync"``（即时查询结果，不是 fire-and-forget）
- provider: ``"builtin"``（框架内置）
- arguments: ``{}``（空，无外部输入——所有触发依据来自内部 room_state）
- 返回：ToolExecutionResult(content=reason 字符串或 None)
"""

from __future__ import annotations

from typing import Any

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult

from .proactive_trigger import ProactiveTrigger
from .room_state import RoomState

__all__ = ["build_proactive_tool_spec", "ProactiveToolProvider", "register_proactive_tool"]


# ---------------------------------------------------------------------------
# ToolSpec：should_speak_proactively 工具定义
# ---------------------------------------------------------------------------


_PROACTIVE_TOOL_NAME = "should_speak_proactively"
_PROACTIVE_TOOL_DESCRIPTION = (
    "询问是否应触发主动发言（冷场救场 / Agenda 推进 / 定时话题 / 外部 API）。"
    "返回触发原因字符串（external/agenda/schedule/cold），或 None 表示不应触发。"
    "工具内部基于 ProactiveTrigger 纯规则判定，频率限制（min_interval / max_per_hour / "
    "topic_required）已内嵌——主播自己主动调此工具可避免无意中违反频率限制。"
)


_PROACTIVE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


def build_proactive_tool_spec() -> ToolSpec:
    """构造 should_speak_proactively 工具的 ToolSpec。"""
    return ToolSpec(
        name=_PROACTIVE_TOOL_NAME,
        description=_PROACTIVE_TOOL_DESCRIPTION,
        parameters_schema=_PROACTIVE_PARAMETERS_SCHEMA,
        kind="sync",
        provider="builtin",
    )


# ---------------------------------------------------------------------------
# Provider 类
# ---------------------------------------------------------------------------


class ProactiveToolProvider:
    """should_speak_proactively 工具的 Provider。

    StreamerAgent 直接 ``registry.register_provider(provider)`` 注册。
    invoke 时按 ``invocation.tool_name == "should_speak_proactively"`` 分发到 ProactiveTrigger。
    """

    def __init__(
        self,
        *,
        trigger: ProactiveTrigger,
        room_state: RoomState,
        external_pending: Any = None,
        agenda_pending: Any = None,
        agenda_ready: Any = None,
    ) -> None:
        self._trigger = trigger
        self._room_state = room_state
        self._external_pending = external_pending
        self._agenda_pending = agenda_pending
        self._agenda_ready = agenda_ready
        self._logger = get_logger("ProactiveTool")

    @property
    def name(self) -> str:
        return "ProactiveTool"

    def list_tools(self):
        return [build_proactive_tool_spec()]

    async def _resolve_flag(self, provider: Any) -> bool:
        """鸭子类型 flag 解析：sync/async callable 返回 bool。"""
        if provider is None:
            return False
        if callable(provider):
            import inspect

            try:
                result = provider()
            except Exception as exc:
                self._logger.warning(f"ProactiveTool: flag provider 调用失败: {exc}")
                return False
            if inspect.isawaitable(result):
                resolved = await result  # type: ignore[no-any-return]
            else:
                resolved = result
            return bool(resolved)
        return bool(provider)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """should_speak_proactively 工具的 invoke。"""
        if invocation.tool_name != _PROACTIVE_TOOL_NAME:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"ProactiveToolProvider 不处理工具 '{invocation.tool_name}'",
            )

        # 解析外部信号（鸭子类型）
        external_pending = await self._resolve_flag(self._external_pending)
        agenda_pending = await self._resolve_flag(self._agenda_pending)
        agenda_ready = await self._resolve_flag(self._agenda_ready)

        try:
            reason = self._trigger.should_trigger(
                room_state=self._room_state,
                now_ms=now_ms(),
                external_pending=external_pending,
                agenda_pending=agenda_pending,
                agenda_ready=agenda_ready,
            )
        except Exception as exc:
            self._logger.error(f"ProactiveTrigger.should_trigger 抛出未捕获异常: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=_PROACTIVE_TOOL_NAME,
                success=False,
                error_message=f"ProactiveTrigger 异常: {type(exc).__name__}: {exc}",
            )

        if reason is None:
            # 不触发：返回明确信息让 LLM 知道为何沉默
            return ToolExecutionResult(
                tool_name=_PROACTIVE_TOOL_NAME,
                success=True,
                content="",
            )

        # 触发：返回 reason
        return ToolExecutionResult(
            tool_name=_PROACTIVE_TOOL_NAME,
            success=True,
            content=reason,
        )


def register_proactive_tool(
    registry: Any,
    *,
    trigger: ProactiveTrigger,
    room_state: RoomState,
    external_pending: Any = None,
    agenda_pending: Any = None,
    agenda_ready: Any = None,
) -> bool:
    """便捷函数：构造 should_speak_proactively 工具 Provider 并注册到 ToolRegistry。

    Args:
        registry: ``ToolRegistry`` 实例
        trigger: ``ProactiveTrigger`` 实例（StreamerAgent 持有）
        room_state: ``RoomState`` 实例（用于快照查询 + is_cold 判定）
        external_pending/agenda_pending/agenda_ready: 鸭子类型 flag（sync/async callable -> bool）

    Returns:
        True = 注册成功（name 唯一）；False = name 已存在，跳过。
    """
    provider = ProactiveToolProvider(
        trigger=trigger,
        room_state=room_state,
        external_pending=external_pending,
        agenda_pending=agenda_pending,
        agenda_ready=agenda_ready,
    )
    return registry.register_provider(provider)
