"""Mapper 纯函数单测：MCP → Amaidesu 工具契约映射

覆盖：
- to_spec（Tool → ToolSpec：name 存 server 原始名，provider 默认 "mcp"、全名派生）
- to_result（CallToolResult → ToolExecutionResult：成功/错误/结构化/image 块）
"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.mcp.mapper import (
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
# to_spec（声明名 = server 原始名；全名派生）
# ---------------------------------------------------------------------------


class TestToSpec:
    def test_basic_mapping(self) -> None:
        tool = FakeMcpTool(
            name="perceive",
            description="观察当前局面",
            input_schema={"type": "object", "properties": {"view": {"type": "string"}}},
        )
        spec = to_spec(tool)
        assert spec.name == "perceive"
        assert spec.description == "观察当前局面"
        assert spec.parameters_schema == {
            "type": "object",
            "properties": {"view": {"type": "string"}},
        }
        assert spec.kind == "sync"
        assert spec.provider == "mcp"
        assert spec.full_name == "mcp_perceive"

    def test_provider_override(self) -> None:
        tool = FakeMcpTool(name="perceive")
        spec = to_spec(tool, provider="game")
        assert spec.provider == "game"
        assert spec.full_name == "game_perceive"

    def test_raw_name_with_prefix_kept_as_is(self) -> None:
        # 原名自带 server 前缀（旧 mod 数据形态）也原样保留为声明名；
        # 全名派生不解析原名，调用 server 时用 spec.name 直呼
        tool = FakeMcpTool(name="serverA_perceive")
        spec = to_spec(tool, provider="serverA")
        assert spec.name == "serverA_perceive"
        assert spec.full_name == "serverA_serverA_perceive"

    def test_no_schema_returns_none(self) -> None:
        tool = FakeMcpTool(name="bare", description="no schema")
        spec = to_spec(tool)
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
