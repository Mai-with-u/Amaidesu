"""Mapper 纯函数单测：MCP → Amaidesu 工具契约映射

覆盖：
- to_spec（Tool → ToolSpec：name 存 server 原始名，provider 默认 "mcp"、全名派生）
- to_result（CallToolResult → ToolExecutionResult：成功/错误/结构化/image 块）
"""

from __future__ import annotations

from typing import Any

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
    def test_text_only_json_is_available_to_programmatic_consumers(self) -> None:
        """单份 JSON 回执既可驱动任务监控，也保留原始文本，不依赖服务器重复发送结构化字段。"""
        text = '{"task_id":"task-1","state":"success","terminal":{"result":{"success":true}}}'
        result = to_result(FakeCallToolResult(content=[FakeContentBlock("text", text=text)]), tool_name="maicraft_task")
        assert result.structured_content["state"] == "success"
        assert result.content == text and result.blocks[0].text == text

    def test_text_only_error_keeps_outcome_certainty(self) -> None:
        """业务拒绝的确定性属于回执事实，不能因缺少结构化通道而遗失。"""
        text = '{"success":false,"error":{"outcome_known":false,"code":"unconfirmed"}}'
        result = to_result(
            FakeCallToolResult(content=[FakeContentBlock("text", text=text)], is_error=True), tool_name="tool"
        )
        assert not result.success and result.structured_content["error"]["outcome_known"] is False

    def test_json_fallback_preserves_metadata_and_multimodal_boundaries(self) -> None:
        """已有元数据优先；多份正文、截断 JSON 和图文混合不能被猜成一个对象。"""
        metadata = {"resources": [{"uri": "example://document"}]}
        result = to_result(
            FakeCallToolResult(content=[FakeContentBlock("text", text='{"body":1}')], structured_content=metadata),
            tool_name="tool",
        )
        assert result.structured_content == metadata
        for blocks in (
            [FakeContentBlock("text", text='{"unfinished":')],
            [FakeContentBlock("text", text='{"a":1}'), FakeContentBlock("text", text='{"b":2}')],
            [FakeContentBlock("text", text='{"a":1}'), FakeContentBlock("image", data="x", mimeType="image/png")],
        ):
            observed = to_result(FakeCallToolResult(content=blocks), tool_name="tool")
            assert observed.structured_content is None and len(observed.blocks) == len(blocks)

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
