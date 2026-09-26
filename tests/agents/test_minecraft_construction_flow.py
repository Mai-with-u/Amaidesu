"""以固定模型回复验证四步建造的宿主调用链，不代替真实模型和游戏中的建造验收。"""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


class ConstructionProvider(BaseToolProvider):
    """只提供工艺、场地、规划和施工回执；任何额外的机器审阅或取材请求都使测试失败。"""

    name = "maicraft"
    category = "game"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=name, provider=self.name, description="建造流程测试", kind="sync")
            for name in ("perceive", "plan", "execute")
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.calls.append((invocation.tool_name, deepcopy(invocation.arguments)))
        if invocation.tool_name == "maicraft_perceive":
            if invocation.arguments["view"] == "knowledge":
                result = {
                    "content_loaded": True,
                    "resources": [{"content": {"process": "完整工艺" * 3300, "sequence": ["a", "b", "c"]}}],
                }
            else:
                assert invocation.arguments["view"] == "construction_site"
                result = {"snapshot_id": "site", "target": {"kind": "landmark", "label": "platform"}}
        elif invocation.tool_name == "maicraft_plan":
            assert invocation.arguments["goal"]["parameters"]["snapshot_id"] == "site"
            result = {"plan_id": "plan", "ready_to_execute": True, "validation": {"valid": True}}
        else:
            assert invocation.arguments["plan_id"] == "plan"
            result = {"accepted": True, "task_id": "build", "status": "running"}
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=result)


@pytest.mark.asyncio
async def test_process_site_blueprint_plan_execute_then_wait() -> None:
    """完整工艺进入上下文，场地编号进入蓝图计划，计划编号直接执行，随后交给宿主等待。"""
    provider = ConstructionProvider()
    registry = ToolRegistry()
    registry.register_provider(provider, visible_to={spec.full_name: ["minecraft"] for spec in provider.list_tools()})
    tracker = TaskTracker(registry, TaskLedger())
    blueprint = {"blocks": [{"offset": [0, 0, 0], "block_id": "minecraft:barrel"}]}
    requests = [
        ("maicraft_perceive", {"view": "knowledge", "resource_uri": "maicraft://knowledge/recipes/example/output"}),
        ("maicraft_perceive", {"view": "construction_site"}),
        (
            "maicraft_plan",
            {
                "goal": {
                    "ability": "maicraft:build_machine",
                    "outcome": "在当前平台建造",
                    "target": {"kind": "landmark", "label": "platform"},
                    "parameters": {"snapshot_id": "site", "blueprint": blueprint, "allow_modify": True},
                }
            },
        ),
        ("maicraft_execute", {"plan_id": "plan", "request_key": "build-once"}),
        ("minecraft_wait", {"reason": "施工已交给 Mod，等待完成或具体问题"}),
    ]
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            Response(success=True, tool_calls=[ToolCall(id=f"call_{index}", name=name, arguments=arguments)])
            for index, (name, arguments) in enumerate(requests)
        ]
    )
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(), llm_manager=llm, tool_registry=registry, task_tracker=tracker, event_bus=bus
    )
    agent._running = True
    agent._register_tools()
    agent.receive_prompt(content="在当前平台建造，已有配方资料，按四步流程执行", source="test")
    await agent._run_task_batch()
    assert provider.calls == requests[:4]
    assert llm.generate.await_count == 5 and agent._task_steps == 5
    assert agent._wait_requested and not agent._task_finished
    assert agent._context_compactor.checkpoints == 0
    resource = agent._messages[3]["content"]
    assert "完整工艺" * 3300 in resource and '"deferred": true' not in resource
    assert agent._current_task_context()["recent_results"][-2]["plan_id"] == "plan"
    # 真正送给决策模型的提示同时保留库存功能边界和批次回执，不把角色解释留在宿主外部说明中。
    policy = agent._messages[0]["content"]
    assert "外部材料 IN" in policy and "内部缓存" in policy and "external_inputs" in policy
    assert "maicraft:use_item" in policy and "ingredient_item_id" in policy
    assert "completed_output_count" in policy and "remaining_output_count" in policy
