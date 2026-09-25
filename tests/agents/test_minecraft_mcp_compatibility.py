"""以真实 MCP SDK 的仅文本回执贯穿声明、任务适配器和建筑调用，覆盖当前 Mod 工具名。"""

import json
from types import SimpleNamespace
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.builder.backend import MinecraftBuilderBackend, validate_schema
from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.observations import repeated_read
from src.agents.minecraft.tool_content import successful_observation
from src.agents.minecraft.tool_names import find_mod_tool
from src.modules.mcp.mapper import to_result
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.models import ToolSpec
from src.modules.tools.registry import ToolRegistry


async def test_text_only_sdk_receipts_drive_current_adapters() -> None:
    """MCP 服务器不重复发送结构化字段时，后台仍能结束任务并读取事件，设计调用也能读到受理号。"""
    server = FastMCP("contract-fixture")
    executions: list[dict[str, Any]] = []

    @server.tool
    async def task(action: str, task_id: str) -> ToolResult:
        """返回指定任务的当前事实。"""
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"task_id": task_id, "state": "success", "terminal": {"result": {"success": True}}}
                    ),
                )
            ]
        )

    @server.tool
    async def perceive(
        view: str, after_cursor: int = 0, wait_ms: int = 0, limit: int = 5, stream_id: str | None = None
    ) -> ToolResult:
        """返回可继续读取的注意流位置。"""
        return ToolResult(
            content=[TextContent(type="text", text=json.dumps({"stream_id": "stream", "cursor": 8, "events": []}))]
        )

    @server.tool
    async def execute(goal: dict[str, Any]) -> ToolResult:
        """记录一次受理而不执行真实游戏操作。"""
        executions.append(goal)
        return ToolResult(content=[TextContent(type="text", text='{"accepted":true,"task_id":"accepted"}')])

    @server.resource("maicraft://building/schema")
    async def schema() -> str:
        """程序校验所需原文包含长说明，不能被替换为引用清单。"""
        return json.dumps(
            {
                "type": "object",
                "properties": {"shape": {"const": "house"}},
                "required": ["shape"],
                "additionalProperties": False,
                "description": "原生设计约束" * 2000,
            },
            ensure_ascii=False,
        )

    async with Client(server) as sdk:
        bridge = SimpleNamespace(connected=True, list_tools=sdk.list_tools, call_tool=sdk.call_tool)
        provider = McpToolProvider(client=bridge, server_name="maicraft")
        await provider.setup()
        # 通知生命周期已由独立回归覆盖，这里只驱动真实调用和声明绑定。
        provider.subscribe_task_notifications = None
        agent = MinecraftAgent(MinecraftConfig())
        agent._bind_mcp_adapters(provider)
        assert provider.task_query_tool == "maicraft_task"
        assert provider.attention_read_tool == "maicraft_perceive"
        raw = await sdk.call_tool("task", {"action": "get", "task_id": "task"})
        assert raw.structured_content is None
        decoded = to_result(raw, tool_name="maicraft_task")
        assert successful_observation(decoded, {}) == json.loads(raw.content[0].text)
        assert (await provider.query_task("task"))["status"] == "succeeded"
        assert (await provider.read_attention())["cursor"] == 8
        registry = ToolRegistry()
        registry.register_provider(
            provider, visible_to={spec.full_name: ["minecraft"] for spec in provider.list_tools()}
        )
        backend = MinecraftBuilderBackend(MinecraftBuilderConfig(), registry, lambda: sdk)
        receipt = await backend.invoke(
            "maicraft_execute", {"goal": {"ability": "fixture"}}, source="minecraft-builder-react"
        )
        assert receipt["accepted"] is True and len(executions) == 1
        full_schema = json.loads(await backend.read_text("maicraft://building/schema"))
        assert len(full_schema["description"]) > 8000
        assert validate_schema(full_schema, {"wrong": True})
        assert validate_schema(full_schema, {"shape": "house"}) == []


def test_aliases_do_not_replace_explicit_custom_bindings() -> None:
    """兼容只涵盖已发布的四个角色名，明确配置的自定义工具保持原样。"""
    specs = [
        ToolSpec(name=name, description="测试", provider="maicraft") for name in ("execute", "task", "custom_execute")
    ]
    assert find_mod_tool(specs, "maicraft_execute").name == "execute"
    assert find_mod_tool(specs, "custom_execute").name == "custom_execute"
    assert find_mod_tool(specs, "unknown_execute") is None
    assert repeated_read("maicraft_plan", {"plan_id": "old"}, {"same_request_and_result": True})
    assert not repeated_read("maicraft_plan", {"goal": {}}, {"same_request_and_result": True})
