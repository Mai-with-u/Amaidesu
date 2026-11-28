"""DrawingBuilder 知识 ID 结构化解析、design create 契约提示词与重试超时收敛测试。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest

from src.agents.minecraft.drawing import (
    DRAWING_SYSTEM_PROMPT,
    DrawingBuilder,
    extract_building_knowledge_ids,
)
from src.modules.llm.payload import Response
from tests.agents.minecraft_v1_fakes import ManualClock


class _SequencedLlm:
    """按剧本回复并记录每次 generate 入参的轻量模型替身。"""

    def __init__(self, responses: List[Any], *, gate: Optional[asyncio.Event] = None) -> None:
        self._responses = list(responses)
        self._gate = gate
        self.calls: List[Dict[str, Any]] = []

    async def generate(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Response:
        self.calls.append({"messages": messages, "kwargs": dict(kwargs)})
        if self._gate is not None:
            await self._gate.wait()
        item = self._responses.pop(0) if self._responses else Response(success=True, content="{}")
        if callable(item):
            item = item(len(self.calls))
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.parametrize(
    ("catalog", "expected"),
    [
        # MaiCraft 实机标准结构：data.knowledge 对象列表
        (
            {
                "ok": True,
                "data": {
                    "knowledge": [
                        {"id": "maicraft://knowledge/game_mechanics/lighting", "title": "光照"},
                        {"id": "maicraft://knowledge/building/styles", "title": "风格"},
                        {"id": "maicraft://knowledge/building/roofs", "title": "屋顶"},
                        {"id": "maicraft://knowledge/building/design", "title": "图纸格式"},
                        {"id": "maicraft://knowledge/building/example-cottage", "title": "木构小屋"},
                        {"id": "maicraft://knowledge/building/house", "title": "住宅设计"},
                    ],
                    "sources": ["随包常识"],
                },
            },
            [
                "maicraft://knowledge/building/design",
                "maicraft://knowledge/building/roofs",
                "maicraft://knowledge/building/example-cottage",
                "maicraft://knowledge/building/house",
                "maicraft://knowledge/building/styles",
            ],
        ),
        # 简化列表与去重、过滤畸形噪声串
        (
            {
                "ok": True,
                "data": {
                    "docs": [
                        "building/house",
                        "building/design",
                        "building/design",
                        "{ id : maicraft://knowledge/building/broken",
                        "building/with space",
                        "other/topic",
                    ]
                },
            },
            ["building/design", "building/house"],
        ),
        # 空目录或错误回执安全降级为空列表
        ({"ok": True, "data": {"knowledge": []}}, []),
        ({"ok": False, "error": {"code": "not_in_world"}}, []),
        (None, []),
    ],
    ids=["nested-maicraft-catalog", "list-dedupe-and-noise-filter", "empty-catalog", "error-reply", "none-catalog"],
)
def test_extract_building_knowledge_ids_parses_clean_ids_only(catalog: Any, expected: List[str]) -> None:
    """结构化提取干净 building/ 知识 ID：核心文档优先排序、去重、零畸形 JSON 残片。"""
    ids = extract_building_knowledge_ids(catalog)
    assert ids == expected
    assert all("{" not in item and '"' not in item and " " not in item for item in ids)


def test_drawing_system_prompt_matches_current_design_create_schema() -> None:
    """系统提示词明示现行必填 primitive、对象形态 materials 与 ROOF 的 footprint 字段。"""
    for required_token in (
        "minecraft_y_up",
        "block_id",
        "primitive",
        "MESH",
        "ROOF",
        "footprint",
        "BOOLEAN",
        "DIFFERENCE",
        "array",
        "minecraft:air",
    ):
        assert required_token in DRAWING_SYSTEM_PROMPT


async def test_drawing_builder_loads_clean_knowledge_ids_and_passes_doc_text_to_llm() -> None:
    """真实嵌套目录结构下，_load_knowledge 仅用干净 ID 调 maicraft_lookup 并把正文喂给模型。"""
    lookup_ids: List[str] = []
    notices: List[str] = []

    async def maicraft_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "maicraft_lookup":
            doc_id = arguments.get("id")
            if not doc_id:
                return {
                    "ok": True,
                    "data": {
                        "knowledge": [
                            {"id": "maicraft://knowledge/building/design", "title": "图纸格式", "summary": "字段说明"},
                            {"id": "maicraft://knowledge/building/roofs", "title": "屋顶", "summary": "ROOF 参数"},
                            {"id": "maicraft://knowledge/game_mechanics/food", "title": "食物", "summary": "饥饿机制"},
                        ]
                    },
                }
            lookup_ids.append(str(doc_id))
            return {
                "ok": True,
                "data": {"id": doc_id, "title": "格式正文", "text": f"正文内容({doc_id})：primitive 与 block_id"},
            }
        return {"ok": True, "data": {"design_id": "d-42", "name": "木屋"}}

    valid_json = json.dumps(
        {
            "name": "木屋",
            "coordinate_system": "minecraft_y_up",
            "materials": {"Wall": {"block_id": "minecraft:oak_planks"}},
            "objects": [
                {
                    "name": "Box",
                    "type": "MESH",
                    "primitive": "cube",
                    "location": [1.5, 1.5, 1.5],
                    "dimensions": [3, 3, 3],
                    "material": "Wall",
                }
            ],
        },
        ensure_ascii=False,
    )
    done = asyncio.Event()

    def _on_notify(msg: str) -> None:
        notices.append(msg)
        done.set()

    llm = _SequencedLlm([Response(success=True, content=valid_json)])
    builder = DrawingBuilder(
        llm=llm,
        maicraft_call=maicraft_call,
        notify=_on_notify,
        release_epoch_reader=lambda: 0,
        max_validation_retries=2,
    )

    receipt = builder.start({"purpose": "盖一间小木屋"})
    assert receipt["ok"] is True
    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert len(notices) == 1
    assert "design_id=d-42" in notices[0] and "名称=木屋" in notices[0]
    assert lookup_ids == [
        "maicraft://knowledge/building/design",
        "maicraft://knowledge/building/roofs",
    ]
    assert all("{" not in doc_id and ":" in doc_id for doc_id in lookup_ids)
    user_prompt = str(llm.calls[0]["messages"][1]["content"])
    assert "正文内容(maicraft://knowledge/building/design)" in user_prompt
    assert "reasoning_effort" not in llm.calls[0]["kwargs"]


async def test_drawing_builder_degrades_safely_on_lookup_error_and_lowers_effort_on_retry() -> None:
    """目录读取异常时安全降级不崩溃；校验失败进入重试轮时自动压低思考预算并在总预算耗尽时提前收口。"""
    clock = ManualClock(start=100.0)
    create_calls = [0]
    notices: List[str] = []
    done = asyncio.Event()

    def _on_notify(msg: str) -> None:
        notices.append(msg)
        done.set()

    async def maicraft_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        del arguments
        if tool_name == "maicraft_lookup":
            raise RuntimeError("lookup 暂时不可用")
        create_calls[0] += 1
        return {"ok": False, "error": {"code": "invalid_parameter", "message": "objects[0] 缺少 primitive"}}

    def _reply(call_idx: int) -> Response:
        # 模拟首轮耗时 100 秒、重试轮耗时 90 秒 -> 第 3 轮前触及 180 秒总预算提前收口
        clock.advance(100.0 if call_idx == 1 else 90.0)
        return Response(success=True, content='{"name":"草图","materials":{},"objects":[]}')

    llm = _SequencedLlm([_reply, _reply, _reply])
    builder = DrawingBuilder(
        llm=llm,
        maicraft_call=maicraft_call,
        notify=_on_notify,
        release_epoch_reader=lambda: 0,
        max_validation_retries=3,
        attempt_timeout_ms=75_000,
        total_budget_ms=180_000,
        clock=clock,
    )

    receipt = builder.start({"purpose": "盖塔楼"})
    assert receipt["ok"] is True
    await asyncio.wait_for(done.wait(), timeout=2.0)

    assert len(notices) == 1
    assert "图纸生成失败" in notices[0]
    assert "预算上限" in notices[0]
    assert len(llm.calls) == 2
    assert create_calls[0] == 2
    assert "reasoning_effort" not in llm.calls[0]["kwargs"]
    assert llm.calls[1]["kwargs"].get("reasoning_effort") == "low"


async def test_drawing_builder_single_attempt_timeout_notifies_failure_without_unbounded_block() -> None:
    """单次生成挂起超限时迅速收口为失败通知，绝不无界阻塞双模型拉满 240 秒。"""
    notices: List[str] = []
    done = asyncio.Event()

    def _on_notify(msg: str) -> None:
        notices.append(msg)
        done.set()

    gate = asyncio.Event()
    llm = _SequencedLlm([], gate=gate)

    async def maicraft_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        del tool_name, arguments
        return {"ok": True, "data": {"knowledge": []}}

    builder = DrawingBuilder(
        llm=llm,
        maicraft_call=maicraft_call,
        notify=_on_notify,
        release_epoch_reader=lambda: 0,
        max_validation_retries=2,
        attempt_timeout_ms=20,
        total_budget_ms=1_000,
    )
    receipt = builder.start({"purpose": "建个小屋"})
    assert receipt["ok"] is True
    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert len(notices) == 1
    assert "图纸生成失败" in notices[0]
    assert "超时" in notices[0]
    assert len(llm.calls) == 1
