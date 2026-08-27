"""
ToolRegistry 单元测试（Wave 3 / §1.5）

覆盖：
- 注册 / 去重（先注册保留）
- 已知工具 invoke 返回正确 result
- **未知工具 invoke 返回失败 result，不抛异常**（核心契约）
- 实现异常 → 失败 result，不抛
- Provider 整体注册
- to_llm_definitions 转换
- 异步工具 kind="async" result_event 默认 ``tool.result.<name>``
- @tool 装饰器
"""

from __future__ import annotations

from typing import List

import pytest

from src.modules.tools import (
    ToolInvocation,
    ToolProvider,
    ToolRegistry,
    ToolSpec,
    tool,
)
from src.modules.tools.models import Provider, ToolExecutionResult


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


# =============================================================================
# 核心契约：未知工具不抛异常，返回失败 result
# =============================================================================


async def test_invoke_unknown_tool_returns_failure_result_not_raises(
    registry: ToolRegistry,
) -> None:
    """§1.5 核心契约：未知工具 invoke 必须返回失败 ToolExecutionResult，不抛异常。"""
    inv = ToolInvocation(tool_name="definitely_not_a_tool", source="test")
    # 关键：不应抛——直接 await 即可
    result = await registry.invoke(inv)
    assert isinstance(result, ToolExecutionResult)
    assert result.tool_name == "definitely_not_a_tool"
    assert result.success is False
    assert "未知" in result.error_message or "未找到" in result.error_message


async def test_invoke_unknown_tool_chain_does_not_propagate(registry: ToolRegistry) -> None:
    """连续多次调用未知工具——一次都不抛。"""
    invocations = [ToolInvocation(tool_name=f"missing_{i}") for i in range(5)]
    results = await registry.invoke_many(invocations)
    assert len(results) == 5
    for r in results:
        assert r.success is False


# =============================================================================
# 已知工具的正常路径
# =============================================================================


async def test_invoke_known_tool_returns_correct_result(registry: ToolRegistry) -> None:
    """已知工具 invoke 返回正确 result。"""

    async def _echo(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name="echo",
            success=True,
            content=str(inv.arguments.get("msg", "")),
        )

    spec = ToolSpec(name="echo", description="echo back", kind="sync")
    assert registry.register(spec, _echo) is True

    res = await registry.invoke(ToolInvocation(tool_name="echo", arguments={"msg": "hi"}))
    assert res.success is True
    assert res.content == "hi"


async def test_register_dedup_keeps_first_registration(registry: ToolRegistry) -> None:
    """重复注册——保留先注册的；后注册返回 False。"""

    async def impl_a(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="dup", success=True, content="A")

    async def impl_b(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="dup", success=True, content="B")

    spec = ToolSpec(name="dup", description="dedup", kind="sync")
    assert registry.register(spec, impl_a) is True
    # 重复：返回 False，保留先注册
    assert registry.register(spec, impl_b) is False

    res = await registry.invoke(ToolInvocation(tool_name="dup"))
    assert res.success is True
    assert res.content == "A", "后注册的实现不应替换先注册的"


# =============================================================================
# 实现异常 → 失败 result，不抛
# =============================================================================


async def test_invoke_implementation_exception_isolation(registry: ToolRegistry) -> None:
    """实现抛出异常 → 调用方收到失败 result，不抛异常。"""

    async def _bad(inv: ToolInvocation) -> ToolExecutionResult:
        raise RuntimeError("boom!")

    spec = ToolSpec(name="bad", description="raise", kind="sync")
    registry.register(spec, _bad)

    res = await registry.invoke(ToolInvocation(tool_name="bad"))
    assert res.success is False
    assert "boom!" in res.error_message


# =============================================================================
# Provider 整体注册
# =============================================================================


class _SampleProvider(ToolProvider):
    @property
    def name(self) -> str:
        return "SampleProvider"

    def __init__(self) -> None:
        self._specs: List[ToolSpec] = [
            ToolSpec(name="p_a", description="a", kind="sync", provider="game"),
            ToolSpec(name="p_b", description="b", kind="sync", provider="game"),
        ]

    def list_tools(self):
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if invocation.tool_name == "p_a":
            return ToolExecutionResult(tool_name="p_a", success=True, content="from_a")
        if invocation.tool_name == "p_b":
            return ToolExecutionResult(tool_name="p_b", success=True, content="from_b")
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=False,
            error_message="not_in_provider",
        )


async def test_provider_registration_and_tools_listed(registry: ToolRegistry) -> None:
    """注册 Provider 后，list_tools 包含 Provider 暴露的全部工具。"""
    provider = _SampleProvider()
    new_count = registry.register_provider(provider)
    assert new_count == 2
    tools = registry.list_tools()
    names = {t.name for t in tools}
    assert names == {"p_a", "p_b"}
    # provider 过滤
    game_tools = registry.list_tools(provider="game")
    assert len(game_tools) == 2
    builtin_tools = registry.list_tools(provider="builtin")
    assert len(builtin_tools) == 0


async def test_register_provider_is_idempotent(registry: ToolRegistry) -> None:
    provider = _SampleProvider()
    assert registry.register_provider(provider) == 2
    assert registry.register_provider(provider) == 0, "同一 Provider 重复注册返 0"


# =============================================================================
# Async tool：result_event 默认 tool.result.<name>
# =============================================================================


async def test_async_tool_default_result_event(registry: ToolRegistry) -> None:
    """异步工具默认 result_event = ``tool.result.<name>``。"""
    spec = ToolSpec(name="speak", description="async", kind="async")
    assert spec.result_event == "", "未显式指定时 result_event 应为空字符串"
    assert spec.resolve_result_event() == "tool.result.speak"


async def test_async_tool_custom_result_event(registry: ToolRegistry) -> None:
    """异步工具可定制 result_event（如 set_goal_feedback）。"""
    spec = ToolSpec(
        name="set_goal",
        description="set goal",
        kind="async",
        result_event="tool.result.set_goal_feedback",
    )
    assert spec.resolve_result_event() == "tool.result.set_goal_feedback"


# =============================================================================
# to_llm_definitions 转换
# =============================================================================


async def test_to_llm_definitions_shape(registry: ToolRegistry) -> None:
    """to_llm_definitions 返回 LLM 视角定义列表（OpenAI function calling 形态）。"""

    async def _t(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=inv.tool_name, success=True)

    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }
    registry.register(
        ToolSpec(name="t", description="D", kind="sync", parameters_schema=schema),
        _t,
    )
    defs = registry.to_llm_definitions()
    assert len(defs) == 1
    assert defs[0]["name"] == "t"
    assert defs[0]["description"] == "D"
    assert "parameters" in defs[0]


# =============================================================================
# @tool 装饰器
# =============================================================================


def test_tool_decorator_pending_mode_does_not_touch_default_registry():
    """@tool 装饰器无 ``registry=`` 时只入 pending 表，不污染默认 registry。

    验证 pending 模式：装饰器**不**触发 ``default_tool_registry()``。
    """
    from src.modules.tools.decorator import _clear_pending, _pending_count
    from src.modules.tools.registry import (
        default_tool_registry,
        set_default_registry,
    )

    _clear_pending()
    # 隔离默认 registry：用 sentinel 检测是否被污染
    saved = default_tool_registry()
    sentinel = ToolRegistry()
    set_default_registry(sentinel)
    try:
        @tool(description="hi")
        async def hi(invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name="hi", success=True, content="hello")

        spec = hi.tool_spec  # type: ignore[attr-defined]
        assert spec.name == "hi"
        assert spec.description == "hi"
        # pending 模式不应触碰默认 registry
        assert _pending_count() == 1
        assert sentinel.has("hi") is False, "pending 模式不应写入默认单例"
    finally:
        set_default_registry(saved)
        _clear_pending()


def test_tool_decorator_explicit_registry_mode():
    """@tool 装饰器传 ``registry=`` 时立即注册到该 registry（测试兼容路径）。"""
    reg = ToolRegistry()

    @tool(description="hi", registry=reg)
    async def hi(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="hi", success=True, content="hello")

    spec = hi.tool_spec  # type: ignore[attr-defined]
    assert spec.name == "hi"
    assert spec.description == "hi"
    assert reg.has("hi")


# =============================================================================
# 通用：has / get / __len__
# =============================================================================


def test_registry_collections_protocol(registry: ToolRegistry) -> None:
    async def _t(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="x", success=True)

    registry.register(ToolSpec(name="x", description="d", kind="sync"), _t)
    assert "x" in registry
    assert "not_in" not in registry
    assert len(registry) == 1
    spec = registry.get("x")
    assert spec is not None
    assert spec.name == "x"
