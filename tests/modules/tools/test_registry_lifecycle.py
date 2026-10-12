"""
ToolRegistry.start_providers / stop_providers 生命周期批量启停单测。

覆盖五条核心路径：
- 连接型 Provider（覆写 setup）被启动并计入报告
- 自管理生命周期 Provider（manages_own_lifecycle=True）被跳过
- 单 Provider setup 失败不阻断其余（失败入报告，其余照常启动）
- start 幂等：重复调用返回空报告且不再触发 setup
- stop 对称收尾：已启动者 cleanup、自管理者跳过；未启动时安全短路
"""

from __future__ import annotations

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry


class _LifecycleProvider(BaseToolProvider):
    """连接型 Provider 替身：覆写 setup/cleanup 并记录调用次数。"""

    def __init__(self, name: str, *, setup_error: Exception | None = None) -> None:
        self._name = name
        self._setup_error = setup_error
        self.setup_calls = 0
        self.cleanup_calls = 0

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self):
        return [ToolSpec(name="t", description="t", kind="sync", provider=self._name)]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def setup(self) -> None:
        self.setup_calls += 1
        if self._setup_error is not None:
            raise self._setup_error

    async def cleanup(self) -> None:
        self.cleanup_calls += 1


class _SelfManagedProvider(_LifecycleProvider):
    """自管理生命周期替身（MCP 形态）：批量启停应跳过。"""

    manages_own_lifecycle = True


def _register_lifecycle(registry: ToolRegistry, provider: BaseToolProvider) -> None:
    count = registry.register_provider(provider)
    assert count == 1, f"替身工具注册失败: {provider.name} (count={count})"


async def test_start_starts_connection_providers() -> None:
    registry = ToolRegistry()
    provider = _LifecycleProvider("conn")
    _register_lifecycle(registry, provider)

    report = await registry.start_providers()

    assert report == {"conn": True}
    assert provider.setup_calls == 1


async def test_start_skips_self_managed_providers() -> None:
    registry = ToolRegistry()
    provider = _SelfManagedProvider("self_managed")
    _register_lifecycle(registry, provider)

    report = await registry.start_providers()

    assert report == {}
    assert provider.setup_calls == 0


async def test_start_isolates_single_failure() -> None:
    registry = ToolRegistry()
    bad = _LifecycleProvider("bad", setup_error=RuntimeError("连接失败"))
    good = _LifecycleProvider("good")
    _register_lifecycle(registry, bad)
    _register_lifecycle(registry, good)

    report = await registry.start_providers()

    assert report == {"bad": False, "good": True}
    assert bad.setup_calls == 1
    assert good.setup_calls == 1


async def test_start_is_idempotent() -> None:
    registry = ToolRegistry()
    provider = _LifecycleProvider("conn")
    _register_lifecycle(registry, provider)

    assert await registry.start_providers() == {"conn": True}
    assert await registry.start_providers() == {}
    assert provider.setup_calls == 1


async def test_stop_cleans_up_started_providers_only() -> None:
    registry = ToolRegistry()
    conn = _LifecycleProvider("conn")
    self_managed = _SelfManagedProvider("self_managed")
    _register_lifecycle(registry, conn)
    _register_lifecycle(registry, self_managed)

    # 未启动先停：安全短路，无任何 cleanup
    await registry.stop_providers()
    assert conn.cleanup_calls == 0

    await registry.start_providers()
    await registry.stop_providers()

    assert conn.cleanup_calls == 1
    assert self_managed.cleanup_calls == 0
