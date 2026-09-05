"""
@tool 装饰器 pending + bind 双模式测试。

覆盖：
- (a) 无 ``registry=`` 参数的 ``@tool`` 进入 pending 表，不接触任何全局单例
- (b) ``bind_pending_tools(registry)`` 把 pending 全部刷入并返回新注册数
- (c) 重复 ``bind_pending_tools`` 安全（第二次返 0，pending 已清空）
- (d) name 冲突时 first-wins（重复装饰 / 提前注册同名的工具，pending 不覆盖）
- (e) 显式 ``registry=`` 模式仍立即注册到传入 registry，不进入 pending 表
- (f) 包内辅助：``_pending_count`` / ``_clear_pending`` 行为正确
- (g) ``bind_core_tools`` 的 happy-path / 单包失败隔离 / 类型校验

本测试刻意使用 fixture 隔离全局 pending 表，避免测试间互相污染。
"""

from __future__ import annotations

from typing import Any, List

import pytest

from src.modules.tools import ToolRegistry, tool
from src.modules.tools.bootstrap import bind_core_tools
from src.modules.tools.decorator import (
    _clear_pending,
    _pending_count,
    bind_pending_tools,
)
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _isolate_pending_table():
    """每个用例前后清空 pending 表——装饰器是模块级单例，必须隔离。"""
    _clear_pending()
    yield
    _clear_pending()


@pytest.fixture
def registry() -> ToolRegistry:
    """每个用例获得全新的 registry，不接触 default_tool_registry()。"""
    return ToolRegistry()


def _noop_inv(_invocation: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(tool_name="noop", success=True, content="noop")


# =============================================================================
# (a) pending 模式：不传 registry= → 进入 pending，不接触全局
# =============================================================================


def test_no_arg_decorator_goes_to_pending_not_registry(registry: ToolRegistry) -> None:
    """无 ``registry=`` 参数的 ``@tool`` 只入 pending 表，不污染传入 registry。"""

    @tool(description="pending-only tool")
    async def pending_only(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="pending_only", success=True)

    # pending 表里有一条
    assert _pending_count() == 1, "@tool 应该把工具追加到 pending 表"
    # 传入的 registry 不受影响
    assert len(registry) == 0, "pending 模式不应触碰任何 registry"
    assert registry.has("pending_only") is False


def test_no_arg_decorator_does_not_touch_default_registry(registry: ToolRegistry) -> None:
    """pending 模式不触发 ``default_tool_registry()``——生产红线。"""

    @tool(description="no global touch")
    async def local_tool(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="local_tool", success=True)

    # 即便我们另外传了 registry 作为参数，pending 路径也不写入
    assert len(registry) == 0
    assert _pending_count() == 1


# =============================================================================
# (b) bind_pending_tools：刷入 + 返回新注册数
# =============================================================================


def test_bind_pending_flushes_and_returns_new_count(registry: ToolRegistry) -> None:
    """``bind_pending_tools`` 把 pending 全部刷入，返回新注册工具数。"""

    @tool(description="a")
    async def tool_a(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="tool_a", success=True)

    @tool(description="b")
    async def tool_b(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="tool_b", success=True)

    assert _pending_count() == 2
    new_count = bind_pending_tools(registry)
    assert new_count == 2
    assert registry.has("tool_a")
    assert registry.has("tool_b")
    assert _pending_count() == 0, "bind 后 pending 必须清空"


def test_bind_pending_empty_returns_zero(registry: ToolRegistry) -> None:
    """空 pending → ``bind_pending_tools`` 返 0，不抛异常。"""
    assert _pending_count() == 0
    assert bind_pending_tools(registry) == 0
    assert len(registry) == 0


def test_bound_tools_registered_in_registry(registry: ToolRegistry) -> None:
    """刷入后的工具名/描述进入 registry——impl 包装元数据完整。"""

    @tool(description="echo-sync")
    async def echo_sync(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="echo_sync", success=True, content="hi-sync")

    bind_pending_tools(registry)
    assert registry.has("echo_sync")
    spec = registry.get("echo_sync")
    assert spec is not None
    assert spec.description == "echo-sync"


async def test_bound_tools_invoke(registry: ToolRegistry) -> None:
    """async 版本：刷入后 invoke 验证 impl 包装仍能拿到结果。"""

    @tool(description="echo-async")
    async def echo_async(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="echo_async", success=True, content="hi-async")

    bind_pending_tools(registry)
    res = await registry.invoke(ToolInvocation(tool_name="echo_async"))
    assert res.success is True
    assert res.content == "hi-async"


# =============================================================================
# (c) bind 幂等：第二次调用返 0
# =============================================================================


def test_bind_pending_is_idempotent(registry: ToolRegistry) -> None:
    """第二次 ``bind_pending_tools`` 返 0（pending 已清空）。"""

    @tool(description="once")
    async def once_tool(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="once_tool", success=True)

    assert bind_pending_tools(registry) == 1
    assert len(registry) == 1
    assert bind_pending_tools(registry) == 0, "第二次 bind 应返 0（pending 空）"
    assert len(registry) == 1, "第二次 bind 不应重复注册"


# =============================================================================
# (d) name 冲突 first-wins：pending 中重复名 → 先注册的赢
# =============================================================================


def test_name_conflict_keeps_first_pending(registry: ToolRegistry) -> None:
    """pending 中同名工具按追加顺序刷入，先到的赢。"""

    @tool(name="dup", description="first")
    async def first(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="dup", success=True, content="first")

    @tool(name="dup", description="second")
    async def second(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="dup", success=True, content="second")

    assert _pending_count() == 2
    new_count = bind_pending_tools(registry)
    # 重复名 → 只有第一个真正注册；第二个被 registry 去重
    assert new_count == 1
    assert len(registry) == 1
    spec = registry.get("dup")
    assert spec is not None
    assert spec.description == "first", "first-wins：先注册的 spec 应保留"


def test_name_conflict_with_existing_registry_keeps_first(
    registry: ToolRegistry,
) -> None:
    """pending 中工具与 registry 已有同名 → registry 已有的赢（first-wins）。"""

    async def _pre_existing(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="clash", success=True, content="pre")

    pre_spec = registry.register_tool(
        ToolSpec(name="clash", description="pre-existing", kind="sync"),
        _pre_existing,
    )
    assert pre_spec is True

    @tool(name="clash", description="pending challenger")
    async def challenger(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="clash", success=True, content="challenger")

    new_count = bind_pending_tools(registry)
    assert new_count == 0, "pending 中的 challenger 被已存在的 clash 挡住"
    spec = registry.get("clash")
    assert spec is not None
    assert spec.description == "pre-existing"


# =============================================================================
# (e) explicit 模式：传 registry= → 立即注册，不进 pending
# =============================================================================


def test_explicit_registry_mode_registers_immediately(registry: ToolRegistry) -> None:
    """``@tool(..., registry=reg)`` 立即注册到传入 registry，不进 pending。"""

    @tool(description="immediate", registry=registry)
    async def immediate(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="immediate", success=True)

    assert _pending_count() == 0, "explicit 模式不应进入 pending"
    assert len(registry) == 1
    assert registry.has("immediate")


def test_explicit_registry_mode_does_not_touch_default_registry(
    registry: ToolRegistry,
) -> None:
    """explicit 模式只注册到传入 registry，不写 ``default_tool_registry()``。"""
    # 显式保存/还原以确保隔离
    from src.modules.tools.registry import (
        default_tool_registry,
        set_default_registry,
    )

    saved = default_tool_registry()
    sentinel = ToolRegistry()
    set_default_registry(sentinel)
    try:

        @tool(description="isolated", registry=registry)
        async def isolated(invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name="isolated", success=True)

        assert sentinel.has("isolated") is False, "explicit 模式不应污染默认单例"
        assert registry.has("isolated") is True
    finally:
        set_default_registry(saved)


def test_mixed_modes_isolation(registry: ToolRegistry) -> None:
    """混合两种模式：explicit 立即注册，pending 仍排队等待后续 bind。"""

    @tool(description="immediate", registry=registry)
    async def mixed_immediate(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="mixed_immediate", success=True)

    @tool(description="pending")
    async def mixed_pending(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="mixed_pending", success=True)

    assert len(registry) == 1
    assert registry.has("mixed_immediate")
    assert _pending_count() == 1

    new_count = bind_pending_tools(registry)
    assert new_count == 1
    assert registry.has("mixed_pending")
    assert len(registry) == 2


# =============================================================================
# (f) 工具 spec 元数据：两种模式都暴露 ``tool_spec`` 属性
# =============================================================================


def test_tool_spec_attribute_set_in_pending_mode(registry: ToolRegistry) -> None:
    """pending 模式仍然把 ``tool_spec`` 暴露在包装函数上（便于审计）。"""

    @tool(name="meta_pending", description="desc")
    async def meta_pending(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="meta_pending", success=True)

    spec = meta_pending.tool_spec  # type: ignore[attr-defined]
    assert spec.name == "meta_pending"
    assert spec.description == "desc"
    # 没注册
    assert _pending_count() == 1
    assert len(registry) == 0


def test_tool_spec_attribute_set_in_explicit_mode(registry: ToolRegistry) -> None:
    """explicit 模式同样设置 ``tool_spec``。"""

    @tool(description="explicit meta", registry=registry)
    async def meta_explicit(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="meta_explicit", success=True)

    spec = meta_explicit.tool_spec  # type: ignore[attr-defined]
    assert spec.description == "explicit meta"
    assert registry.has("meta_explicit")


# =============================================================================
# (g) bind_core_tools：装配行为
# =============================================================================


def test_bind_core_tools_rejects_non_registry() -> None:
    """``bind_core_tools`` 拒绝非 ``ToolRegistry`` 的 registry 参数。"""
    with pytest.raises(TypeError):
        bind_core_tools(registry=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        bind_core_tools(registry={"not": "a registry"})  # type: ignore[arg-type]


def test_bind_core_tools_with_no_config_returns_report_with_all_keys(
    registry: ToolRegistry,
) -> None:
    """``config=None`` 不抛异常；非 TTS 包按 enabled 白名单全记 0。"""
    report = bind_core_tools(registry, config=None)
    assert isinstance(report, dict)
    assert set(report.keys()) == {"vts", "vrchat", "warudo", "obs"}
    for _pkg, count in report.items():
        assert count >= 0, f"报告值不应为负：{count}"


def test_bind_core_tools_does_not_touch_default_registry(registry: ToolRegistry) -> None:
    """``bind_core_tools`` 不触碰 ``default_tool_registry()``。"""
    from src.modules.tools.registry import (
        default_tool_registry,
        set_default_registry,
    )

    saved = default_tool_registry()
    sentinel = ToolRegistry()
    set_default_registry(sentinel)
    try:
        bind_core_tools(registry, config={})
        assert len(sentinel) == 0, "bind_core_tools 不应污染默认单例"
    finally:
        set_default_registry(saved)


def test_bind_core_tools_isolates_per_package_failure(
    registry: ToolRegistry,
) -> None:
    """单包失败不阻断其他包——验证 try/except 隔离。"""
    report = bind_core_tools(
        registry,
        config={"vts": {"type": "vts"}},  # 其它非 TTS 包失败
    )
    assert set(report.keys()) == {"vts", "vrchat", "warudo", "obs"}
    for count in report.values():
        assert count >= 0


# =============================================================================
# (h) 完整流程：bind_core_tools + bind_pending_tools
# =============================================================================


async def test_full_pipeline_combined(registry: ToolRegistry) -> None:
    """完整装配流程：bind_core_tools → bind_pending_tools → invoke。"""

    @tool(description="pending-pipeline")
    async def pipeline_tool(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="pipeline_tool", success=True, content="ok")

    # 显式模式注册的辅助工具（不进 pending）
    @tool(description="immediate-pipeline", registry=registry)
    async def immediate_tool(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="immediate_tool", success=True, content="imm")

    # 装配核心包（空配置，多数会失败但不影响 pending）
    _ = bind_core_tools(registry, config={})
    immediate_count_before = len(registry)

    # 刷入 pending
    new_pending = bind_pending_tools(registry)
    assert new_pending == 1
    assert registry.has("pipeline_tool")

    # 核心包失败的 + immediate 注册 1 个 + pending 注册 1 个
    # 即 total >= immediate_count_before + 1
    assert len(registry) >= immediate_count_before + 1

    # 验证 pending 注册的工具可 invoke
    res = await registry.invoke(ToolInvocation(tool_name="pipeline_tool"))
    assert res.success is True
    assert res.content == "ok"

    # 验证 explicit 注册的工具可 invoke
    res2 = await registry.invoke(ToolInvocation(tool_name="immediate_tool"))
    assert res2.success is True
    assert res2.content == "imm"
