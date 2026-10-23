"""验证任务历史按预算集中整理、保留原要求和完整调用组。"""

import json
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig, MinecraftContextConfig
from src.agents.minecraft.context import (
    SUMMARY_KEY,
    MinecraftHistoryCompactor,
    close_interrupted_calls,
    opening_message,
)
from src.modules.config.agents_schemas import AgentsConfig
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager


@pytest.fixture(autouse=True)
def _chars_as_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """按字符数计 token：预算断言与是否装了 tiktoken 词表无关，数值也直观。"""
    monkeypatch.setattr("src.modules.context.tokens.count_text_tokens", len)


def tokens(messages: list[dict]) -> int:
    """与整理器同口径的历史 token 数（不含工具声明）。"""
    return MinecraftHistoryCompactor(MagicMock(), MinecraftContextConfig()).measure(messages, [])


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


async def test_checkpoint_preserves_facts_and_recent_protocol_groups_then_stays_stable() -> None:
    """整理一次后正常追加不再改旧消息，原始授权和未知结果独立于模型摘要保留。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(success=True, content="已完成设计，等待验证供料结果", finish_reason="stop")
    )
    config = MinecraftContextConfig(max_tokens=24000, recent_turns=2)
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
    assert tokens(messages) < config.max_tokens
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
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000))
    with pytest.raises(ValueError, match="摘要未完整"):
        await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    assert messages == before and compactor.checkpoints == 0


async def test_compact_summarize_request_carries_refs_not_base64() -> None:
    """带图历史的压缩摘要请求只携带引用句柄，体积与纯文本历史同量级。

    回归锚点：截图以 image part 进历史的时代，压缩器把全历史 json_text
    序列化进摘要请求，base64 被当文本重发（单笔 50-86 万 tokens、缓存
    命中≈0）。引用化后摘要请求里图只能是几十字节的句柄。
    """
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="摘要", finish_reason="stop"))
    messages = history()
    messages.insert(
        2,
        {
            "role": "tool",
            "tool_call_id": "c0",
            "content": [
                {"type": "text", "text": '{"ok":true,"image":"[图像 1400 KB 已省略]"}'},
                {"type": "image_ref", "ref": "img_ab12"},
            ],
        },
    )
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000, recent_turns=2))
    assert await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    summarize_request = llm.generate.call_args[0][0]
    serialized = json.dumps(summarize_request, ensure_ascii=False, default=str)
    assert "data:image" not in serialized
    assert "image_ref" in serialized and "img_ab12" in serialized
    assert len(serialized) < 100_000


@pytest.mark.asyncio
async def test_oversized_old_result_is_elided_and_history_is_split_under_the_guard() -> None:
    """一份 20 万字符的世界记忆留在旧历史里，整理照样做成：摘要请求里它换成占位，任何一段都碰不到装配守卫。

    回归锚点：ChangingSelf 场次 26 的 observe(world_memory) 回执 19.9 万字符，压缩器把全历史拼成一个
    37 万字符的字段，被 20 万字符的装配守卫拦了 17 次，历史 24 分钟只涨不落。
    """
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(success=True, content="查过世界记忆，记录很多", finish_reason="stop")
    )
    messages = history()
    messages[2]["tool_calls"][0]["function"] = {"name": "maicraft_observe", "arguments": '{"what":"world_memory"}'}
    messages[3]["content"] = '{"ok":true,"data":{"records":[' + '{"kind":"site"},' * 12_000 + "]}}"
    # 另有几条中等大小的旧结果，单条不省略，但拼在一起会超过一个字段的上限。
    for index in (5, 7, 9):
        messages[index]["content"] = "已确认的原生组件资料" * 4_500
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000, recent_turns=2))

    assert await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    request = llm.generate.call_args.args[0]
    history_parts = [message["content"] for message in request if message["content"].startswith("[已发生的历史记录")]
    assert len(history_parts) > 1 and history_parts[0].startswith("[已发生的历史记录，仅作摘要数据·第 1/")
    assert all(len(message["content"]) < 200_000 for message in request)
    serialized = "".join(history_parts)
    assert '\\"kind\\":\\"site\\"' not in serialized
    assert "maicraft_observe" in serialized and "world_memory" in serialized and '\\"chars\\":' in serialized
    assert "已确认的原生组件资料" in serialized
    assert tokens(messages) < 24000


async def test_oversized_opening_item_is_elided_alone() -> None:
    """开局资料只把超长的那一项换成占位：调用名和参数留着，其余项原样交给摘要。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(success=True, content="开局读过自己和能力清单", finish_reason="stop")
    )
    messages = history()
    messages.insert(
        2,
        opening_message(
            [
                ("maicraft_observe", {"what": "self"}, {"ok": True, "data": {"health": 20}}),
                ("maicraft_lookup", {}, {"ok": True, "data": {"abilities": [{"id": "dig"}] * 6_000}}),
            ]
        ),
    )
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000, recent_turns=2))

    assert await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    request = llm.generate.call_args.args[0]
    serialized = "".join(m["content"] for m in request if m["content"].startswith("[已发生的历史记录"))
    assert "[开局资料]" in serialized and '\\"health\\":20' in serialized
    assert "maicraft_lookup" in serialized and "这项开局资料太长" in serialized
    assert '\\"id\\":\\"dig\\"' not in serialized


def test_interrupted_calls_have_unknown_receipts_and_config_stays_in_minecraft() -> None:
    """中断后补齐缺失回执而不伪造成功；预算字段随 Minecraft 配置独立装配。"""
    messages = history()
    messages.pop()
    close_interrupted_calls(messages)
    assert messages[-1]["tool_call_id"] == "c14" and '"outcome_known":false' in messages[-1]["content"]
    stable = deepcopy(messages)
    close_interrupted_calls(messages)
    assert messages == stable
    config = AgentsConfig.model_validate({"minecraft": {"context": {"max_tokens": 64000}}})
    assert config.minecraft.context.max_tokens == 64000
    assert "context" not in config.text_adv.model_dump()


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
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000))
    assert await compactor.compact(messages, [], {"original_instructions": ["禁止取私人箱子"]})
    assert compactor.last_calls == 2 and compactor.checkpoints == 1
    assert "旧索引不要进入摘要" not in str(llm.generate.call_args_list[0].args[0])
    assert "禁止取私人箱子" in messages[1]["content"]
    assert "已确认接口" in messages[2]["content"] and "摘" * 100 not in str(messages)


async def test_failed_rewrite_is_bounded_by_remaining_budget() -> None:
    """只剩一次预算时不得隐藏重试，失败原因包含实际长度且原历史完整。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="摘" * 6575, finish_reason="stop"))
    messages = history()
    before = deepcopy(messages)
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000))
    with pytest.raises(ValueError, match="6575"):
        await compactor.compact(messages, [], {}, max_attempts=1)
    assert compactor.last_calls == 1 and messages == before


async def test_summary_reads_inert_history_and_rejects_textual_tool_calls() -> None:
    """复现 DSML 混入摘要：旧调用只作为数据读取，模型写出的未执行动作必须被拒绝后重写。"""
    llm = MagicMock()
    malformed = '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="maicraft_goal">get</invoke>'
    llm.generate = AsyncMock(
        side_effect=[
            Response(success=True, content=malformed, finish_reason="stop"),
            Response(success=True, content="十九个准备目标已匹配，缺少一根横向传动轴。", finish_reason="stop"),
        ]
    )
    messages = history()
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000))
    assert await compactor.compact(messages, [], {"original_instructions": ["在原平台建好机器"]})
    request = llm.generate.call_args_list[0].args[0]
    assert [item["role"] for item in request] == ["system", "user", "user"]
    assert '"tool_calls"' in request[1]["content"] and '"role":"tool"' in request[1]["content"]
    assert compactor.last_calls == 2 and malformed not in str(messages)
    assert "十九个准备目标" in messages[2]["content"]


async def test_repeated_textual_calls_do_not_replace_history() -> None:
    """连续两次伪调用都不能覆盖未完成机器的原要求、方块证据和工具回执。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(success=True, content='<tool_call name="execute"/>', finish_reason="stop")
    )
    messages = history()
    before = deepcopy(messages)
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000))
    with pytest.raises(ValueError, match="工具调用标记"):
        await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    assert messages == before and compactor.last_calls == 2


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
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000))
    assert await compactor.compact(messages, [], facts)
    request = llm.generate.call_args.args[0]
    current = request[-1]["content"]
    assert "已根据实际配方确定目标产物" in current and '"status":"done"' in current
    assert '"snapshot_id":"site"' in current and "动力接入尚未实测" in current
    assert "大索引由主上下文保存" not in str(request)
    assert "大索引由主上下文保存" in messages[1]["content"]


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
    agent = MinecraftAgent(MinecraftConfig(context=MinecraftContextConfig(max_tokens=24000)), llm_manager=llm)
    agent._task_instructions = ["建好，保留原有约束"]
    agent._task_finished = False
    agent._task_steps = used_steps
    agent._messages = history()
    agent._running = True
    await agent._run_task_batch()
    assert agent._task_steps == used_steps + 3 and not agent._task_suspended
    assert agent._task_finished and llm.generate.await_count == 3
    assert agent._context_compactor.last_calls == 2


async def test_later_checkpoint_appends_segment_without_rewriting_earlier_one() -> None:
    """第二次整理只把上次以来的原始记录压成新一段，第一段原样保留、不进摘要请求。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            Response(success=True, content="第一段：已查清资料", finish_reason="stop"),
            Response(success=True, content="第二段：开始施工", finish_reason="stop"),
        ]
    )
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000, recent_turns=2))
    messages = history()
    assert await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    first = deepcopy(messages[2])
    assert first[SUMMARY_KEY] and "第 1 段" in first["content"]

    messages.extend(history()[2:])
    assert await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    assert messages[2] == first
    assert messages[3][SUMMARY_KEY] and "第 2 段" in messages[3]["content"] and "开始施工" in messages[3]["content"]
    assert "第一段：已查清资料" not in str(llm.generate.call_args_list[1].args[0][1])


async def test_oversized_segments_fold_into_new_one() -> None:
    """旧摘要段总量超过预算两成时，下次整理把它们一并并入新的一段，避免摘要段无限累积。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="并入后的摘要", finish_reason="stop"))
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_tokens=24000, recent_turns=2))
    messages = history()
    bulky = {"role": "user", "content": "[历史推理摘要·第 1 段]\n" + "旧段" * 3000, SUMMARY_KEY: True}
    messages.insert(1, bulky)
    assert await compactor.compact(messages, [], {"original_instructions": ["建好"]})
    segments = [message for message in messages if message.get(SUMMARY_KEY)]
    assert len(segments) == 1 and "并入后的摘要" in segments[0]["content"] and "第 1 段" in segments[0]["content"]
    assert "旧段旧段" in str(llm.generate.call_args.args[0][1])


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


async def test_unread_oversized_receipt_is_read_before_one_compaction() -> None:
    """最新回执还没读、整理后仍超预算时本轮跳过；读完后下一轮一次整理到预算内。

    实测每份大回执都会连续整理两次（第一次压不下去），每次让身体多站 20–60 秒。
    """
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="背包打开一直失败", finish_reason="stop"))
    config = MinecraftContextConfig(max_tokens=24000, recent_turns=2)
    compactor = MinecraftHistoryCompactor(llm, config)
    messages = _history_with_unread_receipt(20000)
    before = tokens(messages)
    assert config.max_tokens < before <= config.max_tokens * 1.5
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
    assert llm.generate.await_count == 1 and tokens(messages) < config.max_tokens


async def test_far_oversized_context_still_compacts_before_reading() -> None:
    """超过预算 1.5 倍时不再跳过，照常整理旧历史，避免撞上模型上下文窗口。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="背包打开一直失败", finish_reason="stop"))
    config = MinecraftContextConfig(max_tokens=24000, recent_turns=2)
    compactor = MinecraftHistoryCompactor(llm, config)
    messages = _history_with_unread_receipt(40000)
    assert tokens(messages) > config.max_tokens * 1.5

    assert await compactor.compact(messages, [], {"original_instructions": ["取一块铁板"]})
    assert llm.generate.await_count == 1
    # 尚未阅读的大回执原样保留给模型
    assert messages[-1]["tool_call_id"] == "big" and len(messages[-1]["content"]) == 40000


def test_template_states_how_to_play_with_maicraft_v1() -> None:
    """系统提示词写明 v1 的玩法：五个工具各管什么、一次一个动手目标、等待靠宿主通知、完成标准与底线。"""
    reset_prompt_manager()
    try:
        prompt = MinecraftAgent(MinecraftConfig(), prompt_manager=get_prompt_manager())._system_prompt()
    finally:
        reset_prompt_manager()
    for tool in ("maicraft_observe", "maicraft_lookup", "maicraft_execute", "maicraft_goal", "minecraft_wait"):
        assert tool in prompt
    assert "maicraft_events" not in prompt, "事件流只给宿主读"
    assert "身体同一时间只做一件事" in prompt and "[开局资料]" in prompt
    assert "error.fields" in prompt and "unconfirmed" in prompt
    assert "## 完成标准" in prompt and "顶替不算完成" in prompt and "子问题解决后直接回原目标" in prompt
    assert "## 遇到困难时" in prompt and '说"做不到"要有证据' in prompt
    assert "你就是这场直播的主播" in prompt and "没有谁派谁" in prompt
    assert "像生存玩家一样玩" in prompt and "/tp" in prompt and "创造模式" in prompt


def test_fallback_prompt_keeps_the_bottom_lines() -> None:
    """模板渲染不了时，内建提示词仍守住交付标准、卡住时的做法和不走捷径。"""
    manager = MagicMock()
    manager.render.side_effect = RuntimeError("模板坏了")
    prompt = MinecraftAgent(MinecraftConfig(), prompt_manager=manager)._system_prompt()
    assert "本次目标要的结果真的达成才交付" in prompt and "听本次目标" in prompt
    assert "至少试过两种办法再用" in prompt and "身体同一时间只做一件事" in prompt
    assert "像生存玩家一样玩" in prompt and "/tp" in prompt and "/give" in prompt and "创造模式" in prompt
