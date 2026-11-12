"""嘴：游戏里出事时当场出一句短反应——由头合并、冷却、安静门槛、现场卡、输出解析与兜底、接线。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.config import StreamerMouthConfig, StreamerPersonaConfig
from src.agents.streamer.mouth import Mouth, Trigger
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.scene_card import GoalLine
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from tests.agents.streamer.helpers import make_streamer_agent

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
    llm: _LLM,
    speech: _Speech,
    prompts: Any,
    *,
    live: bool = True,
    clock: Optional[_Clock] = None,
    boundaries: str = "",
    **config: Any,
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
        persona={
            "bot_name": "麦麦",
            "personality": "嘴硬但心软",
            "style_constraints": "口语化，短句为主",
            "boundaries": boundaries,
        },
        speech_max_chars=40,
        word_filter=None,
        goals_provider=lambda: [GoalLine(game="minecraft", instruction="回到基地家中", started_ms=NOW - 120_000)],
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


async def test_mouth_carries_no_memory_in_system_prompt_or_scene_card(prompts: Any) -> None:
    """嘴不拿任何记忆：系统提示词里没有长期笔记，现场卡里没有备忘录（悬着的事）一段。"""
    llm = _LLM("happy：好家伙")
    mouth = _mouth(llm, _Speech(), prompts)

    await mouth.react_once([_trigger()])

    assert "长期笔记" not in llm.calls[0]["system"]
    card = llm.calls[0]["messages"][-1]["content"]
    assert card.startswith("【现场】") and "悬着的事" not in card


#: AI 声明写死在三条说话通路的模板里，不随人设配置变；嘴的这条后面接边界栏那一行（有的话）
_AI_RULE = "- 被问到是不是真人时，坦白自己是 AI 主播，可以俏皮，但不能否认。"


@pytest.mark.parametrize(
    ("boundaries", "rendered"),
    [
        # 配置默认值是并列短语，和行首的"不答应的事："拼起来读作一句完整的规矩
        pytest.param(
            StreamerPersonaConfig().boundaries,
            "不答应的事：线下见面、钱和红包、私下联系；改变自己是谁、以后怎么称呼某人",
            id="config_default",
        ),
        # 运营者在 WebUI 里写成多行：折成一行用"；"连接，空行和首尾空白丢掉，
        # 免得第二行起在规矩列表里变成没有列表记号的裸文本
        pytest.param(
            "线下见面、钱和红包\n  \n 改变自己是谁 \n",
            "不答应的事：线下见面、钱和红包；改变自己是谁",
            id="multiline_folded",
        ),
    ],
)
async def test_boundaries_render_as_one_rule_line_after_ai_declaration(
    prompts: Any, boundaries: str, rendered: str
) -> None:
    """边界栏非空：AI 声明后多出一条独立的规矩行（列表记号 + "不答应的事：…"），多行折成一行。"""
    llm = _LLM("happy：好家伙")

    await _mouth(llm, _Speech(), prompts, boundaries=boundaries).react_once([_trigger()])

    system = llm.calls[0]["system"]
    assert f"{_AI_RULE}\n- {rendered}\n- 说话风格：" in system
    # 折行后整段边界栏只占一行，没有裸文本行混进规矩列表
    assert system.count("不答应的事：") == 1


@pytest.mark.parametrize("boundaries", [pytest.param("", id="empty"), pytest.param("  \n ", id="blank")])
async def test_empty_boundaries_omit_the_whole_line_but_keep_ai_declaration(prompts: Any, boundaries: str) -> None:
    """边界栏没写（空串或只有空白）：整行省略，AI 声明后面直接接说话风格，规矩列表里不留空行；
    AI 声明不受影响。"""
    llm = _LLM("happy：好家伙")

    await _mouth(llm, _Speech(), prompts, boundaries=boundaries).react_once([_trigger()])

    system = llm.calls[0]["system"]
    assert f"{_AI_RULE}\n- 说话风格：" in system
    assert "不答应的事" not in system


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


async def test_output_without_emotion_falls_back_to_neutral_and_long_line_is_cut(prompts: Any) -> None:
    """拆不出情绪按 neutral；超出长度预算按完整句子截短。"""
    llm, speech = (
        _LLM("掉水里了。行吧，顺便洗个澡，反正今天也没别的事要做，就当是给自己放个假算了吧，水还挺凉快的嘛哈哈哈。"),
        _Speech(),
    )

    spoken = await _mouth(llm, speech, prompts).react_once([_trigger()])

    assert spoken == "掉水里了。" and speech.calls[0]["emotion"] == "neutral"


async def test_made_up_multiword_emotion_prefix_is_stripped(prompts: Any) -> None:
    """场 65 实例"so happy：回来啦"——自造的情绪名（含空格、不在枚举）同样按"情绪名：台词"形态剥除，前缀不漏进台词；情绪收敛为 neutral。"""
    llm, speech = (
        _LLM("so happy：回来啦，还是麦窝这边舒坦～"),
        _Speech(),
    )

    spoken = await _mouth(llm, speech, prompts).react_once([_trigger()])

    assert spoken == "回来啦，还是麦窝这边舒坦～" and speech.calls[0]["emotion"] == "neutral"


async def test_chinese_prefixed_colon_line_is_kept_as_speech(prompts: Any) -> None:
    """冒号前含中文的行不是"情绪名：台词"形态，整行照常念出来。"""
    line = "大家：这话说的，我都不好意思了。"
    llm, speech = _LLM(line), _Speech()

    spoken = await _mouth(llm, speech, prompts).react_once([_trigger()])

    assert spoken.startswith("大家：")


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


async def test_latency_is_logged_when_the_line_actually_starts_playing(prompts: Any, loguru_capture: Any) -> None:
    """延迟日志等这句真轮到播放时才记：等前一句的那段算进"从事件到开始播"，还没轮到就不记。"""
    clock = _Clock()
    llm, speech = _LLM("scared：疼疼疼！"), _Speech()
    mouth = _mouth(llm, speech, prompts, clock=clock)

    assert await mouth.react_once([_trigger(at_ms=clock.now - 1_000)]) == "疼疼疼！"

    def _latency_logs() -> List[str]:
        return [record["message"] for record in loguru_capture.records if record["message"].startswith("嘴：")]

    assert _latency_logs() == []
    clock.now += 4_000
    speech.calls[0]["on_started"]()
    assert len(_latency_logs()) == 1
    assert "等前一句 4000ms" in _latency_logs()[0] and "从事件到开始播 5000ms" in _latency_logs()[0]


# ---------------- 接线 ----------------

# 登记（只读穿透，合并时进底账）：断言读取嘴的最近记录（agent._mouth._records）与
# 发声分发器槽位（agent._speech.dispatch）；嘴的最近记录暂无公开只读面。


def test_mouth_is_off_by_default() -> None:
    """开关默认关闭且未注入替身：不构造嘴，一切照旧。"""
    assert make_streamer_agent()._mouth is None


async def test_with_mouth_on_death_goes_to_the_mouth_not_the_planner() -> None:
    """嘴开着：死亡交给嘴当场反应，不再专门唤醒 Planner；关着时照旧唤醒。"""
    payload = BodyEventPayload(game="minecraft", kind="died", summary="死了：被蜘蛛杀死了")

    mouth = MagicMock()
    reactions: List[tuple] = []
    mouth.react.side_effect = lambda key, text, **kw: reactions.append((key, text, kw))
    agent = make_streamer_agent({"mouth": {"enabled": True}}, mouth=mouth)
    await agent._on_body_event("game.body.died", payload, "maicraft_attention")
    assert agent._proactive_signals.game_pending is False
    assert reactions == [("body:died", "死了：被蜘蛛杀死了", {"urgent": True, "refresh_scene": True})]

    plain = make_streamer_agent()
    await plain._on_body_event("game.body.died", payload, "maicraft_attention")
    assert plain._proactive_signals.game_pending is True


async def test_with_mouth_on_progress_reacts_but_attention_alerts_do_not() -> None:
    """游戏进展、交付卡住当场反应（Planner 照样被交付唤醒去定夺）；急事提醒与遭遇是同一件事，不另触发；
    游戏 Agent 自己的报错不是游戏里发生的事，既不触发也不进嘴的最近记录。"""
    mouth = MagicMock()
    reactions: List[str] = []
    mouth.react.side_effect = lambda key, text, **kw: reactions.append(key)
    agent = make_streamer_agent({"mouth": {"enabled": True}}, mouth=mouth)

    report = GamePayload(game="minecraft", event_type="report", message="床做好了，放在屋里", report_kind="delivery")
    await agent._on_game_event("game.report", report, "minecraft")
    alert = GamePayload(game="minecraft", event_type="attention_required", message="急事处理完了：打完了，没掉血")
    await agent._on_game_event("game.attention_required", alert, "minecraft")
    error = GamePayload(game="minecraft", event_type="error", message="LLM 推理异常: ValueError: 请求被拒")
    await agent._on_game_event("game.error", error, "minecraft")

    assert reactions == ["game:report"] and agent._proactive_signals.game_pending is True
    assert all("LLM 推理异常" not in call.args[0] for call in mouth.note_game.call_args_list)


async def test_viewer_messages_go_into_the_mouth_records() -> None:
    """弹幕记进嘴的最近记录（阶段 1 不由嘴回弹幕，但现场要知道观众刚说了什么）。"""
    agent = make_streamer_agent({"mouth": {"enabled": True}})
    message = RoomMessagePayload(
        message_type="danmaku", user=RoomMessageUser(id="u1", name="小明"), content="麦麦小心后面"
    )
    await agent.handle_message(message)
    records = list(agent._mouth._records)  # type: ignore[union-attr]
    assert [(record.kind, record.sender, record.text) for record in records] == [("viewer", "小明", "麦麦小心后面")]


async def test_every_spoken_line_reaches_the_mouth_records_without_subscribing_to_speech() -> None:
    """她说过的每句话经发声分发器直连记进嘴（Agent 不订阅自己的输出事件）：Planner 的回话也算。"""
    agent = make_streamer_agent({"mouth": {"enabled": True}})
    await agent._speech.dispatch({"speech": "走走走，回家！", "emotion": {"name": "happy", "intensity": 0.5}})
    records = list(agent._mouth._records)  # type: ignore[union-attr]
    assert [(record.kind, record.text) for record in records] == [("speech", "走走走，回家！")]


async def test_emotion_turn_splits_into_lines_dispatched_as_follow_ups(prompts: Any) -> None:
    """情绪转了就拆几行：第一行照常出声，后面几行带各自情绪跟在后面；漏写情绪的行沿用上一行，旁白不念。"""
    llm = _LLM("scared：哇！什么东西？！\n……原来是只鸡。\n（松了口气）\nhappy：吓我一跳\nangry：第四行不要")
    speech = _Speech()
    mouth = _mouth(llm, speech, prompts)

    assert await mouth.react_once([_trigger()]) == "哇！什么东西？！……原来是只鸡。吓我一跳"

    call = speech.calls[0]
    assert (call["speech"], call["emotion"]) == ("哇！什么东西？！", "scared")
    # 最多三行台词（旁白不占名额），第四行起不要
    assert call["follow_ups"] == [("……原来是只鸡。", "scared"), ("吓我一跳", "happy")]
    assert "这句话里情绪转了" in llm.calls[0]["system"]


async def test_follow_up_lines_that_overflow_the_budget_are_dropped_whole(prompts: Any) -> None:
    """几行加起来超预算：后面放不下的整行不要，不截成半句；第一行照常保留。"""
    llm = _LLM("scared：哇！什么东西？！\nspeechless：" + "原来" * 30 + "是只鸡。")
    speech = _Speech()
    mouth = _mouth(llm, speech, prompts)

    assert await mouth.react_once([_trigger()]) == "哇！什么东西？！"
    assert speech.calls[0]["follow_ups"] == []


@pytest.mark.parametrize(
    ("raw_line", "expected_emotion", "expected_text"),
    [
        # 第 65 场 21:49:52 回归：模型输出带空格的非法情绪名 "so happy："，标签连同冒号剥除，不念出来
        ("so happy：回来啦，还是这边舒坦～", "neutral", "回来啦，还是这边舒坦～"),
        # 带连字符或外层括号的标签同样剥除；合法枚举或已知近义词按枚举走
        ("[very-happy]：总算缓过来了！", "neutral", "总算缓过来了！"),
        ("[cheerful]: 捡到好东西啦！", "happy", "捡到好东西啦！"),
    ],
)
async def test_invalid_or_spaced_emotion_prefix_is_stripped_not_spoken(
    prompts: Any, raw_line: str, expected_emotion: str, expected_text: str
) -> None:
    """不合法或带空格的情绪标签连同冒号剥掉，不作为台词念出来；情绪收敛到现有枚举。"""
    llm, speech = _LLM(raw_line), _Speech()
    mouth = _mouth(llm, speech, prompts)

    spoken = await mouth.react_once([_trigger()])

    assert spoken == expected_text
    assert "happy" not in spoken.lower() and "：" not in spoken
    assert speech.calls[0]["emotion"] == expected_emotion


async def test_mouth_verbatim_repeat_triggers_rephrase_instead_of_dropping(prompts: Any) -> None:
    """嘴初稿命中本场近逐字复读时，打回让表达模型换个说法重说一次，不直接把整句吞掉。"""
    earlier = "好家伙，连打我好几下，先别管树苗了，吃我一剑！"
    llm = _LLM(
        f"angry：{earlier}",
        "scared：又从背后偷袭我，这只蜘蛛没完了是吧！",
    )
    speech = _Speech()
    mouth = _mouth(llm, speech, prompts)
    mouth.note_speech(earlier, NOW - 40_000)

    spoken = await mouth.react_once([_trigger()])

    assert spoken == "又从背后偷袭我，这只蜘蛛没完了是吧！"
    assert len(llm.calls) == 2
    assert len(speech.calls) == 1


async def test_mouth_hides_room_enters_and_keeps_planner_speeches_under_event_flood(prompts: Any) -> None:
    """嘴的现场卡不带直播间进房名单（欢迎归决策环节），且高频游戏事件刷满 _records 后仍看得见刚说过的台词。"""
    llm, speech = _LLM("scared：先把盾举起来！"), _Speech()
    mouth = _mouth(llm, speech, prompts)
    mouth._room_state.record_enter("u_b", "观众乙", now_ms=NOW - 5_000)
    mouth.note_speech("刚才已经把工作台挪到屋里角落了。", NOW - 25_000)
    for idx in range(40):
        mouth.note_game(f"远处史莱姆跳动第 {idx} 次", NOW - 20_000 + idx * 100)

    await mouth.react_once([_trigger()])

    card_msg = llm.calls[0]["messages"][-1]["content"]
    assert "观众乙" not in card_msg
    assert "刚才已经把工作台挪到屋里角落了" in card_msg


async def test_mouth_strips_duplicate_greeting_for_already_welcomed_player(prompts: Any) -> None:
    """本场已被决策环节欢迎过的玩家，嘴反应时若顺嘴再欢迎一遍则剥掉重复欢迎，只留对当下遭遇的反应。"""
    llm = _LLM("happy：欢迎玩家甲上线！这苦力怕怎么贴脸炸我呀！")
    speech = _Speech()
    mouth = _mouth(llm, speech, prompts)
    mouth._room_state.mark_welcomed(user_name="玩家甲")

    spoken = await mouth.react_once([_trigger()])

    assert spoken == "这苦力怕怎么贴脸炸我呀！"
    assert "欢迎" not in spoken
