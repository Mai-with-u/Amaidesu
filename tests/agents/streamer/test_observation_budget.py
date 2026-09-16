"""工具观察预算单测——超预算按体量整段丢弃 + 自证标记。

覆盖：
- 未超预算：观察原样返回，不带任何自证字段
- 超预算：丢弃体量最大的段，稀缺段（电梯楼层）必须留下，且落回预算内
- 自证：被丢的键名全部写入 ``_omitted``，观察里不做"静默丢失"
- 非对象形态：没有段可丢，退回带标记的前缀截断
- 调用点：``_invoke_registry_tool`` 走同一渲染路径（截断口径只有一处实现）
"""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.planner import (
    _OBSERVATION_MAX_CHARS,
    _OBSERVATION_OMITTED_KEY,
    _OBSERVATION_TRUNCATED_KEY,
    Planner,
    _render_observation,
)
from src.agents.streamer.room_state import RoomState
from src.modules.tools.models import ToolExecutionResult

_MARKERS = {_OBSERVATION_TRUNCATED_KEY, _OBSERVATION_OMITTED_KEY}


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


def test_observation_within_budget_is_returned_untouched() -> None:
    data = {"ok": True, "floors": [{"id": "floor:10"}]}
    rendered = _render_observation(data)
    assert rendered == json.dumps(data, ensure_ascii=False)
    assert _OBSERVATION_TRUNCATED_KEY not in rendered


def test_oversized_observation_keeps_the_scarce_section() -> None:
    """电梯楼层排在两段大体量诊断之后，按插入顺序前缀切时它必被切掉。"""
    payload = _surroundings_shaped()
    assert len(json.dumps(payload, ensure_ascii=False)) > _OBSERVATION_MAX_CHARS

    rendered = _render_observation(payload)
    parsed = json.loads(rendered)

    assert len(rendered) <= _OBSERVATION_MAX_CHARS
    assert parsed["elevators"]["elevators"][0]["floor_list_state"] == "synchronized"
    assert parsed[_OBSERVATION_TRUNCATED_KEY] is True
    assert "terrain_overview" in parsed[_OBSERVATION_OMITTED_KEY]


def test_omitted_names_exactly_what_was_dropped() -> None:
    """观察里不做静默丢失：留下的段与点名的段合起来就是原始键全集。"""
    payload = _surroundings_shaped()
    parsed = json.loads(_render_observation(payload))

    kept = set(parsed) - _MARKERS
    omitted = set(parsed[_OBSERVATION_OMITTED_KEY])
    assert kept | omitted == set(payload)
    assert not kept & omitted
    assert len(parsed[_OBSERVATION_OMITTED_KEY]) == len(omitted)


def test_single_huge_section_is_named_not_silently_dropped() -> None:
    payload = {"ok": True, "tasks": ["t" * 9000]}
    parsed = json.loads(_render_observation(payload))
    assert parsed[_OBSERVATION_OMITTED_KEY] == ["tasks"]
    assert "tasks" not in parsed


def test_non_mapping_payload_falls_back_to_marked_prefix_cut() -> None:
    rendered = _render_observation(["x" * 5000])
    assert len(rendered) == _OBSERVATION_MAX_CHARS + len("…（截断）")
    assert rendered.endswith("…（截断）")


async def test_registry_observation_goes_through_the_renderer() -> None:
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="maicraft_perceive",
            success=True,
            structured_content=_surroundings_shaped(),
        )
    )
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("maicraft_perceive", {"view": "surroundings"})
    parsed = json.loads(observed)

    assert len(observed) <= _OBSERVATION_MAX_CHARS
    assert parsed[_OBSERVATION_TRUNCATED_KEY] is True
    assert "elevators" in parsed
