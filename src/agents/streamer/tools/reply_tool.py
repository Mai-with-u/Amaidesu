"""reply_tool - 主播表达工具（Planner ReAct 循环的出口）

**真工具**——注册到 ToolRegistry 供 LLM 调用；底层执行器是
``Replyer`` 表达引擎（Agent 内部件，**不**注册为工具）。

调用链（Planner ReAct 架构）：
- Planner（决策主体，ReAct 循环）决定说话时调用 reply 工具
- reply 工具 invoke → Replyer.generate（人设渲染 + 情绪词表 + 敏感词净化）
- 返回 {speech, emotion, metadata}——由 StreamerAgent 送发言队列（TTS/字幕/皮套）

工具契约：
- kind: ``"sync"``（gather 等齐结果；Replyer 是一次性 LLM 调用，不是 fire-and-forget）
- provider: ``"streamer"``（主播 Agent 自有工具；全名 streamer_reply）
- arguments: ``{topic_summary, reply_guidance, target?, confidence?, batch_text?}``
- 失败兜底：ToolExecutionResult(success=False, error_message=...)
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Dict, List, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.planner import PlannerVerdictPayload
from src.modules.logging import get_logger
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult

from ..plan import DecisionPlan
from ..replyer import Replyer

__all__ = [
    "build_reply_tool_spec",
]


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
    """构造 reply 工具的 ToolSpec（声明名 reply；全名 streamer_reply 派生自 provider）。"""
    return ToolSpec(
        name=_REPLY_TOOL_NAME,
        description=_REPLY_TOOL_DESCRIPTION,
        parameters_schema=_REPLY_PARAMETERS_SCHEMA,
        kind="sync",
        provider="streamer",
    )


# 派生全名（唯一实现经 ToolSpec.full_name；分发与调用统一用它）
_REPLY_TOOL_FULL_NAME = build_reply_tool_spec().full_name


# ---------------------------------------------------------------------------
# Provider 类
# ---------------------------------------------------------------------------


class ReplyToolProvider:
    """reply 工具的 Provider（满足 ``ToolProvider`` 协议）。

    StreamerAgent 直接 ``registry.register_provider(reply_tool_provider)`` 注册。
    实现 ``invoke`` 时按 ``invocation.tool_name == 全名（streamer_reply）`` 分发到 ``Replyer``。
    """

    def __init__(
        self,
        *,
        replyer: Replyer,
        history_provider: Optional[Any] = None,
        rundown_text_provider: Optional[Any] = None,
        event_bus: Optional[Any] = None,
    ) -> None:
        self._replyer = replyer
        self._history_provider = history_provider
        self._rundown_text_provider = rundown_text_provider
        # 本轮思考流回调（LLM 层形态 on_delta）；由 Planner 在 reply 调用前设置、
        # 调用后清理（一次性槽位，ReAct 串行无并发）
        self._thinking_callback: Optional[Any] = None
        # 可选 EventBus：reply 调用入口发布 planner.verdict（裁决时刻即时事实）
        self._event_bus = event_bus
        self._logger = get_logger("ReplyTool")

    def set_thinking_callback(self, callback: Optional[Any]) -> None:
        """设置/清理本轮 replyer 阶段的思考流回调（Planner 每轮一次性注入）。"""
        self._thinking_callback = callback

    def _emit_verdict(self, args: Dict[str, Any], round_id: str) -> None:
        """发布 ``planner.verdict``（裁决时刻即时事实；观测旁路，失败不阻断）。"""
        if self._event_bus is None:
            return

        async def _do_emit() -> None:
            event_bus = self._event_bus
            if event_bus is None:
                return
            try:
                confidence_raw = args.get("confidence", 0.9)
                try:
                    confidence = float(confidence_raw) if confidence_raw is not None else 0.9
                except (TypeError, ValueError):
                    confidence = 0.9
                target = args.get("target")
                await event_bus.emit(
                    CoreEvents.PLANNER_VERDICT,
                    PlannerVerdictPayload(
                        round_id=round_id,
                        topic_summary=str(args.get("topic_summary", "") or ""),
                        reply_guidance=str(args.get("reply_guidance", "") or ""),
                        confidence=min(1.0, max(0.0, confidence)),
                        target=target if isinstance(target, str) else None,
                        reply_to_message_id=None,
                    ),
                    source="reply_tool",
                )
            except Exception as exc:  # noqa: BLE001 - 观测旁路，不反噬调用方
                self._logger.warning(f"planner.verdict 发布失败（已忽略）: {exc}")

        try:
            asyncio.create_task(_do_emit())
        except RuntimeError as exc:
            self._logger.warning(f"planner.verdict 任务创建失败（已忽略）: {exc}")

    @property
    def name(self) -> str:
        return "streamer"

    def list_tools(self):
        return [build_reply_tool_spec()]

    async def _await_maybe(self, result: Any) -> Any:
        """await result（若它是 awaitable），否则直接返回。"""
        if inspect.isawaitable(result):
            return await result  # type: ignore[no-any-return]
        return result

    async def _resolve_history(self) -> Optional[List[Any]]:
        if self._history_provider is None:
            return None
        try:
            return await self._await_maybe(self._history_provider())  # type: ignore[no-any-return]
        except Exception as exc:
            self._logger.warning(f"reply_tool: history_provider 调用失败: {exc}")
            return None

    async def _resolve_rundown(self) -> Optional[str]:
        if self._rundown_text_provider is None:
            return None
        try:
            return await self._await_maybe(self._rundown_text_provider())  # type: ignore[no-any-return]
        except Exception as exc:
            self._logger.warning(f"reply_tool: rundown_text_provider 调用失败: {exc}")
            return None

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """reply 工具的 invoke（ToolProvider 协议）。

        契约：
        - 接收 invocation.arguments：topic_summary / reply_guidance / target / confidence / batch_text
        - 把意图参数适配为 DecisionPlan(should_reply=True)（Replyer.generate 接口形态）
        - 调用 Replyer.generate
        - 返回 ToolExecutionResult（成功时 structured_content 为结果 dict，失败时 error_message 非空）
        """
        if invocation.tool_name != _REPLY_TOOL_FULL_NAME:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"ReplyToolProvider 不处理工具 '{invocation.tool_name}'",
            )

        args = invocation.arguments or {}

        # 裁决时刻即时事实：reply 被调用即 Planner 已决定回应（表达生成之前）
        self._emit_verdict(args, round_id=invocation.round_id)

        topic_summary = str(args.get("topic_summary", "") or "")
        reply_guidance = str(args.get("reply_guidance", "") or "")
        raw_target = args.get("target", None)
        target = raw_target if isinstance(raw_target, str) else None
        confidence_raw = args.get("confidence", 0.9)
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else 0.9
        except (TypeError, ValueError):
            confidence = 0.9

        # batch_text → 暂存为 raw_text（Replyer.generate 接受弹幕批次列表；
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
            history = await self._resolve_history()
            rundown = await self._resolve_rundown()
        except Exception as exc:
            self._logger.error(f"reply_tool: 解析依赖失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"reply_tool 依赖解析失败: {type(exc).__name__}: {exc}",
            )

        # 一次性槽位：取出即清（防异常路径残留跨轮回调；Planner finally 兜底再清一次）
        thinking_callback = self._thinking_callback
        self._thinking_callback = None
        try:
            result = await self._replyer.generate(
                plan=plan,
                batch=batch,
                history=history,
                rundown=rundown,
                on_delta=thinking_callback,
            )
        except Exception as exc:
            self._logger.error(f"reply_tool: Replyer.generate 抛出未捕获异常: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"Replyer.generate 异常: {type(exc).__name__}: {exc}",
            )

        if result is None:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=("Replyer 返回 None（降级：LLM 失败 / 脏 JSON / 空 text / word_filter 丢弃）"),
            )

        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            structured_content=result,
        )
