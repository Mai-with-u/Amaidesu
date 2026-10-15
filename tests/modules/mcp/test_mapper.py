"""MCP 映射纯函数：Tool → ToolSpec（声明名存 server 原始名，全名派生）、CallToolResult → ToolExecutionResult"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.modules.mcp.mapper import to_result, to_spec
from src.modules.tools.models import ResultBlock


def _block(type_: str, **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(type=type_, **kwargs)


def _result(content: Any = None, is_error: bool = False, structured_content: Any = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, is_error=is_error, structured_content=structured_content)


def test_to_spec_maps_schema_provider_and_keeps_prefixed_raw_names() -> None:
    schema = {"type": "object", "properties": {"view": {"type": "string"}}}
    spec = to_spec(SimpleNamespace(name="perceive", description="观察当前局面", inputSchema=schema))
    assert (spec.name, spec.description, spec.parameters_schema, spec.kind) == (
        "perceive",
        "观察当前局面",
        schema,
        "sync",
    )
    assert (spec.provider, spec.full_name) == ("mcp", "mcp_perceive")

    assert (
        to_spec(SimpleNamespace(name="perceive", description="", inputSchema=None), provider="game").full_name
        == "game_perceive"
    )
    bare = to_spec(SimpleNamespace(name="bare", description="no schema", inputSchema=None))
    assert bare.parameters_schema is None

    prefixed = to_spec(SimpleNamespace(name="serverA_perceive", description="", inputSchema=None), provider="serverA")
    assert (
        prefixed.name == "serverA_perceive" and prefixed.full_name == "serverA_serverA_perceive"
    )  # 不解析原名，调用 server 时直呼 spec.name


def test_text_only_json_receipts_become_structured_but_only_when_unambiguous() -> None:
    text = '{"task_id":"task-1","state":"success","terminal":{"result":{"success":true}}}'
    ok = to_result(_result([_block("text", text=text)]), tool_name="maicraft_task")
    assert ok.structured_content["state"] == "success" and ok.content == text and ok.blocks[0].text == text

    refused_text = '{"success":false,"error":{"outcome_known":false,"code":"unconfirmed"}}'
    refused = to_result(_result([_block("text", text=refused_text)], is_error=True), tool_name="tool")
    assert not refused.success and refused.structured_content["error"]["outcome_known"] is False  # 确定性属于回执事实

    metadata = {"resources": [{"uri": "example://document"}]}
    kept = to_result(_result([_block("text", text='{"body":1}')], structured_content=metadata), tool_name="tool")
    assert kept.structured_content == metadata  # 已有元数据优先
    for blocks in (
        [_block("text", text='{"unfinished":')],
        [_block("text", text='{"a":1}'), _block("text", text='{"b":2}')],
        [_block("text", text='{"a":1}'), _block("image", data="x", mimeType="image/png")],
    ):
        observed = to_result(_result(blocks), tool_name="tool")
        assert observed.structured_content is None and len(observed.blocks) == len(blocks)  # 截断/多份/图文混合不猜


def test_result_text_image_dict_error_and_none_shapes() -> None:
    text = to_result(_result([_block("text", text="木头 3 个")], structured_content={"items": 3}), tool_name="t")
    assert (text.success, text.content, text.structured_content) == (True, "木头 3 个", {"items": 3})
    assert len(text.blocks) == 1 and text.blocks[0].kind == "text"

    mixed = to_result(
        _result([_block("text", text="看这里"), _block("image", data="aGVsbG8=", mimeType="image/png")]), tool_name="t"
    )
    image = mixed.blocks[1]
    assert (image.kind, image.data, image.mime_type) == ("image", "aGVsbG8=", "image/png")

    error = to_result(_result([_block("text", text="目标不可达")], is_error=True), tool_name="t")
    assert error.success is False and "目标不可达" in error.error_message

    failure = to_result(None, tool_name="t")
    assert failure.success is False and "失败" in failure.error_message

    as_dict = to_result(_result([{"type": "text", "text": "dict text"}]), tool_name="t")
    assert (
        as_dict.success
        and as_dict.content == "dict text"
        and as_dict.blocks == [ResultBlock(kind="text", text="dict text")]
    )

    no_content = to_result(_result(None, structured_content={"x": 1}), tool_name="t")
    assert (no_content.success, no_content.content, no_content.structured_content) == (True, "", {"x": 1})


@pytest.mark.parametrize("tool_name", ["serverA_execute", "mc_tool"])
def test_tool_name_is_carried_through(tool_name: str) -> None:
    assert to_result(_result([_block("text", text="ok")]), tool_name=tool_name).tool_name == tool_name
