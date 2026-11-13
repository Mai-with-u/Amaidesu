"""Planner 系统提示词里的底线与规则：AI 声明、边界栏、做不到的不答应、备忘录口径、前情目录——
用真实提示词模板渲染，经 plan() 取发给模型的系统提示词。"""

from __future__ import annotations

from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerPersonaConfig
from src.agents.streamer.game_now import GameNow, GoalResultLine
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.planner_render import render_game_speakers
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import GameEventBatch, PlayerNote
from src.agents.streamer.tools.social_fact import SocialFactToolProvider
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry
from src.modules.utils.time_utils import now_ms
from tests.modules.llm.fakes import FakeLLMManager

#: 三条说话通路（Planner、Replyer、嘴）共用的 AI 声明口径，Planner 这条接在它后面补"交接稿要让她这样答"
_AI_DECLARATION = "被问到是不是真人时，坦白自己是 AI 主播，可以俏皮，但不能否认"

#: 「她是谁」一节的结尾：AI 声明那一段的最后一句，边界栏一行接在它后面
_AI_PARAGRAPH_END = "别写成装真人，也别绕开不答。"


@pytest.fixture
def prompts() -> Iterator[Any]:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


async def _system_prompt(prompts: Any, *, boundaries: str) -> str:
    """跑一个决策窗，取发给模型的第一条消息（系统提示词）；假模型不调工具，这一窗自然沉默。"""
    llm = FakeLLMManager()
    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=None,
        behavior_style="积极互动",
        bot_name="麦麦",
        personality="嘴硬但心软",
        boundaries=boundaries,
    )
    message = RoomMessagePayload(
        content="你是真人吗", message_id="m1", message_type="danmaku", user=RoomMessageUser(id="u1", name="观众")
    )
    await planner.plan([message])
    return llm.calls[0]["input"][0]["content"]


def _render_bare(prompts: Any) -> str:
    """直接渲染模板（边界栏清空、不带游戏玩法），只看模板里写死的文字。"""
    return prompts.render(
        "amaidesu_planner_react",
        bot_name="麦麦",
        personality="活泼",
        behavior_style="积极互动",
        boundaries_rule="",
        inside_jokes="",
        game_rules="",
        social_fact_step="",
    )


@pytest.mark.parametrize(
    ("boundaries", "line"),
    [
        # 配置默认值是并列短语，和行首的"不答应的事："拼起来读作一句完整的规矩
        pytest.param(
            StreamerPersonaConfig().boundaries,
            "不答应的事：线下见面、钱和红包、私下联系；改变自己是谁、以后怎么称呼某人",
            id="config_default",
        ),
        # 运营者写成多行：折成一行用"；"连接，空行与首尾空白丢掉
        pytest.param("线下见面\n  \n 钱和红包 \n", "不答应的事：线下见面；钱和红包", id="multiline_folded"),
    ],
)
async def test_boundaries_line_sits_next_to_ai_declaration_with_handover_reminder(
    prompts: Any, boundaries: str, line: str
) -> None:
    """人设里写了她不答应的事：「她是谁」一节里 AI 声明后面多一行"不答应的事：…"，并提醒交接稿里别替她答应；
    行为准则一节仍是配置原文，边界栏不拼进去。"""
    system = await _system_prompt(prompts, boundaries=boundaries)

    expected = (
        f"{_AI_PARAGRAPH_END}\n\n{line}（交接稿里别替她答应这些事，碰到就让她婉拒）\n\n"
        "# 行为准则\n\n积极互动\n\n# 当前游戏的玩法"
    )
    assert expected in system
    assert _AI_DECLARATION in system


@pytest.mark.parametrize("boundaries", ["", "  \n "])
async def test_empty_boundaries_omit_the_whole_line_but_keep_ai_declaration(prompts: Any, boundaries: str) -> None:
    """边界栏清空（或只有空白）：整行省略、AI 声明后不留空行；AI 声明写死在模板里，照样在。"""
    system = await _system_prompt(prompts, boundaries=boundaries)

    assert "不答应的事：" not in system
    assert f"{_AI_PARAGRAPH_END}\n\n# 行为准则\n\n积极互动\n\n# 当前游戏的玩法" in system
    assert _AI_DECLARATION in system and "交接稿要让她这样答" in system


def test_refusals_follow_the_boundaries_line_not_a_hardcoded_list(prompts: Any) -> None:
    """ "做不到的不答应"按人设里的边界栏办：边界栏清空时模板里不再有写死的越界清单；
    不向观众打听私人信息是隐私底线（观众当众说出的私事会被弹幕记录存下来），不随边界栏变，单独写一句。"""
    prompt = _render_bare(prompts)

    assert "「她是谁」里列出的她不答应的事" in prompt
    assert all(word not in prompt for word in ("线下见面", "要钱", "要私人信息"))
    assert "不管人设里列没列，都别让她向观众打听住址、电话、学校这类私人信息" in prompt


def test_prompt_template_tells_planner_to_admit_not_to_invent_for_prelude_directory(prompts: Any) -> None:
    """前情目录规则：本场早先的段落只剩目录一行、眼前没有细节时，坦白记不清、可以问弹幕，不凭目录编细节；
    往场回顾工具翻不到本场说过的话，规则里明说不要为这个去调它。"""
    prompt = _render_bare(prompts)

    assert "历史开头的【前情目录】列着已移出上下文的段落" in prompt
    assert "坦白记不清、可以问问弹幕，别凭目录一行编细节" in prompt
    assert "拿不准就先用 `memory_recall` 查本场说过的话" in prompt


def test_prompt_template_reads_a_trailing_open_paren_as_a_joke(prompts: Any) -> None:
    """句末单个"（"是玩笑口气、不是话没说完：第 58 场她两回追问"括号一开就撒手"，没听懂"那我家女仆（"。"""
    prompt = _render_bare(prompts)

    assert '句末单个"（"是语气，不是话没说完' in prompt
    assert '"那我家女仆（"意思是"那是我家的女仆啦"' in prompt


def test_prompt_template_keeps_evaluation_out_of_topic_summary(prompts: Any) -> None:
    """事实段不写定性评价词：评价与态度全部留给 reply_guidance 按人设从裸事实推导。

    事实段先把处境框成"白忙"，同一轮再"推导"态度，推导结果被框定锁死——
    挫折事实经带评价的框定必然滑向自嘲。
    """
    prompt = _render_bare(prompts)

    assert "也不做定性评价" in prompt
    assert "「白忙」「白找」「冤」「狼狈」「白转」「白搭」这类评价词不写进事实段" in prompt
    assert "它们会把处境先框成一种态度" in prompt


def test_prompt_template_does_not_blame_present_players_for_protected_blocks(prompts: Any) -> None:
    """被保护挡住时只说记录上不归她：第 58 场她说"这正门被人用两块橡木板钉死了……谁干的呀"，那其实是她自己的家。"""
    prompt = _render_bare(prompts)

    assert "被挡住时不怪到人头上" in prompt
    assert '不让她追问"谁干的"，也不安到在场玩家头上' in prompt
    assert "中性地问是谁放的" in prompt


def test_prompt_template_restates_player_request_before_state(prompts: Any) -> None:
    """玩家托付或问句触发的轮次，话题摘要第一句先复述谁要什么：诉求框定逐轮漂移时，纸面上先露出来。"""
    prompt = _render_bare(prompts)

    assert "摘要先复述诉求" in prompt
    assert "第一句先写清谁要什么" in prompt
    assert "再写她的状态" in prompt


def test_delegate_rules_asks_back_when_request_target_is_unclear() -> None:
    """诉求对象说不清时先反问确认，不按主播自己的库存或状态替对方回答；歧义澄清落在委派规则，不重复写。"""
    reset_prompt_manager()
    try:
        rules = get_prompt_manager().render("amaidesu_planner_delegate_rules")
    finally:
        reset_prompt_manager()

    assert "玩家托付里诉求归谁说不准时" in rules
    assert "先在游戏聊天里反问确认" in rules
    assert "不按主播自己的库存或状态替对方回答" in rules
    assert "托付有歧义时先反问" not in rules


def test_prompt_template_treats_retraction_as_cancellation(prompts: Any) -> None:
    """有人更正或宣布不用做了：撤对应的单或补进在途单、记一条取代旧行的事实、口头认账——不当闲聊带过。"""
    prompt = _render_bare(prompts)

    assert "有人更正或宣布不用做了，按作废处理" in prompt
    assert "被作废的事对应哪一单就撤哪一单" in prompt and "`framework_task_settle`" in prompt
    assert "口头认账" in prompt
    # 直接渲染（变量为空，即工具没注册的口径）：规则不点名社交事实工具
    assert "streamer_social_fact" not in prompt


def _registry(with_social_fact: bool) -> ToolRegistry:
    registry = ToolRegistry()
    if with_social_fact:
        registry.register_provider(
            SocialFactToolProvider(repo=None, session_provider=lambda: None, game_scope_provider=lambda: ("", "", ""))
        )
    return registry


def _planner(prompts: Any, registry: ToolRegistry) -> Planner:
    return Planner(
        config=PlannerConfig(),
        llm_service=FakeLLMManager(),
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=registry,
        behavior_style="积极互动",
        bot_name="麦麦",
        personality="嘴硬但心软",
    )


@pytest.mark.parametrize("registered", [True, False])
def test_social_fact_tool_mentions_follow_registration(prompts: Any, registered: bool) -> None:
    """作废条款里"记一条取代旧行的事实"一步，只在社交事实工具注册时出现。"""
    planner = _planner(prompts, _registry(registered))
    prompt, failure = planner._render_system_prompt()
    assert failure is None and prompt is not None
    step = "用 `streamer_social_fact` 把作废记成一条新事实，取代旧的那一行"
    assert (step in prompt) is registered
    assert ("streamer_social_fact" in prompt) is registered
    # 这一步插在撤单、补单之后，口头认账之前
    if registered:
        assert prompt.index("补进那一单") < prompt.index(step) < prompt.index("口头认账")


@pytest.mark.parametrize("registered", [True, False])
async def test_proactive_action_options_mention_social_fact_only_when_registered(
    prompts: Any, registered: bool
) -> None:
    """主动窗【情境】的行动选项里"记一笔游戏里的社交事实"只在工具注册时列出。"""
    planner = _planner(prompts, _registry(registered))
    text, failure = await planner._assemble_reference([], None, None, forced=False, proactive=True)
    assert failure is None and text is not None
    assert ("记一笔游戏里的社交事实（streamer_social_fact）" in text) is registered


async def test_a3_and_a4_pit_trap_checks_own_recent_digging_and_explosions_before_answering(prompts: Any) -> None:
    """A3 + A4 补 1 回归（第 61 场 21:45 '还有陷阱' / 第 65 场 22:41 '卡住了'）：
    玩家甲在公屏说'这边还有陷阱''还有陷阱 2 号'或指出卡住时，【游戏那边此刻】呈现近期挖掘与失败记录，
    系统提示词明确要求先对照自己最近挖过什么、哪里爆炸过、哪步没成，是自己造成的就认下来去处理，不反问'在哪儿'。"""
    ts = now_ms()
    captured_messages: list[list[dict]] = []
    invoked: list[tuple[str, dict]] = []

    async def _generate(messages: list[dict], **_: Any) -> Response:
        captured_messages.append([dict(m) for m in messages])
        return Response(
            success=True,
            tool_calls=[
                ToolCall(
                    id="d1",
                    name="framework_delegate",
                    arguments={"agent": "minecraft", "instruction": "回营地把刚才挖土留下的坑填平"},
                ),
                ToolCall(
                    id="r1",
                    name="streamer_reply",
                    arguments={
                        "topic_summary": "玩家甲提醒营地边有陷阱坑，游戏记录显示她刚才在营地边挖过泥土且有爆炸坑，现去回填",
                        "reply_guidance": "爽快认下坑是自己挖材料留下的并说这就去填平",
                        "topic_tag": "营地填坑",
                        "to_player": "玩家甲",
                        "emotion": "neutral",
                        "intensity": 0.4,
                    },
                ),
            ],
        )

    async def _tool_handler(inv: Any) -> ToolExecutionResult:
        invoked.append((inv.tool_name, dict(inv.arguments)))
        if inv.tool_name == "framework_delegate":
            return ToolExecutionResult(
                tool_name=inv.tool_name,
                success=True,
                structured_content={"accepted": True, "task_id": "deleg_fill", "executor": "minecraft"},
            )
        return ToolExecutionResult(
            tool_name=inv.tool_name,
            success=True,
            structured_content={
                "speech": "哎那几个坑是我刚刨土留下的，我去填上！",
                "emotion": {"name": "neutral"},
                "metadata": {},
            },
        )

    registry = ToolRegistry()
    specs = [
        ToolSpec(name="framework_delegate", description="", kind="sync", provider=""),
        ToolSpec(name="streamer_reply", description="", kind="sync", provider=""),
    ]
    registry.register_provider(make_provider_from_specs("", [(s, _tool_handler) for s in specs]))
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=_generate)

    game_now = GameNow(
        goal_results=(
            GoalResultLine(
                at_ms=ts - 60_000, game="minecraft", outcome="成了", purpose="就地挖取泥土", summary="挖了 16 块泥土"
            ),
            GoalResultLine(
                at_ms=ts - 20_000, game="minecraft", outcome="没成", purpose="走回工作台", summary="走不到目标"
            ),
        )
    )
    planner = Planner(
        config=PlannerConfig(planner_max_steps=2),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=registry,
        behavior_style="积极互动",
        bot_name="麦麦",
        personality="活泼",
        env=PlannerEnv(reply_provider_factory=lambda: MagicMock(), game_now_provider=lambda _a: game_now),
    )

    chat_line = "[21:45 minecraft 公屏] 玩家 玩家甲：这边还有陷阱，还有陷阱 2 号"
    outcome = await planner.plan(
        [],
        proactive=True,
        game_batch=GameEventBatch(
            events=("[21:43 遭遇·minecraft·attacked] 营地旁发生爆炸（已结束）", chat_line),
            fresh_chat=(chat_line,),
            player_notes=(PlayerNote(who="玩家甲", said=True, line=chat_line, timestamp_ms=ts),),
        ),
    )

    assert outcome.replied is True
    system_text = captured_messages[0][0]["content"]
    ref_text = captured_messages[0][-1]["content"]
    assert "有人说附近有陷阱、地上全是坑" in system_text and "不要反问在哪儿" in system_text
    assert "有人指出她卡住了、过载了" in system_text and "不许只盯着此刻子动作硬顶说没卡住" in system_text
    assert "成了：就地挖取泥土→挖了 16 块泥土" in ref_text and "没成：走回工作台→走不到目标" in ref_text
    assert [name for name, _ in invoked] == ["framework_delegate", "streamer_reply"]


async def test_a4_why_not_use_capability_checks_game_records_instead_of_fabricating_excuses(prompts: Any) -> None:
    """A4 补 5 回归（第 59 场 20:46 '怎么不用连锁'）：
    玩家甲或观众乙问'为什么不用某种做法/连锁'且当前速览无记录时，系统守则要求先查游戏记录，
    记录里没有就说去看看并向游戏那边查问核实，绝不现编缺少条件或资格的理由。"""
    captured_system: list[str] = []
    invoked_tools: list[str] = []

    async def _generate(messages: list[dict], **_: Any) -> Response:
        if not captured_system:
            captured_system.append(messages[0]["content"])
            return Response(
                success=True,
                tool_calls=[ToolCall(id="q1", name="minecraft_get_work_log", arguments={})],
            )
        return Response(
            success=True,
            tool_calls=[
                ToolCall(
                    id="p1",
                    name="framework_prompt",
                    arguments={"agent": "minecraft", "content": "核对挖石时连锁破坏能不能用上"},
                ),
                ToolCall(
                    id="r1",
                    name="streamer_reply",
                    arguments={
                        "topic_summary": "观众乙问为什么挖矿不用连锁，工作文档里暂未写明原因，已安排在游戏里核对试一下",
                        "reply_guidance": "应一声说这就看看连锁怎么没连上，不编造装备缺词条之类的理由",
                        "topic_tag": "核对连锁挖矿",
                        "target": "m_chain",
                        "emotion": "neutral",
                        "intensity": 0.3,
                    },
                ),
            ],
        )

    async def _tool_handler(inv: Any) -> ToolExecutionResult:
        invoked_tools.append(inv.tool_name)
        if inv.tool_name == "minecraft_get_work_log":
            return ToolExecutionResult(
                tool_name=inv.tool_name,
                success=True,
                structured_content={"todos": "挖圆石", "notebook": "暂无连锁记录"},
            )
        if inv.tool_name == "framework_prompt":
            return ToolExecutionResult(
                tool_name=inv.tool_name,
                success=True,
                structured_content={"delivered": True, "executor": "minecraft"},
            )
        return ToolExecutionResult(
            tool_name=inv.tool_name,
            success=True,
            structured_content={
                "speech": "咦我看看，刚光顾着往下刨了，我试下连锁！",
                "emotion": {"name": "neutral"},
                "metadata": {},
            },
        )

    registry = ToolRegistry()
    specs = [
        ToolSpec(name="minecraft_get_work_log", description="", kind="sync", provider=""),
        ToolSpec(name="framework_prompt", description="", kind="sync", provider=""),
        ToolSpec(name="streamer_reply", description="", kind="sync", provider=""),
    ]
    registry.register_provider(make_provider_from_specs("", [(s, _tool_handler) for s in specs]))
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=_generate)

    planner = Planner(
        config=PlannerConfig(planner_max_steps=3),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=registry,
        behavior_style="积极互动",
        bot_name="麦麦",
        personality="活泼",
        env=PlannerEnv(reply_provider_factory=lambda: MagicMock()),
    )
    danmaku = RoomMessagePayload(
        content="怎么不用连锁挖呀",
        message_id="m_chain",
        message_type="danmaku",
        user=RoomMessageUser(id="u_viewer_b", name="观众乙"),
    )
    outcome = await planner.plan([danmaku])

    assert outcome.replied is True
    assert "有人问她为什么不用某种做法或能力时" in captured_system[0]
    assert "不许凭猜测现编缺少某样条件或资格之类的理由" in captured_system[0]
    assert invoked_tools == ["minecraft_get_work_log", "framework_prompt", "streamer_reply"]


async def test_s66_remote_public_chat_exclamation_marks_absent_speakers_and_guards_against_self_attribution(
    prompts: Any,
) -> None:
    """第 66 场 22:55:21 回归：两名玩家远在沙漠神殿发全服公屏感叹'wc'/'全是压力板'，主播独自在营地西边修压机。
    即便营地机器归属字段写着玩家甲，现场速览的周边实体里没有这两名玩家——参考段必须点明两人当前不在身边，
    且系统提示词明确未点名她的异地环境感叹不能硬套成自己手头工位的状况，拿不准可顺口问清在哪碰上的。"""
    captured_messages: list[list[dict]] = []

    async def _generate(messages: list[dict], **_: Any) -> Response:
        captured_messages.append(list(messages))
        return Response(
            success=True,
            tool_calls=[
                ToolCall(
                    id="r_afar",
                    name="streamer_reply",
                    arguments={
                        "topic_summary": "玩家甲和玩家乙在游戏公屏感叹那边全是压力板，两人当前不在营地身边",
                        "reply_guidance": "顺口问一句他们跑到哪儿碰上那么多压力板了，别认领成自己修压机脚下的状况",
                        "topic_tag": "远处玩家遇机关",
                        "to_player": "玩家乙",
                        "emotion": "neutral",
                        "intensity": 0.4,
                    },
                )
            ],
        )

    async def _tool_handler(inv: Any) -> ToolExecutionResult:
        if inv.tool_name == "minecraft_glance":
            return ToolExecutionResult(
                tool_name="minecraft_glance",
                success=True,
                structured_content={
                    "tool": "glance",
                    "body": {"dimension": "minecraft:overworld", "position": [12, 64, -8]},
                    "entities": [{"type": "touhou_little_maid:maid", "name": "小女仆", "distance": 3.0}],
                    "facilities": [
                        {"block": "create:mechanical_press", "distance": 2.0, "whose": "玩家甲"},
                    ],
                },
            )
        return ToolExecutionResult(
            tool_name=inv.tool_name,
            success=True,
            structured_content={
                "speech": "你们跑到哪儿踩着一堆压力板啦？",
                "emotion": {"name": "neutral"},
                "metadata": {},
            },
        )

    registry = ToolRegistry()
    specs = [
        ToolSpec(name="minecraft_glance", description="", kind="sync", provider=""),
        ToolSpec(name="streamer_reply", description="", kind="sync", provider=""),
    ]
    registry.register_provider(make_provider_from_specs("", [(s, _tool_handler) for s in specs]))
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=_generate)
    ts = now_ms()
    planner = Planner(
        config=PlannerConfig(planner_max_steps=2, reference_tools=["minecraft_glance"]),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=registry,
        behavior_style="积极互动",
        bot_name="麦麦",
        personality="活泼",
        env=PlannerEnv(reply_provider_factory=lambda: MagicMock()),
    )

    line_a = "[22:55 minecraft 公屏] 玩家 玩家甲：wc"
    line_b = "[22:55 minecraft 公屏] 玩家 玩家乙：全是压力板"
    outcome = await planner.plan(
        [],
        proactive=True,
        game_batch=GameEventBatch(
            events=(line_a, line_b),
            fresh_chat=(line_a, line_b),
            player_notes=(
                PlayerNote(who="玩家甲", said=True, line=line_a, timestamp_ms=ts - 1000),
                PlayerNote(who="玩家乙", said=True, line=line_b, timestamp_ms=ts),
            ),
        ),
    )

    assert outcome.replied is True
    system_text = captured_messages[0][0]["content"]
    ref_text = captured_messages[0][-1]["content"]
    assert "不在她身边且未点名她的玩家感叹机关、怪物、地形或掉落物" in system_text
    assert "不凭空认领到自己脚下" in system_text and "顺口问清在哪碰上的" in system_text
    assert "玩家 玩家甲、玩家 玩家乙 当前不在【现场速览】身边" in ref_text
    assert "未点名她的环境感叹是对方那边的遭遇" in ref_text


def test_s66_nearby_and_named_players_keep_positive_response_guardrail(prompts: Any) -> None:
    """配对副作用护栏：不能因为防远端公屏误认，压低对身边围观互动、点名主播或向主播求助搭话玩家的回应积极度。"""
    system_text = prompts.render(
        "amaidesu_planner_react",
        bot_name="麦麦",
        personality="活泼",
        behavior_style="积极互动",
        boundaries_rule="",
        inside_jokes="",
        game_rules="",
        social_fact_step="",
    )
    assert "点名她、向她求助搭话或就在身边围观互动的玩家，照常积极回应" in system_text

    # 玩家甲就在现场速览身边围观，玩家乙在远处向主播求助：两边都明确保留积极回应与游戏内回话提醒
    overview = (
        'minecraft_glance: {"tool": "glance", "body": {"dimension": "minecraft:overworld"}, '
        '"entities": [{"type": "minecraft:player", "name": "玩家甲", "distance": 4.0}]}'
    )
    notes = (
        PlayerNote(who="玩家甲", said=True, line="[22:56 minecraft 公屏] 玩家 玩家甲：这台压机转起来了"),
        PlayerNote(who="玩家乙", said=True, line="[22:56 minecraft 公屏] 玩家 玩家乙：麦麦家里还有铁锭吗"),
    )
    mixed = render_game_speakers(notes, overview=overview)
    assert "在游戏里回一句" in mixed
    assert "玩家 玩家甲 当前就在身边，围观搭话照常积极回应" in mixed
    assert "玩家 玩家乙 当前不在身边" in mixed and "点名她或求助搭话的照常积极回应" in mixed

    # 全部发言玩家都在身边时，直接标明就在【现场速览】身边并积极回应
    all_here = render_game_speakers(notes[:1], overview=overview)
    assert "玩家 玩家甲 当前就在【现场速览】身边，围观互动或搭话照常积极回应" in all_here
