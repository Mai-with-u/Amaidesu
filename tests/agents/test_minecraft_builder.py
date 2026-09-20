"""按需建造的真实协作边界：非阻塞委派、动态资料、校验交付与施工门禁。"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.builder.backend import validate_schema
from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.agents.minecraft.builder.controller import MinecraftBuilderController
from src.agents.minecraft.config import MinecraftConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


class FakeResources:
    """模拟新 Mod 的资源协议，支持在测试中发布新 Schema 与能力。"""

    def __init__(self) -> None:
        self.catalog: dict[str, Any] = {
            "protocol_version": 1,
            "revision": "cap-1",
            "design_schema_uri": "maicraft://building/schema",
            "design_schema_revision": "schema-1",
            "capabilities": ["basic"],
            "resources": [
                {"uri": "maicraft://building/guide", "title": "测试资料", "revision": "guide-1", "requires": ["basic"]},
                {
                    "uri": "maicraft://building/future",
                    "title": "未来能力",
                    "revision": "future-1",
                    "requires": ["lattice"],
                },
            ],
        }
        self.schema: dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "$defs": {"shape": {"type": "string", "enum": ["house"]}},
            "properties": {"shape": {"$ref": "#/$defs/shape"}},
            "required": ["shape"],
            "additionalProperties": False,
        }
        self.reads: list[str] = []
        self.list_calls = 0
        self.available = True

    async def list_resources(self) -> list[dict[str, str]]:
        """空闲时不应有资源发现，旧 Mod 没目录时也不能伪造能力。"""
        self.list_calls += 1
        return [{"uri": "maicraft://building/index"}] if self.available else []

    async def read_resource(self, uri: str) -> list[dict[str, str]]:
        """资料带有 $ref 字样，证明不会再次进入本地模板变量替换。"""
        self.reads.append(uri)
        values = {
            "maicraft://building/index": json.dumps(self.catalog),
            "maicraft://building/schema": json.dumps(self.schema),
            "maicraft://building/guide": "测试用资料：$ref 保持原文；它只指导当前设计。",
        }
        return [{"uri": uri, "text": values[uri]}]


class FakeModProvider(BaseToolProvider):
    """游戏侧的校验和施工事实源；测试确保只有父 Agent 调施工。"""

    name = "maicraft"
    category = "mcp"

    def __init__(self, resources: FakeResources) -> None:
        self.resources = resources
        self.calls: list[ToolInvocation] = []
        self.valid = True
        self.status = "running"
        self.uncertain_once = False

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=name, provider=self.name, description="测试新建造接口", parameters_schema={"type": "object"})
            for name in ("builder_validate", "builder_execute", "builder_preview")
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """返回机器可检查的设计引用；施工重试使用相同幂等键返回同一任务。"""
        self.calls.append(invocation)
        if invocation.tool_name == "maicraft_builder_validate":
            payload = {
                "valid": self.valid,
                "artifact_ref": "draft-1",
                "errors": [] if self.valid else ["门口被墙堵住"],
                "design_schema_revision": self.resources.catalog["design_schema_revision"],
                "capability_revision": self.resources.catalog["revision"],
            }
        elif invocation.tool_name == "maicraft_builder_execute":
            if self.uncertain_once:
                self.uncertain_once = False
                return ToolExecutionResult(
                    tool_name=invocation.tool_name, success=False, error_message="传输中断，受理结果未明"
                )
            payload = {"accepted": True, "task_id": "construction-1"}
        else:
            payload = {"preview": "设计预览摘要"}
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=payload)

    async def query_task(self, task_id: str) -> dict[str, Any]:
        """施工真相通过已有 TaskTracker 返回，与设计任务状态分开。"""
        return {"status": self.status, "summary": "游戏中的施工状态"}


@dataclass
class Harness:
    """把一次游戏会话的测试替身收拢，清理由 fixture 保证。"""

    parent: MinecraftAgent
    builder: MinecraftBuilderController
    llm: Any
    resources: FakeResources
    mod: FakeModProvider
    registry: ToolRegistry
    tracker: TaskTracker

    async def call(self, name: str, arguments: dict[str, Any], source: str = "minecraft-react") -> ToolExecutionResult:
        """使用真实工具注册路径，验证可见性之外的调用分发。"""
        return await self.registry.invoke(ToolInvocation(tool_name=name, arguments=arguments, source=source))

    async def request(self, intent: str = "build", **kwargs: Any) -> str:
        """走父 Agent 的受理路径，包含禁止重复 provider 登记的接线。"""
        receipt = await self.parent._execute_tool(
            "minecraft_builder_request",
            {
                "requirements": "设计一座房子",
                "intent": intent,
                **kwargs,
            },
        )
        assert receipt.get("accepted") is True, receipt
        self.parent._track_receipt("minecraft_builder_request", receipt)
        return receipt["task_id"]

    async def finish_worker(self) -> None:
        """等待测试设计结束，不靠固定睡眠推断任务成功。"""
        assert self.builder._worker is not None
        await asyncio.wait_for(asyncio.shield(self.builder._worker), timeout=3)


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    """启动真正的 Minecraft Agent，但借用测试 MCP 而不连接游戏。"""
    resources = FakeResources()
    mod = FakeModProvider(resources)
    registry = ToolRegistry()
    registry.register_provider(mod, visible_to={spec.full_name: ["minecraft"] for spec in mod.list_tools()})
    tracker = TaskTracker(registry, TaskLedger())
    llm = MagicMock()
    llm.generate = AsyncMock()
    parent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False)),
        llm_manager=llm,
        tool_registry=registry,
        task_tracker=tracker,
    )
    await parent.start()
    parent._mcp_client = resources
    assert parent._builder is not None
    try:
        yield Harness(parent, parent._builder, llm, resources, mod, registry, tracker)
    finally:
        await parent.stop()


def response(name: str, arguments: dict[str, Any]) -> Response:
    """用中立工具调用模拟模型，模型不接触 Minecraft 内部状态。"""
    return Response(success=True, content="", tool_calls=[ToolCall(id=name, name=name, arguments=arguments)])


def valid_design() -> list[Response]:
    """设计必须先通过 Mod 校验，再显式交付。"""
    return [
        response("minecraft_builder_work_validate", {"design": {"shape": "house"}}),
        response("minecraft_builder_work_finish", {"summary": "房子设计已完成"}),
    ]


async def test_idle_visibility_and_parent_stop(harness: Harness) -> None:
    """启用游戏只注册父级入口，禁用游戏后连入口也不可见。"""
    assert not harness.llm.generate.called and harness.resources.list_calls == 0
    assert harness.builder._active is None
    assert "minecraft_builder_request" in {
        spec.full_name for spec in harness.registry.list_tools(for_agent="minecraft")
    }
    assert not any(
        spec.provider.startswith("minecraft_builder") for spec in harness.registry.list_tools(for_agent="streamer")
    )
    await harness.parent.stop()
    assert not any(spec.provider.startswith("minecraft_builder") for spec in harness.registry.list_tools())


async def test_disabled_builder_has_no_tools_or_prompt() -> None:
    """显式关闭建造功能时父 Agent 不宣传或装配它。"""
    parent = MinecraftAgent(
        MinecraftConfig(builder={"enabled": False}, mcp=McpServerConfig(enabled=False)), tool_registry=ToolRegistry()
    )
    await parent.start()
    try:
        assert parent._builder is None
        assert "minecraft_builder" not in parent._system_prompt()
        assert all("builder" not in spec.full_name for spec in parent.list_tools())
    finally:
        await parent.stop()


async def test_slow_design_is_nonblocking_and_registered_once(harness: Harness) -> None:
    """模型迟迟未返回时父级仍能写工作笔记，受理回执保持 agent 型。"""
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(*args: Any, **kwargs: Any) -> Response:
        started.set()
        await release.wait()
        return response("minecraft_builder_work_fail", {"reason": "测试终止"})

    harness.llm.generate.side_effect = slow
    task_id = await harness.request(request_key="same-request")
    await asyncio.wait_for(started.wait(), 2)
    assert harness.tracker.ledger.get(task_id).source == "agent"
    duplicate = await harness.request(request_key="same-request")
    assert duplicate == task_id and harness.llm.generate.await_count == 1
    result = await harness.parent._execute_tool(
        "minecraft_notebook", {"action": "write", "content": "同时处理其他工作"}
    )
    assert result["success"] and harness.parent.get_state_snapshot()["notebook"] == "同时处理其他工作"
    assert await harness.parent._handle_report("delivery", "全部建好了", "") is not None
    release.set()
    await harness.finish_worker()


async def test_read_resources_and_new_capability_without_python_branch(harness: Harness) -> None:
    """Mod 改变设计 Schema 后直接生成新形状，选中的教程按任务注入。"""
    harness.resources.schema["$defs"]["shape"]["enum"] = ["net_pattern"]
    harness.llm.generate.side_effect = [
        response("minecraft_builder_work_read_resource", {"uri": "maicraft://building/guide"}),
        response("minecraft_builder_work_validate", {"design": {"shape": "net_pattern"}}),
        response("minecraft_builder_work_finish", {"summary": "新能力设计"}),
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    job = harness.builder._jobs[task_id]
    assert job.status == "succeeded" and job.result.design == {"shape": "net_pattern"}
    assert harness.builder._active is None
    second_context = json.loads(harness.llm.generate.await_args_list[1].args[0][1]["content"])
    assert "$ref" in second_context["selected_resources"]["maicraft://building/guide"]
    assert job.result.resource_refs == {"maicraft://building/guide": "guide-1"}
    assert harness.tracker.ledger.get(task_id) is None
    result = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "status"})
    assert result.structured_content["result"]["artifact_ref"] == "draft-1"
    assert "design" not in result.structured_content["result"]
    assert harness.parent._pending_task_count() == 0


async def test_design_completion_requires_real_construction(harness: Harness) -> None:
    """设计终态移出账本后仍不能交付；施工回执被登记到真正的 Mod provider。"""
    harness.llm.generate.side_effect = valid_design()
    task_id = await harness.request()
    await harness.finish_worker()
    assert harness.parent._pending_task_count() == 1
    assert await harness.parent._handle_report("delivery", "已经建好", "") is not None
    result = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "execute"})
    assert result.success, result.error_message
    receipt = result.structured_content
    harness.parent._track_receipt("minecraft_builder_task", receipt)
    record = harness.tracker.ledger.get(receipt["task_id"])
    assert record.source == "provider" and record.provider == "maicraft"
    assert len(harness.tracker.ledger.active_task_ids()) == 1
    assert harness.mod.calls[-1].source == "minecraft-react"
    assert "design" not in harness.mod.calls[-1].arguments
    harness.mod.status = "succeeded"
    await harness.tracker.step()
    harness.parent.on_task_notification(
        TaskChangedPayload(task_id=receipt["task_id"], status="succeeded", initiator="minecraft", executor="maicraft")
    )
    assert harness.parent._pending_task_count() == 0
    duplicate = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "execute"})
    assert duplicate.structured_content["task_id"] == receipt["task_id"]
    assert len([call for call in harness.mod.calls if call.tool_name.endswith("execute")]) == 1
