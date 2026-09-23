"""验证任务历史按预算集中整理、保留原要求和完整调用组。"""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.config import MinecraftContextConfig
from src.agents.minecraft.context import MinecraftHistoryCompactor, close_interrupted_calls, context_chars
from src.modules.config.agents_schemas import AgentsConfig
from src.modules.llm.payload import Response, ToolCall


def history() -> list[dict]:
    """包含多轮完整工具往返，预算超限来自历史累积而非孤立协议消息。"""
    messages = [{"role": "system", "content": "完成玩家目标"}, {"role": "user", "content": "建好；禁止取私人箱子"}]
    for i in range(15):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "检查已有资料",
                    "tool_calls": [
                        {"id": f"c{i}", "type": "function", "function": {"name": "observe", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": f"c{i}", "content": "已确认的原生组件资料" * 350},
            ]
        )
    return messages


@pytest.mark.asyncio
async def test_checkpoint_preserves_facts_and_recent_protocol_groups_then_stays_stable() -> None:
    """整理一次后正常追加不再改旧消息，原始授权和未知结果独立于模型摘要保留。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(success=True, content="已完成设计，等待验证供料结果", finish_reason="stop")
    )
    config = MinecraftContextConfig(max_context_chars=24000, recent_turns=2)
    compactor = MinecraftHistoryCompactor(llm, config)
    messages = history()
    facts = {"original_instructions": ["建好；禁止取私人箱子"], "outcome_known": False, "artifact_ref": "design-1"}
    assert await compactor.compact(messages, [], facts)
    assert context_chars(messages, []) < config.max_context_chars
    assert "禁止取私人箱子" in messages[1]["content"] and '"outcome_known":false' in messages[1]["content"]
    pending = set()
    for message in messages:
        if message["role"] == "assistant":
            pending.update(call["id"] for call in message.get("tool_calls", []))
        elif message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
    assert not pending
    stable = deepcopy(messages)
    messages.append({"role": "user", "content": "供料任务完成"})
    assert not await compactor.compact(messages, [], facts)
    assert messages[: len(stable)] == stable and llm.generate.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        Response(success=False, error="provider unavailable"),
        Response(success=True, content="未完整摘要", finish_reason="length"),
        Response(success=True, tool_calls=[ToolCall(name="execute", arguments={})]),
    ],
)
async def test_failed_checkpoint_keeps_original_history(response: Response) -> None:
    """摘要失败或试图调用工具时保留所有原件，不能用半份总结覆盖工作历史。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=response)
    messages = history()
    before = deepcopy(messages)
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_context_chars=24000))
    with pytest.raises(ValueError, match="摘要未完整"):
        await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    assert messages == before and compactor.checkpoints == 0


def test_interrupted_calls_have_unknown_receipts_and_config_stays_in_minecraft() -> None:
    """中断后补齐缺失回执而不伪造成功；预算字段随 Minecraft 配置独立装配。"""
    messages = history()
    messages.pop()
    close_interrupted_calls(messages)
    assert messages[-1]["tool_call_id"] == "c14" and '"outcome_known":false' in messages[-1]["content"]
    stable = deepcopy(messages)
    close_interrupted_calls(messages)
    assert messages == stable
    config = AgentsConfig.model_validate({"minecraft": {"context": {"max_context_chars": 64000}}})
    assert config.minecraft.context.max_context_chars == 64000
    assert "context" not in config.text_adv.model_dump()
