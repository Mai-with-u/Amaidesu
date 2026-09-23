"""核对大型施工回执可追溯、教材可复用，动态库存不能被缓存掩盖。"""

import json

import pytest

from src.agents.minecraft.observations import MinecraftObservations, json_text


def test_large_receipt_retains_lossless_paged_details() -> None:
    """大勘测先给短摘要，指定决策字段仍可完整读取，原对象变动不污染旧记录。"""
    store = MinecraftObservations()
    value = {
        "state": "waiting_for_decision",
        "decision": {"decision_id": "choice-1", "options": ["retry", "cancel"]},
        "blocks": [{"id": "minecraft:stone", "index": index} for index in range(20000)],
    }
    shown = store.present("maicraft_task", {"action": "get"}, value)
    assert len(json_text(shown)) < 6000
    assert shown["summary"]["decision"]["decision_id"] == "choice-1"
    result_id = shown["observation_id"]
    value["decision"]["decision_id"] = "changed"
    assert json.loads(store.read(result_id, "/decision")["content"])["decision_id"] == "choice-1"
    original = json_text(value["blocks"])
    offset = 0
    chunks = []
    while offset is not None:
        page = store.read(result_id, "/blocks", offset, 8000)
        chunks.append(page["content"])
        offset = page["next_offset"]
    assert "".join(chunks) == original


def test_only_successful_static_queries_are_reused() -> None:
    """同一本教材重复读取复用引用；库存、任务结果和失败读取必须重新访问真实来源。"""
    store = MinecraftObservations()
    args = {"view": "knowledge", "resource_uri": "maicraft://knowledge/processes"}
    store.present("maicraft_perceive", args, {"content": "教材"})
    assert store.cached("maicraft_perceive", dict(reversed(list(args.items()))))["reused_observation"]
    for view in ["situation", "attention", "tasks", "machines"]:
        store.present("maicraft_perceive", {"view": view}, {"state": "running"})
        assert store.cached("maicraft_perceive", {"view": view}) is None
    store.invalidate_static()
    assert store.cached("maicraft_perceive", args) is None
    store.present("maicraft_perceive", args, {"ok": False, "error": "离线"})
    assert store.cached("maicraft_perceive", args) is None


def test_observation_paths_and_task_lifetime() -> None:
    """特殊键遵循 JSON Pointer；结束任务后旧编号不能意外读到新任务内容。"""
    store = MinecraftObservations()
    store.present("test", {}, {"a/b": {"~key": "材料"}})
    assert json.loads(store.read("observation-1", "/a~1b/~0key")["content"]) == "材料"
    with pytest.raises(ValueError):
        store.read("observation-1", limit=0)
    store.clear()
    store.present("test", {}, {"new": True})
    with pytest.raises(ValueError):
        store.read("observation-1")
