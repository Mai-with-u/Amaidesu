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
- arguments: ``{topic_summary, emotion, intensity, is_novel, reply_guidance?, topic_tag?, target?}``
- 新颖自检：``is_novel=false`` 或未如实申报布尔值立即打回——她说出的内容有没有新东西，
  由她自己在调用前如实申报
- 她已说过的台词先交表达侧，聊天记录里看不到的那几句单列成"更早一点你说过的话"
- 失败兜底：ToolExecutionResult(success=False, error_message=...)
"""

from __future__ import annotations

import inspect
from typing import Any, ClassVar, Dict, Iterable, List, Optional

from src.agents.streamer.speech_guard import strip_memory_numbers
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.planner import PlannerVerdictPayload
from src.modules.logging import get_logger
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

from ..plan import DecisionPlan, ReplyRoundAttachment, assistant_speech_lines
from ..replyer import Replyer

__all__ = [
    "build_reply_tool_spec",
    "coerce_emotion",
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


def coerce_emotion(raw: Any) -> tuple[Emotion, str]:
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
                "新颖自检（必填）：填 true 前先确认本条有增量——增量只认事实上的新："
                "新事件、新进展、新的互动对象、新信息；同一件事换个说法、换个态度或换种情绪再讲，不算新；"
                "拿不准时按没有增量处理——"
                "这里宁严勿松，打回重说的代价只是一轮，复述漏出去观众是直接听到的。"
                "数值变化、把说过的结论或打算换个说法再讲都不算。"
                "没有就如实填 false——工具会拒收并让你换内容或本轮别发言。"
            ),
        },
        "reply_guidance": {
            "type": "string",
            "description": "回复指引：一件事、一个态度，一句话以内——这轮让她对哪件事开口、她对这件事的态度（态度从事实与人设推导，不套定式）；需要她避开什么才写不提什么（可空；不写成台词）",
        },
        "topic_tag": {
            "type": "string",
            "description": (
                "话题短名（可空）：3–8 个字，给本轮话题起个名字，如 照明进度、玩家打人、女仆妖精。"
                "还是同一件事就用同一个短名；换话题就换新名；只写事实指向，不带评价。"
            ),
        },
        "target": {
            "type": "string",
            "description": "要回应的弹幕 message_id 或片段（可空；面向全体时省略）",
        },
        "to_player": {
            "type": "string",
            "description": (
                "这句在回应游戏里的哪位玩家（可空）：谢他送的东西、接他在公屏说的话时，照游戏条目里写的名字抄；"
                "回观众弹幕或面向全体时不填。"
            ),
        },
        "emotion": {
            "type": "string",
            "enum": [emotion.value for emotion in Emotion],
            "description": (
                "Planner 决定本句台词的情绪，结合消息和回复指引选择——值域是场景情绪或 neutral："
                "这轮有明确的场景情绪就填对应的那一种，没有就填 neutral，不为凑表情硬安一个"
            ),
        },
        "intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Planner 决定本句情绪强度（0.0-1.0）",
        },
        "use_memory": {
            "type": "string",
            "description": (
                "要点名的那件旧事的编号（可空，一次至多一条）：【她记得的事】或【这一批想起来的事】里"
                "标着的 约#N（答应过的事）或 档#N（记得的事），写编号本身（如 约#7、档#41）。"
                "台词会拿到该说的内容；填了别的编号按没填处理。"
            ),
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
    ) -> None:
        self._replyer = replyer
        self._history_provider = history_provider
        self._rundown_text_provider = rundown_text_provider
        # 可选 EventBus：reply 调用入口发布 planner.verdict（裁决时刻即时事实）
        self._event_bus = event_bus
        self._logger = get_logger("ReplyTool")

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

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """reply 工具的 invoke（ToolProvider 协议）。

        契约：
        - 接收 invocation.arguments：topic_summary / reply_guidance / target / emotion / intensity
        - 本窗交接材料（思考流回调/弹幕批/游戏聊天/状态事实块/闸豁免）从
          ``invocation.attachment`` 取（``ReplyRoundAttachment``；None 按空材料处理，
          attachment 为其他类型属编程错误，返回失败结果暴露）
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

        attachment = invocation.attachment
        if attachment is not None and not isinstance(attachment, ReplyRoundAttachment):
            self._logger.error(
                f"reply_tool: attachment 类型不符（期望 ReplyRoundAttachment，"
                f"实为 {type(attachment).__name__}），按编程错误打回"
            )
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=(f"attachment 类型不符：期望 ReplyRoundAttachment，实为 {type(attachment).__name__}"),
            )
        # None = 本窗无宿主材料，按空材料处理
        materials = attachment if attachment is not None else ReplyRoundAttachment()

        args = invocation.arguments or {}

        # 裁决时刻即时事实：reply 被调用即 Planner 已决定回应（表达生成之前）
        await self._emit_verdict(args, round_id=invocation.round_id)

        # 房管写交接稿时可能照抄简报里的 档#N、约#N：交给说话侧之前去掉，她看不到编号就不会念出来
        topic_summary, numbers_in_topic = strip_memory_numbers(str(args.get("topic_summary", "") or ""))
        reply_guidance, numbers_in_guidance = strip_memory_numbers(str(args.get("reply_guidance", "") or ""))
        raw_target = args.get("target", None)
        target: Optional[str] = None
        numbers_in_target = 0
        if isinstance(raw_target, str):
            target, numbers_in_target = strip_memory_numbers(raw_target)
        stripped = numbers_in_topic + numbers_in_guidance + numbers_in_target
        if stripped:
            self._logger.info(f"reply_tool: 交接稿里带了 {stripped} 处记忆编号，交给说话侧之前去掉了")

        # 新颖自检：false 或未如实申报布尔值都立即打回，防绕过
        declared_novel = args.get("is_novel")
        if not isinstance(declared_novel, bool):
            self._logger.info("新颖自检打回：is_novel=未申报")
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=(
                    "is_novel 必须如实申报（布尔值 true/false）：填 true 前先确认本条包含观众没听过的内容。"
                ),
            )
        if not declared_novel:
            self._logger.info("新颖自检打回：is_novel=false")
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message="你自评这是重复——要么拿出观众没听过的新内容重新调用（新进展、新互动这类事实层的新），要么本轮别发言",
            )

        # 本窗弹幕批随 attachment 传入；主动发言或脱离 Planner 调用时为空列表
        batch: List[Any] = list(materials.round_batch)

        # 情绪值先收敛到枚举：名单外的近义词就近映射，不为一个情绪词打回整轮
        emotion, emotion_note = coerce_emotion(args.get("emotion", "neutral"))
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

        # 她已经说过的台词（旧→新）：Planner 路径在窗起点算好随单携带；
        # 裸调用（无 attachment 语料）时从刚读到的历史里现取，读失败（None）按空语料处理。
        # 同一份语料先交表达侧：聊天记录里看不到的那几句单列成"更早一点你说过的话"让她别再说一遍
        speech_history = materials.speech_history
        if speech_history is None:
            speech_history = assistant_speech_lines(history)

        # 思考流回调随 attachment 一次性传入（调用即弃，无跨轮残留）
        try:
            result = await self._replyer.generate(
                plan=plan,
                batch=batch,
                history=history,
                rundown=rundown,
                on_delta=materials.thinking_callback,
                game_chat=list(materials.game_chat),
                state_facts=materials.state_facts,
                recent_speeches=speech_history,
                remembered=materials.remembered,
                game_people=list(materials.game_people),
                reply_to=materials.reply_to,
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
