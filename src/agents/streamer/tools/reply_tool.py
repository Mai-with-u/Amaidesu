"""reply_tool - 主播表达工具（Planner ReAct 循环的出口）

**真工具**——注册到 ToolRegistry 供 LLM 调用；底层执行器是
``Replyer`` 表达引擎（Agent 内部件，**不**注册为工具）。

调用链（Planner ReAct 架构）：
- Planner（决策主体，ReAct 循环）决定说话时调用 reply 工具
- reply 工具 invoke → Replyer.generate（复古提示词渲染 + 纯文本台词 + 敏感词净化）
- 返回 {speech, emotion, metadata}——由 StreamerAgent 送发言队列（TTS/字幕/虚拟形象）

工具契约：
- kind: ``"sync"``（gather 等齐结果；Replyer 是一次性 LLM 调用，不是 fire-and-forget）
- provider: ``"streamer"``（主播 Agent 自有工具；全名 streamer_reply）
- arguments: ``{topic_summary, emotion, intensity, is_novel, reply_guidance?, target?}``
- 发言闸门：``is_novel=false`` 立即打回；生成后的成品台词对最近发言做
  bigram 包含率检查（topic_gate 纯函数），超阈值同样打回——每个决策窗
  最多打回 1 次后引导沉默（计数由 Planner 每窗 ``reset_speech_gate`` 清零）
- 失败兜底：ToolExecutionResult(success=False, error_message=...)
"""

from __future__ import annotations

import inspect
from typing import Any, ClassVar, Dict, Iterable, List, Optional

from src.agents.streamer.canonical import turn_to_message
from src.agents.streamer.topic_gate import (
    SPEECH_DUPLICATE_RATIO,
    SPEECH_GATE_WINDOW,
    speech_overlap_ratio,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.planner import PlannerVerdictPayload
from src.modules.logging import get_logger
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

from ..plan import DecisionPlan
from ..replyer import Replyer

__all__ = [
    "build_reply_tool_spec",
]


# ---------------------------------------------------------------------------
# 情绪值收敛：Planner 给出名单外的情绪词时映射到最近的枚举值
# ---------------------------------------------------------------------------

# 工具定义里虽带了枚举，部分模型（实测 gemini）仍常填 cheerful / playful 这类近义词。
# 拒收会让 Planner 整轮重跑一步（一场 22 句台词被拒 18 次，每次多等十几秒），
# 而情绪只驱动表情与语气，差一档不影响事实——所以就近映射，映射不到按 neutral 并记日志
_EMOTION_ALIASES: Dict[str, Emotion] = {
    **dict.fromkeys(
        ("cheerful", "joyful", "pleased", "amused", "glad", "delighted", "content", "relieved", "grateful", "warm"),
        Emotion.HAPPY,
    ),
    **dict.fromkeys(("lively", "energetic", "enthusiastic", "thrilled", "eager", "hyped"), Emotion.EXCITED),
    **dict.fromkeys(("playful", "teasing", "mischievous", "proud", "confident", "cocky", "sly"), Emotion.SMUG),
    **dict.fromkeys(("shocked", "amazed", "astonished", "startled", "curious"), Emotion.SURPRISED),
    **dict.fromkeys(("nervous", "anxious", "worried", "fearful", "afraid"), Emotion.SCARED),
    **dict.fromkeys(("awkward", "sheepish", "flustered"), Emotion.EMBARRASSED),
    **dict.fromkeys(("bashful",), Emotion.SHY),
    **dict.fromkeys(("annoyed", "frustrated", "irritated", "mad", "grumpy"), Emotion.ANGRY),
    **dict.fromkeys(("disappointed", "upset", "lonely", "down", "gloomy"), Emotion.SAD),
    **dict.fromkeys(("sleepy", "exhausted", "bored"), Emotion.TIRED),
    **dict.fromkeys(("determined", "focused", "thoughtful"), Emotion.SERIOUS),
    **dict.fromkeys(("helpless", "dumbfounded", "exasperated"), Emotion.SPEECHLESS),
    **dict.fromkeys(("puzzled", "perplexed"), Emotion.CONFUSED),
    **dict.fromkeys(("affectionate", "loving", "adoring"), Emotion.LOVE),
    **dict.fromkeys(("tearful", "sobbing"), Emotion.CRYING),
    **dict.fromkeys(("calm", "relaxed", "chill"), Emotion.NEUTRAL),
}


def _coerce_emotion(raw: Any) -> tuple[Emotion, str]:
    """把 Planner 给的情绪值收敛到枚举，返回（情绪, 改写说明）；说明为空表示原样合法。

    合法值原样通过；名单外的常见近义词就近映射；其余一律按 neutral。
    """
    text = str(raw or "").strip().lower()
    try:
        return Emotion(text), ""
    except ValueError:
        mapped = _EMOTION_ALIASES.get(text, Emotion.NEUTRAL)
        return mapped, f"{raw!r} 不在情绪名单，按 {mapped.value} 处理"


# ---------------------------------------------------------------------------
# ToolSpec：reply 工具定义
# ---------------------------------------------------------------------------


_REPLY_TOOL_NAME = "reply"
_REPLY_TOOL_DESCRIPTION = (
    "主播发言：消费 Planner 决策（topic_summary / reply_guidance / target），"
    "由 Planner 提供情绪与强度，Replyer 只生成纯文本台词。"
    "仅在 Planner 明确 should_reply=true 时调用；否则 LLM 应保持沉默。"
    "返回 {speech, emotion, metadata}，由发声管线消费。"
)


_REPLY_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic_summary": {
            "type": "string",
            "description": "Planner 决策的话题摘要（必填；来自 Planner.plan 输出）",
        },
        "is_novel": {
            "type": "boolean",
            "description": (
                "新颖自检（必填）：填 true 前先确认本条有增量——信息上的新（新事实/新进展/新互动对象），"
                "或表达上的新（她对已知的事有新态度、新联想、新玩笑，或扯出相关旧事）；"
                "数值变化、把说过的结论或打算换个说法再讲都不算。"
                "没有就如实填 false——工具会拒收并让你换内容或本轮别发言。"
            ),
        },
        "reply_guidance": {
            "type": "string",
            "description": "回复指引：一件事、一个态度，一句话以内——说什么方向、对谁说、不提什么，以及她对这件事的态度与语气（可空；不写成台词）",
        },
        "target": {
            "type": "string",
            "description": "要回应的弹幕 message_id 或片段（可空；面向全体时省略）",
        },
        "emotion": {
            "type": "string",
            "enum": [emotion.value for emotion in Emotion],
            "description": "Planner 决定本句台词的情绪，结合消息和回复指引选择",
        },
        "intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Planner 决定本句情绪强度（0.0-1.0）",
        },
    },
    "required": ["topic_summary", "emotion", "intensity", "is_novel"],
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


class ReplyToolProvider(BaseToolProvider):
    """reply 工具的 Provider（满足 ``ToolProvider`` 协议）。

    StreamerAgent 直接 ``registry.register_provider(reply_tool_provider)`` 注册。
    实现 ``invoke`` 时按 ``invocation.tool_name == 全名（streamer_reply）`` 分发到 ``Replyer``。
    """

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）：
    # 主播是框架核心 Agent，工具页归随 Agent 的 framework 分类（同 Minecraft 归 game）
    category: ClassVar[str] = "framework"

    def __init__(
        self,
        *,
        replyer: Replyer,
        history_provider: Optional[Any] = None,
        rundown_text_provider: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        gate_window: int = SPEECH_GATE_WINDOW,
        gate_ratio: float = SPEECH_DUPLICATE_RATIO,
    ) -> None:
        self._replyer = replyer
        self._history_provider = history_provider
        self._rundown_text_provider = rundown_text_provider
        # 台词级重合度检查参数：比对最近多少条已说发言、包含率超过多少打回
        self._gate_window = max(0, int(gate_window))
        self._gate_ratio = float(gate_ratio)
        # 本决策窗内台词重合度检查已打回的次数（Planner 每窗 reset_speech_gate 清零；
        # 打回 1 次后仍重复即引导本轮沉默，不再给重试机会）
        self._gate_bounces: int = 0
        # 本窗是否豁免台词重合度检查（收播道别窗：道别话题与收尾环节发言字面重合是常态）
        self._gate_exempt: bool = False
        # 本轮思考流回调（LLM 层形态 on_delta）；由 Planner 在 reply 调用前设置、
        # 调用后清理（一次性槽位，ReAct 串行无并发）
        self._thinking_callback: Optional[Any] = None
        # 本轮弹幕批（Planner 在 reply 调用前设置、调用后清理）：表达侧据此看到观众原话，
        # 回应弹幕时不会被当成"本批无弹幕"的主动发言
        self._round_batch: List[Any] = []
        # 本窗新到的游戏聊天原话（Planner 在 reply 调用前设置、调用后清理）：玩家在游戏里搭话
        # 促发的一轮弹幕批是空的，没有它表达侧只能看到转述，会当成冷场自言自语
        self._round_game_chat: List[str] = []
        # 本窗状态跃迁事实块（Planner 在 reply 调用前设置、调用后清理）：表达侧的客观事实底稿
        self._round_state_facts: str = ""
        # 可选 EventBus：reply 调用入口发布 planner.verdict（裁决时刻即时事实）
        self._event_bus = event_bus
        self._logger = get_logger("ReplyTool")

    def set_thinking_callback(self, callback: Optional[Any]) -> None:
        """设置/清理本轮 replyer 阶段的思考流回调（Planner 每轮一次性注入）。"""
        self._thinking_callback = callback

    def set_round_batch(self, batch: Optional[List[Any]]) -> None:
        """设置/清理本轮弹幕批（Planner 每轮一次性注入；None 即清空）。"""
        self._round_batch = list(batch or [])

    def set_round_game_chat(self, lines: Optional[List[str]]) -> None:
        """设置/清理本窗新到的游戏聊天原话（Planner 每轮一次性注入；None 即清空）。"""
        self._round_game_chat = list(lines or [])

    def set_round_state_facts(self, text: Optional[str]) -> None:
        """设置/清理本窗状态跃迁事实块（Planner 每轮一次性注入；None 即清空）。"""
        self._round_state_facts = str(text or "")

    def reset_speech_gate(self) -> None:
        """清零本决策窗的台词重合度打回计数（Planner 每窗 plan() 入口调用）。"""
        self._gate_bounces = 0

    def set_speech_gate_exempt(self, exempt: bool) -> None:
        """设置本窗是否豁免台词重合度检查（收播道别窗由 Planner 在 reply 调用前设置）。"""
        self._gate_exempt = bool(exempt)

    @property
    def gate_bounces(self) -> int:
        """本决策窗内台词重合度检查已打回的次数（观测口径，Planner 写进 outcome）。"""
        return self._gate_bounces

    async def _emit_verdict(self, args: Dict[str, Any], round_id: str) -> None:
        """发布 ``planner.verdict``（裁决时刻即时事实；观测旁路，失败不阻断）。

        直接 ``await event_bus.emit(...)`` —— emit 内部已把 handler 派发为受跟踪后台任务，
        外层再包一层裸任务既多余又无持有（同 P1-2 缺口）；await 后即返回，
        不阻塞 invoke 主路径（handler 在 emit 内部后台跑）。
        """
        event_bus = self._event_bus
        if event_bus is None:
            return
        try:
            target = args.get("target")
            await event_bus.emit(
                CoreEvents.PLANNER_VERDICT,
                PlannerVerdictPayload(
                    round_id=round_id,
                    topic_summary=str(args.get("topic_summary", "") or ""),
                    reply_guidance=str(args.get("reply_guidance", "") or ""),
                    target=target if isinstance(target, str) else None,
                ),
                source="reply_tool",
            )
        except Exception as exc:  # noqa: BLE001 - 观测旁路，不反噬调用方
            self._logger.warning(f"planner.verdict 发布失败（已忽略）: {exc}")

    @property
    def name(self) -> str:
        return "streamer"

    def list_tools(self) -> Iterable[ToolSpec]:
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

    async def _recent_speeches(self) -> List[str]:
        """对话历史 assistant 行的台词原文（旧→新，截取检查窗口最近 N 条）。

        台词级查重的语料来源：历史里自己说过的话才是观众听过的内容；
        历史读取失败按空语料处理（无从比对即放行，不反噬表达链路）。
        """
        history = await self._resolve_history()
        if not history:
            return []
        lines: List[str] = []
        for turn in history:
            message = turn_to_message(turn)
            content = str(message.get("content") or "").strip()
            if message.get("role") == "assistant" and content:
                lines.append(content)
        return lines[-self._gate_window :] if self._gate_window > 0 else []

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """reply 工具的 invoke（ToolProvider 协议）。

        契约：
        - 接收 invocation.arguments：topic_summary / reply_guidance / target / emotion / intensity
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
        await self._emit_verdict(args, round_id=invocation.round_id)

        topic_summary = str(args.get("topic_summary", "") or "")
        reply_guidance = str(args.get("reply_guidance", "") or "")
        raw_target = args.get("target", None)
        target = raw_target if isinstance(raw_target, str) else None

        # 新颖自检（第一道闸）：false 或未如实申报布尔值都立即打回，防绕过
        declared_novel = args.get("is_novel")
        if not isinstance(declared_novel, bool):
            self._logger.info("发言闸打回：is_novel=未申报 结果=bounce 重合度=未检查")
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=(
                    "is_novel 必须如实申报（布尔值 true/false）：填 true 前先确认本条包含观众没听过的内容。"
                ),
            )
        if not declared_novel:
            self._logger.info("发言闸打回：is_novel=false 结果=bounce 重合度=未检查")
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message="你自评这是重复——要么换个新角度拿出真内容重新调用（新进展，或她对这件事的新态度、新联想），要么本轮别发言",
            )

        # 本轮弹幕批由 Planner 调用前注入；主动发言或脱离 Planner 调用时为空列表
        batch: List[Any] = list(self._round_batch)

        # 情绪值先收敛到枚举：名单外的近义词就近映射，不为一个情绪词打回整轮
        emotion, emotion_note = _coerce_emotion(args.get("emotion", "neutral"))
        if emotion_note:
            self._logger.warning(f"reply_tool: {emotion_note}")

        # 构造 plan（must should_reply=true）
        try:
            plan = DecisionPlan(
                should_reply=True,
                target=target,
                topic_summary=topic_summary,
                reply_guidance=reply_guidance,
                emotion=emotion,
                intensity=args.get("intensity", 0.5),
            )
        except (TypeError, ValueError) as exc:
            self._logger.warning(f"reply_tool: Planner 情绪强度无效: {exc}", exc=True)
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"Planner 情绪强度无效: {exc}",
            )

        try:
            history = await self._resolve_history()
            rundown = await self._resolve_rundown()
        except Exception as exc:
            self._logger.exception(f"reply_tool: 解析依赖失败: {exc}")
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
                game_chat=list(self._round_game_chat),
                state_facts=self._round_state_facts,
            )
        except Exception as exc:
            self._logger.exception(f"reply_tool: Replyer.generate 抛出未捕获异常: {exc}")
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"Replyer.generate 异常: {type(exc).__name__}: {exc}",
            )

        if result is None:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message="Replyer 返回 None（LLM 失败 / 空台词 / word_filter 丢弃）",
            )

        # 台词级重合度检查（第二道闸，生成后）：成品台词对最近发言的包含率超阈值即打回；
        # 每个决策窗打回 1 次后仍重复，改引导本轮沉默（计数由 Planner 每窗清零）
        speech = str(result.get("speech") or "")
        if self._gate_exempt:
            self._logger.info("发言闸豁免：is_novel=true 结果=pass 重合度=未检查")
        else:
            ratio = speech_overlap_ratio(speech, await self._recent_speeches())
            if ratio >= self._gate_ratio:
                retry_exhausted = self._gate_bounces >= 1
                self._gate_bounces += 1
                self._logger.info(
                    f"发言闸打回：is_novel=true 结果=bounce 重合度={ratio:.2f}（本窗第 {self._gate_bounces} 次）"
                )
                if retry_exhausted:
                    error_message = (
                        f"重试后的台词仍与最近发言大面积重复（重合度 {ratio:.0%}）。"
                        "本轮请保持沉默：不再调用任何工具，直接结束。"
                    )
                else:
                    error_message = (
                        f"台词与最近发言大面积重复（重合度 {ratio:.0%}；换皮措辞、数值变化不算新内容）——"
                        "要么换个新角度拿出真内容重新调用（新进展，或她对这件事的新态度、新联想），要么本轮别发言。"
                    )
                return ToolExecutionResult(
                    tool_name=invocation.tool_name,
                    success=False,
                    error_message=error_message,
                )
            self._logger.info(f"发言闸放行：is_novel=true 结果=pass 重合度={ratio:.2f}")

        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            structured_content={
                "speech": result["speech"],
                "emotion": {"name": plan.emotion.value, "intensity": plan.intensity},
                "metadata": {
                    "source_id": "streamer_agent",
                    "target": plan.target,
                    "topic_summary": plan.topic_summary,
                    "reply_guidance": plan.reply_guidance,
                },
                "llm_request_id": result.get("llm_request_id"),
            },
        )
