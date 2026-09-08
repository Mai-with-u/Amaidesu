"""Mapper 纯函数单测：MCP → Amaidesu 工具契约映射

覆盖：
- normalize_tool_name（无条件加前缀）
- to_spec（Tool → ToolSpec：name/description/inputSchema/provider）
- to_result（CallToolResult → ToolExecutionResult：成功/错误/结构化/image 块）
"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.mcp.mapper import (
    normalize_tool_name,
    to_result,
    to_spec,
)
from src.modules.tools.models import ResultBlock


class FakeMcpTool:
    """mcp.types.Tool 的鸭子类型（有 name/description/inputSchema）。"""

    def __init__(self, name: str, description: str = "", input_schema: Any = None) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class FakeContentBlock:
    """mcp.types.TextContent / ImageContent 的鸭子类型。"""

    def __init__(self, type_: str, **kwargs: Any) -> None:
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeCallToolResult:
    """mcp CallToolResult 的鸭子类型。"""

    def __init__(
        self,
        content: Any = None,
        is_error: bool = False,
        structured_content: Any = None,
    ) -> None:
        self.content = content
        self.is_error = is_error
        self.structured_content = structured_content


# ---------------------------------------------------------------------------
# normalize_tool_name（无条件加前缀）
# ---------------------------------------------------------------------------


class TestToolName:
    def test_prefix_added_when_missing(self) -> None:
        assert normalize_tool_name("perceive", "serverA_") == "serverA_perceive"

    def test_prefix_always_added_even_if_present(self) -> None:
        # 原名已带 server 前缀也照样再加：不猜原名形态，注册名唯一即可，
        # 调用时由 Provider 映射表还原原名
        assert normalize_tool_name("serverA_perceive", "serverA_") == "serverA_serverA_perceive"

    def test_no_prefix_returns_raw(self) -> None:
        assert normalize_tool_name("perceive", "") == "perceive"


# ---------------------------------------------------------------------------
# to_spec
# ---------------------------------------------------------------------------


class TestToSpec:
    def test_basic_mapping(self) -> None:
        tool = FakeMcpTool(
            name="perceive",
            description="观察当前局面",
            input_schema={"type": "object", "properties": {"view": {"type": "string"}}},
        )
        spec = to_spec(tool, prefix="serverA_")
        assert spec.name == "serverA_perceive"
        assert spec.description == "观察当前局面"
        assert spec.parameters_schema == {
            "type": "object",
            "properties": {"view": {"type": "string"}},
        }
        assert spec.kind == "sync"
        assert spec.provider == "mcp"

    def test_provider_override(self) -> None:
        tool = FakeMcpTool(name="perceive")
        spec = to_spec(tool, prefix="serverA_", provider="game")
        assert spec.provider == "game"

    def test_no_schema_returns_none(self) -> None:
        tool = FakeMcpTool(name="bare", description="no schema")
        spec = to_spec(tool, prefix="")
        assert spec.parameters_schema is None


# ---------------------------------------------------------------------------
# to_result
# ---------------------------------------------------------------------------


class TestToResult:
    def test_success_text_content(self) -> None:
        result = FakeCallToolResult(
            content=[FakeContentBlock("text", text="木头 3 个")],
            structured_content={"items": 3},
        )
        exec_result = to_result(result, tool_name="serverA_execute")
        assert exec_result.success is True
        assert exec_result.content == "木头 3 个"
        assert exec_result.structured_content == {"items": 3}
        assert len(exec_result.blocks) == 1
        assert exec_result.blocks[0].kind == "text"

    def test_image_block_mapped(self) -> None:
        result = FakeCallToolResult(
            content=[
                FakeContentBlock("text", text="看这里"),
                FakeContentBlock("image", data="aGVsbG8=", mimeType="image/png"),
            ],
        )
        exec_result = to_result(result, tool_name="mc_look")
        assert len(exec_result.blocks) == 2
        image_block = exec_result.blocks[1]
        assert image_block.kind == "image"
        assert image_block.data == "aGVsbG8="
        assert image_block.mime_type == "image/png"

    def test_error_result(self) -> None:
        result = FakeCallToolResult(
            content=[FakeContentBlock("text", text="目标不可达")],
            is_error=True,
        )
        exec_result = to_result(result, tool_name="serverA_execute")
        assert exec_result.success is False
        assert "目标不可达" in exec_result.error_message

    def test_none_result_failure(self) -> None:
        exec_result = to_result(None, tool_name="mc_tool")
        assert exec_result.success is False
        assert "失败" in exec_result.error_message

    def test_dict_content_blocks(self) -> None:
        result = FakeCallToolResult(
            content=[{"type": "text", "text": "dict text"}],
        )
        exec_result = to_result(result, tool_name="mc_tool")
        assert exec_result.success is True
        assert exec_result.content == "dict text"
        assert exec_result.blocks == [ResultBlock(kind="text", text="dict text")]

    def test_content_none_ok(self) -> None:
        result = FakeCallToolResult(content=None, structured_content={"x": 1})
        exec_result = to_result(result, tool_name="mc_tool")
        assert exec_result.success is True
        assert exec_result.content == ""
        assert exec_result.structured_content == {"x": 1}
