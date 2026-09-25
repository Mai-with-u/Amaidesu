"""验证程序取回省略证据时保留页序、任务身份与游标，不让引用被当成完整结果。"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.attention_collector import MaicraftAttentionCollector
from src.agents.minecraft.builder.backend import validate_schema
from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.agents.minecraft.builder.models import BuildCatalog
from src.agents.minecraft.builder.scene_protocol import MinecraftSceneProtocol
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.readback import read_value
from src.agents.minecraft.task_facts import decision_facts
from src.modules.tools.tasks import TaskLedger


def reference(path: str) -> dict[str, Any]:
    """引用明确指向省略位置，不夹带可冒充实际结果的业务值。"""
    return {"omitted": True, "type": "object", "detail_path": path}


async def test_nested_pages_restore_false_values_and_unicode() -> None:
    """跨页保留 false 与表情，文本偏移使用 Mod 的 UTF-16 单位。"""

    async def page(path: str, offset: int) -> dict[str, Any]:
        if path == "/result":
            return {
                "path": path,
                "type": "object",
                "offset": offset,
                "total": 2,
                "items": [
                    {"key": "allowed", "value": False},
                    {"key": "text", "value": reference(path + "/text")},
                ],
            }
        if offset == 0:
            return {"path": path, "type": "string", "offset": 0, "total": 3, "value": "🌲", "next_offset": 2}
        return {"path": path, "type": "string", "offset": 2, "total": 3, "value": "木"}

    assert await read_value(page, "/result") == {"allowed": False, "text": "🌲木"}
    with pytest.raises(ValueError, match="预算"):
        await read_value(page, "/result", max_calls=1)


@pytest.mark.parametrize(
    "bad",
    [
        {"path": "/other", "offset": 0},
        {"path": "", "offset": 0, "type": "array", "total": 1, "items": []},
        {"path": "", "offset": 0, "type": "array", "total": 1, "items": [{"index": 1, "value": 2}]},
        {"path": "", "offset": 0, "type": "string", "total": 5, "value": "a", "next_offset": 0},
    ],
)
async def test_incomplete_pages_are_rejected(bad: dict[str, Any]) -> None:
    """缺页、重复偏移和错位索引必须失败，不能交付看起来完整的空证据。"""
    with pytest.raises(ValueError):
        await read_value(AsyncMock(return_value=bad), "")


async def test_attention_reference_is_read_before_cursor_commit() -> None:
    """冻结事件读取失败时保留旧游标，随后读取成功才交付伤害事实。"""
    bus = MagicMock()
    bus.emit = AsyncMock()
    collector = MaicraftAttentionCollector(event_bus=bus)
    collector._primed = True
    collector._stream_id = "stream"
    collector._cursor = 5
    events_ref = {**reference("/events"), "resource_uri": "maicraft://receipts/fixture?path=%2Fevents&offset=0&limit=5"}
    collector._provider = MagicMock()
    collector._provider.read_attention = AsyncMock(
        return_value={"stream_id": "stream", "cursor": 6, "events": events_ref}
    )
    collector._client = MagicMock()
    collector._client.read_resource = AsyncMock(side_effect=ValueError("receipt_expired"))
    with pytest.raises(ValueError, match="receipt_expired"):
        await collector._drain()
    assert collector._cursor == 5 and bus.emit.await_count == 0
    event = {
        "cursor": 6,
        "type": "agent.damaged",
        "priority": "important",
        "data": {"cause": {"causing_entity_type_id": "minecraft:zombie"}},
    }

    async def read(uri: str) -> list[dict[str, str]]:
        query = parse_qs(urlsplit(uri).query)
        assert query["path"] == ["/events"] and query["offset"] == ["0"]
        return [
            {
                "text": json.dumps(
                    {
                        "path": "/events",
                        "offset": 0,
                        "type": "array",
                        "total": 1,
                        "items": [{"index": 0, "value": event}],
                        "snapshot_only": True,
                        "details_uri": "maicraft://receipts/fixture",
                    }
                )
            }
        ]

    collector._client.read_resource = AsyncMock(side_effect=read)
    await collector._drain()
    assert collector._cursor == 6 and bus.emit.await_count == 1
    assert bus.emit.await_args.args[1].attacker == "minecraft:zombie"


async def test_scene_evidence_readback_never_resubmits_execution() -> None:
    """设计摘要被折叠时读取原任务的版本与场景号，完整确认前不交付可施工产物。"""
    catalog = BuildCatalog(
        protocol_version=1,
        revision="cap",
        design_schema_uri="maicraft://building/schema",
        design_schema_revision="schema",
        capabilities=[],
        resources=[],
    )
    config = MinecraftBuilderConfig(execute_tool="execute", task_tool="task")
    calls: list[tuple[str, dict[str, Any]]] = []
    data = {
        "construction_started": False,
        "capability_revision": "cap",
        "design_schema_revision": "schema",
        "scene_id": "scene",
    }

    async def invoke(name: str, args: dict[str, Any], *, source: str) -> dict[str, Any]:
        calls.append((name, args))
        if name == "execute":
            return {"accepted": True, "task_id": "task"}
        result: dict[str, Any] = {"task_id": "task", "state": "success"}
        path = args.get("path")
        if not path:
            result["terminal"] = {"result": {"success": True, "data": reference("/terminal/result/data")}}
        else:
            values = (
                {"success": True, "data": reference("/terminal/result/data")} if path == "/terminal/result" else data
            )
            result["detail"] = {
                "path": path,
                "type": "object",
                "offset": 0,
                "total": len(values),
                "items": [{"key": key, "value": value} for key, value in values.items()],
            }
        return result

    result = await MinecraftSceneProtocol(config, invoke).design_operation(
        "create_scene", {"scene": {}}, catalog, {}, request_key="stable"
    )
    assert result["valid"] is True and result["artifact_ref"] == "scene"
    assert [name for name, _ in calls].count("execute") == 1
    assert all(args["action"] == "get" for name, args in calls if name == "task")


def test_schema_manifest_cannot_become_an_unconstrained_schema() -> None:
    """归档清单是普通 JSON，必须在 JSON Schema 验证器把未知关键字忽略之前拒绝。"""
    with pytest.raises(ValueError, match="完整原文"):
        validate_schema(
            {"source_uri": "maicraft://building/schema", "response_partial": True, "text": reference("/text")},
            {"invalid_design": True},
        )


def test_partial_failure_keeps_known_flags_and_remote_read_route() -> None:
    """上下文整理保留真实不确定性，并明确区分本地概要路径和 Mod 原件路径。"""
    path = "/decision/context/failure/data"
    snapshot = {
        "task_id": "task",
        "decision": {
            "decision_id": "decision",
            "options": [{"choice": "cancel"}],
            "context": {
                "failure": {
                    "data": {
                        **reference(path),
                        "summary": {"outcome_uncertain": True, "mechanical_retry_allowed": False},
                    }
                }
            },
        },
    }
    facts = decision_facts(snapshot)
    evidence = facts["failure_evidence"]
    assert any(row.get("outcome_uncertain") is True and row["mechanical_retry_allowed"] is False for row in evidence)
    deferred = next(row for row in evidence if row.get("omitted"))
    assert deferred["read_arguments"] == {
        "tool": "maicraft_task",
        "arguments": {"action": "get", "task_id": "task", "path": path},
    }


async def test_short_task_events_require_current_evidence_and_deliver_new_decisions() -> None:
    """简短完成通知不能跳过交付门禁；同状态的新问题也要携带核实后的完整选项唤醒。"""
    tracker = MagicMock()
    tracker.ledger = TaskLedger()
    tracker.ledger.register(
        task_id="work",
        provider="maicraft",
        tool="maicraft_execute",
        initiator="minecraft",
        executor="maicraft",
        source="provider",
    )
    agent = MinecraftAgent(MinecraftConfig(), task_tracker=tracker)
    agent._task_finished = False
    agent._running = True
    agent._attention_primed = True
    provider = MagicMock()
    agent._attention_provider = provider
    event = {"task_id": "work", "type": "completed", "data": {"success": True}}
    provider.read_attention = AsyncMock(
        return_value={"schema_version": 3, "stream_id": "stream", "cursor": 7, "events": [event, event]}
    )
    provider.query_task = AsyncMock(side_effect=ValueError("temporarily unavailable"))
    await agent._drain_attention()
    assert tracker.ledger.get("work") is not None and not agent._message_queue
    provider.query_task.assert_awaited_once_with("work")
    tracker.notify.assert_called_once_with("work")
    for index in range(2):
        snapshot = {
            "task_id": "work",
            "state": "waiting_for_decision",
            "decision": {
                "decision_id": f"decision-{index}",
                "question": "选择当前处理方式",
                "options": [{"choice": "cancel"}],
                "context": {"ordinary_retry_allowed": False},
            },
        }
        provider.query_task = AsyncMock(return_value={"status": "waiting_for_decision", "snapshot": snapshot})
        await agent._drain_attention()
        assert agent._task_progress["work"]["decision"]["decision_id"] == f"decision-{index}"
    assert len(agent._message_queue) == 2 and tracker.ledger.get("work").status == "waiting_for_decision"
