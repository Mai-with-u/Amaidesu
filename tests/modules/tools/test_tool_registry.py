"""
ToolRegistry 单元测试。

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

import asyncio
from typing import List

import pytest

from src.modules.tools import (
    ToolInvocation,
    ToolProvider,
    ToolRegistry,
    ToolSpec,
    tool,
)
from src.modules.tools.models import ToolExecutionResult


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
    """核心契约：未知工具 invoke 必须返回失败 ToolExecutionResult，不抛异常。"""
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

    # 自声明归属分类（provider 名 → 分类 正交）
    category = "game"

    def __init__(self) -> None:
        self._specs: List[ToolSpec] = [
            # 裸名（不带 provider 前缀）：register_provider 会自动补 "game_" 前缀
            ToolSpec(name="p_a", description="a", kind="sync", provider="game"),
            ToolSpec(name="p_b", description="b", kind="sync", provider="game"),
        ]

    def list_tools(self):
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        # 注册名统一为 <provider>_<name>；本地分发剥前缀归一
        local = invocation.tool_name.removeprefix("game_")
        if local == "p_a":
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True, content="from_a")
        if local == "p_b":
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True, content="from_b")
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=False,
            error_message="not_in_provider",
        )


async def test_provider_registration_auto_prefixes_bare_names(registry: ToolRegistry) -> None:
    """裸名工具注册进 registry 时自动补 ``<provider>_`` 前缀（无条件）。"""
    provider = _SampleProvider()
    new_count = registry.register_provider(provider)
    assert new_count == 2
    tools = registry.list_tools()
    names = {t.name for t in tools}
    assert names == {"game_p_a", "game_p_b"}
    # provider 过滤（按提供者名）
    game_tools = registry.list_tools(provider="game")
    assert len(game_tools) == 2
    builtin_tools = registry.list_tools(provider="builtin")
    assert len(builtin_tools) == 0
    # 前缀改写不得污染 provider 返回的原 spec（list_tools 仍返回裸名）
    raw_names = {s.name for s in provider.list_tools()}
    assert raw_names == {"p_a", "p_b"}


async def test_provider_category_recorded_and_queryable(registry: ToolRegistry) -> None:
    """注册时记录 provider 自声明的分类；list_categories / category 过滤可用。"""
    provider = _SampleProvider()
    registry.register_provider(provider)
    assert registry.list_categories() == ["game"]
    cat_tools = registry.list_tools(category="game")
    assert {t.name for t in cat_tools} == {"game_p_a", "game_p_b"}
    assert registry.list_tools(category="avatar") == []
    # 未声明分类的 provider 不产生空分类
    assert registry.list_categories() == ["game"]


async def test_provider_registered_name_invocable(registry: ToolRegistry) -> None:
    """provider 分发剥前缀归一后，按注册名 invoke 可命中。"""
    provider = _SampleProvider()
    registry.register_provider(provider)
    res = await registry.invoke(ToolInvocation(tool_name="game_p_a"))
    assert res.success is True
    assert res.content == "from_a"
    assert res.tool_name == "game_p_a"


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


# =============================================================================
# tool.result.<name> 结果事件广播
# =============================================================================


async def test_invoke_emits_tool_result_event_when_event_bus_attached() -> None:
    """挂载 EventBus 后，调用完成 emit tool.result.<name>（success 路径）。"""
    from src.modules.events.event_bus import EventBus
    from src.modules.events.payloads.tool_result import ToolResultPayload

    bus = EventBus(enable_stats=False)
    received: list[tuple[str, ToolResultPayload]] = []

    async def _on_result(event_name: str, payload: ToolResultPayload, source: str) -> None:
        received.append((event_name, payload))

    bus.on("tool.result.#", _on_result, ToolResultPayload)

    registry = ToolRegistry(event_bus=bus)

    async def _ok(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=inv.tool_name, success=True, content="done")

    registry.register(ToolSpec(name="my_tool", description="d", kind="sync"), _ok)
    res = await registry.invoke(ToolInvocation(tool_name="my_tool", source="test"))
    assert res.success is True
    await asyncio.sleep(0.01)  # emit 为 fire-and-forget，让派发任务跑完
    assert len(received) == 1
    event_name, payload = received[0]
    assert event_name == "tool.result.my_tool"
    assert payload.tool_name == "my_tool"
    assert payload.status == "success"
    assert payload.result == {"content": "done"}


async def test_invoke_emits_tool_result_error_event(registry: ToolRegistry) -> None:
    """实现异常路径同样广播（status=error），且不反噬调用结果。"""
    from src.modules.events.event_bus import EventBus
    from src.modules.events.payloads.tool_result import ToolResultPayload

    bus = EventBus(enable_stats=False)
    received: list[ToolResultPayload] = []

    async def _on_result(event_name: str, payload: ToolResultPayload, source: str) -> None:
        received.append(payload)

    bus.on("tool.result.#", _on_result, ToolResultPayload)
    registry._event_bus = bus

    async def _bad(inv: ToolInvocation) -> ToolExecutionResult:
        raise RuntimeError("boom!")

    registry.register(ToolSpec(name="bad_tool", description="d", kind="sync"), _bad)
    res = await registry.invoke(ToolInvocation(tool_name="bad_tool"))
    assert res.success is False
    await asyncio.sleep(0.01)  # emit 为 fire-and-forget，让派发任务跑完
    assert len(received) == 1
    assert received[0].status == "error"
    assert "boom!" in received[0].error_message


async def test_invoke_without_event_bus_skips_emit(registry: ToolRegistry) -> None:
    """未挂载 EventBus（默认）不广播——行为与旧版一致。"""
    registry._event_bus = None

    async def _ok(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=inv.tool_name, success=True)

    registry.register(ToolSpec(name="quiet", description="d", kind="sync"), _ok)
    res = await registry.invoke(ToolInvocation(tool_name="quiet"))
    assert res.success is True  # 不抛即通过
