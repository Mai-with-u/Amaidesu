"""完整工具观察进入 Planner，超长字段和尾部证据都不丢失。"""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.planner import (
    Planner,
    _render_observation,
)
from src.agents.streamer.room_state import RoomState
from src.modules.tools.models import ToolExecutionResult


def _surroundings_shaped() -> Dict[str, Any]:
    """形状与真实 ``perceive(view="surroundings")`` 响应一致：电梯段排在大体量诊断之后。"""
    return {
        "position": {"x": -136.2809165787706, "y": 135.0, "z": 174.3212246501019},
        "dimension": "minecraft:overworld",
        "sky_light": 15,
        "nearby_entities": [],
        "nearby_signs": [],
        "local_decision_summary": {
            "standable_regions": [{"region": f"region_{i}", "evidence": "x" * 320} for i in range(5)]
        },
        "terrain_overview": {
            "regions": [
                {"direction": "west", "surface_material": "minecraft:stone", "evidence": "y" * 340} for _ in range(12)
            ]
        },
        "elevators": {
            "integration_available": True,
            "elevators": [
                {
                    "elevator_id": "3f1d0a5e-1c2b-4d3e-8f90-abcdef012345",
                    "distance": 4.5,
                    "floor_list_state": "synchronized",
                    "floors": [{"id": "floor:10", "short_name": "1", "served": True}],
                }
            ],
        },
        "physical_structures": [{"direction": "north", "evidence": "z" * 420} for _ in range(3)],
    }


def _make_planner(tool_registry: Any) -> Planner:
    return Planner(
        config={"planner_max_steps": 2},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=tool_registry,
        reply_provider=MagicMock(),
    )


def test_observation_keeps_every_field() -> None:
    """电梯楼层和大体积地形诊断完整共存，不能靠丢段减小观察。"""
    payload = _surroundings_shaped()
    assert len(json.dumps(payload, ensure_ascii=False)) > 6144
    assert json.loads(_render_observation(payload)) == payload


def test_single_large_field_and_non_mapping_results_remain_valid() -> None:
    """大字段、字符串和数组仍是完整 JSON，尾部任务条件可以被解析。"""
    for payload in ({"tasks": ["任务" * 10000 + "保留入口"]}, ["x" * 9000], "内容" * 9000):
        assert json.loads(_render_observation(payload)) == payload


async def test_registry_observation_preserves_long_content() -> None:
    """真实工具回调后的结构化结果与补充文本都完整交给 Planner。"""
    registry = MagicMock()
    payload = _surroundings_shaped()
    supplement = "补充事实" * 10000
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="maicraft_perceive",
            success=True,
            structured_content=payload,
            content=supplement,
        )
    )
    observed = await _make_planner(registry)._invoke_registry_tool("maicraft_perceive", {"view": "surroundings"})
    assert json.loads(observed) == {**payload, "ok": True, "content": supplement}


async def test_text_only_tool_result_reaches_the_observation() -> None:
    """信息获取型工具（content 文本产出、无 structured_content）的内容必须进观察。

    回归锚点：web_search 上线时 LLM 只看到 {"ok": true}，搜索结果文本在
    观察构造处被丢弃（工具执行侧与 tool.result 事件里内容完好）。
    """
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="web_search",
            success=True,
            content="[1] Minecraft 官网\n    https://www.minecraft.net",
        )
    )
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("web_search", {"query": "minecraft"})
    parsed = json.loads(observed)

    assert parsed["ok"] is True
    assert "Minecraft 官网" in parsed["content"]


async def test_structured_and_text_content_coexist_in_observation() -> None:
    """结构化结果与 content 文本并存时两者都可见（结构化键不被覆盖）。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="some_tool",
            success=True,
            content="补充文本",
            structured_content={"status": "done", "content": "结构化自带 content"},
        )
    )
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("some_tool", {})
    parsed = json.loads(observed)

    assert parsed["status"] == "done"
    assert parsed["content"] == "结构化自带 content"


async def test_action_tool_with_empty_content_keeps_ok_only_shape() -> None:
    """空 content 的动作型工具维持 {"ok": true} 形态（不引入噪音键）。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=ToolExecutionResult(tool_name="some_action", success=True, content=""))
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("some_action", {})
    assert json.loads(observed) == {"ok": True}
