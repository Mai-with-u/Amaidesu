"""人设边界栏接线：配置里的 persona.boundaries 经 Agent 装配，Planner 与嘴发给模型的系统提示词里都带它。

各通路自己的模板渲染另有测试；这里验模板之外的一环：Agent 装配时是否真把配置里的边界栏
递给了 Planner 和嘴（Replyer 通路在它自己的测试里验）。用真实提示词模板和假 LLM，
从 Agent 的公开入口驱动：决策走调试门面，嘴走总线上的游戏事件；只断言假 LLM 记下的请求里
有没有"不答应的事：…"这一行，不依赖它在提示词里的位置。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

from src.agents.streamer.config import StreamerPersonaConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from tests.agents.streamer.helpers import make_streamer_agent
from tests.modules.llm.fakes import FakeLLMManager

#: 嘴生成反应、决策轮收到这句都不会再调工具：决策自然收束为静默，嘴念出一句台词
_REPLY = "happy：好家伙"

#: 三条说话通路（Planner、Replyer、嘴）共用的 AI 声明口径
_AI_DECLARATION = "被问到是不是真人时，坦白自己是 AI 主播，可以俏皮，但不能否认"

#: 等嘴开口的安全上限；正常几百毫秒就够，只在出问题时才会等满（事件触发，不是轮询）
_SPEECH_TIMEOUT_S = 5.0


@pytest.fixture
def prompts() -> Iterator[Any]:
    """真实提示词模板：接线测试要的是模板拼出的最终文本，不是 MagicMock 的 "PROMPT"。"""
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


async def _system_prompts_sent(prompts: Any, boundaries: Optional[str]) -> Tuple[List[str], List[str]]:
    """起一个开着嘴的 Agent，让决策与嘴各发出一次请求，返回 (决策的系统提示词们, 嘴的系统提示词们)。

    顺序：先走决策轮（调试门面送一条弹幕）-> 开播（嘴只在开播期间说话）-> 总线上来一条游戏交付
    事件，嘴按它生成一句、出声 -> 等发言事件到达，此时假 LLM 已记下嘴的请求。
    ``boundaries`` 为 None 时不覆盖，用配置默认值。
    """
    llm = FakeLLMManager(response=Response(success=True, content=_REPLY))
    bus = EventBus()
    sections: Dict[str, Any] = {"mouth": {"enabled": True}, "background": {"enabled": False}}
    if boundaries is not None:
        sections["persona"] = {"boundaries": boundaries}
    agent = make_streamer_agent(sections, llm=llm, prompt=prompts, event_bus=bus)

    spoken = asyncio.Event()

    async def _on_speech(event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        spoken.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)
    await agent.start()
    try:
        await agent.debug_test_decision(batch=[{"nickname": "观众", "text": "你是真人吗"}])

        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1, title="测试场"), source="test")
        await bus.wait_until_drained()
        report = GamePayload(
            game="minecraft", event_type="report", message="床做好了，放在屋里", report_kind="delivery"
        )
        await bus.emit(CoreEvents.GAME_REPORT, report, source="minecraft")
        # 嘴把连发的由头攒一小会儿再生成（模块内固定的攒批窗口），等它真出声再看请求
        await asyncio.wait_for(spoken.wait(), timeout=_SPEECH_TIMEOUT_S)
    finally:
        await agent.cleanup()
        await bus.cleanup()

    planner = [_planner_system(call) for call in llm.calls if call.get("profile") == "planner"]
    mouth = [call["system"] for call in llm.calls if call.get("profile") == "replyer" and "system" in call]
    return planner, mouth


def _planner_system(call: Dict[str, Any]) -> str:
    """决策请求里的系统提示词：消息列表里 system 角色的那几条拼起来。"""
    return "\n".join(message["content"] for message in call["input"] if message.get("role") == "system")


@pytest.mark.parametrize(
    ("boundaries", "expected"),
    [
        # 运营者在人设里写的一段话，原样接在"不答应的事："后面
        pytest.param("线下见面、钱和红包", "不答应的事：线下见面、钱和红包", id="custom_text"),
        # 没改人设：配置默认值（并列短语）经同一条路径带出，读作完整的一句规矩
        pytest.param(None, "不答应的事：" + StreamerPersonaConfig().boundaries, id="config_default"),
    ],
)
async def test_configured_boundaries_reach_planner_and_mouth_prompts(
    prompts: Any, boundaries: Optional[str], expected: str
) -> None:
    """人设里有边界栏：决策与嘴发给模型的系统提示词里都有"不答应的事：<那段文字>"。"""
    planner, mouth = await _system_prompts_sent(prompts, boundaries)

    assert planner and mouth
    assert all(expected in system for system in planner)
    assert all(expected in system for system in mouth)


@pytest.mark.parametrize("boundaries", [pytest.param("", id="empty"), pytest.param("  \n ", id="blank")])
async def test_empty_boundaries_leave_no_line_in_planner_or_mouth_prompts(prompts: Any, boundaries: str) -> None:
    """边界栏清空（或只有空白）：两条通路的系统提示词里都没有"不答应的事："这一行，也不留空标签。"""
    planner, mouth = await _system_prompts_sent(prompts, boundaries)

    assert planner and mouth
    assert all("不答应的事：" not in system for system in planner + mouth)
    # 清空边界栏删不掉 AI 声明：它写死在模板里，不跟着人设配置走
    assert all(_AI_DECLARATION in system for system in planner + mouth)
