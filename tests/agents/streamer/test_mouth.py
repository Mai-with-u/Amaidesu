"""嘴：游戏里出事时当场出一句短反应——由头合并、冷却、安静门槛、现场卡、输出解析与兜底。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from src.agents.streamer.config import StreamerMouthConfig
from src.agents.streamer.mouth import Mouth, Trigger
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.scene_card import GoalLine
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager

NOW = 1_800_000_000_000


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> int:
        return self.now


class _LLM:
    """模型替身：按顺序回放回复，记下每次的消息与系统提示词。"""

    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.calls: List[Dict[str, Any]] = []

    async def generate(self, messages: List[Dict[str, str]], *, system: str, profile: str) -> Response:
        self.calls.append({"messages": messages, "system": system, "profile": profile})
        content = self.contents.pop(0) if self.contents else "[沉默]"
        return Response(success=True, content=content, request_id=f"req_{len(self.calls)}")


class _Speech:
    """发声分发器替身：记下反应通道的调用。"""

    def __init__(self, accept: bool = True) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.accept = accept

    async def dispatch_reaction(self, speech: str, **kwargs: Any) -> Optional[str]:
        self.calls.append({"speech": speech, **kwargs})
        return f"utt_{len(self.calls)}" if self.accept else None


@pytest.fixture
def prompts():
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


def _mouth(
    llm: _LLM, speech: _Speech, prompts: Any, *, live: bool = True, clock: Optional[_Clock] = None, **config: Any
) -> Mouth:
    clock = clock or _Clock()
    scene_reads: List[int] = []

    async def _scene() -> str:
        scene_reads.append(clock())
        return "在主世界的平原。白天，晴。身边有苦力怕在左后方，是敌对的。"

    mouth = Mouth(
        StreamerMouthConfig(enabled=True, **config),
        llm_service=llm,
        prompt_service=prompts,
        speech=speech,
        room_state=RoomState(),
        persona={"bot_name": "麦麦", "personality": "嘴硬但心软", "style_constraints": "口语化，短句为主"},
        speech_max_chars=40,
        gate_window=10,
        gate_ratio=0.5,
        word_filter=None,
        goals_provider=lambda: [GoalLine(game="minecraft", instruction="回到基地家中", started_ms=NOW - 120_000)],
        notebook_provider=lambda: "",
        segment_provider=lambda: "互动闲聊",
        scene_reader=_scene,
        is_live=lambda: live,
        clock=clock,
    )
    mouth.scene_reads = scene_reads  # type: ignore[attr-defined]
    return mouth


def _trigger(
    text: str = "被苦力怕炸到一下，血掉了不少", *, key: str = "body:attacked", urgent: bool = True, at_ms: int = NOW
) -> Trigger:
    return Trigger(at_ms=at_ms, key=key, text=text, urgent=urgent, refresh_scene=False)


async def test_urgent_encounter_gets_one_short_reaction_with_scene_card(prompts: Any) -> None:
    """被打了：嘴拿人设与现场卡生成一句，带情绪插队出声，并记下发言时刻。"""
    llm, speech = _LLM("scared：哇！谁炸我？！"), _Speech()
    mouth = _mouth(llm, speech, prompts)
    mouth.note_speech("走走走，回家！", NOW - 30_000)

    assert await mouth.react_once([_trigger()]) == "哇！谁炸我？！"

    call = speech.calls[0]
    assert (call["emotion"], call["priority"], call["wait_ms"], call["gap_ms"]) == ("scared", 5, 6000, 1500)
    system = llm.calls[0]["system"]
    assert "你叫 麦麦" in system and "你嘴硬但心软" in system and "不超过 40 字" in system
    last = llm.calls[0]["messages"][-1]["content"]
    assert last.startswith("【现场】") and "在主世界的平原" in last
    assert "【这次的由头】刚才游戏里：被苦力怕炸到一下，血掉了不少" in last
    assert "（这是打算，不一定已经做到" in last and "【你刚说过】（别再说一遍）\n- 走走走，回家！" in last
    assert llm.calls[0]["messages"][0] == {"role": "assistant", "content": "走走走，回家！"}
    assert mouth._room_state.last_speech_ms == NOW


async def test_silence_and_cooldown_keep_the_mouth_from_chattering(prompts: Any) -> None:
    """嘴可以不说；同一类遭遇冷却期内只算一次，第二次连模型都不叫。"""
    llm, speech = _LLM("[沉默]"), _Speech()
    clock = _Clock()
    mouth = _mouth(llm, speech, prompts, clock=clock)

    assert await mouth.react_once([_trigger()]) is None
    clock.now += 5_000
    assert await mouth.react_once([_trigger(at_ms=clock.now)]) is None
    assert len(llm.calls) == 1 and speech.calls == []

    clock.now += 16_000
    llm.contents.append("angry：又来？！")
    assert await mouth.react_once([_trigger(at_ms=clock.now)]) == "又来？！"


async def test_small_things_wait_for_quiet(prompts: Any) -> None:
    """急事开始结束、天色这类小事：她刚说过话就不开口，安静一阵才为它说一句。"""
    llm, speech = _LLM("neutral：天亮了，可以出门了。"), _Speech()
    clock = _Clock()
    mouth = _mouth(llm, speech, prompts, clock=clock)
    mouth._room_state.record_speech(NOW - 5_000)
    small = _trigger("天色变了：天亮了", key="body:unknown", urgent=False)

    assert await mouth.react_once([small]) is None and llm.calls == []

    mouth._room_state.record_speech(NOW - 30_000)
    assert (
        await mouth.react_once([_trigger("天色变了：天亮了", key="body:unknown2", urgent=False)])
        == "天亮了，可以出门了。"
    )


async def test_not_live_or_stale_trigger_says_nothing(prompts: Any) -> None:
    """没开播不说；事件过去太久（再说就成了事后讲故事）也不说。"""
    llm, speech = _LLM("scared：哇！"), _Speech()
    assert await _mouth(llm, speech, prompts, live=False).react_once([_trigger()]) is None
    assert await _mouth(llm, speech, prompts).react_once([_trigger(at_ms=NOW - 25_000)]) is None
    assert llm.calls == []


async def test_repeat_of_her_own_line_is_dropped(prompts: Any) -> None:
    """和她最近说过的话大面积重复：直接丢弃，不重试。"""
    llm, speech = _LLM("scared：哇！谁炸我？！"), _Speech()
    mouth = _mouth(llm, speech, prompts)
    mouth.note_speech("哇！谁炸我？！", NOW - 40_000)

    assert await mouth.react_once([_trigger()]) is None and speech.calls == []


async def test_output_without_emotion_falls_back_to_neutral_and_long_line_is_cut(prompts: Any) -> None:
    """拆不出情绪按 neutral；超出长度预算按完整句子截短。"""
    llm, speech = (
        _LLM("掉水里了。行吧，顺便洗个澡，反正今天也没别的事要做，就当是给自己放个假算了吧，水还挺凉快的嘛哈哈哈。"),
        _Speech(),
    )

    spoken = await _mouth(llm, speech, prompts).react_once([_trigger()])

    assert spoken == "掉水里了。" and speech.calls[0]["emotion"] == "neutral"


async def test_scene_is_reread_only_when_stale_or_the_world_changed(prompts: Any) -> None:
    """一句话现场按间隔重读；死亡、重生、换维度时立即重读。"""
    llm, speech = _LLM("scared：哇！", "sad：唉。", "crying：没了……"), _Speech()
    clock = _Clock()
    mouth = _mouth(llm, speech, prompts, clock=clock)

    await mouth.react_once([_trigger(key="body:a")])
    clock.now += 3_000
    await mouth.react_once([_trigger(key="body:b", at_ms=clock.now)])
    assert len(mouth.scene_reads) == 1  # type: ignore[attr-defined]

    clock.now += 1_000
    died = Trigger(at_ms=clock.now, key="body:died", text="死了：被蜘蛛杀死了", urgent=True, refresh_scene=True)
    await mouth.react_once([died])
    assert len(mouth.scene_reads) == 2  # type: ignore[attr-defined]


async def test_loop_merges_back_to_back_triggers_into_one_reaction(prompts: Any) -> None:
    """同一瞬间连发的由头并成一次生成：只叫一次模型，由头里两件事都在。"""
    llm, speech = _LLM("scared：怎么回事啊！"), _Speech()
    mouth = _mouth(llm, speech, prompts, clock=lambda: NOW)  # type: ignore[arg-type]
    await mouth.start()
    try:
        mouth.react("body:attacked", "被苦力怕炸到一下", urgent=True, at_ms=NOW)
        mouth.react("body:need_unhandled", "有件事没办成：饿了但没吃上", urgent=True, at_ms=NOW)
        for _ in range(100):
            if speech.calls:
                break
            await asyncio.sleep(0.02)
    finally:
        await mouth.stop()
    assert len(llm.calls) == 1
    last = llm.calls[0]["messages"][-1]["content"]
    assert "被苦力怕炸到一下；有件事没办成：饿了但没吃上" in last
