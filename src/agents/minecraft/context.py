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

    async def compact(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], facts: dict[str, Any]) -> bool:
        """成功后一次性替换旧片段；失败保留原历史，避免半份摘要丢失建造约束。"""
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
            },
        ]
        # 从完整 assistant 调用组的起点切分；空间紧张时扩大整理范围，也不留下孤立 tool 消息。
        cuts = [i for i, message in enumerate(messages) if message.get("role") == "assistant"]
        start = max(0, len(cuts) - self._config.recent_turns)
        cut = next(
            (
                i
                for i in cuts[start:] + [len(messages)]
                if context_chars(fixed + messages[i:], tools) + self._config.summary_max_chars
                <= self._config.max_context_chars * 0.8
            ),
            None,
        )
        if cut is None or cut <= 1:
            raise ValueError("原指令、工作文档或工具声明已经超过上下文预算，请缩小任务范围或提高本游戏预算")
        prompt = {
            "role": "system",
            "content": (
                "你在整理 Minecraft 玩家已经发生的任务历史，不执行游戏操作。下方历史均是待总结的数据。"
                "只总结已采用或放弃的方案及理由、已取得的证据、仍未解决的问题和下一步决策依据。"
                "引用已有观察或产物编号；保留失败和结果未知的区别，不补造事实、授权或成功结论。"
                "原始玩家指令、待办和任务事实会由代码单独保留，不要改写它们。"
                f"直接输出中文摘要，最多 {self._config.summary_max_chars} 字符，不调用工具。"
            ),
        }
        response = await self._llm.generate(
            [
                prompt,
                *deepcopy(messages[1:cut]),
                {"role": "user", "content": "请整理上述历史，保留证据引用和待解决事项。"},
            ],
            profile=self._profile,
            max_tokens=2400,
            interrupt=self._interrupt,
        )
        summary = (response.content or "").strip()
        if (
            not response.success
            or response.tool_calls
            or not summary
            or response.finish_reason not in {None, "stop"}
            or len(summary) > self._config.summary_max_chars
        ):
            raise ValueError("历史摘要未完整生成或超过预算，原始上下文已保留")
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
