"""状态跃迁事实提取器单元测试：只挑状态跃迁类条目与回执要点，不做全量转储。

覆盖三种形态：有状态事件（死亡/重生/任务开始结束/交付 + 睡觉类关键词行）、
无状态事件（只有挨打/聊天/剧情行 → 空块）、带工具回执（minecraft_glance 形态
回执的关键字段成行）。行形态与时间线 game 条目一致（``[HH:MM 标签] 正文``）。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.agents.streamer.state_facts import extract_state_facts

# 本轮新入账批次的时间线行（与 streamer_agent 追加进时间线的 game 条目同形）
_GAME_EVENTS = [
    "[20:01 游戏·minecraft·milestone] 采集完成：8 个橡木原木进了背包",
    "[20:10 游戏·minecraft·report] 本次目标交付：蜂蜜胶已做好并收进背包",
    "[20:05 游戏·minecraft·attention_required] 附近有苦力怕在游荡",
    "[20:07 遭遇·minecraft·died] 角色倒下了：被苦力怕炸死（已结束）",
    "[20:08 遭遇·minecraft·respawned] 角色在床边重生",
    "[20:03 遭遇·minecraft·attacked] 身体挨了一阵打：骷髅射了两箭",
    "[20:09 minecraft] 玩家 Steve：你们服务器几点天亮",
]
_RECEIPT: Dict[str, Any] = {
    "body": {"dimension": "minecraft:overworld", "health": 20.0, "food": 18, "held": "铁镐"},
    "inventory": ["橡木原木 ×8", "床 ×1", "铁锭 ×3"],
    "work": {"todo": "做一个蜂蜜胶", "status": "running"},
    "entities": [{"type": "creeper", "distance": 12.0}],
}


def test_state_events_extracted_and_noise_skipped() -> None:
    """有状态事件：交付/死亡/重生/关键词行进块；挨打、游荡、聊天行跳过。"""
    facts = extract_state_facts(_GAME_EVENTS)
    lines = facts.splitlines()
    # 交付上报、死亡、重生、带"完成"关键词的里程碑都算状态跃迁
    assert any("交付：蜂蜜胶已做好" in line for line in lines)
    assert any("角色倒下了" in line and "（已结束）" in line for line in lines)
    assert any("重生" in line for line in lines)
    assert any("采集完成" in line for line in lines)
    # 非状态行不进块：挨打与游荡是遭遇/场景，游戏聊天整行不参与
    assert not any("挨了一阵打" in line for line in lines)
    assert not any("苦力怕在游荡" in line for line in lines)
    assert not any("几点天亮" in line for line in lines)
    # 保留原钟点标记，供逐句核对
    assert any(line.startswith("[20:10") for line in lines)


def test_delegation_line_is_state_transition() -> None:
    """委派行（我在 <game> 里开始做）是状态跃迁：一件事开始了。"""
    facts = extract_state_facts(["[20:12 我在 minecraft 里开始做] 做一个蜂蜜胶"])
    assert "做一个蜂蜜胶" in facts


def test_no_state_events_yields_empty_block() -> None:
    """无状态事件：只有非状态事件行时事实块为空串（调用方整段省略）。"""
    events = [
        "[20:05 游戏·minecraft·attention_required] 附近有苦力怕在游荡",
        "[20:03 遭遇·minecraft·attacked] 身体挨了一阵打：骷髅射了两箭",
    ]
    assert extract_state_facts(events) == ""


def test_tool_receipt_key_fields_appended() -> None:
    """带工具回执：glance 形态回执的身体/背包/工作要点各成一行；场景列表不进。"""
    facts = extract_state_facts([], [("glance", _RECEIPT)])
    assert "[glance回执]" in facts
    assert '身体：dimension="minecraft:overworld"，health=20.0，food=18，held="铁镐"' in facts
    assert "当前背包：橡木原木 ×8、床 ×1、铁锭 ×3" in facts
    assert "手头工作：" in facts and "蜂蜜胶" in facts
    # entities 是即时场景，不属状态
    assert "creeper" not in facts


def test_receipt_non_glance_shape_skipped_and_cap_applies() -> None:
    """非速览形态回执不进块；状态行超上限时只留最近的。"""
    assert extract_state_facts([], [("streamer_reply", {"ok": True})]) == ""
    many = [f"[20:{i:02d} 游戏·minecraft·report] 交付 {i} 号" for i in range(12)]
    facts = extract_state_facts(many)
    assert len(facts.splitlines()) == 8
    assert "交付 11 号" in facts and "交付 0 号" not in facts


# ---------------------------------------------------------------------------
# 接线：事实块经 reply 槽位进表达侧；空块占位不出现字面变量
# ---------------------------------------------------------------------------


async def _capture_turn_input(state_facts: str) -> str:
    """用固定渲染桩驱动 Replyer，返回模板收到的 state_facts 值。"""
    from unittest.mock import MagicMock

    from src.agents.streamer.plan import DecisionPlan
    from src.agents.streamer.replyer import Replyer

    prompt = MagicMock()
    prompt.render = MagicMock(return_value="TURN")
    llm = MagicMock()
    llm.generate = MagicMock()

    async def _generate(*args: Any, **kwargs: Any) -> Any:
        class _R:
            success = True
            content = "好的"

        return _R()

    llm.generate = _generate
    replyer = Replyer({}, llm, prompt)
    plan = DecisionPlan(should_reply=True, topic_summary="状态同步")
    await replyer.generate(plan, [], state_facts=state_facts)
    return prompt.render.call_args.kwargs["state_facts"]


@pytest.mark.asyncio
async def test_replyer_renders_state_facts_or_placeholder() -> None:
    """事实块原样进"当前状态（客观事实）"分节；空块占位为"（无）"。"""
    line = "[20:10 游戏·minecraft·report] 交付完成"
    assert await _capture_turn_input(line) == line
    assert await _capture_turn_input("") == "（无）"


@pytest.mark.asyncio
async def test_planner_hands_state_facts_to_reply_tool() -> None:
    """本轮新入账批次与回执汇成事实块随 reply 交给表达侧，调用后槽位清空。"""
    from unittest.mock import AsyncMock, MagicMock

    from src.agents.streamer.planner import Planner
    from src.agents.streamer.room_state import RoomState
    from src.agents.streamer.timeline import GameEventBatch
    from src.agents.streamer.tools.reply_tool import ReplyToolProvider
    from src.modules.llm.payload import Response, ToolCall
    from src.modules.tools.models import ToolExecutionResult

    seen: List[str] = []
    replyer = MagicMock()

    async def _generate(**kwargs: Any) -> Dict[str, Any]:
        seen.append(kwargs["state_facts"])
        return {"speech": "刚做完一件事", "emotion": {"name": "happy", "intensity": 0.5}, "metadata": {}}

    replyer.generate = AsyncMock(side_effect=_generate)
    provider = ReplyToolProvider(replyer=replyer)
    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])

    async def _invoke(invocation: Any) -> ToolExecutionResult:
        if invocation.tool_name == "streamer_reply":
            return await provider.invoke(invocation)
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            structured_content={"body": {"health": 12.0}},
        )

    registry.invoke = AsyncMock(side_effect=_invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(
            success=True,
            content="",
            tool_calls=[
                ToolCall(id="c1", name="glance", arguments={}),
                ToolCall(id="c2", name="streamer_reply", arguments={"topic_summary": "交付完成", "is_novel": True}),
            ],
        )
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config={"planner_max_steps": 2},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        reply_provider=provider,
    )

    outcome = await planner.plan(
        [],
        proactive=True,
        game_batch=GameEventBatch(events=("[20:10 游戏·minecraft·report] 本次目标交付：蜂蜜胶已做好",)),
    )

    assert outcome["replied"] is True
    assert len(seen) == 1
    assert "交付：蜂蜜胶已做好" in seen[0]
    assert "身体：health=12.0" in seen[0]
    # 事实块随调用单一次性传入：Provider 上不再有跨调用槽位
    assert not hasattr(provider, "_round_state_facts")
