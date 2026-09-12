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
- 简单工具路径：``make_provider_from_specs`` 构造 + 注册
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
)
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import BaseToolProvider, make_provider_from_specs


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
        return "game"

    # 自声明归属分类（provider 名 → 分类 正交）
    category = "game"

    def __init__(self) -> None:
        # 声明名是裸名；对外全名 = <provider>_<工具名>（ToolSpec.full_name 派生）
        self._specs: List[ToolSpec] = [
            ToolSpec(name="p_a", description="a", kind="sync", provider="game"),
            ToolSpec(name="p_b", description="b", kind="sync", provider="game"),
        ]

    def list_tools(self):
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        # 调用方使用的就是派生全名，等值对照分发
        name = invocation.tool_name
        if name == "game_p_a":
            return ToolExecutionResult(tool_name=name, success=True, content="from_a")
        if name == "game_p_b":
            return ToolExecutionResult(tool_name=name, success=True, content="from_b")
        return ToolExecutionResult(
            tool_name=name,
            success=False,
            error_message="not_in_provider",
        )


async def test_provider_registration_keys_are_derived_full_names(registry: ToolRegistry) -> None:
    """注册键 = 派生全名；spec 原样存储（声明名保持裸名，无改名拷贝）。"""
    provider = _SampleProvider()
    new_count = registry.register_provider(provider)
    assert new_count == 2
    tools = registry.list_tools()
    full_names = {t.full_name for t in tools}
    assert full_names == {"game_p_a", "game_p_b"}
    # spec 原样存储：声明名仍是裸名（list_tools 返回的就是 provider 声明的 spec）
    bare_names = {t.name for t in tools}
    assert bare_names == {"p_a", "p_b"}
    # provider 过滤（按提供者名）
    game_tools = registry.list_tools(provider="game")
    assert len(game_tools) == 2
    builtin_tools = registry.list_tools(provider="builtin")
    assert len(builtin_tools) == 0


async def test_provider_name_spec_provider_mismatch_rejected(registry: ToolRegistry) -> None:
    """提供者单名校验（fail-fast）：provider.name 与 spec.provider 不同值 → 抛错。"""

    class MismatchedProvider(_SampleProvider):
        @property
        def name(self) -> str:
            return "other"

    with pytest.raises(ValueError) as exc_info:
        registry.register_provider(MismatchedProvider())
    assert "other" in str(exc_info.value)
    assert "game" in str(exc_info.value)


async def test_provider_category_recorded_and_queryable(registry: ToolRegistry) -> None:
    """注册时记录 provider 自声明的分类；list_categories / category 过滤可用。"""
    provider = _SampleProvider()
    registry.register_provider(provider)
    assert registry.list_categories() == ["game"]
    cat_tools = registry.list_tools(category="game")
    assert {t.full_name for t in cat_tools} == {"game_p_a", "game_p_b"}
    assert registry.list_tools(category="avatar") == []
    # 未声明分类的 provider 不产生空分类
    assert registry.list_categories() == ["game"]


async def test_provider_registered_name_invocable(registry: ToolRegistry) -> None:
    """按派生全名 invoke 可命中（调用与索引共用全名）。"""
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
# 简单工具路径：make_provider_from_specs
# =============================================================================


def _build_hello_provider() -> ToolProvider:
    """构造一个 spec + 函数 的固定 Provider（简单工具正典形态）。"""

    async def hi(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, content="hello")

    return make_provider_from_specs(
        "simple",
        [(ToolSpec(name="hi", description="hi", provider="simple"), hi)],
    )


def test_make_provider_from_specs_registers_and_invokes():
    """spec + 函数经 make_provider_from_specs 构造后可注册、按注册名可调用。"""
    reg = ToolRegistry()
    provider = _build_hello_provider()
    assert reg.register_provider(provider) == 1
    assert reg.has("simple_hi")


async def test_make_provider_from_specs_invoke_by_registered_name():
    """按注册名（``<provider>_<name>``）invoke 命中实现函数。"""
    reg = ToolRegistry()
    reg.register_provider(_build_hello_provider())
    res = await reg.invoke(ToolInvocation(tool_name="simple_hi"))
    assert res.success is True
    assert res.content == "hello"
    assert res.tool_name == "simple_hi"


def test_make_provider_from_specs_no_side_effects_on_default_registry():
    """构造与注册只影响显式传入的 registry，不触碰默认单例。"""
    from src.modules.tools.registry import (
        default_tool_registry,
        set_default_registry,
    )

    saved = default_tool_registry()
    sentinel = ToolRegistry()
    set_default_registry(sentinel)
    try:
        reg = ToolRegistry()
        reg.register_provider(_build_hello_provider())
        assert reg.has("simple_hi")
        assert sentinel.has("simple_hi") is False, "注册不应写入默认单例"
    finally:
        set_default_registry(saved)


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


async def test_invoke_emits_tool_result_with_arguments_echo(registry: ToolRegistry) -> None:
    """tool.result 事件携带调用方入参（arguments 透传），供 WebUI 对齐展示。"""
    from src.modules.events.event_bus import EventBus
    from src.modules.events.payloads.tool_result import ToolResultPayload

    bus = EventBus(enable_stats=False)
    received: list[ToolResultPayload] = []

    async def _on_result(event_name: str, payload: ToolResultPayload, source: str) -> None:
        received.append(payload)

    bus.on("tool.result.#", _on_result, ToolResultPayload)
    registry._event_bus = bus

    async def _ok(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=inv.tool_name, success=True)

    registry.register(ToolSpec(name="arg_tool", description="d", kind="sync"), _ok)
    await registry.invoke(ToolInvocation(tool_name="arg_tool", source="test", arguments={"city": "上海", "n": 3}))
    await registry.invoke(ToolInvocation(tool_name="arg_tool", source="test"))
    await asyncio.sleep(0.01)  # emit 为 fire-and-forget，让派发任务跑完
    assert len(received) == 2
    assert received[0].arguments == {"city": "上海", "n": 3}
    assert received[1].arguments == {}  # 缺省入参落空 dict，不落 None


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


# =============================================================================
# 可见名单（visible_to，ADR-012）
#
# - 注册处逐工具声明（值 = Agent 注册名列表或 ["*"]；未声明默认全员）
# - for_agent 按名单计算工具面；不传 for_agent = 运营全集
# - invoke() 不查名单——受众治理只管发现面（编名直调是已知边界）
# =============================================================================


def test_visible_to_recorded_and_for_agent_filters(registry: ToolRegistry) -> None:
    """visible_to 注册处声明生效：for_agent 按名单计算工具面；未列工具默认全员。"""

    class MixedProvider(BaseToolProvider):
        @property
        def name(self) -> str:
            return "mixed"

        def list_tools(self):
            return [
                ToolSpec(name="open", description="o", kind="sync", provider="mixed"),
                ToolSpec(name="secret", description="s", kind="sync", provider="mixed"),
            ]

        async def invoke(self, invocation: ToolInvocation):
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    registry.register_provider(
        MixedProvider(), visible_to={"mixed_secret": ["minecraft"]}
    )

    # 名单查询
    assert registry.visible_to_of("mixed_open") == ["*"]  # 未声明 = 全员
    assert registry.visible_to_of("mixed_secret") == ["minecraft"]
    # for_agent 计算工具面
    streamer_face = {s.full_name for s in registry.list_tools(for_agent="streamer")}
    assert streamer_face == {"mixed_open"}  # secret 对主播不可见
    minecraft_face = {s.full_name for s in registry.list_tools(for_agent="minecraft")}
    assert minecraft_face == {"mixed_open", "mixed_secret"}
    # 不传 for_agent = 运营全集
    everything = {s.full_name for s in registry.list_tools()}
    assert everything == {"mixed_open", "mixed_secret"}


def test_for_agent_wildcard_visible_to_all(registry: ToolRegistry) -> None:
    """visible_to 显式声明 ["*"] 等价全员可见。"""

    class OpenProvider(BaseToolProvider):
        @property
        def name(self) -> str:
            return "open"

        def list_tools(self):
            return [ToolSpec(name="x", description="x", kind="sync", provider="open")]

        async def invoke(self, invocation: ToolInvocation):
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    registry.register_provider(OpenProvider(), visible_to={"open_x": ["*"]})
    assert {s.full_name for s in registry.list_tools(for_agent="anyone")} == {"open_x"}


def test_visible_to_empty_list_rejected(registry: ToolRegistry) -> None:
    """校验失败：空表非法。"""
    provider = _SampleProvider()
    with pytest.raises(ValueError, match="非空列表"):
        registry.register_provider(provider, visible_to={"game_p_a": []})


def test_visible_to_wildcard_mixed_rejected(registry: ToolRegistry) -> None:
    """校验失败：'*' 只能单独出现（["*","streamer"] 非法）。"""
    provider = _SampleProvider()
    with pytest.raises(ValueError, match="单独出现"):
        registry.register_provider(provider, visible_to={"game_p_a": ["*", "streamer"]})


def test_visible_to_unknown_key_rejected(registry: ToolRegistry) -> None:
    """校验失败：键拼错（未命中本注册项声明的工具全名）即报错。"""
    provider = _SampleProvider()
    with pytest.raises(ValueError, match="未命中"):
        registry.register_provider(provider, visible_to={"game_pa_typo": ["streamer"]})


def test_clear_resets_visible_to(registry: ToolRegistry) -> None:
    """clear() 同步清空 _visible_to（与其他内部容器一致）。"""
    provider = _SampleProvider()
    registry.register_provider(provider, visible_to={"game_p_a": ["minecraft"]})
    assert registry.visible_to_of("game_p_a") == ["minecraft"]

    registry.clear()
    assert registry.visible_to_of("game_p_a") == ["*"]


async def test_invoke_not_in_visible_list_is_not_blocked(registry: ToolRegistry) -> None:
    """名单只管发现面：不在名单内的 Agent 编名直调仍按"已知工具"路径执行。

    LLM 幻觉编名直调保留工具是已知的受众治理边界，此测试固定该契约
    （与 ADR-009/012 的 invoke 不查身份一致）。
    """
    provider = _SampleProvider()
    registry.register_provider(provider, visible_to={"game_p_a": ["minecraft"]})

    # streamer 的工具面看不到 game_p_a，但 invoke 仍能命中
    assert "game_p_a" not in {s.full_name for s in registry.list_tools(for_agent="streamer")}
    res = await registry.invoke(ToolInvocation(tool_name="game_p_a"))
    assert res.success is True
    assert res.content == "from_a"
    assert res.tool_name == "game_p_a"


# =============================================================================
# 命名模型：full_name 派生与停用列表警告
# =============================================================================


def test_full_name_derivation() -> None:
    """全名 = <provider>_<工具名> 派生；同一 spec 重复计算结果一致（不存下来）。"""
    spec = ToolSpec(name="todo", description="d", kind="sync", provider="minecraft")
    assert spec.full_name == "minecraft_todo"
    assert spec.full_name == spec.full_name


def test_full_name_empty_provider_falls_back_to_name() -> None:
    """provider 为空（匿名工具）时全名 = 工具名本身（兜底，不应出现）。"""
    spec = ToolSpec(name="echo", description="d", kind="sync")
    assert spec.provider == ""
    assert spec.full_name == "echo"


def test_apply_disabled_warns_on_unmatched_names(
    registry: ToolRegistry,
) -> None:
    """停用列表中未注册的名字触发 warning 并列出（防改名后静默失效）；已存在名字正常生效。"""
    from loguru import logger

    provider = _SampleProvider()
    registry.register_provider(provider)

    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(m), level="WARNING")
    try:
        effective = registry.apply_disabled(["game_p_a", "不存在的工具名"])
    finally:
        logger.remove(handler_id)

    assert effective == 1
    assert registry.is_disabled("game_p_a") is True
    assert registry.disabled_tools == ["game_p_a"]
    assert any("不存在的工具名" in m for m in messages), "未匹配条目应出现在 warning 日志"
