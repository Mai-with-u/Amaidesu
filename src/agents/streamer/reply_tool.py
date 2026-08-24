"""reply_tool - 主播 reply 工具入口（Wave 6 / §1.5）

**真工具**——通过 ``@tool`` 装饰器注册到 ToolRegistry，供 LLM 调用。
调用入口由 StreamerAgent 设置（提供 invoke 桥接）；底层执行器是
``Replyer`` 表达引擎（Stage 2 内脏，**不**注册为工具）。

§1.4 一体结构：
- Planner（决策核心）**不是**工具
- Replyer（表达引擎）**不是**工具
- reply 工具 = Planner 决策 "should_reply=true" 之后的执行入口

§1.5 工具契约：
- kind: ``"sync"``（gather 等齐结果；Replyer 是一次性 LLM 调用，不是 fire-and-forget）
- provider: ``"builtin"``（框架内置，非独立源）
- arguments: ``{topic_summary, reply_guidance, target?, batch?}``
- 失败兜底：ToolExecutionResult(success=False, error_message=...)

Wave 6 设计：
- StreamerAgent 持有 Replyer 实例；reply_tool 仅持有 Replyer 引用 + persona + history
- LLM 调 reply 工具 → reply_tool.invoke → Replyer.generate → ToolExecutionResult

实现要点：
- Replyer **不**注册为工具（用户拍板：Agent 内脏）
- reply_tool **必须**注册为工具（LLM 看的入口）
- LLM 决策 should_reply=true 后才能调 reply；reply_tool 内部不重复 Planner 决策
"""

from __future__ import annotations

import inspect
import json
from typing import Any, List, Optional, Union

from src.modules.logging import get_logger
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult

from .plan import DecisionPlan
from .replyer import Replyer

__all__ = ["build_reply_tool_spec", "build_reply_tool_invoker", "register_reply_tool"]


# ---------------------------------------------------------------------------
# ToolSpec：reply 工具定义
# ---------------------------------------------------------------------------


_REPLY_TOOL_NAME = "reply"
_REPLY_TOOL_DESCRIPTION = (
    "主播发言：消费 Planner 决策（topic_summary / reply_guidance / target），"
    "调 Replyer 表达引擎生成实际台词 + 情绪 + 动作。"
    "仅在 Planner 明确 should_reply=true 时调用；否则 LLM 应保持沉默。"
    "返回 {speech, emotion, action, metadata}，由下游工具层消费。"
)


_REPLY_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic_summary": {
            "type": "string",
            "description": "Planner 决策的话题摘要（必填；来自 Planner.plan 输出）",
        },
        "reply_guidance": {
            "type": "string",
            "description": "Planner 给 Replyer 的回复指引（语气、重点等；可空）",
        },
        "target": {
            "type": "string",
            "description": "要回应的弹幕 message_id 或片段（可空；面向全体时省略）",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Planner 决策置信度（0.0-1.0；可空）",
        },
        "batch_text": {
            "type": "string",
            "description": "本批弹幕文本（可空；主动发言时省略）",
        },
    },
    "required": ["topic_summary"],
}


def build_reply_tool_spec() -> ToolSpec:
    """构造 reply 工具的 ToolSpec（供 ToolRegistry.register 调用）。

    Returns:
        reply 工具的 ToolSpec（kind=sync, provider=builtin）。
    """
    return ToolSpec(
        name=_REPLY_TOOL_NAME,
        description=_REPLY_TOOL_DESCRIPTION,
        parameters_schema=_REPLY_PARAMETERS_SCHEMA,
        kind="sync",
        provider="builtin",
    )


# ---------------------------------------------------------------------------
# Provider 类
# ---------------------------------------------------------------------------


class ReplyToolProvider:
    """reply 工具的 Provider（满足 ``ToolProvider`` 协议）。

    StreamerAgent 直接 ``registry.register_provider(reply_tool_provider)`` 注册。
    实现 ``invoke`` 时按 ``invocation.tool_name == "reply"`` 分发到 ``Replyer``。
    """

    def __init__(
        self,
        *,
        replyer: Replyer,
        persona: Union[dict[str, Any], Any, None],
        history_provider: Optional[Any] = None,
        agenda_text_provider: Optional[Any] = None,
    ) -> None:
        self._replyer = replyer
        self._persona = persona
        self._history_provider = history_provider
        self._agenda_text_provider = agenda_text_provider
        self._logger = get_logger("ReplyTool")

    @property
    def name(self) -> str:
        return "ReplyTool"

    def list_tools(self):
        return [build_reply_tool_spec()]

    async def _await_maybe(self, result: Any) -> Any:
        """await result（若它是 awaitable），否则直接返回。"""
        if inspect.isawaitable(result):
            return await result  # type: ignore[no-any-return]
        return result

    async def _resolve_persona(self) -> dict[str, Any]:
        if callable(self._persona):
            resolved = await self._await_maybe(self._persona())
            if not isinstance(resolved, dict):
                return {}
            return resolved
        if isinstance(self._persona, dict):
            return self._persona
        return {}

    async def _resolve_history(self) -> Optional[List[Any]]:
        if self._history_provider is None:
            return None
        try:
            return await self._await_maybe(self._history_provider())  # type: ignore[no-any-return]
        except Exception as exc:
            self._logger.warning(f"reply_tool: history_provider 调用失败: {exc}")
            return None

    async def _resolve_agenda(self) -> Optional[str]:
        if self._agenda_text_provider is None:
            return None
        try:
            return await self._await_maybe(self._agenda_text_provider())  # type: ignore[no-any-return]
        except Exception as exc:
            self._logger.warning(f"reply_tool: agenda_text_provider 调用失败: {exc}")
            return None

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """reply 工具的 invoke（ToolProvider 协议）。

        契约：
        - 接收 invocation.arguments：topic_summary / reply_guidance / target / confidence / batch_text
        - 构造 DecisionPlan(should_reply=true)，绕过 Planner（Planner 已在更早轮次裁决）
        - 调用 Replyer.generate
        - 返回 ToolExecutionResult（成功时 content 为 JSON 字符串，失败时 error_message 非空）
        """
        if invocation.tool_name != _REPLY_TOOL_NAME:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"ReplyToolProvider 不处理工具 '{invocation.tool_name}'",
            )

        args = invocation.arguments or {}
        topic_summary = str(args.get("topic_summary", "") or "")
        reply_guidance = str(args.get("reply_guidance", "") or "")
        target_raw = args.get("target", None)
        target = target_raw if isinstance(target_raw, str) else None
        confidence_raw = args.get("confidence", 0.9)
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else 0.9
        except (TypeError, ValueError):
            confidence = 0.9

        # batch_text → 暂存为 raw_text（Replyer.generate 接受 NormalizedMessage 列表；
        # 工具调用时无原始结构，故传空列表；Replyer 仍能基于 plan + persona 生成）
        batch: List[Any] = []

        # 构造 plan（must should_reply=true）
        plan = DecisionPlan(
            should_reply=True,
            target=target,
            topic_summary=topic_summary,
            reply_guidance=reply_guidance,
            confidence=confidence,
        )

        try:
            persona_dict = await self._resolve_persona()
            history = await self._resolve_history()
            agenda = await self._resolve_agenda()
        except Exception as exc:
            self._logger.error(f"reply_tool: 解析依赖失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=_REPLY_TOOL_NAME,
                success=False,
                error_message=f"reply_tool 依赖解析失败: {type(exc).__name__}: {exc}",
            )

        try:
            result = await self._replyer.generate(
                plan=plan,
                batch=batch,
                persona=persona_dict,
                history=history,
                agenda=agenda,
            )
        except Exception as exc:
            self._logger.error(f"reply_tool: Replyer.generate 抛出未捕获异常: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=_REPLY_TOOL_NAME,
                success=False,
                error_message=f"Replyer.generate 异常: {type(exc).__name__}: {exc}",
            )

        if result is None:
            return ToolExecutionResult(
                tool_name=_REPLY_TOOL_NAME,
                success=False,
                error_message=("Replyer 返回 None（降级：LLM 失败 / 脏 JSON / 空 text / profanity 丢弃）"),
            )

        return ToolExecutionResult(
            tool_name=_REPLY_TOOL_NAME,
            success=True,
            content=json.dumps(result, ensure_ascii=False),
        )


def build_reply_tool_invoker(
    *,
    replyer: Replyer,
    persona: Any,
    history_provider: Any = None,
    agenda_text_provider: Any = None,
):
    """便捷构造 reply 工具的 invoker 函数（直接喂给 ``ToolRegistry.register``）。

    Args:
        replyer: Stage 2 表达引擎实例
        persona: 人设 dict 或可调用对象
        history_provider: 可选，async/sync 调用返回 List
        agenda_text_provider: 可选，async/sync 调用返回 str

    Returns:
        async invoker 函数，可直接 ``registry.register(spec, invoker)``。
    """
    provider = ReplyToolProvider(
        replyer=replyer,
        persona=persona,
        history_provider=history_provider,
        agenda_text_provider=agenda_text_provider,
    )

    async def _invoker(invocation: ToolInvocation) -> ToolExecutionResult:
        return await provider.invoke(invocation)

    return _invoker


def register_reply_tool(
    registry: Any,
    *,
    replyer: Replyer,
    persona: Any,
    history_provider: Any = None,
    agenda_text_provider: Any = None,
) -> bool:
    """便捷函数：构造 reply 工具 spec + invoker 并注册到 ToolRegistry。

    Args:
        registry: ``ToolRegistry`` 实例
        replyer: Stage 2 表达引擎实例（StreamerAgent 持有）
        persona: 人设 dict 或可调用对象
        history_provider: 可选，async/sync 调用返回 List[ConversationMessage]
        agenda_text_provider: 可选，async/sync 调用返回 str

    Returns:
        True = 注册成功（name 唯一）；False = name 已存在，跳过。
    """
    spec = build_reply_tool_spec()
    invoker = build_reply_tool_invoker(
        replyer=replyer,
        persona=persona,
        history_provider=history_provider,
        agenda_text_provider=agenda_text_provider,
    )
    return registry.register(spec, invoker)
