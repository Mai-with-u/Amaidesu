"""验证大型游戏资料可展开、错误证据保留，以及历史观察不会被改写为新事实。"""

from copy import deepcopy

import pytest

from src.agents.minecraft.observations import MinecraftObservations, json_text


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
