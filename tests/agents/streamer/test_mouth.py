"""即时反应：游戏里出事时当场出一句短反应——由头合并、冷却、安静门槛、现场卡、输出解析与兜底、接线。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.config import StreamerMouthConfig, StreamerPersonaConfig
from src.agents.streamer.mouth import Mouth, Trigger
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.scene_card import GoalLine
from src.agents.streamer.speech_dispatcher import SPEECH_PRIORITY_HIGHLIGHT
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
    """被打了：即时反应拿人设与现场卡生成一句，带情绪插队出声，并记下发言时刻。"""
    llm, speech = _LLM("scared：哇！谁炸我？！"), _Speech()
    mouth = _mouth(llm, speech, prompts)
    mouth.note_speech("走走走，回家！", NOW - 30_000)

    assert await mouth.react_once([_trigger()]) == "哇！谁炸我？！"

    call = speech.calls[0]
    assert (call["emotion"], call["priority"], call["allow_hard_interrupt"], call["wait_ms"], call["gap_ms"]) == (
        "scared",
        SPEECH_PRIORITY_HIGHLIGHT,
        False,
        6000,
        1500,
    )
    system = llm.calls[0]["system"]
    assert "你叫 麦麦" in system and "你嘴硬但心软" in system and "不超过 40 字" in system
    last = llm.calls[0]["messages"][-1]["content"]
    assert last.startswith("【现场】") and "在主世界的平原" in last
    assert "【这次的由头】刚才游戏里：被苦力怕炸到一下，血掉了不少" in last
    assert "（这是打算，不一定已经做到" in last and "【你刚说过】（别再说一遍）\n- 走走走，回家！" in last
    assert llm.calls[0]["messages"][0] == {"role": "assistant", "content": "走走走，回家！"}
    assert mouth._room_state.last_speech_ms == NOW


async def test_mouth_carries_no_memory_in_system_prompt_or_scene_card(prompts: Any) -> None:
    """即时反应不拿任何记忆：系统提示词里没有长期笔记，现场卡里没有备忘录（悬着的事）一段。"""
    llm = _LLM("happy：好家伙")
    mouth = _mouth(llm, _Speech(), prompts)

    await mouth.react_once([_trigger()])

    assert "长期笔记" not in llm.calls[0]["system"]
    card = llm.calls[0]["messages"][-1]["content"]
    assert card.startswith("【现场】") and "悬着的事" not in card


async def test_mouth_rule_keeps_broadcast_talk_third_person(prompts: Any) -> None:
    """口播不直呼游戏玩家「你」：现场归属那条规矩里带明他们听不到直播，别对着镜头喊「你」。"""
    llm = _LLM("happy：好家伙")

    await _mouth(llm, _Speech(), prompts).react_once([_trigger()])

    assert "他们听不到直播，别对着镜头喊「你」" in llm.calls[0]["system"]


#: AI 声明写死在三条说话通路的模板里，不随人设配置变；即时反应的这条后面接边界栏那一行（有的话）
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
    """即时反应可以不说；同一类遭遇冷却期内只算一次，第二次连模型都不叫。"""
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


async def test_separate_sighting_buckets_do_not_swallow_each_other_in_cooldown(prompts: Any) -> None:
    """稀有矿与强敌分属不同冷却桶：刚见矿又撞见强敌不会互吞；同一种目击在冷却窗内不连叫。"""
    llm, speech = _LLM("happy：哇，钻石！", "scared：怎么还有监守者？！"), _Speech()
    clock = _Clock()
    mouth = _mouth(llm, speech, prompts, clock=clock)

    ore_first = _trigger("眼前露出了深层钻石矿石好几块（在前方）", key="sighting:rare_ore", at_ms=clock.now)
    assert await mouth.react_once([ore_first]) == "哇，钻石！"

    clock.now += 2_000
    ore_again = _trigger("眼前露出了绿宝石矿石（在左侧）", key="sighting:rare_ore", at_ms=clock.now)
    assert await mouth.react_once([ore_again]) is None

    boss_now = _trigger("眼前撞见了监守者（在前方）", key="sighting:boss", at_ms=clock.now)
    assert await mouth.react_once([boss_now]) == "怎么还有监守者？！"
    assert len(llm.calls) == 2 and len(speech.calls) == 2


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


async def test_chinese_emotion_prefix_is_stripped_and_maps_to_enum(prompts: Any) -> None:
    """泄漏实例"害羞：我错了…"——中文情绪名前缀同样剥除不念，情绪收敛为 shy。"""
    llm, speech = _LLM("害羞：我错了…这可怎么办呀。"), _Speech()

    spoken = await _mouth(llm, speech, prompts).react_once([_trigger()])

    assert spoken == "我错了…这可怎么办呀。" and speech.calls[0]["emotion"] == "shy"


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
        return [record["message"] for record in loguru_capture.records if record["message"].startswith("即时反应：")]

    assert _latency_logs() == []
    clock.now += 4_000
    speech.calls[0]["on_started"]()
    assert len(_latency_logs()) == 1
    assert "等前一句 4000ms" in _latency_logs()[0] and "从事件到开始播 5000ms" in _latency_logs()[0]


# ---------------- 接线 ----------------

# 登记（只读穿透，合并时进底账）：断言读取即时反应的最近记录（agent._mouth._records）与
# 发声分发器槽位（agent._speech.dispatch）；即时反应的最近记录暂无公开只读面。


def test_mouth_is_off_by_default() -> None:
    """开关默认关闭且未注入替身：不构造即时反应，一切照旧。"""
    assert make_streamer_agent()._mouth is None


async def test_accidental_death_triggers_both_mouth_and_planner_while_chosen_suicide_stays_quiet() -> None:
    """意外真死亡：即时反应开着时当场短反应并同步唤醒 Planner 善后；自选寻死回家既不触发即时反应也不按事故唤醒。"""
    accident = BodyEventPayload(game="minecraft", kind="died", summary="死了：被蜘蛛杀死了", intentional=False)
    chosen = BodyEventPayload(game="minecraft", kind="died", summary="借死亡不掉落回重生点，不是出事", intentional=True)

    mouth = MagicMock()
    reactions: List[tuple] = []
    mouth.react.side_effect = lambda key, text, **kw: reactions.append((key, text, kw))
    agent = make_streamer_agent({"mouth": {"enabled": True}}, mouth=mouth)

    await agent._on_body_event("game.body.died", chosen, "maicraft_attention")
    assert reactions == []
    assert agent._proactive_signals.game_pending is False
    assert mouth.note_game.call_count == 1

    await agent._on_body_event("game.body.died", accident, "maicraft_attention")
    assert agent._proactive_signals.game_pending is True
    assert agent._flush_kick.is_set() is True
    assert reactions == [("body:died", "死了：被蜘蛛杀死了", {"refresh_scene": True, "kind": "died"})]

    plain = make_streamer_agent()
    await plain._on_body_event("game.body.died", accident, "maicraft_attention")
    assert plain._proactive_signals.game_pending is True


async def test_routine_hurt_and_sky_stay_silent_while_rare_sighting_reacts_and_wakes_planner() -> None:
    """摔伤、挨打、天色、装备坏只留进即时反应的近期背景不叫唤；视野撞见稀有矿或强敌时即时反应惊呼并同步唤醒 Planner 委派。"""
    mouth = MagicMock()
    reactions: List[tuple] = []
    mouth.react.side_effect = lambda key, text, **kw: reactions.append((key, text, kw))
    agent = make_streamer_agent({"mouth": {"enabled": True}}, mouth=mouth)

    for kind, summary in (
        ("attacked", "摔了一下"),
        ("attacked", "不知从哪射来箭打中一下，血掉了不少"),
        ("reflex_started", "先放下手上的事：被怪盯上了，先自卫"),
        ("sky_changed", "天色变了：天黑了"),
        ("gear_broken", "装备用坏了：铁镐坏了"),
        ("need_unhandled", "有件事没办成：饿了但没吃上"),
    ):
        await agent._on_body_event(
            f"game.body.{kind}",
            BodyEventPayload(game="minecraft", kind=kind, summary=summary),  # type: ignore[arg-type]
            "maicraft_attention",
        )

    assert reactions == []
    assert agent._proactive_signals.game_pending is False
    assert mouth.note_game.call_count == 6

    ore = BodyEventPayload(
        game="minecraft",
        kind="rare_sighted",
        summary="眼前露出了深层钻石矿石好几块（在前方）",
        sighting_kind="rare_ore",
    )
    await agent._on_body_event("game.body.rare_sighted", ore, "maicraft_attention")
    assert reactions == [
        (
            "sighting:rare_ore",
            "眼前露出了深层钻石矿石好几块（在前方）",
            {"refresh_scene": False, "kind": "rare_sighted"},
        )
    ]
    assert agent._proactive_signals.game_pending is True
    assert agent._flush_kick.is_set() is True
    assert agent._game_ledger.has_pending_urgent() is True


async def test_only_notable_milestone_triggers_mouth_while_routine_events_stay_background() -> None:
    """高光里程碑（开箱出稀罕宝物）触发即时反应；常规步进完成、交付上报与急事提醒只留背景不触发即时反应；报错不入背景。"""
    mouth = MagicMock()
    reactions: List[str] = []
    mouth.react.side_effect = lambda key, text, **kw: reactions.append(key)
    agent = make_streamer_agent({"mouth": {"enabled": True}}, mouth=mouth)

    step = GamePayload(game="minecraft", event_type="milestone", message="采集完成，8 个圆石进了背包", notable=False)
    await agent._on_game_event("game.milestone", step, "maicraft_attention")
    loot = GamePayload(
        game="minecraft", event_type="milestone", message="打开战利品箱开出了海洋之心 ×1，已经拿走", notable=True
    )
    await agent._on_game_event("game.milestone", loot, "maicraft_attention")
    report = GamePayload(game="minecraft", event_type="report", message="床做好了，放在屋里", report_kind="delivery")
    await agent._on_game_event("game.report", report, "minecraft")
    alert = GamePayload(game="minecraft", event_type="attention_required", message="急事处理完了：打完了，没掉血")
    await agent._on_game_event("game.attention_required", alert, "minecraft")
    error = GamePayload(game="minecraft", event_type="error", message="LLM 推理异常: ValueError: 请求被拒")
    await agent._on_game_event("game.error", error, "minecraft")

    assert reactions == ["game:notable_milestone"] and agent._proactive_signals.game_pending is True
    assert mouth.note_game.call_count == 4
    assert all("LLM 推理异常" not in call.args[0] for call in mouth.note_game.call_args_list)


def test_mouth_and_delegate_prompts_align_on_rare_sighting_handoff(prompts: Any) -> None:
    """提示词契约：即时反应不为自己拍板下一步行动承诺；委派守则指导对 rare_sighted 搁下常规在途任务优先采掘或定夺强敌。"""
    mouth_sys = prompts.render(
        "amaidesu_mouth",
        bot_name="麦麦",
        personality="活泼",
        style_constraints="口语化",
        max_chars="40",
        emotions="happy",
        boundaries_rule="",
    )
    delegate_rules = prompts.render("amaidesu_planner_delegate_rules")

    assert "具体怎么行动留给紧随其后的决策定夺，不替自己拍板承诺下一步行动" in mouth_sys
    assert "rare_sighted" in delegate_rules
    assert "when_busy='replace', current='shelve'" in delegate_rules


async def test_viewer_messages_go_into_the_mouth_records() -> None:
    """弹幕记进即时反应的最近记录（阶段 1 不由即时反应回弹幕，但现场要知道观众刚说了什么）。"""
    agent = make_streamer_agent({"mouth": {"enabled": True}})
    message = RoomMessagePayload(
        message_type="danmaku", user=RoomMessageUser(id="u1", name="小明"), content="麦麦小心后面"
    )
    await agent.handle_message(message)
    records = list(agent._mouth._records)  # type: ignore[union-attr]
    assert [(record.kind, record.sender, record.text) for record in records] == [("viewer", "小明", "麦麦小心后面")]


async def test_every_spoken_line_reaches_the_mouth_records_without_subscribing_to_speech() -> None:
    """她说过的每句话经发声分发器直连记进即时反应（Agent 不订阅自己的输出事件）：Planner 的回话也算。"""
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
    """即时反应初稿命中本场近逐字复读时，打回让表达模型换个说法重说一次，不直接把整句吞掉。"""
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
    """即时反应的现场卡不带直播间进房名单（欢迎归规划器），且高频游戏事件刷满 _records 后仍看得见刚说过的台词。"""
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
    """本场已被规划器欢迎过的玩家，即时反应时若顺嘴再欢迎一遍则剥掉重复欢迎，只留对当下遭遇的反应。"""
    llm = _LLM("happy：欢迎玩家甲上线！这苦力怕怎么贴脸炸我呀！")
    speech = _Speech()
    mouth = _mouth(llm, speech, prompts)
    mouth._room_state.mark_welcomed(user_name="玩家甲")

    spoken = await mouth.react_once([_trigger()])

    assert spoken == "这苦力怕怎么贴脸炸我呀！"
    assert "欢迎" not in spoken


@pytest.mark.parametrize(
    ("raw_line", "expected_emotion", "expected_text"),
    [
        ("害羞：哪有你说的那么厉害啦！", "shy", "哪有你说的那么厉害啦！"),
        ("【尴尬】：刚才那一脚踩空没人看见吧……", "embarrassed", "刚才那一脚踩空没人看见吧……"),
        ("(无语): 这苦力怕怎么还带拐弯的。", "speechless", "这苦力怕怎么还带拐弯的。"),
        ("[开心]：捡到金苹果啦！", "happy", "捡到金苹果啦！"),
        ("高兴: 铁锭终于烧好了！", "happy", "铁锭终于烧好了！"),
        ("兴奋：前面露出一大片钻石矿！", "excited", "前面露出一大片钻石矿！"),
        ("【激动】: 快看箱子里的附魔书！", "excited", "快看箱子里的附魔书！"),
        ("害怕：谁在背后射我？！", "scared", "谁在背后射我？！"),
        ("(紧张)：底下全是岩浆声。", "scared", "底下全是岩浆声。"),
        ("惊恐：监守者钻出来了！", "scared", "监守者钻出来了！"),
        ("惊讶：这箱子里居然有海绵！", "surprised", "这箱子里居然有海绵！"),
        ("[吃惊]: 一下出了四只小鸡！", "surprised", "一下出了四只小鸡！"),
        ("困惑：这红石线怎么没亮呢？", "confused", "这红石线怎么没亮呢？"),
        ("疑惑：刚才明明放这儿的呀。", "confused", "刚才明明放这儿的呀。"),
        ("疲惫：总算把这层石头挖通了。", "tired", "总算把这层石头挖通了。"),
        ("累：跑了大半张地图。", "tired", "跑了大半张地图。"),
        ("难过：钻石镐还是断掉了……", "sad", "钻石镐还是断掉了……"),
        ("伤心：小狼没跟上来。", "sad", "小狼没跟上来。"),
        ("（委屈）：明明我已经举盾了嘛！", "sad", "明明我已经举盾了嘛！"),
        ("哭泣：全掉进岩浆里了呜呜！", "crying", "全掉进岩浆里了呜呜！"),
        ("生气：这骷髅没完了是吧！", "angry", "这骷髅没完了是吧！"),
        ("愤怒：还敢来偷袭！", "angry", "还敢来偷袭！"),
        ("得意：这波落地水帅不帅！", "smug", "这波落地水帅不帅！"),
        ("严肃：这下面得慢点探。", "serious", "这下面得慢点探。"),
        ("认真：先把退路搭稳。", "serious", "先把退路搭稳。"),
        ("平静：先把火把插一圈。", "neutral", "先把火把插一圈。"),
        ("感慨：这片山谷风景真不错。", "neutral", "这片山谷风景真不错。"),
    ],
)
async def test_short_chinese_emotion_prefixes_are_stripped_and_mapped(
    prompts: Any, raw_line: str, expected_emotion: str, expected_text: str
) -> None:
    """行首常见短中文情绪词前缀（含可选括号与中英文冒号）识别后剥除不念进 TTS，并映射对应 Emotion 枚举（未知情绪词归 neutral）。"""
    llm, speech = _LLM(raw_line), _Speech()
    mouth = _mouth(llm, speech, prompts)

    spoken = await mouth.react_once([_trigger()])

    assert spoken == expected_text
    assert speech.calls[0]["emotion"] == expected_emotion


@pytest.mark.parametrize(
    "normal_line",
    [
        "问题是：现在包里一把铁镐都没了。",
        "注意：脚底下就是岩浆池！",
        "PlayerA：你怎么突然跑到我后面来了！",
        "Player_B: 等我先把这边火把插上。",
        "Alice：谢谢你扔过来的面包呀！",
        "Bob: 前头那座桥是你搭的吗？",
        "大家：这话说的，我都不好意思了。",
    ],
)
async def test_normal_colon_clauses_and_player_names_are_never_stripped_as_emotion(
    prompts: Any, normal_line: str
) -> None:
    """正常台词开头的提示词冒号与玩家名冒号绝不能误判成情绪标签剥除。"""
    llm, speech = _LLM(normal_line), _Speech()
    mouth = _mouth(llm, speech, prompts)

    spoken = await mouth.react_once([_trigger()])

    assert spoken == normal_line
    assert speech.calls[0]["emotion"] == "neutral"
