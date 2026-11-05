"""本文件测 MinecraftAgent 的看画面主题：observe(picture) 的图怎么给模型、模型不能看图时转文字、
一个任务里看画面的次数与间隔、图不进工具结果事件、截桌面的工具不给游戏 Agent、主播碰不到画面视图、看画面的提示词口径。"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Dict, Iterator, List

import pytest

from src.agents.minecraft.config import MinecraftPictureConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry
from src.modules.vision.look_at_screen import LOOK_AT_SCREEN_SPEC
from src.modules.vision.image_store import VisionImageStore, set_image_store

from .minecraft_v1_fakes import (
    PICTURE_BASE64,
    FakeMaicraft,
    ScriptLlm,
    _call,
    _reports,
    _resp,
    _wait_until,
    make_minecraft_agent,
    offline_config,
)


@pytest.fixture
def image_store() -> Iterator[VisionImageStore]:
    """每个用例一份干净的图库：能看图的路径把图存进去换句柄。"""
    store = VisionImageStore()
    set_image_store(store)
    try:
        yield store
    finally:
        store.clear()


def _tool_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [message for message in messages if message.get("role") == "tool"]


def _look_then_deliver(arguments: Dict[str, Any]) -> Any:
    """剧本：先看一眼画面 -> 再看一眼自己 -> 交付。看画面后的那一次请求带着图，再下一次就没了。"""

    def script(messages: List[Dict[str, Any]]) -> Response:
        done = len(_tool_messages(messages))
        if done == 0:
            return _resp("看一眼盖好的房子", [_call("maicraft_observe", {"what": "picture", **arguments}, "p1")])
        if done == 1:
            return _resp("屋顶有点歪，记下来", [_call("maicraft_observe", {"what": "self"}, "s1")])
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "房子看过了"})])

    return script


def _observation(message: Dict[str, Any]) -> Dict[str, Any]:
    """一条工具消息里的观察：后台折叠给消息编了号，正文前面有编号前缀，从第一个花括号读起。"""
    content = str(message["content"])
    return json.loads(content[content.index("{") :])


def _image_refs(message: Dict[str, Any]) -> List[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [part["ref"] for part in content if isinstance(part, dict) and part.get("type") == "image_ref"]


async def test_a_visual_model_sees_the_picture_once_and_history_keeps_only_text(image_store: VisionImageStore) -> None:
    """能看图的模型：看画面后的下一次请求尾部挂着那张图；历史里只有文字观察，再下一次请求图就没了。"""
    server = FakeMaicraft()
    llm = ScriptLlm(_look_then_deliver({}), visual=True)
    agent, _llm, bus = await make_minecraft_agent(server, llm=llm)

    agent.receive_delegation(instruction="看看盖好的房子好不好看", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    after_picture, later = llm.seen[1], llm.seen[2]
    refs = _image_refs(after_picture[-1])
    assert after_picture[-1]["role"] == "user" and len(refs) == 1
    assert image_store.get(refs[0]) == f"data:image/jpeg;base64,{PICTURE_BASE64}"
    observation = _tool_messages(after_picture)[0]["content"]
    assert '"in_picture"' in observation and PICTURE_BASE64 not in observation
    assert not any(_image_refs(message) for message in later), "图只挂一次，下一次请求就没了"
    assert PICTURE_BASE64 not in json.dumps(later, ensure_ascii=False)
    assert llm.vision_prompts == [], "能看图就不再转文字"
    await agent.stop()


@pytest.mark.parametrize(
    ("vision_reply", "expected"),
    [
        ("屋顶左边比右边低一格，箱子在门口，牛在屋后", "屋顶左边比右边低一格，箱子在门口，牛在屋后"),
        ("", "画面没能转成文字"),
    ],
    ids=["described", "vision_model_failed"],
)
async def test_a_text_only_model_gets_the_picture_described(vision_reply: str, expected: str) -> None:
    """不能看图的模型：按这次调用拼问题交给视觉模型，转出的文字接在观察后面；转不出来就照实说。"""
    server = FakeMaicraft()
    llm = ScriptLlm(_look_then_deliver({"id": "小木屋"}), vision_reply=vision_reply)
    agent, _llm, bus = await make_minecraft_agent(server, llm=llm)

    agent.receive_delegation(instruction="看看盖好的房子好不好看", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    observation = _observation(_tool_messages(llm.seen[1])[0])
    assert observation["picture_description"] == expected
    assert observation["data"]["summary"] == server.picture_view["summary"], "文字那一块原样保留"
    question = llm.vision_prompts[0]
    assert "「小木屋」" in question and "b1（minecraft:chest）" in question and "e1（minecraft:cow）" in question
    assert "e9" not in question, "没标在图上的编号不问"
    assert not any(_image_refs(message) for request in llm.seen for message in request)
    await agent.stop()


async def test_pictures_beyond_the_task_budget_or_too_soon_never_reach_the_mod() -> None:
    """一个任务里看画面有次数和间隔：第 7 张、离上一张不到 10 秒都不调 Mod，直接回一句改用结构化数据。"""
    server = FakeMaicraft()
    now = [1000.0]
    agent, _llm, _bus = await make_minecraft_agent(server, clock=lambda: now[0])

    def mod_pictures() -> int:
        return sum(1 for arguments in server.calls_to("observe") if arguments.get("what") == "picture")

    assert (await agent._execute_tool("maicraft_observe", {"what": "picture"}))["ok"] is True
    now[0] += 3
    too_soon = await agent._execute_tool("maicraft_observe", {"what": "picture"})
    assert too_soon["ok"] is False and too_soon["error"]["code"] == "picture_limit"
    assert "7 秒后才能再看" in too_soon["error"]["message"]
    assert mod_pictures() == 1, "间隔不够不调 Mod"
    now[0] += 7
    for _ in range(5):
        assert (await agent._execute_tool("maicraft_observe", {"what": "picture"}))["ok"] is True
        now[0] += 10
    assert mod_pictures() == 6

    seventh = await agent._execute_tool("maicraft_observe", {"what": "picture"})
    assert seventh["ok"] is False
    assert seventh["error"]["message"] == "这个任务已经看了 6 次画面，接下来用 observe 的结构化数据"
    assert mod_pictures() == 6
    await agent.stop()


async def test_the_picture_budget_follows_the_config() -> None:
    """次数与间隔是配置项：调成每个任务 1 次后第二张就不调 Mod；调成 0 就不让看。"""
    server = FakeMaicraft()
    config = offline_config(events_wait_ms=1000).model_copy(
        update={"picture": MinecraftPictureConfig(max_per_task=1, min_interval_ms=0)}
    )
    agent, _llm, _bus = await make_minecraft_agent(server, config=config)

    assert (await agent._execute_tool("maicraft_observe", {"what": "picture"}))["ok"] is True
    second = await agent._execute_tool("maicraft_observe", {"what": "picture"})
    assert second["error"]["message"] == "这个任务已经看了 1 次画面，接下来用 observe 的结构化数据"
    assert MinecraftPictureConfig().max_per_task == 6 and MinecraftPictureConfig().min_interval_ms == 10_000
    await agent.stop()


async def test_the_picture_tool_result_event_carries_text_and_a_note_but_no_base64() -> None:
    """图不进事件流（事件也落诊断记录）：maicraft_observe 的结果事件只有文字，外加"带了一张图"。"""
    bus = EventBus(enable_stats=False)
    received: List[ToolResultPayload] = []

    async def on_result(event_name: str, payload: ToolResultPayload, source: str) -> None:
        received.append(payload)

    bus.on("tool.result.#", on_result, ToolResultPayload)
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server, registry=ToolRegistry(event_bus=bus))
    try:
        await agent._execute_tool("maicraft_observe", {"what": "picture"})
        await bus.wait_until_drained()
        [event] = [payload for payload in received if payload.arguments.get("what") == "picture"]
        assert PICTURE_BASE64 not in json.dumps(event.model_dump(), ensure_ascii=False)
        assert event.result["attached_images"] == "带了 1 张图（原图不进事件）"
        assert '"in_picture"' in event.result["content"]
    finally:
        await agent.stop()
        await bus.cleanup()


async def _screen_stub(invocation: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(tool_name=invocation.tool_name, success=True, content="桌面")


async def test_the_game_agent_does_not_get_the_desktop_screenshot_tool_but_others_still_do() -> None:
    """截桌面的工具截到的多半是 OBS 或控制台：不交给游戏 Agent 的模型；它的可见名单不动，别的 Agent 照用。"""
    registry = ToolRegistry()
    registry.register_provider(make_provider_from_specs("vision", [(replace(LOOK_AT_SCREEN_SPEC), _screen_stub)]))
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, registry=registry)

    agent.receive_delegation(instruction="看看周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    given = {tool["name"] for tool in llm.tools_given[0]}
    assert "vision_look_at_screen" not in given and "maicraft_observe" in given
    for other in ("text_adv", "streamer"):
        assert "vision_look_at_screen" in {spec.full_name for spec in registry.list_tools(for_agent=other)}
    await agent.stop()


async def test_the_streamer_never_reaches_the_picture_view() -> None:
    """主播一直看着直播画面：她的速览只读结构化数据，MaiCraft 的工具（含画面视图）对她不可见。"""
    server = FakeMaicraft()
    registry = ToolRegistry()
    agent, _llm, _bus = await make_minecraft_agent(server, registry=registry)

    await agent._glance()

    assert {arguments.get("what") for arguments in server.calls_to("observe")} <= {"self", "scene", "world_memory"}
    streamer_tools = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
    assert "maicraft_observe" not in streamer_tools and "minecraft_glance" in streamer_tools
    await agent.stop()


def test_game_prompt_says_when_a_picture_is_worth_it_and_how_to_read_it() -> None:
    """游戏 Agent 提示词：默认不看画面、要转头先暂停、退几步装下再拍；谁说了什么不从图里认。"""
    reset_prompt_manager()
    try:
        game = get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()
    assert "## 看画面" in game and "**默认不看**" in game
    assert "with_menu=true" in game and "unlabeled=true" in game and "step_back_blocks" in game
    assert "maicraft_goal(operation=pause)" in game
    assert "events(topic=chat)" in game and "截图里的聊天栏" not in game
