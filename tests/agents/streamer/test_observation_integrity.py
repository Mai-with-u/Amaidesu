"""工具观察进入 Planner：字段与尾部证据不丢、文本产出可见、二进制载荷换占位符、视觉调用方走尾部临时图"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner, _render_observation
from src.agents.streamer.room_state import RoomState
from src.modules.llm.payload import Reasoning
from src.modules.tools.models import ResultBlock, ToolExecutionResult
from src.modules.vision.image_store import VisionImageStore, set_image_store

_IMAGE = "A" * 2048  # 纯 base64 字符集、超 1KB、无空白 → 视为二进制载荷


def _surroundings_shaped() -> Dict[str, Any]:
    """形状与真实 perceive(view="surroundings") 一致：电梯段排在大体量诊断之后。"""
    return {
        "position": {"x": -136.28, "y": 135.0, "z": 174.32},
        "local_decision_summary": {"standable_regions": [{"region": f"r{i}", "evidence": "x" * 320} for i in range(5)]},
        "terrain_overview": {"regions": [{"direction": "west", "evidence": "y" * 340} for _ in range(12)]},
        "elevators": {"elevators": [{"elevator_id": "3f1d0a5e", "floors": [{"id": "floor:10", "served": True}]}]},
        "physical_structures": [{"direction": "north", "evidence": "z" * 420} for _ in range(3)],
    }


def _planner(result: ToolExecutionResult | None = None, *, visual: bool = False) -> Planner:
    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(return_value=result)
    llm_service = MagicMock()
    llm_service.is_profile_visual = MagicMock(return_value=visual)
    return Planner(
        config={"planner_max_steps": 2},
        llm_service=llm_service,
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=registry,
        reply_provider=MagicMock(),
    )


def test_render_observation_keeps_every_field_and_non_mapping_payloads() -> None:
    """电梯楼层与大体积地形诊断完整共存，不靠丢段减小观察；大字段/字符串/数组仍是完整 JSON。"""
    payload = _surroundings_shaped()
    assert len(json.dumps(payload, ensure_ascii=False)) > 6144
    for value in (payload, {"tasks": ["任务" * 10000 + "保留入口"]}, ["x " * 5000], "内容" * 9000):
        assert json.loads(_render_observation(value)) == value


_ARTICLE = "这是一段很长的中文检索正文，讲 Minecraft 的红石机制。" * 200
_URL = "https://example.com/wiki/红石?lang=zh&q=redstone+repeater&section=3#比较器"


@pytest.mark.parametrize(
    ("structured", "content", "expected"),
    [
        # 结构化结果与长补充文本都完整交给 Planner
        (
            _surroundings_shaped(),
            "补充事实" * 10000,
            {**_surroundings_shaped(), "ok": True, "content": "补充事实" * 10000},
        ),
        # 信息获取型工具只产出文本：内容必须进观察（web_search 曾只剩 {"ok": true}）
        (
            None,
            "[1] Minecraft 官网\n    https://www.minecraft.net",
            {"ok": True, "content": "[1] Minecraft 官网\n    https://www.minecraft.net"},
        ),
        # 结构化自带 content 时不被补充文本覆盖
        (
            {"status": "done", "content": "结构化自带 content"},
            "补充文本",
            {"ok": True, "status": "done", "content": "结构化自带 content"},
        ),
        # 空 content 的动作型工具不引入噪音键
        (None, "", {"ok": True}),
        # 合法长文本与带标点 URL 不被占位符误伤
        ({"url": _URL, "content": _ARTICLE}, "", {"ok": True, "url": _URL, "content": _ARTICLE}),
        # base64 截图换占位符（WebUI 工具卡契约照旧走 tool.result 事件；曾单笔请求图片 token 数十万）
        (
            {"text": "", "width": 1920, "height": 1080, "image": _IMAGE},
            "(no text extracted)",
            {
                "ok": True,
                "text": "",
                "width": 1920,
                "height": 1080,
                "image": "[图像 1 KB 已省略]",
                "content": "(no text extracted)",
            },
        ),
    ],
    ids=["long_structured_and_text", "text_only", "structured_content_wins", "action_ok_only", "long_prose", "base64"],
)
async def test_registry_observation_shape(structured, content: str, expected: dict) -> None:
    result = ToolExecutionResult(tool_name="t", success=True, structured_content=structured, content=content)
    planner = _planner(result)
    observed = await planner._invoke_registry_tool("some_tool", {})

    assert _IMAGE not in observed
    assert json.loads(observed) == expected
    # caller_profile 是能力判定维度，source 是场景观测维度，各填各的
    invocation = planner._tool_registry.invoke.call_args[0][0]
    assert (invocation.caller_profile, invocation.source) == ("planner", "planner-react")


@pytest.fixture
def image_store():
    store = VisionImageStore()
    set_image_store(store)
    yield store
    store.clear()


def _image_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name="vision_look_at_screen",
        success=True,
        content="(no text extracted)",
        blocks=[ResultBlock(kind="image", data=_IMAGE, mime_type="image/png")],
        structured_content={"text": "", "width": 1920, "image": _IMAGE},
    )


async def test_visual_caller_gets_text_observation_and_one_shot_tail_image(image_store) -> None:
    """观察本身是纯文本（图位是占位说明），图句柄登记到尾部临时图片，只取一次。"""
    planner = _planner(_image_result(), visual=True)
    observation = await planner._invoke_registry_tool("vision_look_at_screen", {})

    assert _IMAGE not in observation and "[图像 1 KB 已省略]" in observation
    tail = planner._tail_images.drain()
    refs = [part["ref"] for part in tail["content"] if part["type"] == "image_ref"]
    assert tail["role"] == "user" and len(refs) == 1
    assert image_store.get(refs[0]) == f"data:image/png;base64,{_IMAGE}"
    assert planner._tail_images.drain() is None

    no_image = _planner(ToolExecutionResult(tool_name="g", success=True, structured_content={"ok": True}), visual=True)
    assert json.loads(await no_image._invoke_registry_tool("minecraft_glance", {})) == {"ok": True}
    assert no_image._tail_images.drain() is None


async def test_tail_image_rides_only_the_next_request(image_store) -> None:
    """截图只出现在紧随回执的那次请求尾部；再下一步只剩文字回执，前缀与上一次一致（缓存不失效）。"""
    planner = _planner(_image_result(), visual=True)
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
    assert sent[2][: len(sent[1]) - 1] == sent[1][:-1]
    assert not has_image(sent[2][: len(sent[1]) - 1])


async def test_echoed_reasoning_rides_along_within_the_round() -> None:
    """模型开着回传开关时，上一步的思考随 assistant 消息进本轮历史，下一步请求带着它。"""
    planner = _planner(ToolExecutionResult(tool_name="memory_query", success=True, structured_content={"hit": 1}))
    sent: list = []
    echo = Reasoning(text="先查记忆再决定说不说", model="m")
    query = SimpleNamespace(id="c1", name="memory_query", arguments={})
    responses = [
        SimpleNamespace(success=True, content="", tool_calls=[query], request_id="r1", reasoning_echo=echo),
        SimpleNamespace(success=True, content="不说了", tool_calls=[], request_id="r2"),
    ]

    async def generate(messages: list, **_: Any) -> Any:
        sent.append(list(messages))
        return responses[len(sent) - 1]

    planner._llm_service.generate = generate
    await planner.plan([], proactive=True)

    assert len(sent) == 2
    assert [m.get("reasoning") for m in sent[1] if m.get("role") == "assistant"] == [echo.model_dump()]


async def test_reference_tools_view_filters_binary_payloads() -> None:
    """参考段与工具观察同走对话安全视图：参考工具返回截图时只留占位符。"""
    result = ToolExecutionResult(
        tool_name="vision_look_at_screen", success=True, structured_content={"text": "画面正常", "image": _IMAGE}
    )
    planner = _planner(result)
    planner.typed_config.reference_tools = ["vision_look_at_screen"]

    reference = await planner._read_reference_tools("round-1")
    assert _IMAGE not in reference and "[图像 1 KB 已省略]" in reference and "画面正常" in reference
