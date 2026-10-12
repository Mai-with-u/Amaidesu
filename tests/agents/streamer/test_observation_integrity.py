"""完整工具观察进入 Planner，超长字段和尾部证据都不丢失。"""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.planner import (
    Planner,
    _render_observation,
)
from src.agents.streamer.room_state import RoomState
from src.modules.tools.conversation import is_tool_message
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


async def test_visual_caller_receives_image_parts_in_tool_message() -> None:
    """视觉调用方的带图观察 = 占位符版观察文本 + image_ref 引用 part 的完整 tool 消息。

    图走 blocks → image_ref 引用 part：base64 本体入 VisionImageStore，
    历史里只放句柄（注水是引擎装配期职责）。调用方按 is_tool_message
    识别后整条追加。
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

        observation = await planner._invoke_registry_tool("vision_look_at_screen", {}, tool_call_id="call_1")

        assert is_tool_message(observation)
        assert observation["tool_call_id"] == "call_1"
        parts = observation["content"]
        assert parts[0]["type"] == "text"
        assert image not in parts[0]["text"]
        assert "[图像 1 KB 已省略]" in parts[0]["text"]
        # 历史里只放引用句柄；句柄在 store 里能取回原图（注水依据），且 base64 不落历史
        assert parts[1]["type"] == "image_ref"
        assert parts[1]["ref"].startswith("img_")
        assert store.get(parts[1]["ref"]) == f"data:image/png;base64,{image}"
        assert image not in json.dumps(observation, ensure_ascii=False)
    finally:
        store.clear()


async def test_visual_caller_without_image_keeps_text_observation() -> None:
    """视觉调用方调无图工具：观察维持纯文本（消息 dict 只在有图时出现）。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(tool_name="minecraft_glance", success=True, structured_content={"ok": True})
    )
    planner = _make_planner(registry, visual=True)

    observation = await planner._invoke_registry_tool("minecraft_glance", {}, tool_call_id="call_2")

    assert isinstance(observation, str)
    assert json.loads(observation) == {"ok": True}


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
