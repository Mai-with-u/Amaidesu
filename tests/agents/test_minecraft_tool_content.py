"""按真实 MCP 双通道回执验证资料正文不会被结构化元数据遮掉。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.tool_content import failed_observation, successful_observation
from src.modules.mcp.mapper import to_result
from src.modules.tools.models import ResultBlock, ToolExecutionResult


@pytest.mark.asyncio
async def test_knowledge_text_body_survives_mcp_metadata_and_can_be_read_back() -> None:
    """Mod 正文在 content、地址在 structuredContent，两路都必须进入玩家的观察原件。"""
    uri = "maicraft://knowledge/example"
    body = '{"operation":"assemble","required_items":["example:input"],"rule":"原生接口约束"}'
    result = to_result(
        SimpleNamespace(
            is_error=False,
            structured_content={"resources": [{"uri": uri, "mimeType": "application/json"}]},
            content=[SimpleNamespace(type="text", text=body)],
        ),
        tool_name="maicraft_perceive",
    )
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=result)
    agent = MinecraftAgent(MinecraftConfig(), tool_registry=registry)
    request = {"view": "knowledge", "resource_uri": uri}
    observed = await agent._execute_tool("maicraft_perceive", request)
    assert observed["resources"][0]["content"]["required_items"] == ["example:input"]
    assert observed["content_loaded"] is True
    shown = agent._observations.present("maicraft_perceive", request, observed)
    read = agent._read_observation({"ref": shown["_observation"]["ref"], "path": "/resources/0/content/rule"})
    assert read["text"] == "原生接口约束"


def test_metadata_without_body_is_not_reported_as_loaded() -> None:
    """明确区分只找到资源地址和已经取得正文，模型不能把空壳资料当作已读。"""
    result = ToolExecutionResult(
        tool_name="read", success=True, structured_content={"resources": [{"uri": "maicraft://knowledge/example"}]}
    )
    observed = successful_observation(result, {"view": "knowledge", "resource_uri": "maicraft://knowledge/example"})
    assert observed["ok"] is False and observed["content_loaded"] is False
    assert observed["error"]["code"] == "resource_body_missing"


def test_text_metadata_pairing_and_duplicate_state_do_not_lose_information() -> None:
    """多文档按顺序保存，普通工具两路重复的状态只保留一份。"""
    result = ToolExecutionResult(
        tool_name="read",
        success=True,
        structured_content={"resources": [{"uri": "one"}, {"uri": "two"}]},
        blocks=[ResultBlock(text='{"count":3}'), ResultBlock(text="# 接口说明\n正文")],
    )
    observed = successful_observation(result, {"view": "knowledge", "resource_uri": "one"})
    assert [r["content"] for r in observed["resources"]] == [{"count": 3}, "# 接口说明\n正文"]
    ordinary = ToolExecutionResult(
        tool_name="state", success=True, content='{"health":20}', structured_content={"health": 20}
    )
    assert successful_observation(ordinary, {}) == {"health": 20}
    ordinary.content = "额外的完整性说明"
    assert successful_observation(ordinary, {})["text"] == ordinary.content


def test_sdk_business_error_keeps_uncertain_outcome_and_diagnostics() -> None:
    """恢复 SDK 异常携带的 JSON，结果未知不会被当成可直接重试的普通失败。"""
    result = ToolExecutionResult(
        tool_name="execute",
        success=False,
        error_message='MCP 业务错误: {"success":false,"error":{"code":"supply_failed","outcome_known":false,"path":"materials"}}',
    )
    observed = failed_observation(result, "maicraft_execute")
    assert observed["ok"] is False and observed["error"]["outcome_known"] is False
    assert observed["error"]["path"] == "materials"


def test_implicit_knowledge_read_preserves_partial_reference_state() -> None:
    """只给 URI 也要配对正文；归档索引不能被标成已读取完整教材。"""
    uri = "maicraft://knowledge/large"
    result = ToolExecutionResult(
        tool_name="read",
        success=True,
        structured_content={"resources": [{"uri": uri}]},
        content='{"source_uri":"maicraft://knowledge/large","response_partial":true,"text":{"omitted":true,"resource_uri":"maicraft://receipts/ref"}}',
    )
    observed = successful_observation(result, {"resource_uri": uri})
    assert observed["content_loaded"] is False and observed["content_partial"] is True
    assert observed["resources"][0]["content"]["text"]["resource_uri"] == "maicraft://receipts/ref"
