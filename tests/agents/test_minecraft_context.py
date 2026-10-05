"""验证任务历史按预算集中整理、保留原要求和完整调用组。"""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig, MinecraftContextConfig
from src.agents.minecraft.context import MinecraftHistoryCompactor, close_interrupted_calls, context_chars
from src.modules.config.agents_schemas import AgentsConfig
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager


@pytest.mark.parametrize("with_template", [False, True])
def test_material_priority_is_shared_by_template_and_fallback(with_template: bool) -> None:
    """不同提示入口都保留随身与无线库存优先，普通取材不被模型自行缩成逐木种采矿。"""
    manager = MagicMock() if with_template else None
    if manager is not None:
        manager.render.return_value = "通过原生工具完成游戏任务"
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock(), prompt_manager=manager)
    prompt = agent._system_prompt()
    assert "先用随身库存和可立即使用的 AE 无线现货" in prompt
    assert "默认省略 allowed_sources" in prompt and "只有用户明确限定来源才收窄" in prompt
    assert "不按木种逐个试搜" in prompt


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
    # 当前计划独立于旧笔记保存；真正整理历史后，编号和 ready 状态仍留在事实区。
    facts = {
        "original_instructions": ["建好；禁止取私人箱子"],
        "outcome_known": False,
        "artifact_ref": "design-1",
        "notebook": "旧笔记：方案尚待校验",
        "plan_facts": [{"plan_id": "validated-plan", "state": "ready", "result_ref": "plan-observation"}],
    }
    assert await compactor.compact(messages, [], facts)
    assert context_chars(messages, []) < config.max_context_chars
    assert "禁止取私人箱子" in messages[1]["content"] and '"outcome_known":false' in messages[1]["content"]
    assert '"plan_id":"validated-plan"' in messages[1]["content"] and '"state":"ready"' in messages[1]["content"]
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first",
    [
        Response(success=True, content="摘" * 6575, finish_reason="stop"),
        Response(success=True, content="未完成", finish_reason="length"),
    ],
)
async def test_oversized_or_incomplete_summary_is_rewritten_before_commit(first: Response) -> None:
    """复现实战 6575 字符摘要，先有界缩写，不直接丢弃任务或截断半份总结。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[first, Response(success=True, content="已确认接口，下一步提交方案供校验", finish_reason="stop")]
    )
    messages = history()
    messages.insert(2, {"role": "user", "content": "旧索引不要进入摘要", "_minecraft_context_facts": True})
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_context_chars=24000))
    assert await compactor.compact(messages, [], {"original_instructions": ["禁止取私人箱子"]})
    assert compactor.last_calls == 2 and compactor.checkpoints == 1
    assert "旧索引不要进入摘要" not in str(llm.generate.call_args_list[0].args[0])
    assert "禁止取私人箱子" in messages[1]["content"]
    assert "已确认接口" in messages[2]["content"] and "摘" * 100 not in str(messages)


@pytest.mark.asyncio
async def test_failed_rewrite_is_bounded_by_remaining_budget() -> None:
    """只剩一次预算时不得隐藏重试，失败原因包含实际长度且原历史完整。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="摘" * 6575, finish_reason="stop"))
    messages = history()
    before = deepcopy(messages)
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_context_chars=24000))
    with pytest.raises(ValueError, match="6575"):
        await compactor.compact(messages, [], {}, max_attempts=1)
    assert compactor.last_calls == 1 and messages == before


@pytest.mark.asyncio
async def test_summary_reads_inert_history_and_rejects_textual_tool_calls() -> None:
    """复现 DSML 混入摘要：旧调用只作为数据读取，模型写出的未执行动作必须被拒绝后重写。"""
    llm = MagicMock()
    malformed = '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="maicraft_task">get</invoke>'
    llm.generate = AsyncMock(
        side_effect=[
            Response(success=True, content=malformed, finish_reason="stop"),
            Response(success=True, content="十九个准备目标已匹配，缺少一根横向传动轴。", finish_reason="stop"),
        ]
    )
    messages = history()
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_context_chars=24000))
    assert await compactor.compact(messages, [], {"original_instructions": ["在原平台建好机器"]})
    request = llm.generate.call_args_list[0].args[0]
    assert [item["role"] for item in request] == ["system", "user", "user"]
    assert '"tool_calls"' in request[1]["content"] and '"role":"tool"' in request[1]["content"]
    assert compactor.last_calls == 2 and malformed not in str(messages)
    assert "十九个准备目标" in messages[2]["content"]


@pytest.mark.asyncio
async def test_repeated_textual_calls_do_not_replace_history() -> None:
    """连续两次伪调用都不能覆盖未完成机器的原要求、方块证据和工具回执。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(success=True, content='<tool_call name="execute"/>', finish_reason="stop")
    )
    messages = history()
    before = deepcopy(messages)
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_context_chars=24000))
    with pytest.raises(ValueError, match="工具调用标记"):
        await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    assert messages == before and compactor.last_calls == 2


@pytest.mark.asyncio
async def test_checkpoint_compares_old_doubts_with_current_decisions() -> None:
    """旧片段仍在讨论目标时，摘要必须看到后续已经采用的解释和真实执行回执。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="方案依据已保留", finish_reason="stop"))
    messages = history()
    messages.insert(1, {"role": "user", "content": "旧疑问：目标名称还不明确，需要再次确认"})
    facts = {
        "original_instructions": ["在平台上建好机器，遵守禁用模组"],
        "todo": [{"content": "已根据实际配方确定目标产物", "status": "done"}],
        "notebook": "采用已有工艺，先形成蓝图，动力接入尚未实测",
        "background_tasks": [{"task_id": "survey", "status": "succeeded", "snapshot_id": "site"}],
        "observations": [{"ref": "大索引由主上下文保存"}],
    }
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_context_chars=24000))
    assert await compactor.compact(messages, [], facts)
    request = llm.generate.call_args.args[0]
    current = request[-1]["content"]
    assert "已根据实际配方确定目标产物" in current and '"status":"done"' in current
    assert '"snapshot_id":"site"' in current and "动力接入尚未实测" in current
    assert "大索引由主上下文保存" not in str(request)
    assert "大索引由主上下文保存" in messages[1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("used_steps", [49, 50, 75])
async def test_game_loop_summarizes_and_continues_past_fifty_steps(used_steps: int) -> None:
    """长任务接近或超过五十步时仍可重写摘要，随后继续核验并交付原游戏目标。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            Response(success=True, content="摘" * 6575, finish_reason="stop"),
            Response(success=True, content="已确认接口，待提交方案", finish_reason="stop"),
            Response(success=True, content="施工已核验，目标完成", finish_reason="stop"),
        ]
    )
    agent = MinecraftAgent(MinecraftConfig(context=MinecraftContextConfig(max_context_chars=24000)), llm_manager=llm)
    agent._task_instructions = ["建好，保留原有约束"]
    agent._task_finished = False
    agent._task_steps = used_steps
    agent._messages = history()
    agent._running = True
    await agent._run_task_batch()
    assert agent._task_steps == used_steps + 3 and not agent._task_suspended
    assert agent._task_finished and llm.generate.await_count == 3
    assert agent._context_compactor.last_calls == 2


def _history_with_unread_receipt(receipt_chars: int) -> list[dict]:
    """几轮已读的小回执，加上最新一组尚未阅读的大回执（实测 13 万字的整份任务回执）。"""
    messages = [{"role": "system", "content": "完成玩家目标"}, {"role": "user", "content": "取一块铁板"}]
    for i in range(4):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "查看取材回执",
                    "tool_calls": [
                        {"id": f"s{i}", "type": "function", "function": {"name": "observe", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": f"s{i}", "content": "背包打开失败" * 300},
            ]
        )
    messages.extend(
        [
            {
                "role": "assistant",
                "content": "整份读取最新任务回执",
                "tool_calls": [{"id": "big", "type": "function", "function": {"name": "task", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "big", "content": "完整回执" * (receipt_chars // 4)},
        ]
    )
    return messages


@pytest.mark.asyncio
async def test_unread_oversized_receipt_is_read_before_one_compaction() -> None:
    """最新回执还没读、整理后仍超预算时本轮跳过；读完后下一轮一次整理到预算内。

    实测每份大回执都会连续整理两次（第一次压不下去），每次让身体多站 20–60 秒。
    """
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="背包打开一直失败", finish_reason="stop"))
    config = MinecraftContextConfig(max_context_chars=24000, recent_turns=2)
    compactor = MinecraftHistoryCompactor(llm, config)
    messages = _history_with_unread_receipt(20000)
    before = context_chars(messages, [])
    assert config.max_context_chars < before <= config.max_context_chars * 1.5
    snapshot = deepcopy(messages)

    assert not await compactor.compact(messages, [], {"original_instructions": ["取一块铁板"]})
    assert messages == snapshot and llm.generate.await_count == 0 and compactor.last_calls == 0

    # 模型读完大回执、发出下一步后，大回执已是旧历史，一次整理即可回到预算内
    messages.extend(
        [
            {
                "role": "assistant",
                "content": "改走合成",
                "tool_calls": [{"id": "next", "type": "function", "function": {"name": "craft", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "next", "content": "已受理"},
        ]
    )
    assert await compactor.compact(messages, [], {"original_instructions": ["取一块铁板"]})
    assert llm.generate.await_count == 1 and context_chars(messages, []) < config.max_context_chars


@pytest.mark.asyncio
async def test_far_oversized_context_still_compacts_before_reading() -> None:
    """超过预算 1.5 倍时不再跳过，照常整理旧历史，避免撞上模型上下文窗口。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="背包打开一直失败", finish_reason="stop"))
    config = MinecraftContextConfig(max_context_chars=24000, recent_turns=2)
    compactor = MinecraftHistoryCompactor(llm, config)
    messages = _history_with_unread_receipt(40000)
    assert context_chars(messages, []) > config.max_context_chars * 1.5

    assert await compactor.compact(messages, [], {"original_instructions": ["取一块铁板"]})
    assert llm.generate.await_count == 1
    # 尚未阅读的大回执原样保留给模型
    assert messages[-1]["tool_call_id"] == "big" and len(messages[-1]["content"]) == 40000


@pytest.mark.parametrize("with_template", [False, True])
def test_pacing_rules_reach_template_and_fallback(with_template: bool) -> None:
    """模板与内建兜底都要求：身体执行任务时准备下一步、按需读取、换路不重试、上报只写要点。"""
    reset_prompt_manager()
    try:
        manager = get_prompt_manager() if with_template else None
        prompt = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock(), prompt_manager=manager)._system_prompt()
    finally:
        reset_prompt_manager()
    if with_template:
        assert "身体执行任务的这段时间是你的准备时间" in prompt and "身体闲着时观众在等" in prompt
        assert "按需读取" in prompt and "不整份读取大回执" in prompt
        assert "换路而不是重试" in prompt and "任务被暂停" in prompt
        assert "念给观众听" in prompt and "写 JSON 数字，不加引号" in prompt
    else:
        assert "先准备下一步要用的契约与方案" in prompt and "任务被暂停时按原因处理" in prompt
        assert "按 ref/path 只读需要的字段" in prompt and "同一路径连续失败就换路径" in prompt
