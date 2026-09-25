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


def test_all_design_errors_are_returned_to_the_model() -> None:
    """部件数量超过旧错误条数上限时，最后一个部件的格式问题仍可见。"""
    errors = validate_schema({"type": "array", "items": {"type": "integer"}}, ["无效部件"] * 25)
    assert [error["path"] for error in errors] == [f"/{index}" for index in range(25)]


class FakeResources:
    """模拟新 Mod 的资源协议，支持在测试中发布新 Schema 与能力。"""

    def __init__(self) -> None:
        self.catalog: dict[str, Any] = {
            "protocol_version": 1,
            "revision": "cap-1",
            "design_schema_uri": "maicraft://building/schema",
            "design_schema_revision": "schema-1",
            "capabilities": ["basic"],
            "edit_schema_ref": "#/$defs/scene_edits",
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
            "$defs": {
                "shape": {"type": "string", "enum": ["house"]},
                "scene_edits": {
                    "type": "object",
                    "properties": {"objects": {"type": "array"}},
                    "additionalProperties": False,
                },
            },
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
        self.design_results: dict[str, dict[str, Any]] = {}
        self.saved_scenes: dict[str, dict[str, Any]] = {}
        self.pending_design_queries = 0

    def operations(self, operation: str) -> list[ToolInvocation]:
        """同一个 execute 原名承载不同操作，按实际 goal 判断设计和施工。"""
        return [
            call
            for call in self.calls
            if call.arguments.get("goal", {}).get("parameters", {}).get("operation") == operation
        ]

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=name, provider=self.name, description="测试新建造接口", parameters_schema={"type": "object"})
            for name in ("maicraft_execute", "maicraft_task")
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """返回机器可检查的设计引用；施工重试使用相同幂等键返回同一任务。"""
        self.calls.append(invocation)
        parameters = invocation.arguments.get("goal", {}).get("parameters", {})
        operation = parameters.get("operation")
        if invocation.tool_name == "maicraft_maicraft_task":
            if self.pending_design_queries:
                self.pending_design_queries -= 1
                payload = {"task_id": invocation.arguments["task_id"], "state": "running"}
            else:
                payload = self.design_results[invocation.arguments["task_id"]]
        elif operation == "build":
            if self.uncertain_once:
                self.uncertain_once = False
                return ToolExecutionResult(
                    tool_name=invocation.tool_name, success=False, error_message="传输中断，受理结果未明"
                )
            payload = {"accepted": True, "task_id": "construction-1"}
        else:
            task_id = invocation.arguments["request_key"]
            if task_id not in self.design_results:
                scene_id = parameters.get("scene_id", "")
                if operation in {"create_scene", "update_scene"}:
                    previous = scene_id
                    scene_id = f"draft-{len(self.saved_scenes) + 1}"
                    data = {
                        "scene_id": scene_id,
                        "scene_uri": f"maicraft://knowledge/build/scene/{scene_id}",
                        "construction_started": False,
                        "design_schema_revision": self.resources.catalog["design_schema_revision"],
                        "capability_revision": self.resources.catalog["revision"],
                        "anchor": {"x": 0, "y": 64, "z": 0, "dimension": "minecraft:overworld"},
                    }
                    if previous:
                        data["parent_scene_id"] = previous
                    if self.valid:
                        self.saved_scenes[scene_id] = data
                else:
                    data = dict(self.saved_scenes[scene_id])
                    data.update(page=parameters.get("page", 0), has_more=False)
                self.design_results[task_id] = {
                    "task_id": task_id,
                    "state": "success" if self.valid else "failed",
                    "terminal": {
                        "result": {
                            "success": self.valid,
                            "message": "编译完成" if self.valid else "对象表达不合法",
                            "data": data,
                        }
                    },
                }
            payload = {"accepted": True, "task_id": task_id}
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
    return Response(
        success=True,
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCall(id=name, name=name, arguments=arguments)],
    )


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


@pytest.mark.parametrize("reason", ["length", "content_filter", None])
async def test_incomplete_model_turn_never_reaches_mod(harness: Harness, reason: str | None) -> None:
    """即使参数恰好是合法 JSON，只要模型未正常结束，本轮就不能进入 Mod。"""
    incomplete = valid_design()[0].model_copy(update={"finish_reason": reason})
    harness.llm.generate.side_effect = [incomplete, *valid_design()]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    assert harness.builder._jobs[task_id].status == "succeeded"
    assert len(harness.mod.operations("create_scene")) == 1
    assert "max_tokens" not in harness.llm.generate.call_args.kwargs
    assert harness.llm.generate.call_args.kwargs["strict_tool_arguments"] is True


async def test_bad_arguments_invalidate_entire_turn_and_old_candidate(harness: Harness) -> None:
    """同轮出现不完整补丁时，不能先执行 finish 把上一个候选交付出去。"""
    broken = Response(
        success=True,
        finish_reason="tool_calls",
        tool_calls=[
            ToolCall(id="finish", name="minecraft_builder_work_finish", arguments={"summary": "过早交付"}),
            ToolCall(
                id="broken",
                name="minecraft_builder_work_validate",
                arguments={},
                raw_arguments='{"design":',
                arguments_error="JSON 未结束",
            ),
        ],
    )
    harness.llm.generate.side_effect = [
        valid_design()[0],
        broken,
        valid_design()[1],
        response("minecraft_builder_work_fail", {"reason": "完整设计尚未重新校验"}),
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    assert harness.builder._jobs[task_id].status == "failed"
    assert harness.builder._jobs[task_id].result is None


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
    # 后选教材追加在回执中，第一条用户上下文不会随着选读而变化。
    second_messages = harness.llm.generate.await_args_list[1].args[0]
    second_context = json.loads(second_messages[1]["content"])
    assert "maicraft://building/guide" not in second_context["selected_resources"]
    assert any("$ref" in message.get("content", "") for message in second_messages if message["role"] == "tool")
    assert job.result.resource_refs == {"maicraft://building/guide": "guide-1"}
    assert harness.tracker.ledger.get(task_id) is None
    result = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "status"})
    assert result.structured_content["result"]["artifact_ref"] == "draft-1"
    assert "design" not in result.structured_content["result"]
    assert harness.parent._pending_task_count() == 0


async def test_task_start_tutorial_is_in_first_prompt_and_read_once(harness: Harness) -> None:
    """接到建房委派才读基础教材，首轮即可使用；重复选读不浪费通道或改变版本依据。"""
    guide = harness.resources.catalog["resources"][0]
    guide["load_policy"] = "task_start"
    assert not harness.resources.reads
    harness.llm.generate.side_effect = [
        response("minecraft_builder_work_read_resource", {"uri": guide["uri"]}),
        response("minecraft_builder_work_validate", {"design": {"shape": "house"}}),
        response("minecraft_builder_work_finish", {"summary": "遵循基础方法的设计"}),
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    first_context = json.loads(harness.llm.generate.await_args_list[0].args[0][1]["content"])
    assert "$ref" in first_context["selected_resources"][guide["uri"]]
    assert harness.resources.reads.count(guide["uri"]) == 1
    assert "maicraft://building/future" not in harness.resources.reads
    assert harness.builder._jobs[task_id].result.resource_refs == {guide["uri"]: guide["revision"]}


async def test_design_history_appends_without_rewriting_old_resources(harness: Harness) -> None:
    """超过六轮工具往返后教材和旧回执仍稳定，重复选读复用同一份已加载教材。"""
    harness.llm.generate.side_effect = [
        *[response("minecraft_builder_work_read_resource", {"uri": "maicraft://building/guide"}) for _ in range(8)],
        *valid_design(),
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    inputs = [call.args[0] for call in harness.llm.generate.await_args_list]
    assert all(new[: len(old)] == old for old, new in zip(inputs, inputs[1:], strict=False))
    assert "旧设计观察已压缩" not in str(inputs[-1])
    assert harness.resources.reads.count("maicraft://building/guide") == 1
    assert harness.builder._jobs[task_id].status == "succeeded"


async def test_design_checkpoint_uses_own_profile_and_preserves_loaded_resources(harness: Harness) -> None:
    """新教材先用于校验方案，再整理旧分析；教材与有效候选保留，随后仍能交付。"""
    harness.builder._config.max_context_chars = 24000
    first = response("minecraft_builder_work_read_resource", {"uri": "maicraft://building/guide"})
    first.content = "候选方案分析" * 7000
    validate, finish = valid_design()
    harness.llm.generate.side_effect = [
        first,
        validate,
        Response(success=True, content="已读取教材，下一步验证方案", finish_reason="stop"),
        finish,
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    calls = harness.llm.generate.await_args_list
    # 首次工具回执未经摘要交给第二次设计决策，第三次调用才整理已经读过的分析。
    assert len(calls) == 4 and calls[2].kwargs["profile"] == "minecraft_builder"
    assert "max_tokens" not in calls[2].kwargs
    assert first.content in str(calls[1].args[0])
    assert "maicraft://building/guide" in str(calls[3].args[0])
    assert harness.builder._jobs[task_id].status == "succeeded"


@pytest.mark.parametrize("incompatibility", ["capability", "schema", "large"])
async def test_task_start_tutorial_preserves_compatibility_and_full_text(
    harness: Harness, incompatibility: str
) -> None:
    """不兼容教材在推理前拒绝，长教材完整注入后可以正常完成设计。"""
    guide = harness.resources.catalog["resources"][0]
    guide["load_policy"] = "task_start"
    if incompatibility == "capability":
        guide["requires"] = ["unavailable"]
    elif incompatibility == "schema":
        guide["compatible_schema_revisions"] = ["outdated"]
    else:
        read = harness.resources.read_resource

        async def oversized_read(uri: str) -> list[dict[str, str]]:
            """正文超过旧资料上限时仍保留最后一项具体设计要求。"""
            if uri == guide["uri"]:
                return [{"uri": uri, "text": "材" * 24001 + "保留入口"}]
            return await read(uri)

        harness.resources.read_resource = oversized_read
        harness.llm.generate.side_effect = valid_design()
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    if incompatibility == "large":
        assert harness.builder._jobs[task_id].status == "succeeded"
        assert "材" * 24001 + "保留入口" in str(harness.llm.generate.call_args_list[0].args[0])
    else:
        assert harness.builder._jobs[task_id].status == "failed"
        assert not harness.llm.generate.called and not harness.mod.calls


async def test_design_completion_requires_real_construction(harness: Harness) -> None:
    """设计终态移出账本后仍不能交付；施工回执被登记到真正的 Mod provider。"""
    harness.llm.generate.side_effect = valid_design()
    task_id = await harness.request()
    await harness.finish_worker()
    assert harness.parent._pending_task_count() == 1
    # 审阅完成后需要主动发起施工，不能睡等一个尚未创建的施工任务。
    assert harness.parent._actionable_task_ids() == {task_id}
    assert await harness.parent._handle_report("delivery", "已经建好", "") is not None
    result = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "execute"})
    assert result.success, result.error_message
    receipt = result.structured_content
    assert harness.parent._actionable_task_ids() == set(), "真实施工已受理时才可以等待后台进展"
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
    assert len(harness.mod.operations("build")) == 1


@pytest.mark.parametrize("resource_uri", ["maicraft://building/future", "file:///unexpected"])
async def test_unavailable_or_unknown_resources_are_not_read(harness: Harness, resource_uri: str) -> None:
    """缺少技法能力或猜出的 URI 都不能被当作有效教材。"""
    harness.llm.generate.side_effect = [
        response("minecraft_builder_work_read_resource", {"uri": resource_uri}),
        response("minecraft_builder_work_fail", {"reason": "没有可用资料"}),
    ]
    await harness.request(intent="design")
    await harness.finish_worker()
    assert resource_uri not in harness.resources.reads


async def test_old_mod_fails_before_model_inference(harness: Harness) -> None:
    """旧 Mod 无目录时明确失败，不能调用旧语义建造工具或浪费模型推理。"""
    harness.resources.available = False
    task_id = await harness.request()
    await harness.finish_worker()
    job = harness.builder._jobs[task_id]
    assert job.status == "failed" and "尚未发布" in job.summary
    assert not harness.llm.generate.called and not harness.mod.calls


async def test_schema_error_can_be_repaired_before_delivery(harness: Harness) -> None:
    """格式错误直接回给设计循环自纠，不让非法草稿进入 Mod。"""
    harness.llm.generate.side_effect = [
        response("minecraft_builder_work_validate", {"design": {"shape": "bad"}}),
        *valid_design(),
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    assert harness.builder._jobs[task_id].status == "succeeded"
    assert len(harness.mod.operations("create_scene")) == 1


async def test_failed_new_validation_invalidates_old_candidate(harness: Harness) -> None:
    """修改后的非法草稿不能借上一次校验通过的结果交付。"""
    harness.llm.generate.side_effect = [
        valid_design()[0],
        response("minecraft_builder_work_validate", {"design": {"shape": "bad"}}),
        valid_design()[1],
        response("minecraft_builder_work_fail", {"reason": "无法修复"}),
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    assert harness.builder._jobs[task_id].status == "failed"
    assert harness.builder._jobs[task_id].result is None


async def test_child_cannot_execute_or_call_parent_tools(harness: Harness) -> None:
    """即使模型编造施工工具名，也不会绕过本轮工具授权。"""
    harness.llm.generate.side_effect = [
        response(
            "maicraft_maicraft_execute", {"goal": {"ability": "maicraft:build", "parameters": {"operation": "build"}}}
        ),
        response("minecraft_builder_work_fail", {"reason": "不允许施工"}),
    ]
    await harness.request(intent="design")
    await harness.finish_worker()
    assert not harness.mod.calls
    result = await harness.call("minecraft_builder_request", {"requirements": "越界请求"}, source="streamer-react")
    assert not result.success


async def test_cancel_before_child_starts_clears_ledger(harness: Harness) -> None:
    """首个调度点前取消也产生终态，之后不再调用模型。"""
    task_id = await harness.request()
    result = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "cancel"})
    assert result.success and result.structured_content["status"] == "cancelled"
    assert harness.tracker.ledger.get(task_id) is None
    assert harness.parent._pending_task_count() == 0 and not harness.llm.generate.called


async def test_stop_cancels_inflight_design_and_removes_work_tools(harness: Harness) -> None:
    """父级停机收束长推理，不留下子 Agent 的工具或活跃任务。"""
    started = asyncio.Event()

    async def slow(*args: Any, **kwargs: Any) -> Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("不应到达")

    harness.llm.generate.side_effect = slow
    task_id = await harness.request()
    await asyncio.wait_for(started.wait(), 2)
    await harness.parent.stop()
    assert harness.tracker.ledger.get(task_id) is None
    assert harness.builder._jobs[task_id].status == "cancelled"
    assert not any(spec.provider.startswith("minecraft_builder") for spec in harness.registry.list_tools())


async def test_revision_keeps_previous_request_and_replaces_pending_obligation(harness: Harness) -> None:
    """完成的设计可以按追加要求修订，新任务拥有独立版本且旧结果不再阻挡交付。"""
    harness.llm.generate.side_effect = valid_design() * 2
    task_id = await harness.request()
    await harness.finish_worker()
    result = await harness.call(
        "minecraft_builder_task", {"task_id": task_id, "action": "revise", "requirements": "更换入口"}
    )
    assert result.success, result.error_message
    revised_id = result.structured_content["task_id"]
    await harness.finish_worker()
    job = harness.builder._jobs[revised_id]
    assert job.request_revision == 2 and "更换入口" in job.request.requirements
    assert job.request.context["previous_scene_id"] == "draft-1"
    assert "previous_design" not in job.request.context
    assert harness.builder.pending_ids() == {revised_id}


async def test_catalog_change_prevents_stale_execution(harness: Harness) -> None:
    """设计后 Mod 能力变更时，旧图不能在新语义下悄悄施工。"""
    harness.llm.generate.side_effect = valid_design()
    task_id = await harness.request()
    await harness.finish_worker()
    harness.resources.catalog["revision"] = "cap-2"
    result = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "execute"})
    assert not result.success and "版本已变化" in result.error_message
    assert len(harness.mod.operations("build")) == 0


async def test_uncertain_execution_retries_same_key_and_blocks_revision(harness: Harness) -> None:
    """施工受理结果不明时禁止换图，重试沿用同一幂等键。"""
    harness.llm.generate.side_effect = valid_design()
    task_id = await harness.request()
    await harness.finish_worker()
    harness.mod.uncertain_once = True
    first = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "execute"})
    assert not first.success
    revision = await harness.call(
        "minecraft_builder_task", {"task_id": task_id, "action": "revise", "requirements": "重建"}
    )
    assert not revision.success
    retry = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "execute"})
    assert retry.success
    executions = harness.mod.operations("build")
    assert [call.arguments["request_key"] for call in executions] == [f"{task_id}:build", f"{task_id}:build"]


def test_remote_schema_references_never_fetch_network() -> None:
    """缺失定义直接失败，不把 Mod Schema 的远程引用变成隐式网络请求。"""
    with pytest.raises(Exception, match="Unresolvable"):
        validate_schema({"$ref": "https://invalid.example/schema"}, {})


def test_scene_transport_bindings_must_differ() -> None:
    """任务查询不能被绑定到受理入口；设计与施工则在同一受理工具中按操作隔离。"""
    with pytest.raises(ValueError, match="不同工具"):
        MinecraftBuilderConfig(task_tool="same", execute_tool="same")


async def test_cancel_rejects_model_reply_that_swallowed_cancellation(harness: Harness) -> None:
    """底层客户端迟到返回时，显式取消标记仍阻止后续 Mod 校验。"""
    started = asyncio.Event()

    async def stubborn(*args: Any, **kwargs: Any) -> Response:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # 模拟远端结果与本地取消竞态，不能因此重新激活这份设计。
            return valid_design()[0]
        raise AssertionError("不应到达")

    harness.llm.generate.side_effect = stubborn
    task_id = await harness.request()
    await asyncio.wait_for(started.wait(), 2)
    result = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "cancel"})
    assert result.success and harness.builder._jobs[task_id].status == "cancelled"
    assert not harness.mod.calls


async def test_parent_pause_holds_design_actions_until_resume(harness: Harness) -> None:
    """长推理完成时若游戏已暂停，设计工具也要等父级恢复后才执行。"""
    started, release, returned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def delayed(*args: Any, **kwargs: Any) -> Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            returned.set()
            return valid_design()[0]
        return valid_design()[1]

    harness.llm.generate.side_effect = delayed
    task_id = await harness.request(intent="design")
    await asyncio.wait_for(started.wait(), 2)
    await harness.parent._on_pause()
    release.set()
    await asyncio.wait_for(returned.wait(), 2)
    await asyncio.sleep(0)
    assert not harness.mod.calls
    await harness.parent._on_resume()
    await harness.finish_worker()
    assert harness.builder._jobs[task_id].status == "succeeded"


async def test_design_total_timeout_includes_llm_wait(harness: Harness) -> None:
    """不依赖模型内部超时，任务预算到期会终止设计并保存可读原因。"""
    harness.builder._config.task_timeout_ms = 1000

    async def forever(*args: Any, **kwargs: Any) -> Response:
        await asyncio.Event().wait()
        raise AssertionError("不应到达")

    harness.llm.generate.side_effect = forever
    task_id = await harness.request()
    await harness.finish_worker()
    assert harness.builder._jobs[task_id].status == "timeout"
    assert harness.tracker.ledger.get(task_id) is None
    assert not harness.registry.list_tools(provider="minecraft_builder_work")


async def test_paused_design_can_be_cancelled(harness: Harness) -> None:
    """暂停不应迫使用户先恢复长推理才能取消任务。"""
    task_id = await harness.request()
    await harness.parent._on_pause()
    cancelled = await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "cancel"})
    assert cancelled.success and harness.tracker.ledger.get(task_id) is None


async def test_second_design_does_not_start_competing_construction(harness: Harness) -> None:
    """角色施工时可以准备另一份设计，但第二份施工必须等身体空闲。"""
    harness.llm.generate.side_effect = valid_design() * 2
    first = await harness.request()
    await harness.finish_worker()
    started = await harness.call("minecraft_builder_task", {"task_id": first, "action": "execute"})
    assert started.success
    second = await harness.request()
    await harness.finish_worker()
    conflict = await harness.call("minecraft_builder_task", {"task_id": second, "action": "execute"})
    assert not conflict.success and "另一份设计" in conflict.error_message
    assert len(harness.mod.operations("build")) == 1


async def test_mod_acceptance_is_not_design_success(harness: Harness) -> None:
    """Mod 接收无效设计后返回失败终态，客户端不能把 accepted 当作已校验产物。"""
    harness.mod.valid = False
    harness.llm.generate.side_effect = [
        *valid_design(),
        response("minecraft_builder_work_fail", {"reason": "无法编译"}),
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    assert harness.builder._jobs[task_id].status == "failed"
    assert harness.builder._jobs[task_id].result is None
    assert not harness.mod.saved_scenes


async def test_pending_mod_design_waits_without_extra_model_calls(harness: Harness) -> None:
    """设计操作尚未终结时由代码等待，不能花推理轮次轮询受理回执。"""
    harness.builder._config.operation_poll_interval_ms = 1
    harness.mod.pending_design_queries = 2
    harness.llm.generate.side_effect = valid_design()
    await harness.request(intent="design")
    await harness.finish_worker()
    assert harness.llm.generate.await_count == 2
    assert len([call for call in harness.mod.calls if call.arguments.get("action") == "get"]) == 3


async def test_named_edit_creates_new_scene_without_copying_full_model(harness: Harness) -> None:
    """对象补丁由 Mod 合并，新的场景引用取代候选，旧场景继续存在。"""
    edits = {"objects": [{"name": "screen", "pattern": {"rows": ["01", "10"]}}]}
    harness.llm.generate.side_effect = [
        valid_design()[0],
        response("minecraft_builder_work_update", {"edits": edits}),
        response("minecraft_builder_work_inspect", {"kind": "object", "name": "screen", "page": 1}),
        valid_design()[1],
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    result = harness.builder._jobs[task_id].result
    assert result.artifact_ref == "draft-2" and result.design is None
    assert result.validation["parent_scene_id"] == "draft-1"
    assert "draft-1" in harness.mod.saved_scenes
    update = harness.mod.operations("update_scene")[0].arguments["goal"]
    assert update["ability"] == "maicraft:design_build"
    assert update["parameters"]["edits"] == edits and "scene" not in update["parameters"]
    assert "target" not in update
    inspect = harness.mod.operations("get_object_info")[0].arguments["goal"]["parameters"]
    assert inspect["object_name"] == "screen" and inspect["page"] == 1


async def test_same_design_retry_reuses_key_distinct_from_construction(harness: Harness) -> None:
    """同内容重试不会产生重复场景，施工使用单独的稳定键。"""
    harness.llm.generate.side_effect = [valid_design()[0], *valid_design()]
    task_id = await harness.request()
    await harness.finish_worker()
    await harness.call("minecraft_builder_task", {"task_id": task_id, "action": "execute"})
    keys = [call.arguments["request_key"] for call in harness.mod.operations("create_scene")]
    assert keys[0] == keys[1] and len(harness.mod.saved_scenes) == 1
    assert keys[0] != harness.mod.operations("build")[0].arguments["request_key"]


async def test_invalid_named_edit_is_rejected_before_mod(harness: Harness) -> None:
    """编辑格式也使用 Mod 指定的局部 Schema，错误补丁不能进入场景存储。"""
    harness.llm.generate.side_effect = [
        valid_design()[0],
        response("minecraft_builder_work_update", {"edits": {"objects": "不是数组"}}),
        response("minecraft_builder_work_fail", {"reason": "无法修正对象列表"}),
    ]
    task_id = await harness.request(intent="design")
    await harness.finish_worker()
    assert not harness.mod.operations("update_scene")
    assert harness.builder._jobs[task_id].result is None


@pytest.mark.parametrize("stall_at_completion", [False, True])
async def test_real_completion_event_wakes_parent_once_with_design_reference(
    harness: Harness, stall_at_completion: bool
) -> None:
    """任务完成走真实事件总线，父级不把本地任务号拿去 Mod 查询或自动报告建好。"""
    await harness.parent.stop()
    bus = EventBus()
    ledger = TaskLedger(bus)
    tracker = TaskTracker(harness.registry, ledger)
    parent_called = asyncio.Event()
    changes: list[TaskChangedPayload] = []
    design_calls = 0

    async def observe(event_name: str, payload: TaskChangedPayload, source: str) -> None:
        changes.append(payload)

    async def generate(messages: list[dict[str, Any]], **kwargs: Any) -> Response:
        nonlocal design_calls
        if kwargs["profile"] == "minecraft_builder":
            result = valid_design()[design_calls]
            design_calls += 1
            return result
        if any(item.get("tool_call_id") == "minecraft_builder_task" for item in messages):
            parent_called.set()
            return Response(success=True, content="施工已受理，等待真实进展", tool_calls=[])
        if any("minecraft_builder_task" in item.get("content", "") for item in messages if item["role"] == "user"):
            if stall_at_completion:
                # 模拟模型误以为设计结束后会自动施工；父循环应指出仍需行动，随后真的提交施工。
                if any("[任务尚需行动]" in item.get("content", "") for item in messages):
                    return response("minecraft_builder_task", {"task_id": changes[0].task_id, "action": "execute"})
                return Response(success=True, content="继续等自动施工", tool_calls=[])
            parent_called.set()
            return Response(success=True, content="收到设计，尚未施工", tool_calls=[])
        if not any(item.get("role") == "tool" for item in messages):
            return response("minecraft_builder_request", {"requirements": "建一座房子"})
        return Response(success=True, content="等待设计完成", tool_calls=[])

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=generate)
    parent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False)),
        llm_manager=llm,
        tool_registry=harness.registry,
        task_tracker=tracker,
        event_bus=bus,
    )
    bus.on(CoreEvents.TASK_CHANGED, observe, model_class=TaskChangedPayload)
    await parent.start()
    parent._mcp_client = harness.resources
    try:
        # 从真正的玩家指令启动父任务，后台通知只能继续它，不能凭空创建新的游戏任务。
        await parent.send_prompt("建一座房子")
        await asyncio.wait_for(parent_called.wait(), 2)
        assert len(changes) == 1 and changes[0].executor == "minecraft_builder"
        assert changes[0].snapshot["result"]["artifact_ref"] == "draft-1"
        assert parent.get_state_snapshot()["recent_reports"] == []
        assert parent._pending_task_count() == 1
        if stall_at_completion:
            assert len(harness.mod.operations("build")) == 1
            assert parent._actionable_task_ids() == set()
    finally:
        await parent.stop()
        await bus.cleanup()
