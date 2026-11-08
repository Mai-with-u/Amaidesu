"""读工作文档时附的当前状态快照：现查一次身体现状，笔记里的旧数字只当历史参考。

覆盖三层：快照文本本身的拼装（纯函数）、工具 Provider 的拼接（注入读取器）、
Agent 真的现查一次游戏（接 MaiCraft 替身）。
"""

from __future__ import annotations

from typing import Any, Dict

from src.agents.minecraft.maicraft import MaicraftReply
from src.agents.minecraft.state import MinecraftAgentState
from src.agents.minecraft.tools import MinecraftToolProvider
from src.agents.minecraft.work_log_snapshot import build_snapshot, unavailable_snapshot
from src.modules.tools.models import ToolInvocation

from .minecraft_v1_fakes import FakeMaicraft, make_minecraft_agent


def _ok(data: Dict[str, Any]) -> MaicraftReply:
    return MaicraftReply(ok=True, data=data)


def _invocation(name: str, arguments: dict) -> ToolInvocation:
    return ToolInvocation(tool_name=name, arguments=arguments, source="test")


def test_snapshot_lists_position_equipment_inventory_and_health() -> None:
    """现查到的事实逐项写出来：位置、状态（血量与饱食）、装备、背包。"""
    text = build_snapshot(
        _ok(
            {
                "position": {"x": 120.4, "y": 64.0, "z": -33.2, "dimension": "minecraft:overworld"},
                "health": 18,
                "food": 15,
                "held": "minecraft:iron_pickaxe",
                "armor": ["minecraft:iron_helmet"],
                "inventory": [{"item": "minecraft:cabbage", "count": 1}],
            }
        )
    )
    assert "位置：主世界 (120.4, 64, -33.2)" in text
    assert "状态：血量 18、饱食 15" in text
    assert "装备：铁头盔、手上拿着铁镐" in text
    assert "背包：cabbage ×1" in text
    assert "以这里的数字为准" in text


def test_snapshot_says_unavailable_when_the_read_fails() -> None:
    """现查失败就写明当前不可用，并交代笔记里的数字只能当历史参考。"""
    text = build_snapshot(MaicraftReply(ok=False, error_code="not_connected", error_message="游戏连接还没就绪"))
    assert "当前不可用" in text and "游戏连接还没就绪" in text
    assert "历史参考" in text


def test_snapshot_says_unavailable_without_any_readable_body_data() -> None:
    """读到了但身体数据一个都没报：照实说，不编数字。"""
    text = build_snapshot(_ok({}))
    assert "这次没读到可用的身体数据" in text


def test_unavailable_snapshot_names_the_reason() -> None:
    assert "当前不可用" in unavailable_snapshot("游戏连接还没就绪")


async def test_work_log_appends_the_snapshot_after_the_note() -> None:
    """工具返回的工作文档：笔记正文后面接着一段现查的快照。"""
    state = MinecraftAgentState()
    state.set_notebook("矿脉在 Y=12")

    async def snapshot() -> str:
        return "【当前状态快照】读工作文档这一刻现查的，以这里的数字为准：\n背包：cabbage ×1"

    provider = MinecraftToolProvider(state=state, snapshot_reader=snapshot)
    result = await provider.invoke(_invocation("minecraft_get_work_log", {}))
    notebook = result.structured_content["notebook"]
    assert notebook.startswith("矿脉在 Y=12")
    assert "【当前状态快照】" in notebook and "cabbage ×1" in notebook


async def test_work_log_without_a_reader_still_returns_the_note() -> None:
    """没接上游戏（没有读取器）：照旧给笔记，并写明快照当前不可用，不中断读取。"""
    state = MinecraftAgentState()
    state.set_notebook("矿脉在 Y=12")
    provider = MinecraftToolProvider(state=state)
    result = await provider.invoke(_invocation("minecraft_get_work_log", {}))
    notebook = result.structured_content["notebook"]
    assert "矿脉在 Y=12" in notebook
    assert "当前不可用" in notebook


async def test_work_log_snapshot_failure_does_not_break_the_read() -> None:
    """现查抛错：降级成"当前不可用"，工作文档照常读出来。"""
    state = MinecraftAgentState()
    state.set_notebook("矿脉在 Y=12")

    async def snapshot() -> str:
        raise RuntimeError("读取超时")

    provider = MinecraftToolProvider(state=state, snapshot_reader=snapshot)
    result = await provider.invoke(_invocation("minecraft_get_work_log", {}))
    notebook = result.structured_content["notebook"]
    assert "矿脉在 Y=12" in notebook
    assert "当前不可用" in notebook and "读取超时" in notebook


async def test_agent_reads_the_game_once_to_build_the_snapshot() -> None:
    """Agent 侧：读工作文档时现查一次 observe(self)，快照里的数字来自这一次现查。"""
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server=server)
    try:
        agent._mc_state.set_notebook("矿脉在 Y=12")
        before = len(server.calls_to("observe"))
        result = await agent._tool_provider.invoke(_invocation("minecraft_get_work_log", {}))
        observes = server.calls_to("observe")
        assert len(observes) == before + 1
        assert observes[-1] == {"what": "self"}
        notebook = result.structured_content["notebook"]
        assert "矿脉在 Y=12" in notebook
        assert "【当前状态快照】" in notebook
        assert "oak_log ×6" in notebook  # 替身背包里的橡木原木
    finally:
        await agent.stop()
