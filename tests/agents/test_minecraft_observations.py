"""验证大型游戏资料可展开、错误证据保留，以及历史观察不会被改写为新事实。"""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.observations import MinecraftObservations, json_text
from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry


def test_large_reference_keeps_decision_evidence_and_lossless_original() -> None:
    """长教材只缩小呈现，失败位置、材料缺口和部分覆盖仍直接可见。"""
    history = MinecraftObservations(inline_chars=800)
    original = {
        "ok": False,
        "complete": False,
        "resource_revision": "r1",
        "data": {"content": "# 原生接口\n" + "教材正文\n" * 3000},
        "error": {"outcome_known": False, "message": "材料获取结果未确认", "detail": "诊断" * 900},
        "missing_materials": [{"item_id": "minecraft:stone", "count": 42}],
    }
    before = deepcopy(original)
    shown = history.present("maicraft_perceive", {"resource_uri": "maicraft://knowledge/test"}, original)
    assert shown["ok"] is False and shown["complete"] is False
    assert shown["error"] == original["error"] and shown["missing_materials"] == original["missing_materials"]
    assert shown["data"]["content"]["deferred"] is True
    ref = shown["_observation"]["ref"]
    parts, offset = [], 0
    while True:
        page = history.read({"ref": ref, "path": "/data/content", "offset": offset, "limit": 127})
        parts.append(page["text"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(parts) == original["data"]["content"] and original == before
    original["error"]["message"] = "外部修改"
    assert "材料获取结果未确认" in history.read({"ref": ref, "path": "/error"})["text"]


def test_repeat_marker_requires_identical_request_and_actual_result() -> None:
    """世界库存变了就产生新证据，即使参数相同也不会拿上次库存顶替。"""
    history = MinecraftObservations()
    args = {"view": "situation", "sections": ["inventory"]}
    first = history.present("maicraft_perceive", args, {"inventory": {"stone": 3}})
    stable = deepcopy(first)
    repeated = history.present("maicraft_perceive", args, {"inventory": {"stone": 3}})
    changed = history.present("maicraft_perceive", args, {"inventory": {"stone": 1}})
    assert repeated["_observation"]["same_request_and_result"] is True
    assert changed["inventory"]["stone"] == 1 and not changed["_observation"]["same_request_and_result"]
    assert first == stable and history.repeated_results == 1
    assert len(history.index("inventory")) == 2


def test_selected_process_document_is_read_as_one_unit() -> None:
    """单份一万三千字资料已被明确选中时完整呈现，目录查询仍遵守普通呈现预算。"""
    history = MinecraftObservations()
    uri = "maicraft://knowledge/recipes/example/output"
    body = {"process": "工艺定义" * 3300, "loops": 5, "sequence": ["a", "b", "c"]}
    original = {"resources": [{"uri": uri, "content": body}], "content_loaded": True}
    request = {"view": "knowledge", "resource_uri": uri}
    shown = history.present("maicraft_perceive", request, original)
    assert shown["resources"][0]["content"] == body
    catalog = history.present("maicraft_perceive", {"view": "knowledge"}, original)
    assert catalog["resources"]["deferred"] is True
    large = deepcopy(original)
    large["resources"][0]["content"]["process"] *= 3
    deferred = history.present("maicraft_perceive", request, large)
    assert deferred["resources"]["deferred"] is True


def test_pointer_search_and_expired_references_are_explicit() -> None:
    """包含斜线的字段仍能寻址；过期引用只能报错，不能误读另一份原文。"""
    history = MinecraftObservations(archive_chars=500)
    shown = history.present("knowledge", {}, {"a/b": {"~key": "前文" * 200 + "目标材料" + "后文" * 200}})
    ref = shown["_observation"]["ref"]
    found = history.read({"ref": ref, "path": "/a~1b/~0key", "query": "目标材料", "limit": 40})
    assert "目标材料" in found["text"] and found["historical"] is True
    assert history.read({"ref": ref, "query": "不存在"})["found"] is False
    with pytest.raises(ValueError, match="不存在路径"):
        history.read({"ref": ref, "path": "/missing"})
    history.present("knowledge", {"page": "next"}, {"content": "新的正文" * 200})
    with pytest.raises(ValueError, match="已过期"):
        history.read({"ref": ref})
    assert len(json_text(history.index())) < 1000


@pytest.mark.asyncio
async def test_react_tracks_original_before_presentation_and_reads_through_local_tool() -> None:
    """任务账本获得完整回执，模型看到缩小结果后能通过注册工具补读原文。"""
    raw = {"complete": False, "content": "已审阅的组件规则" * 5000}
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            Response(
                success=True,
                tool_calls=[ToolCall(id="read", name="maicraft_perceive", arguments={"view": "knowledge"})],
            ),
            Response(success=True, content="资料已读取"),
        ]
    )
    bus = MagicMock()
    bus.emit = AsyncMock()
    registry = ToolRegistry()
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=llm, tool_registry=registry, event_bus=bus)
    agent._execute_tool = AsyncMock(return_value=raw)
    agent._track_receipt = MagicMock()
    agent._running = True
    agent._register_tools()
    await agent.send_prompt("读取组件资料")
    await agent._run_task_batch()
    agent._track_receipt.assert_called_once_with("maicraft_perceive", raw)
    ref = agent._observations.index()[0]["ref"]
    result = await registry.invoke(
        ToolInvocation(tool_name="minecraft_observation", arguments={"ref": ref, "path": "/content", "limit": 100})
    )
    assert result.success and result.structured_content["text"] == raw["content"][:100]
    assert result.structured_content["next_offset"] == 100
    assert "minecraft_observation" not in {s.full_name for s in registry.list_tools(for_agent="streamer")}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_arguments", [{"ref": "missing"}, {"path": "/missing"}])
async def test_invalid_observation_reads_remain_correctable(bad_arguments: dict) -> None:
    """错误引用或路径只拒绝该次读取，修正后仍能取得已归档的真实游戏资料。"""
    registry = ToolRegistry(failure_threshold=3)
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock(), tool_registry=registry, event_bus=MagicMock())
    agent._register_tools()
    shown = agent._observations.present("maicraft_perceive", {}, {"position": {"x": 3}})
    ref = shown["_observation"]["ref"]
    for _ in range(4):
        result = await registry.invoke(
            ToolInvocation(tool_name="minecraft_observation", arguments={"ref": ref, **bad_arguments})
        )
        assert not result.success
        assert not registry.is_tripped("minecraft_observation")
    result = await registry.invoke(ToolInvocation(tool_name="minecraft_observation", arguments={"ref": ref}))
    assert result.success and '"x":3' in result.structured_content["text"]
