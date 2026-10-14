"""完整工具观察进入 Planner，超长字段和尾部证据都不丢失。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.planner import (
    Planner,
    _render_observation,
)
from src.agents.streamer.room_state import RoomState
from src.modules.tools.models import ResultBlock, ToolExecutionResult
from src.modules.vision.image_store import VisionImageStore, set_image_store


def _surroundings_shaped() -> Dict[str, Any]:
    """形状与真实 ``perceive(view="surroundings")`` 响应一致：电梯段排在大体量诊断之后。"""
    return {
        "position": {"x": -136.2809165787706, "y": 135.0, "z": 174.3212246501019},
        "dimension": "minecraft:overworld",
        "sky_light": 15,
        "nearby_entities": [],
        "nearby_signs": [],
        "local_decision_summary": {
            "standable_regions": [{"region": f"region_{i}", "evidence": "x" * 320} for i in range(5)]
        },
        "terrain_overview": {
            "regions": [
                {"direction": "west", "surface_material": "minecraft:stone", "evidence": "y" * 340} for _ in range(12)
            ]
        },
        "elevators": {
            "integration_available": True,
            "elevators": [
                {
                    "elevator_id": "3f1d0a5e-1c2b-4d3e-8f90-abcdef012345",
                    "distance": 4.5,
                    "floor_list_state": "synchronized",
                    "floors": [{"id": "floor:10", "short_name": "1", "served": True}],
                }
            ],
        },
        "physical_structures": [{"direction": "north", "evidence": "z" * 420} for _ in range(3)],
    }


def _make_planner(tool_registry: Any, *, visual: bool = False) -> Planner:
    llm_service = MagicMock()
    llm_service.is_profile_visual = MagicMock(return_value=visual)
    return Planner(
        config={"planner_max_steps": 2},
        llm_service=llm_service,
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=tool_registry,
        reply_provider=MagicMock(),
    )


def test_observation_keeps_every_field() -> None:
    """电梯楼层和大体积地形诊断完整共存，不能靠丢段减小观察。"""
    payload = _surroundings_shaped()
    assert len(json.dumps(payload, ensure_ascii=False)) > 6144
    assert json.loads(_render_observation(payload)) == payload


def test_single_large_field_and_non_mapping_results_remain_valid() -> None:
    """大字段、字符串和数组仍是完整 JSON，尾部任务条件可以被解析。

    大字段用含空白的形态：纯 base64 字符集且无空白的超长串属二进制载荷，
    对话视图里有专门的占位符契约（见下方 base64 用例）。
    """
    for payload in ({"tasks": ["任务" * 10000 + "保留入口"]}, ["x " * 5000], "内容" * 9000):
        assert json.loads(_render_observation(payload)) == payload


async def test_registry_observation_preserves_long_content() -> None:
    """真实工具回调后的结构化结果与补充文本都完整交给 Planner。"""
    registry = MagicMock()
    payload = _surroundings_shaped()
    supplement = "补充事实" * 10000
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="maicraft_perceive",
            success=True,
            structured_content=payload,
            content=supplement,
        )
    )
    observed = await _make_planner(registry)._invoke_registry_tool("maicraft_perceive", {"view": "surroundings"})
    assert json.loads(observed) == {**payload, "ok": True, "content": supplement}


async def test_text_only_tool_result_reaches_the_observation() -> None:
    """信息获取型工具（content 文本产出、无 structured_content）的内容必须进观察。

    回归锚点：web_search 上线时 LLM 只看到 {"ok": true}，搜索结果文本在
    观察构造处被丢弃（工具执行侧与 tool.result 事件里内容完好）。
    """
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="web_search",
            success=True,
            content="[1] Minecraft 官网\n    https://www.minecraft.net",
        )
    )
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("web_search", {"query": "minecraft"})
    parsed = json.loads(observed)

    assert parsed["ok"] is True
    assert "Minecraft 官网" in parsed["content"]


async def test_structured_and_text_content_coexist_in_observation() -> None:
    """结构化结果与 content 文本并存时两者都可见（结构化键不被覆盖）。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="some_tool",
            success=True,
            content="补充文本",
            structured_content={"status": "done", "content": "结构化自带 content"},
        )
    )
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("some_tool", {})
    parsed = json.loads(observed)

    assert parsed["status"] == "done"
    assert parsed["content"] == "结构化自带 content"


async def test_action_tool_with_empty_content_keeps_ok_only_shape() -> None:
    """空 content 的动作型工具维持 {"ok": true} 形态（不引入噪音键）。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=ToolExecutionResult(tool_name="some_action", success=True, content=""))
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("some_action", {})
    assert json.loads(observed) == {"ok": True}


async def test_base64_image_becomes_placeholder_in_observation() -> None:
    """base64 截图载荷以占位符进观察，二进制本体不出现在对话消息里。

    回归锚点：vision_look_at_screen 的 structured_content["image"] 是
    WebUI 工具卡的截图展示契约（tool.result 事件原样携带），对话观察
    曾把它整包渲染进消息，单笔请求图片 token 冲到数十万。
    """
    registry = MagicMock()
    image = "A" * 2048  # 纯 base64 字符集、超 1KB、无空白
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="vision_look_at_screen",
            success=True,
            content="(no text extracted)",
            structured_content={
                "text": "",
                "width": 1920,
                "height": 1080,
                "latency_ms": 36,
                "image": image,
            },
        )
    )
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("vision_look_at_screen", {"monitor_index": 0})
    parsed = json.loads(observed)

    assert image not in observed
    assert parsed["image"] == "[图像 1 KB 已省略]"
    assert parsed["ok"] is True
    assert parsed["width"] == 1920
    assert parsed["height"] == 1080


async def test_long_prose_and_urls_pass_through_untouched() -> None:
    """合法长文本不被占位符误伤：中文长文与带标点的 URL 原样进观察。"""
    registry = MagicMock()
    article = "这是一段很长的中文检索正文，讲 Minecraft 的红石机制。" * 200
    url = "https://example.com/wiki/红石?lang=zh&q=redstone+repeater&section=3#比较器"
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="web_fetch_url",
            success=True,
            structured_content={"url": url, "content": article},
        )
    )
    planner = _make_planner(registry)

    observed = await planner._invoke_registry_tool("web_fetch_url", {"url": url})
    parsed = json.loads(observed)

    assert parsed["ok"] is True
    assert parsed["url"] == url
    assert parsed["content"] == article


async def test_caller_profile_forwarded_to_registry() -> None:
    """对话式调用携带 caller_profile（能力判定维度），与场景标识 source 正交。

    caller_profile 供视觉工具按调用方能力分叉（ADR-037）；source 记调用
    出于什么场景（观测维度），两者语义不同、各填各的。
    """
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=ToolExecutionResult(tool_name="some_action", success=True, content=""))
    planner = _make_planner(registry)

    await planner._invoke_registry_tool("some_action", {})

    invocation = registry.invoke.call_args[0][0]
    assert invocation.caller_profile == "planner"
    assert invocation.source == "planner-react"


async def test_visual_caller_gets_text_observation_and_tail_image() -> None:
    """视觉调用方的带图观察：观察本身是纯文本（图位是占位说明），图句柄登记到尾部临时图片。

    下一步请求时图作为一条 user 消息挂在序列最后，只看这一次；消息序列里
    始终没有图片引用，前缀不会因为图过期被改写。
    """
    store = VisionImageStore()
    set_image_store(store)
    try:
        registry = MagicMock()
        image = "A" * 2048
        registry.invoke = AsyncMock(
            return_value=ToolExecutionResult(
                tool_name="vision_look_at_screen",
                success=True,
                content="(no text extracted)",
                blocks=[ResultBlock(kind="image", data=image, mime_type="image/png")],
                structured_content={"text": "", "width": 1920, "image": image},
            )
        )
        planner = _make_planner(registry, visual=True)

        observation = await planner._invoke_registry_tool("vision_look_at_screen", {})

        assert isinstance(observation, str)
        assert image not in observation
        assert "[图像 1 KB 已省略]" in observation
        tail = planner._tail_images.drain()
        assert tail is not None and tail["role"] == "user"
        refs = [part["ref"] for part in tail["content"] if part["type"] == "image_ref"]
        assert len(refs) == 1 and store.get(refs[0]) == f"data:image/png;base64,{image}"
        assert planner._tail_images.drain() is None
    finally:
        store.clear()


async def test_tail_image_rides_only_the_next_request() -> None:
    """截图只出现在紧随回执的那一次请求尾部；再下一步请求里只剩文字回执，前缀与上一次一致。"""
    store = VisionImageStore()
    set_image_store(store)
    try:
        registry = MagicMock()
        registry.list_tools = MagicMock(return_value=[])
        registry.invoke = AsyncMock(
            return_value=ToolExecutionResult(
                tool_name="vision_look_at_screen",
                success=True,
                blocks=[ResultBlock(kind="image", data="A" * 2048, mime_type="image/png")],
                structured_content={"text": ""},
            )
        )
        planner = _make_planner(registry, visual=True)
        planner.max_steps = 3
        sent: list = []
        look = SimpleNamespace(id="c1", name="vision_look_at_screen", arguments={})
        responses = [
            SimpleNamespace(success=True, content="看一眼", tool_calls=[look], request_id="r1"),
            SimpleNamespace(success=True, content="画面里在下雨", tool_calls=[look], request_id="r2"),
            SimpleNamespace(success=True, content="不说了", tool_calls=[], request_id="r3"),
        ]

        async def generate(messages: list, **_: Any) -> Any:
            sent.append(list(messages))
            return responses[len(sent) - 1]

        planner._llm_service.generate = generate
        await planner.plan([], proactive=True)

        def has_image(messages: list) -> bool:
            return any(
                isinstance(m.get("content"), list) and any(p.get("type") == "image_ref" for p in m["content"])
                for m in messages
            )

        assert [has_image(messages) for messages in sent] == [False, True, True]
        # 第二次请求的尾部图在第三次请求里已消失：第三次的前缀 = 第二次去掉尾部那条
        assert sent[2][: len(sent[1]) - 1] == sent[1][:-1]
        assert not has_image(sent[2][: len(sent[1]) - 1])
    finally:
        store.clear()


async def test_visual_caller_without_image_keeps_text_observation() -> None:
    """视觉调用方调无图工具：观察维持纯文本，不登记尾部图片。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(tool_name="minecraft_glance", success=True, structured_content={"ok": True})
    )
    planner = _make_planner(registry, visual=True)

    observation = await planner._invoke_registry_tool("minecraft_glance", {})

    assert isinstance(observation, str)
    assert json.loads(observation) == {"ok": True}
    assert planner._tail_images.drain() is None


async def test_reference_tools_view_filters_binary_payloads() -> None:
    """参考段与工具观察同走对话安全视图：参考工具返回截图时占位符进文本。

    回归锚点：_read_reference_tools 曾把 structured_content 直接 json.dumps
    进参考段——look_at_screen 若被配进 reference_tools，base64 会从这里
    绕过对话视图进上下文（P0 只收口了 _render_observation 一处）。
    """
    registry = MagicMock()
    image = "A" * 2048
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="vision_look_at_screen",
            success=True,
            structured_content={"text": "画面正常", "image": image},
        )
    )
    planner = _make_planner(
        registry,
        visual=False,
    )
    planner.typed_config.reference_tools = ["vision_look_at_screen"]

    reference = await planner._read_reference_tools("round-1")

    assert image not in reference
    assert "[图像 1 KB 已省略]" in reference
    assert "画面正常" in reference
