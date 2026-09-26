"""验证大型游戏资料可展开、错误证据保留，以及历史观察不会被改写为新事实。"""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.observations import MinecraftObservations, json_text
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry


def test_large_reference_keeps_decision_evidence_and_lossless_original() -> None:
    """长教材、失败位置和材料缺口完整返回，原始数据的后续修改不影响已有证据。"""
    history = MinecraftObservations()
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
    assert shown["data"]["content"] == original["data"]["content"]
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
    """单份工艺、目录和超大资料都完整呈现，尾部要求不会被折叠。"""
    history = MinecraftObservations()
    uri = "maicraft://knowledge/recipes/example/output"
    body = {"process": "工艺定义" * 3300, "loops": 5, "sequence": ["a", "b", "c"]}
    original = {"resources": [{"uri": uri, "content": body}], "content_loaded": True}
    request = {"view": "knowledge", "resource_uri": uri}
    shown = history.present("maicraft_perceive", request, original)
    assert shown["resources"][0]["content"] == body
    catalog = history.present("maicraft_perceive", {"view": "knowledge"}, original)
    assert catalog["resources"] == original["resources"]
    large = deepcopy(original)
    large["resources"][0]["content"]["process"] *= 3
    deferred = history.present("maicraft_perceive", request, large)
    assert deferred["resources"] == large["resources"]


def test_pointer_search_and_expired_references_are_explicit() -> None:
    """包含斜线的字段仍能寻址；过期引用只能报错，不能误读另一份原文。"""
    history = MinecraftObservations()
    shown = history.present("knowledge", {}, {"a/b": {"~key": "前文" * 200 + "目标材料" + "后文" * 200}})
    ref = shown["_observation"]["ref"]
    found = history.read({"ref": ref, "path": "/a~1b/~0key", "query": "目标材料", "limit": 40})
    assert "目标材料" in found["text"] and found["historical"] is True
    assert history.read({"ref": ref, "query": "不存在"})["found"] is False
    with pytest.raises(ValueError, match="不存在路径"):
        history.read({"ref": ref, "path": "/missing"})
    history.present("knowledge", {"page": "next"}, {"content": "新的正文" * 200})
    assert history.read({"ref": ref})["complete"] is True
    assert len(history.index()) == 2
    with pytest.raises(ValueError, match="已过期"):
        history.read({"ref": "来自其他任务"})
    assert "knowledge" in json_text(history.index())


def test_paged_read_and_index_preserve_long_results_and_early_requests() -> None:
    """默认小页不丢旧记录，首条长请求和末尾正文仍可按引用及偏移无损找回。"""
    history = MinecraftObservations()
    arguments = {"requirement": "保留现场结构" * 300}
    content = "工艺正文" * 6000 + "尾部验收要求"
    shown = history.present("knowledge", arguments, {"content": content})
    ref = shown["_observation"]["ref"]
    for index in range(25):
        history.present("knowledge", {"index": index}, {"content": str(index)})
    assert len(history.index()) == 20 and history.count == 26
    older = history.read({"offset": 20})
    assert older["total"] == 26 and older["next_offset"] is None
    assert older["observations"][-1]["ref"] == ref
    assert len(older["observations"][-1]["request_preview"]) <= 320
    full = history.read({"ref": ref, "path": "/content"})
    assert full["text"] == content[:4000] and full["complete"] is False
    found = history.read({"ref": ref, "path": "/content", "query": "尾部验收要求"})
    assert found["text"].endswith("尾部验收要求") and found["next_offset"] is None
    parts, offset = [], 0
    while True:
        selected = history.read({"ref": ref, "path": "/content", "offset": offset})
        parts.append(selected["text"])
        if selected["next_offset"] is None:
            break
        offset = selected["next_offset"]
    assert "".join(parts) == content
    assert history.read({"ref": ref, "source": "request", "limit": 10000})["text"] == json_text(arguments)


def test_large_inputs_do_not_expand_the_compaction_index() -> None:
    """两个大型蓝图的输入留在原件中，固定事实索引不会反过来超过整个上下文预算。"""
    history = MinecraftObservations()
    for index in range(2):
        request = {
            "goal": {
                "ability": "maicraft:build_machine",
                "outcome": f"建造 {index}",
                "parameters": {"blueprint": "x" * 71600},
            }
        }
        history.present("maicraft_plan", request, {"plan_id": str(index), "ready_to_execute": True})
    assert len(json_text(history.index())) < 2000
    assert len(history.read({"ref": history.index()[0]["ref"], "source": "request"})["text"]) <= 4000


def test_index_access_times_and_recency_order_are_not_progress() -> None:
    """重新查看同一现场只改变索引访问时间或排序时，不应解除模型的无进展提醒。"""
    history = MinecraftObservations()
    for item in ("first", "second"):
        history.present("maicraft_perceive", {"focus": item}, {"observed": item})
    assert history.read({})["same_request_and_result"] is False
    history.present("maicraft_perceive", {"focus": "first"}, {"observed": "first"})
    assert history.read({})["same_request_and_result"] is True


@pytest.mark.asyncio
async def test_react_tracks_original_before_presentation_and_reads_through_local_tool() -> None:
    """任务账本获得完整回执，模型完整读取回执后仍能通过注册工具查询原文。"""
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
    agent.receive_prompt(content="读取组件资料", source="test")
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


def test_attention_cursor_does_not_turn_same_failed_task_into_new_evidence() -> None:
    """旁边的身体事件推动订阅游标时，反复查询同一失败格仍应被识别为重复，原回执则逐份完整保存。"""
    history = MinecraftObservations()
    args = {"action": "get", "task_id": "machine-1", "path": "/terminal/result/data"}
    receipt = {
        "task_id": "machine-1",
        "state": "failed",
        "failure_code": "placement_no_progress",
        "next_attention": {"after_cursor": 1},
    }
    first = history.present("maicraft_task", args, receipt)
    changed_cursor = deepcopy(receipt)
    changed_cursor["next_attention"]["after_cursor"] = 2
    second = history.present("maicraft_task", args, changed_cursor)
    assert not first["_observation"]["same_request_and_result"]
    assert second["_observation"]["same_request_and_result"]
    assert first["_observation"]["ref"] != second["_observation"]["ref"]
    reordered = dict(reversed(list(changed_cursor.items())))
    assert history.present("maicraft_task", args, reordered)["_observation"]["same_request_and_result"]
    original = history.read({"ref": second["_observation"]["ref"], "path": "/next_attention"})
    assert '"after_cursor":2' in original["text"]
    # 真正的施工失败发生变化时，允许模型重新判断，不能因任务编号相同就丢弃新的诊断。
    changed_cursor["failure_code"] = "material_exhausted"
    assert not history.present("maicraft_task", args, changed_cursor)["_observation"]["same_request_and_result"]
