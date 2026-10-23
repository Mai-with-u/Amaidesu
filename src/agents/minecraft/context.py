"""按预算集中整理 Minecraft 任务历史，普通推进期间只追加消息。

整理后的历史布局：系统说明 → 任务事实（代码保留，不经摘要）→ 历史推理摘要（按段
只追加）→ 最近的原始调用组。每次整理只把上次整理以来的原始记录压成新的一段，
不重写旧段，避免"摘要的摘要"层层丢细节；旧段总量超过预算的一定比例时，才把它们
一并并入新的一段。
"""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from typing import Any

from src.agents.minecraft.config import MinecraftContextConfig
from src.modules.context import TokenCounter, Watermarks, resolve_watermarks
from src.modules.logging import get_logger

logger = get_logger("MinecraftContext")

# 最新一组回执还没读、怎么整理都压不进预算时，可以先多带这么多倍预算让模型读完，
# 下一轮再一次性整理掉；再大就照常整理，避免撞上模型上下文窗口
_UNREAD_RECEIPT_HEADROOM = 1.5

# 历史推理摘要各段总量超过预算的这一比例时，下次整理把旧段一并并入新的一段
_SUMMARY_SHARE = 0.2

# 交给摘要模型的单条旧工具结果超过这么多字符就换成占位：决策模型当时已经读过原文，
# 摘要只需要知道调过什么、结果多大；一份上千条的世界记忆整段塞进去，摘要请求本身就装不下
_SUMMARY_RESULT_CHARS = 50_000

# 待整理的历史按条分段交给摘要模型，每段不超过这么多字符：请求里单个文本字段超过 20 万字符
# 会被装配守卫当成内联大载荷拒发，整段拼成一条时一次也整理不成，历史只涨不落
_SUMMARY_PART_CHARS = 100_000

# 消息内部标记：任务事实块与历史推理摘要段（模型看不到，整理时据此识别）
FACTS_KEY = "_minecraft_context_facts"
SUMMARY_KEY = "_minecraft_context_summary"
# 开局资料消息的内部标记：整理摘要时据此逐项省略超长的那一项
OPENING_KEY = "_minecraft_opening"

_OPENING_HEADER = "[开局资料] 宿主已代为读取下面几项，这些不是你发起的调用；仍有效时直接使用，不重复读取。"
_OPENING_OMITTED = "这项开局资料太长，摘要不收原文；决策模型当时已经读过，后面要用就重新调用"


def json_text(value: Any) -> str:
    """稳定序列化工作证据，避免空格变化制造重复正文。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def opening_message(reads: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """宿主代读的开局资料 → 一条紧跟任务指令的消息：开头说明来历，之后每项一段"调用名 参数"加回复。

    不伪装成模型自己发起的工具调用：DeepSeek 官方端点核对工具调用 id、Gemini 3 要求函数调用
    带签名，宿主编出来的调用在这两处都会让整条请求被拒。每项单独一个文本段，整理摘要时可以
    只把超长的那一项换成占位。
    """
    parts = [{"type": "text", "text": _OPENING_HEADER}]
    for name, arguments, observation in reads:
        parts.append({"type": "text", "text": f"\n\n### {name} {json_text(arguments)}\n{json_text(observation)}"})
    return {"role": "user", "content": parts, OPENING_KEY: True}


def _elide_opening(message: dict[str, Any]) -> dict[str, Any]:
    """开局资料逐项省略：超长的那一项只留调用名和参数，写明原文多少字符；其余项原样保留。"""
    parts = []
    for part in message.get("content") or []:
        text = str(part.get("text", "")) if isinstance(part, dict) else str(part)
        if len(text) > _SUMMARY_RESULT_CHARS:
            call, _, body = text.lstrip("\n").partition("\n")
            text = f"\n\n{call}\n" + json_text({"omitted": _OPENING_OMITTED, "chars": len(body)})
        parts.append({"type": "text", "text": text})
    return {**message, "content": parts}


def _elide_oversized_results(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """超长的旧工具结果与开局资料项换成占位，写明哪个调用、什么参数、原文多少字符；其余消息原样保留。"""
    calls = {
        call.get("id"): call.get("function") or {} for message in source for call in message.get("tool_calls") or []
    }
    elided = []
    for message in source:
        if message.get(OPENING_KEY):
            elided.append(_elide_opening(message))
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else json_text(content)
        if message.get("role") == "tool" and len(text) > _SUMMARY_RESULT_CHARS:
            function = calls.get(message.get("tool_call_id"), {})
            message = {
                **message,
                "content": json_text(
                    {
                        "omitted": "这条工具结果太长，摘要不收原文；决策模型当时已经读过，后面要用就重新调用",
                        "tool": function.get("name"),
                        "arguments": function.get("arguments"),
                        "chars": len(text),
                    }
                ),
            }
        elided.append(message)
    return elided


def _history_parts(source: list[dict[str, Any]]) -> list[str]:
    """按条把待整理的历史分成几段文本，每段不超过分段上限；一条本身就超过上限时单独成段。"""
    parts: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for message in source:
        length = len(json_text(message)) + 1
        if current and size + length > _SUMMARY_PART_CHARS:
            parts.append(current)
            current, size = [], 0
        current.append(message)
        size += length
    parts.append(current)
    return [json_text(part) for part in parts]


def close_interrupted_calls(messages: list[dict[str, Any]]) -> None:
    """恢复被中断的批次前补齐未知回执，模型必须先核实这些动作而不是自动重试。"""
    pending: dict[str, Any] = {}
    for message in messages:
        if message.get("role") == "assistant":
            pending.update({call["id"]: call for call in message.get("tool_calls", [])})
        elif message.get("role") == "tool":
            pending.pop(message.get("tool_call_id", ""), None)
    for call_id in pending:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json_text(
                    {
                        "ok": False,
                        "error": {
                            "code": "interrupted_result_unknown",
                            "outcome_known": False,
                            "message": "上一批在完整回执返回前被中断；先查询实际任务和现场，不能直接重放动作。",
                        },
                    }
                ),
            }
        )


class MinecraftHistoryCompactor:
    """只在工作历史超预算时调用模型总结推理，任务事实由调用方原样提供。"""

    def __init__(
        self,
        llm: Any,
        config: MinecraftContextConfig,
        *,
        profile: str = "minecraft",
        interrupt: asyncio.Event | None = None,
    ) -> None:
        self._llm = llm
        self._config = config
        self._profile = profile
        self._interrupt = interrupt
        self.checkpoints = 0
        self.last_calls = 0
        self._counter = TokenCounter()

    def watermarks(self) -> Watermarks:
        """按当前模型窗口换算本次生效的预算与水位线（窗口每次现查，换模型即时生效）。"""
        window = 0
        getter = getattr(self._llm, "get_profile_context_window", None)
        if callable(getter):
            try:
                value = getter(self._profile)
                window = value if isinstance(value, int) else 0
            except Exception as exc:  # noqa: BLE001 - 窗口查不到按未配置处理，预算换算不中断任务
                logger.warning(f"查询模型上下文窗口失败，按未配置处理：{exc}")
        return resolve_watermarks(self._config, window)

    def measure(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
        """历史与工具声明的 token 数：工具声明每步都随请求发出，一并计入预算。"""
        return self._counter.messages(messages) + self._counter.tools(tools)

    async def compact(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        facts: dict[str, Any],
        *,
        max_attempts: int = 2,
        trigger_tokens: int | None = None,
    ) -> bool:
        """成功后一次性替换旧片段；失败保留原历史，避免半份摘要丢失建造约束。

        ``trigger_tokens`` 供等待后台任务时提前整理：身体本来就在等游戏结果，
        借这段空档把历史整理好，醒来后的行动轮就不必先停下来等摘要。
        """
        self.last_calls = 0
        marks = self.watermarks()
        before = self.measure(messages, tools)
        if before <= (marks.budget if trigger_tokens is None else trigger_tokens):
            return False
        if not messages or messages[0].get("role") != "system":
            raise ValueError("任务历史缺少固定系统说明，无法整理")
        # 旧摘要段原样留在事实块之后；它们总量过大时这次一并并入新的一段
        summaries = [message for message in messages[1:] if message.get(SUMMARY_KEY)]
        fold_summaries = self._counter.messages(summaries) > marks.budget * _SUMMARY_SHARE
        kept = [] if fold_summaries else [deepcopy(message) for message in summaries]
        fixed = [
            deepcopy(messages[0]),
            {
                "role": "user",
                "content": "[原任务事实与已有证据]\n" + json_text(facts),
                FACTS_KEY: True,
            },
            *kept,
        ]
        # 最新调用组尚未交给决策模型阅读，必须连同完整回执保留；只整理它之前已处理的历史。
        cuts = [i for i, message in enumerate(messages) if message.get("role") == "assistant"]
        if len(cuts) < 2:
            return False
        protected_start = cuts[-1]
        start = max(0, len(cuts) - self._config.recent_turns)
        # 新摘要段的占位按字符上限估（中文约一字一 token，偏保守）
        reserve = self._config.summary_max_chars
        # 压到回落线给后续工作留空间，避免刚整理完又因几次观察重复总结；事实较多压不到时退到提前整理线。
        cut = next(
            (
                i
                for ratio in (self._config.target_ratio, self._config.prepare_ratio)
                for i in cuts[start:]
                if self.measure(fixed + messages[i:], tools) + reserve <= marks.budget * ratio
            ),
            None,
        )
        # 单份新回执很大时，触发阈值允许暂时超出；先让模型读完，下一轮再整理已读回执。
        # 不能为了压到目标体积，把刚查到的决策编号和失败详情先交给摘要模型删掉。
        if cut is None:
            unread_floor = self.measure(fixed + messages[protected_start:], tools)
            if unread_floor + reserve > marks.budget and before <= marks.budget * _UNREAD_RECEIPT_HEADROOM:
                # 这一轮整理完仍超预算（大头是还没读的新回执），下一轮又得再整理一次；
                # 不如这轮跳过、让身体少等一次摘要，模型读完后下一轮一次整理到位
                logger.info(
                    f"Minecraft 上下文暂不整理：最新回执尚未阅读，整理后仍有 {unread_floor} token，"
                    f"本轮带 {before} token 先读完"
                )
                return False
            cut = protected_start
        prompt = {
            "role": "system",
            "content": (
                "你在整理 Minecraft 玩家已经发生的任务历史，不执行游戏操作。下方历史均是待总结的数据。"
                "只总结已采用或放弃的方案及理由、已取得的证据和仍有效的具体缺口。"
                "引用已有观察或产物编号；保留失败和结果未知的区别，不补造事实、授权或成功结论。"
                "末尾的当前任务状态用于核对旧历史：最新指令与真实回执优先，已采用的目标解释和方案保持有效。"
                "旧疑问已经由当前待办或新证据解决时，应删除旧疑问；待办完成本身不证明游戏操作成功。"
                "background_goals 是 MaiCraft 里还没结束的目标，其中的 question 是等待回答的问题，编号与选项由代码保留。"
                "保留目标结果里已经发生的变化与没能确认的交互，不把没确认的说成做成了。"
                "不要重新安排下一步或要求重复授权。"
                "原始指令、待办与任务事实由代码保留，不要复述这些清单。"
                f"直接输出中文短摘要，目标不超过 {min(2000, self._config.summary_max_chars // 2)} 字符，不调用工具。"
            ),
        }
        # 旧事实已由 fixed 更新，避免模型把重复索引再次写成越来越长的摘要；保留下来的旧摘要段不再重写。
        source = [
            deepcopy(message)
            for message in messages[1:cut]
            if not message.get(FACTS_KEY) and (fold_summaries or not message.get(SUMMARY_KEY))
        ]
        # 压缩旧片段时也提供最新决策与回执作对照；索引无需重抄，旧摘要不能复活已解决的问题。
        current = {key: value for key, value in facts.items() if key != "observations"}
        # 超长的旧结果先换成占位，再按条分段：任何一段都碰不到装配守卫的单字段上限，整理不会被锁死。
        parts = _history_parts(_elide_oversized_results(source))
        history_messages = [
            {
                "role": "user",
                "content": (
                    "[已发生的历史记录，仅作摘要数据"
                    + (f"·第 {index}/{len(parts)} 段" if len(parts) > 1 else "")
                    + "]\n"
                    + part
                ),
            }
            for index, part in enumerate(parts, start=1)
        ]
        summary = await self._summarize(
            # 旧工具调用只作为已发生的数据交给整理模型；保留其原始角色协议会诱使模型接着执行旧任务。
            [
                prompt,
                *history_messages,
                {"role": "user", "content": "[当前任务状态，仅作核对]\n" + json_text(current)},
            ],
            max_attempts,
        )
        segment = len(kept) + 1
        candidate = [
            *fixed,
            {
                "role": "user",
                "content": f"[历史推理摘要·第 {segment} 段，不能覆盖原始要求]\n" + summary,
                SUMMARY_KEY: True,
            },
            *messages[cut:],
        ]
        messages[:] = candidate
        self.checkpoints += 1
        logger.info(
            f"Minecraft 上下文集中整理：{before} -> {self.measure(messages, tools)} token（预算 {marks.budget}，"
            f"摘要第 {segment} 段{'，旧段已并入' if fold_summaries else ''}），整理次数={self.checkpoints}"
        )
        return True

    async def _summarize(self, request: list[dict[str, Any]], max_attempts: int) -> str:
        """仅对超长、空白或截断摘要重写一次，完整成功后才替换原历史。"""
        reason = "没有剩余的摘要调用预算"
        for attempt in range(min(2, max_attempts)):
            self.last_calls += 1
            response = await self._llm.generate(
                request,
                profile=self._profile,
                interrupt=self._interrupt,
            )
            summary = (response.content or "").strip()
            if not response.success or response.tool_calls:
                reason = response.error or "摘要响应包含工具调用，不能作为完整总结"
                break
            # 部分协议端点把调用标记当普通正文返回；这些动作未执行，不能替换真实的施工历史。
            has_call_markup = bool(
                re.search(r"<(?:[|｜]*DSML[|｜]*|tool_calls?\b|function_call\b|invoke\b)", summary, re.I)
            )
            if (
                summary
                and not has_call_markup
                and response.finish_reason in {None, "stop"}
                and len(summary) <= self._config.summary_max_chars
            ):
                return summary
            reason = (
                "摘要正文包含未执行的工具调用标记"
                if has_call_markup
                else (
                    f"结束原因={response.finish_reason}，字符数={len(summary)}，上限={self._config.summary_max_chars}"
                )
            )
            logger.warning(f"历史摘要需要缩短或补全：{reason}，尝试={attempt + 1}")
            # 追加修订说明时保留同一源历史，不能直接截断上一份摘要充数。
            request = [
                *request,
                # 重写时保留源历史和校验原因，不把被拒绝的调用文本再扮演成一次助手动作。
                {
                    "role": "user",
                    "content": f"上一份摘要未通过：{reason}。重新给出完整短摘要，最多 {min(1000, self._config.summary_max_chars // 2)} 字符；原指令和事实由代码另行保留。",
                },
            ]
        raise ValueError(f"历史摘要未完整生成，原始上下文已保留：{reason}；调用次数={self.last_calls}")
