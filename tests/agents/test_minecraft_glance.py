"""主播看一眼游戏 + 连续失败通报的回归测试。

覆盖：
- glance 纯函数：只保留叙事事实（整格坐标/血量/背包数量/牌子文字/生物），缺失字段不冒充"没有"
- minecraft_glance 端到端：经 Mod 感知工具读两次原生观察，附上身体手头的工作
- 游戏内动作连续失败：第 3 次通报主播、成功清零、之后每 5 次补报
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.glance import glance_situation, glance_surroundings
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.tools.models import ToolExecutionResult, ToolInvocation

_SITUATION: Dict[str, Any] = {
    "dimension": "minecraft:overworld",
    "position": {"x": -85.95, "y": 105.0, "z": 27.1},
    "view": {"yaw": 359.4, "pitch": -4.5},
    "health": 20.0,
    "max_health": 20.0,
    "food": 20,
    "air": 300,
    "time_phase": "day",
    "weather": "clear",
    "in_water": False,
    "inventory": [
        {"item_id": "minecraft:iron_ingot", "count": 3, "location_counts": {"backpack": 3}},
        {"item_id": "minecraft:iron_axe", "count": 12, "variants": [{"resource_id": "items:minecraft:iron_axe#ab"}]},
    ],
    "carried_storage": {"carried_backpacks": 2, "unobserved_backpacks": 2},
    "equipment": {"feet": {"item_id": "minecraft:diamond_boots", "damage": 185, "resource_id": "x"}},
}

_SURROUNDINGS: Dict[str, Any] = {
    "biome": "minecraft:plains",
    "nearby_signs": [
        {"position": {"x": -86, "y": 105, "z": 27}, "distance": 0.8, "front_lines": ["", "停机开关", "", ""]},
        {"distance": 17.5, "front_lines": ["", "MS社区食堂", "（旧工业区站点）", ""], "back_lines": ["", "", "", ""]},
        {"distance": 3.0, "front_lines": ["", "", "", ""]},
    ],
    "nearby_entities": [{"type": "minecraft:bee", "distance": 3.4}],
    "nearby_facilities": {"facilities": [{"block_id": "minecraft:campfire", "count": 3, "nearest_distance": 4.2}]},
    "local_decision_summary": {"standable_regions": [1, 2, 3]},
}


def test_glance_situation_keeps_narrative_facts_only() -> None:
    """身体视图：整格坐标、血量、背包物品数量；视线向量与物品哈希不进主播上下文。"""
    facts = glance_situation(_SITUATION)

    assert facts["body"] == {
        "dimension": "minecraft:overworld",
        "position": [-86, 105, 27],
        "health": "20/20",
        "food": 20,
        "time_phase": "day",
        "weather": "clear",
    }
    assert facts["inventory"] == ["minecraft:iron_ingot ×3", "minecraft:iron_axe ×12"]
    assert facts["equipment"] == {"feet": "minecraft:diamond_boots"}
    # 没打开的随身背包照实说"不知道里面有什么"，不能让主播以为清单就是全部
    assert "2 个随身背包没打开看过" in facts["inventory_note"]
    assert "resource_id" not in json.dumps(facts, ensure_ascii=False)


def test_glance_surroundings_reads_sign_text_and_skips_geometry() -> None:
    """周边视图：牌子文字连成一句、空白牌子不列；几何扫描不进主播上下文。"""
    facts = glance_surroundings(_SURROUNDINGS)

    assert facts["signs"] == [
        {"text": "停机开关", "distance": 0.8},
        {"text": "MS社区食堂 / （旧工业区站点）", "distance": 17.5},
    ]
    assert facts["entities"] == [{"type": "minecraft:bee", "distance": 3.4}]
    assert facts["facilities"] == [{"block": "minecraft:campfire", "count": 3, "nearest_distance": 4.2}]
    assert "local_decision_summary" not in facts


def test_glance_reports_read_failures_instead_of_empty_lists() -> None:
    """读取失败照实写原因，不给出"附近什么都没有"的假象；缺失分区不写。"""
    failed = glance_surroundings({"ok": False, "error": {"message": "MCP 未连接"}})
    partial = glance_surroundings({"biome": "minecraft:plains"})

    assert failed == {"nearby_error": "MCP 未连接"}
    assert partial == {"biome": "minecraft:plains"}


def _agent_with_perceive() -> tuple[MinecraftAgent, List[Dict[str, Any]]]:
    """带假感知工具的 Agent：按 view 返回预置观察，并记录每次调用参数。"""
    calls: List[Dict[str, Any]] = []

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        calls.append(dict(invocation.arguments))
        body = _SITUATION if invocation.arguments.get("view") == "situation" else _SURROUNDINGS
        return ToolExecutionResult(
            tool_name=invocation.tool_name, success=True, structured_content=body, content=json.dumps(body)
        )

    registry = MagicMock()
    registry.invoke = AsyncMock(side_effect=_invoke)
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock(), tool_registry=registry)
    agent._perceive_tool = "maicraft_perceive"
    return agent, calls


@pytest.mark.asyncio
async def test_glance_tool_combines_body_surroundings_and_work() -> None:
    """minecraft_glance：读现状与周边各一次，附上待办与连续失败次数。"""
    agent, calls = _agent_with_perceive()
    agent._mc_state.set_todos([{"content": "拿 1 块铁板", "status": "in_progress"}])
    agent._task_finished = False
    agent._failure_streak = 4

    result = await agent._tool_provider.invoke(ToolInvocation(tool_name="minecraft_glance", arguments={}))

    assert result.success is True
    view = result.structured_content
    assert [call["view"] for call in calls] == ["situation", "surroundings"]
    assert view["body"]["position"] == [-86, 105, 27]
    assert view["signs"][0]["text"] == "停机开关"
    assert view["work"] == {
        "state": "正在做手上的事",
        "todo": [{"content": "拿 1 块铁板", "status": "in_progress"}],
        "failure_streak": 4,
    }


@pytest.mark.asyncio
async def test_glance_without_mod_connection_says_so() -> None:
    """Mod 没连上时不调用任何工具，直接说明看不到，并仍给出身体手头的工作。"""
    agent, calls = _agent_with_perceive()
    agent._perceive_tool = None

    view = await agent._glance()

    assert calls == []
    assert "还没就绪" in view["unavailable"]
    assert view["work"]["state"] == "空闲"


async def _drain(agent: MinecraftAgent) -> None:
    """等通报的后台发射任务跑完。"""
    for _ in range(5):
        if not agent._bg_tasks:
            return
        await asyncio.gather(*list(agent._bg_tasks), return_exceptions=True)


def _notice(task_id: str, status: str, summary: str = "") -> TaskChangedPayload:
    return TaskChangedPayload(
        task_id=task_id, status=status, summary=summary, initiator="minecraft", executor="maicraft"
    )


@pytest.mark.asyncio
async def test_failure_streak_tells_streamer_where_body_is_stuck() -> None:
    """连续失败第 3 次通报主播卡点；成功一次清零；之后每再失败 5 次补报一次。"""
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock(), event_bus=bus)
    agent._task_finished = False

    for index in range(2):
        agent.on_task_notification(_notice(f"t{index}", "failed", "backpack_open_failed"))
    agent.on_task_notification(_notice("ok", "succeeded"))
    assert agent._failure_streak == 0
    for index in range(8):
        agent.on_task_notification(_notice(f"f{index}", "failed", "opened menu does not match"))
    await _drain(agent)

    notices = [
        call.args[1].message
        for call in bus.emit.await_args_list
        if call.args[0] == CoreEvents.GAME_ATTENTION_REQUIRED
    ]
    assert len(notices) == 2
    assert "连续 3 个游戏内动作没有成功" in notices[0] and "opened menu does not match" in notices[0]
    assert "连续 8 个" in notices[1]
