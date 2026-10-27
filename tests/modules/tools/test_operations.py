"""ToolOperations 运营面：提供者目录视图、健康快照委托、标注投影、重连错误报告"""

from __future__ import annotations

from typing import List

import pytest

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.operations import ToolOperations
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry


async def _ok(inv: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(tool_name=inv.tool_name, success=True, content="ok")


class _SampleProvider(BaseToolProvider):
    category = "game"

    def __init__(self, name: str = "sample", tool_names: List[str] | None = None) -> None:
        self._name = name
        self.tool_names: List[str] = tool_names if tool_names is not None else ["a", "b"]

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self):
        return [ToolSpec(name=n, description=n, kind="sync", provider=self._name) for n in self.tool_names]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await _ok(invocation)


def _ops(registry: ToolRegistry) -> ToolOperations:
    return ToolOperations(registry)


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


def test_list_providers_reports_directory_with_counts(registry: ToolRegistry) -> None:
    provider = _SampleProvider()
    registry.register_provider(provider)
    (card,) = _ops(registry).list_providers()
    assert (card["name"], card["category"]) == ("sample", "game")
    assert (card["tool_count"], card["disabled_count"]) == (2, 0)
    assert card["supports_reconnect"] is False and card["last_error"] == "" and card["switch"] is None


def test_list_providers_keeps_degraded_zero_tool_provider_visible(registry: ToolRegistry) -> None:
    """降级登记（连接失败 0 工具）的 Provider 靠目录视图在工具页保持可见可重连。"""
    degraded = _SampleProvider(name="degraded", tool_names=[])
    registry.register_provider(degraded)
    (card,) = _ops(registry).list_providers()
    assert card["tool_count"] == 0 and card["name"] == "degraded"


def test_health_snapshot_and_annotations_delegate(registry: ToolRegistry) -> None:
    ops = _ops(registry)
    assert ops.tool_health_snapshot() == {}  # 无失败历史 → 空快照
    registry.register_provider(_SampleProvider(), visible_to={"sample_a": ["minecraft"]})
    assert ops.scoped_owner_of("sample_a") == "minecraft"  # 单个非默认 Agent = 专属归属
    assert ops.scoped_owner_of("sample_b") == ""  # 未声明落默认名单，无归属
    assert ops.scoped_owner_of("ghost") == ""  # 未注册工具
    assert ops.provider_supports_reconnect("sample_a") is False  # 未覆写 connect 不支持重连


async def test_reconnect_provider_error_reports(registry: ToolRegistry) -> None:
    ops = _ops(registry)
    assert await ops.reconnect_provider("ghost") == {"ok": False, "error": "未注册 Provider: ghost"}
    registry.register_provider(_SampleProvider())
    report = await ops.reconnect_provider("sample")
    assert report["ok"] is False and "不支持手动重连" in report["error"]
