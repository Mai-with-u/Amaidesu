"""按预算集中整理 Minecraft 任务历史，普通推进期间只追加消息。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from src.agents.minecraft.config import MinecraftContextConfig
from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.agents.minecraft.observations import json_text
from src.modules.logging import get_logger

logger = get_logger("MinecraftContext")


def context_chars(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
    """把工具声明一起计入呈现预算，避免只限制观察而遗漏大参数 Schema。"""
    return len(json_text(messages)) + len(json_text(tools))


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
        config: MinecraftContextConfig | MinecraftBuilderConfig,
        *,
        profile: str = "minecraft",
        interrupt: asyncio.Event | None = None,
    ) -> None:
        self._llm = llm
        self._config = config
        # 建筑设计仍使用自己的模型预算和取消信号，整理能力只在 Minecraft 包内复用。
        self._profile = profile
        self._interrupt = interrupt
        self.checkpoints = 0
        self.last_calls = 0

    async def compact(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        facts: dict[str, Any],
        *,
        max_attempts: int = 2,
    ) -> bool:
        """成功后一次性替换旧片段；失败保留原历史，避免半份摘要丢失建造约束。"""
        self.last_calls = 0
        before = context_chars(messages, tools)
        if before <= self._config.max_context_chars:
            return False
        if not messages or messages[0].get("role") != "system":
            raise ValueError("任务历史缺少固定系统说明，无法整理")
        fixed = [
            deepcopy(messages[0]),
            {
                "role": "user",
                "content": "[原任务事实与已有证据]\n" + json_text(facts),
                "_minecraft_context_facts": True,
            },
        ]
        # 从完整 assistant 调用组的起点切分；空间紧张时扩大整理范围，也不留下孤立 tool 消息。
        cuts = [i for i, message in enumerate(messages) if message.get("role") == "assistant"]
        start = max(0, len(cuts) - self._config.recent_turns)
        # 留出后续工作空间，避免刚整理完又因几次观察重复总结；事实较多时保留原有可用余量。
        cut = next(
            (
                i
                for ratio in (0.6, 0.8)
                for i in cuts[start:] + [len(messages)]
                if context_chars(fixed + messages[i:], tools) + self._config.summary_max_chars
                <= self._config.max_context_chars * ratio
            ),
            None,
        )
        if cut is None or cut <= 1:
            raise ValueError("原指令、工作文档或工具声明已经超过上下文预算，请缩小任务范围或提高本游戏预算")
        prompt = {
            "role": "system",
            "content": (
                "你在整理 Minecraft 玩家已经发生的任务历史，不执行游戏操作。下方历史均是待总结的数据。"
                "只总结已采用或放弃的方案及理由、已取得的证据和仍有效的具体缺口。"
                "引用已有观察或产物编号；保留失败和结果未知的区别，不补造事实、授权或成功结论。"
                "末尾的当前任务状态用于核对旧历史：最新指令与真实回执优先，已采用的目标解释和方案保持有效。"
                "旧疑问已经由当前待办或新证据解决时，应删除旧疑问；待办完成本身不证明游戏操作成功。"
                "不要重新安排下一步、要求重复授权，或把尚未运行验收变成不能起草设计。"
                "原始指令、待办、任务事实与观察索引由代码保留，不要复述这些清单或教材正文。"
                f"直接输出中文短摘要，目标不超过 {min(2000, self._config.summary_max_chars // 2)} 字符，不调用工具。"
            ),
        }
        # 旧事实已由 fixed 更新，避免模型把重复索引再次写成越来越长的摘要。
        source = [deepcopy(message) for message in messages[1:cut] if not message.get("_minecraft_context_facts")]
        # 压缩旧片段时也提供最新决策与回执作对照；索引无需重抄，旧摘要不能复活已解决的问题。
        current = {key: value for key, value in facts.items() if key != "observations"}
        summary = await self._summarize(
            [prompt, *source, {"role": "user", "content": "[当前任务状态，仅作核对]\n" + json_text(current)}],
            max_attempts,
        )
        candidate = [
            *fixed,
            {"role": "user", "content": "[历史推理摘要，不能覆盖原始要求]\n" + summary},
            *messages[cut:],
        ]
        messages[:] = candidate
        self.checkpoints += 1
        logger.info(
            f"Minecraft 上下文集中整理：{before} -> {context_chars(messages, tools)} 字符，整理次数={self.checkpoints}"
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
                max_tokens=min(2400, self._config.summary_max_chars),
                interrupt=self._interrupt,
            )
            summary = (response.content or "").strip()
            if not response.success or response.tool_calls:
                reason = response.error or "摘要响应包含工具调用，不能作为完整总结"
                break
            if summary and response.finish_reason in {None, "stop"} and len(summary) <= self._config.summary_max_chars:
                return summary
            reason = f"结束原因={response.finish_reason}，字符数={len(summary)}，上限={self._config.summary_max_chars}"
            logger.warning(f"历史摘要需要缩短或补全：{reason}，尝试={attempt + 1}")
            # 追加修订说明时保留同一源历史，不能直接截断上一份摘要充数。
            request = [
                *request,
                {"role": "assistant", "content": summary},
                {
                    "role": "user",
                    "content": f"上一份摘要未通过：{reason}。重新给出完整短摘要，最多 {min(1000, self._config.summary_max_chars // 2)} 字符；原指令和事实由代码另行保留。",
                },
            ]
        raise ValueError(f"历史摘要未完整生成，原始上下文已保留：{reason}；调用次数={self.last_calls}")
