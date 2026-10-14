"""ToolRegistry：注册去重、invoke 失败不抛、Provider 注册与分类、可见名单（visible_to）、result 事件、停用列表"""

from __future__ import annotations

import asyncio
from typing import List

import pytest
from loguru import logger

from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.tools import ToolInvocation, ToolProvider, ToolRegistry, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import BaseToolProvider, make_provider_from_specs
from src.modules.tools.registry import default_tool_registry, set_default_registry


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


async def _ok(inv: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(tool_name=inv.tool_name, success=True, content="ok")


def _provider(name: str, *tool_names: str) -> ToolProvider:
    """每个工具都用 _ok 实现的最小 Provider。"""
    return make_provider_from_specs(
        name, [(ToolSpec(name=n, description=n, kind="sync", provider=name), _ok) for n in tool_names]
    )


class _SampleProvider(ToolProvider):
    category = "game"  # 自声明归属分类（provider 名与分类正交）

    @property
    def name(self) -> str:
        return "game"

    def __init__(self) -> None:
        # 声明名是裸名；对外全名 = <provider>_<工具名>
        self._specs: List[ToolSpec] = [
            ToolSpec(name="p_a", description="a", kind="sync", provider="game"),
            ToolSpec(name="p_b", description="b", kind="sync", provider="game"),
        ]

    def list_tools(self):
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if invocation.tool_name in ("game_p_a", "game_p_b"):
            return ToolExecutionResult(
                tool_name=invocation.tool_name, success=True, content="from_" + invocation.tool_name[-1]
            )
        return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message="not_in_provider")


async def test_invoke_never_raises_for_unknown_or_failing_tools(registry: ToolRegistry) -> None:
    """核心契约：未知工具与实现抛异常都返回失败 result，调用方不会收到异常。"""
    res = await registry.invoke(ToolInvocation(tool_name="definitely_not_a_tool", source="test"))
    assert isinstance(res, ToolExecutionResult) and res.success is False
    assert res.tool_name == "definitely_not_a_tool" and ("未知" in res.error_message or "未找到" in res.error_message)
    many = await registry.invoke_many([ToolInvocation(tool_name=f"missing_{i}") for i in range(5)])
    assert len(many) == 5 and not any(r.success for r in many)

    async def bad(inv: ToolInvocation) -> ToolExecutionResult:
        raise RuntimeError("boom!")

    registry.register(ToolSpec(name="bad", description="raise", kind="sync"), bad)
    failed = await registry.invoke(ToolInvocation(tool_name="bad"))
    assert failed.success is False and "boom!" in failed.error_message


async def test_register_dedup_keeps_first_and_collection_protocol(registry: ToolRegistry) -> None:
    async def impl(content: str):
        async def run(inv: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name="dup", success=True, content=content)

        return run

    spec = ToolSpec(name="dup", description="dedup", kind="sync")
    assert registry.register(spec, await impl("A")) is True
    assert registry.register(spec, await impl("B")) is False  # 重复注册保留先注册的
    assert (await registry.invoke(ToolInvocation(tool_name="dup"))).content == "A"
    assert "dup" in registry and "not_in" not in registry and len(registry) == 1
    assert registry.get("dup").name == "dup"


async def test_provider_registration_full_names_category_and_idempotence(registry: ToolRegistry) -> None:
    provider = _SampleProvider()
    assert registry.register_provider(provider) == 2
    assert registry.register_provider(provider) == 0  # 同一 Provider 重复注册返回 0

    tools = registry.list_tools()
    assert {t.full_name for t in tools} == {"game_p_a", "game_p_b"}  # 注册键 = 派生全名
    assert {t.name for t in tools} == {"p_a", "p_b"}  # spec 原样存储，声明名仍是裸名
    assert len(registry.list_tools(provider="game")) == 2 and registry.list_tools(provider="framework") == []
    assert registry.list_categories() == ["game"]
    assert {t.full_name for t in registry.list_tools(category="game")} == {"game_p_a", "game_p_b"}
    assert registry.list_tools(category="avatar") == []

    res = await registry.invoke(ToolInvocation(tool_name="game_p_a"))
    assert (res.success, res.content, res.tool_name) == (True, "from_a", "game_p_a")


async def test_provider_name_spec_provider_mismatch_rejected(registry: ToolRegistry) -> None:
    class Mismatched(_SampleProvider):
        @property
        def name(self) -> str:
            return "other"

    with pytest.raises(ValueError) as exc_info:  # fail-fast：provider.name 与 spec.provider 不同值
        registry.register_provider(Mismatched())
    assert "other" in str(exc_info.value) and "game" in str(exc_info.value)


def test_result_event_naming_and_full_name_derivation() -> None:
    assert ToolSpec(name="speak", description="a", kind="async").resolve_result_event() == "tool.result.speak"
    custom = ToolSpec(name="set_goal", description="g", kind="async", result_event="tool.result.set_goal_feedback")
    assert custom.resolve_result_event() == "tool.result.set_goal_feedback"
    assert ToolSpec(name="todo", description="d", kind="sync", provider="minecraft").full_name == "minecraft_todo"
    assert ToolSpec(name="echo", description="d", kind="sync").full_name == "echo"  # 匿名工具兜底为工具名


def test_to_llm_definitions_shape(registry: ToolRegistry) -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    registry.register(ToolSpec(name="t", description="D", kind="sync", parameters_schema=schema), _ok)
    (definition,) = registry.to_llm_definitions()
    assert (definition["name"], definition["description"]) == ("t", "D") and "parameters" in definition


async def test_make_provider_from_specs_registers_by_full_name_without_touching_default_registry() -> None:
    saved = default_tool_registry()
    sentinel = ToolRegistry()
    set_default_registry(sentinel)
    try:
        reg = ToolRegistry()
        assert reg.register_provider(_provider("simple", "hi")) == 1 and reg.has("simple_hi")
        res = await reg.invoke(ToolInvocation(tool_name="simple_hi"))
        assert (res.success, res.tool_name) == (True, "simple_hi")
        assert sentinel.has("simple_hi") is False  # 注册不写入默认单例
    finally:
        set_default_registry(saved)


# tool.result.<name> 结果事件广播


async def _registry_with_bus() -> tuple[ToolRegistry, list[tuple[str, ToolResultPayload]]]:
    bus = EventBus(enable_stats=False)
    received: list[tuple[str, ToolResultPayload]] = []

    async def on_result(event_name: str, payload: ToolResultPayload, source: str) -> None:
        received.append((event_name, payload))

    bus.on("tool.result.#", on_result, ToolResultPayload)
    return ToolRegistry(event_bus=bus), received


async def test_invoke_emits_tool_result_event_with_arguments_and_image_intact() -> None:
    registry, received = await _registry_with_bus()
    registry.register(ToolSpec(name="my_tool", description="d", kind="sync"), _ok)
    await registry.invoke(ToolInvocation(tool_name="my_tool", source="test", arguments={"city": "上海", "n": 3}))
    await registry.invoke(ToolInvocation(tool_name="my_tool", source="test"))

    image = "A" * 2048  # UI 通道契约：structured_content 里的 base64 截图原样进事件，WebUI 工具卡靠它展示

    async def screen(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=inv.tool_name, success=True, structured_content={"text": "", "latency_ms": 12, "image": image}
        )

    registry.register(ToolSpec(name="look_at_screen", description="d", kind="sync", provider="vision"), screen)
    await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", source="test"))
    await asyncio.sleep(0.01)  # emit 为 fire-and-forget，让派发任务跑完

    assert [name for name, _ in received] == ["tool.result.my_tool"] * 2 + ["tool.result.vision_look_at_screen"]
    first, second, shot = (p for _, p in received)
    assert (first.tool_name, first.status, first.result) == ("my_tool", "success", {"content": "ok"})
    assert first.arguments == {"city": "上海", "n": 3} and second.arguments == {}  # 缺省入参落空 dict，不落 None
    assert shot.result["image"] == image


async def test_invoke_failure_is_broadcast_as_error_and_no_bus_is_silent() -> None:
    registry, received = await _registry_with_bus()

    async def bad(inv: ToolInvocation) -> ToolExecutionResult:
        raise RuntimeError("boom!")

    registry.register(ToolSpec(name="bad_tool", description="d", kind="sync"), bad)
    assert (await registry.invoke(ToolInvocation(tool_name="bad_tool"))).success is False
    await asyncio.sleep(0.01)
    ((_, payload),) = received
    assert payload.status == "error" and "boom!" in payload.error_message

    quiet = ToolRegistry()  # 未挂载 EventBus 不广播
    quiet.register(ToolSpec(name="quiet", description="d", kind="sync"), _ok)
    assert (await quiet.invoke(ToolInvocation(tool_name="quiet"))).success is True


# 可见名单：注册处声明；for_agent 按名单过滤；invoke 不查名单


def _face(registry: ToolRegistry, agent: str) -> set[str]:
    return {s.full_name for s in registry.list_tools(for_agent=agent)}


def test_visible_to_defaults_to_streamer_and_for_agent_filters(registry: ToolRegistry) -> None:
    registry.register_provider(_provider("mixed", "open", "secret"), visible_to={"mixed_secret": ["minecraft"]})
    assert registry.visible_to_of("mixed_open") == ["streamer"]  # 未声明 = 默认仅主播（fail-closed）
    assert registry.visible_to_of("mixed_secret") == ["minecraft"]
    assert _face(registry, "streamer") == {"mixed_open"}
    assert _face(registry, "minecraft") == {"mixed_secret"}
    assert {s.full_name for s in registry.list_tools()} == {"mixed_open", "mixed_secret"}  # 不传 for_agent = 运营全集


def test_wildcard_visible_to_is_visible_to_every_agent(registry: ToolRegistry) -> None:
    # vision_look_at_screen 注册处显式 ["*"]：text_adv 的观察循环经它读屏，不吃默认名单
    registry.register_provider(_provider("vision", "look_at_screen"), visible_to={"vision_look_at_screen": ["*"]})
    assert registry.visible_to_of("vision_look_at_screen") == ["*"]
    for agent in ("streamer", "minecraft", "text_adv", "anyone"):
        assert "vision_look_at_screen" in _face(registry, agent)


@pytest.mark.parametrize(
    ("visible_to", "match"),
    [
        ({"game_p_a": []}, "非空列表"),
        ({"game_p_a": ["*", "streamer"]}, "单独出现"),  # '*' 只能单独出现
        ({"game_pa_typo": ["streamer"]}, "未命中"),  # 键拼错即报错
    ],
)
def test_invalid_visible_to_is_rejected(registry: ToolRegistry, visible_to: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        registry.register_provider(_SampleProvider(), visible_to=visible_to)


async def test_visible_list_is_not_enforced_by_invoke_and_clear_resets_it(registry: ToolRegistry) -> None:
    registry.register_provider(_SampleProvider(), visible_to={"game_p_a": ["minecraft"]})
    # 名单只管可见性：LLM 编名直调仍按已知工具执行（受众治理在注册期收敛，已知边界）
    assert "game_p_a" not in _face(registry, "streamer")
    res = await registry.invoke(ToolInvocation(tool_name="game_p_a"))
    assert (res.success, res.content) == (True, "from_a")

    registry.clear()
    assert registry.visible_to_of("game_p_a") == ["streamer"]  # clear 同步清空名单，回到默认


# 停用列表


def test_apply_disabled_warns_on_unmatched_names_and_defers_until_registration(registry: ToolRegistry) -> None:
    provider = _SampleProvider()
    registry.register_provider(provider)

    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(m), level="WARNING")
    try:
        effective = registry.apply_disabled(["game_p_a", "不存在的工具名"])
    finally:
        logger.remove(handler_id)
    assert effective == 1 and registry.is_disabled("game_p_a") and registry.disabled_tools == ["game_p_a"]
    assert any("不存在的工具名" in m for m in messages)  # 防改名后静默失效

    # 连接型 Provider 降级登记时 apply_disabled 先行：未注册条目进待生效集，工具日后注册时承接停用态
    registry.apply_disabled(["game_late_tool"])
    assert registry.is_disabled("game_late_tool") is False
    registry.register(ToolSpec(name="late_tool", description="d", kind="sync", provider="game"), provider.invoke)
    assert registry.is_disabled("game_late_tool") is True
    assert all(s.full_name != "game_late_tool" for s in registry.list_tools())  # 转正后对 LLM 不可见


# =============================================================================
# 工具面使用说明：guides(for_agent) 按可见名单收集
# =============================================================================


class _GuidedProvider(BaseToolProvider):
    """带使用说明的 Provider：说明文本可按读者区分，也可配置为抛异常。"""

    category = "game"

    def __init__(self, name: str, guide: str, *, raise_on_guide: bool = False) -> None:
        self._name = name
        self._guide = guide
        self._raise = raise_on_guide

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self):
        return [ToolSpec(name="act", description="动作", kind="sync", provider=self._name)]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    def guide_for(self, agent_name: str) -> str:
        if self._raise:
            raise RuntimeError("说明坏了")
        return f"{self._guide}（读者 {agent_name}）" if self._guide else ""


def test_guides_collects_visible_providers_in_registration_order(registry: ToolRegistry) -> None:
    """只收对该 Agent 可见且说明非空的提供者，按注册顺序返回 (提供者名, 说明)。"""
    registry.register_provider(_GuidedProvider("alpha", "甲的玩法"), visible_to={"alpha_act": ["streamer"]})
    registry.register_provider(_GuidedProvider("beta", ""), visible_to={"beta_act": ["streamer"]})
    registry.register_provider(_GuidedProvider("gamma", "丙的玩法"), visible_to={"gamma_act": ["other"]})
    registry.register_provider(_GuidedProvider("delta", "丁的玩法"), visible_to={"delta_act": ["*"]})

    guides = registry.guides(for_agent="streamer")

    assert guides == [("alpha", "甲的玩法（读者 streamer）"), ("delta", "丁的玩法（读者 streamer）")]
    assert registry.guides(for_agent="other") == [
        ("gamma", "丙的玩法（读者 other）"),
        ("delta", "丁的玩法（读者 other）"),
    ]


def test_guides_skips_disabled_tools_and_broken_guides(registry: ToolRegistry) -> None:
    """工具全部停用的提供者不算可见；说明抛异常的提供者跳过，不影响其余说明。"""
    registry.register_provider(_GuidedProvider("alpha", "甲的玩法"), visible_to={"alpha_act": ["streamer"]})
    registry.register_provider(
        _GuidedProvider("broken", "坏的", raise_on_guide=True), visible_to={"broken_act": ["streamer"]}
    )
    registry.register_provider(_GuidedProvider("muted", "静音的"), visible_to={"muted_act": ["streamer"]})
    registry.apply_disabled(["muted_act"])

    assert registry.guides(for_agent="streamer") == [("alpha", "甲的玩法（读者 streamer）")]


def test_guides_default_empty_for_plain_provider(registry: ToolRegistry) -> None:
    """没覆写说明的 Provider（基类默认空串、简单工具路径）不出现在结果里。"""

    async def _impl(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    spec = ToolSpec(name="echo", description="回显", kind="sync", provider="plain")
    registry.register_provider(make_provider_from_specs("plain", [(spec, _impl)]), visible_to={"plain_echo": ["*"]})

    assert registry.guides(for_agent="streamer") == []
