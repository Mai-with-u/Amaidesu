"""Planner 提示词与代码的接线守护：AI 声明与边界栏的配置→渲染关系、工具注册状态驱动的条款显隐、
说话人参考段的行为口径——提示词散文本身不逐句锁定（全文一致性由 tests/architecture 守护）。"""

from __future__ import annotations

from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerPersonaConfig
from src.agents.streamer.game_now import GameNow, GoalResultLine
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.planner_render import online_players_from_overview, render_game_speakers
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import GameEventBatch, PlayerNote
from src.agents.streamer.tools.social_fact import SocialFactToolProvider
from src.agents.streamer.trigger_cause import TriggerCause, TriggerKind
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry
from src.modules.utils.time_utils import now_ms
from tests.modules.llm.fakes import FakeLLMManager

#: 三条说话通路（Planner、Replyer、嘴）共用的 AI 声明口径，Planner 这条接在它后面补"发言要点要让她这样答"
_AI_DECLARATION = "被问到是不是真人时，坦白自己是 AI 主播，可以俏皮，但不能否认"

#: 「她是谁」一节的结尾：AI 声明那一段的最后一句，边界栏一行接在它后面
_AI_PARAGRAPH_END = "别写成装真人，也别绕开不答。"


@pytest.fixture
def prompts() -> Iterator[Any]:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


async def _system_prompt(prompts: Any, *, boundaries: str) -> str:
    """跑一个决策回合，取发给模型的第一条消息（系统提示词）；假模型不调工具，这一窗自然沉默。"""
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
    """人设里写了她不答应的事：「她是谁」一节里 AI 声明后面多一行"不答应的事：…"，并提醒发言要点里别替她答应；
    行为准则一节仍是配置原文，边界栏不拼进去。"""
    system = await _system_prompt(prompts, boundaries=boundaries)

    expected = (
        f"{_AI_PARAGRAPH_END}\n\n{line}（发言要点里别替她答应这些事，碰到就让她婉拒）\n\n"
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
    assert _AI_DECLARATION in system


def test_refusals_follow_the_boundaries_line_not_a_hardcoded_list(prompts: Any) -> None:
    """ "做不到的不答应"按人设里的边界栏办：边界栏清空时模板里不再有写死的越界清单；
    不向观众打听私人信息是隐私底线（观众当众说出的私事会被弹幕记录存下来），不随边界栏变，单独写一句。"""
    prompt = _render_bare(prompts)

    assert all(word not in prompt for word in ("线下见面", "要钱", "要私人信息"))


def test_retraction_rule_names_the_task_settle_tool(prompts: Any) -> None:
    """作废条款指名结单工具，且不点名未注册的社交事实工具（模板不教注册表里没有的工具）。"""
    prompt = _render_bare(prompts)

    assert "`framework_task_settle`" in prompt
    # 直接渲染（变量为空，即工具没注册的口径）：规则不点名社交事实工具
    assert "streamer_social_fact" not in prompt


def _registry(with_social_fact: bool, with_avatar_act: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    if with_social_fact:
        registry.register_provider(
            SocialFactToolProvider(repo=None, session_provider=lambda: None, game_scope_provider=lambda: ("", "", ""))
        )
    if with_avatar_act:

        async def _act_ok(inv: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name=inv.tool_name, success=True)

        registry.register_provider(
            make_provider_from_specs(
                "avatar",
                [(ToolSpec(name="act", description="形象动作", kind="sync", provider="avatar"), _act_ok)],
            )
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


@pytest.mark.parametrize("registered", [True, False])
def test_no_game_rules_mention_avatar_act_only_when_registered(prompts: Any, registered: bool) -> None:
    """无游戏玩法段的 avatar_act 肢体动作分句，只在形象动作工具注册时出现。"""
    planner = _planner(prompts, _registry(with_social_fact=False, with_avatar_act=registered))
    rules = planner._render_game_rules()
    assert "舞台杂谈模式" in rules
    assert ("avatar_act" in rules) is registered


@pytest.mark.parametrize("registered", [True, False])
async def test_proactive_action_options_mention_avatar_act_only_when_registered(prompts: Any, registered: bool) -> None:
    """主动窗【情境】的行动选项里"让形象动一下（avatar_act）"只在工具注册时列出。"""
    planner = _planner(prompts, _registry(with_social_fact=False, with_avatar_act=registered))
    text, failure = await planner._assemble_reference([], None, None, forced=False, proactive=True)
    assert failure is None and text is not None
    assert ("让形象动一下（avatar_act，不出声不占台词预算）" in text) is registered


async def test_a3_and_a4_pit_trap_checks_own_recent_digging_and_explosions_before_answering(prompts: Any) -> None:
    """A3 + A4 补 1 回归（第 61 场 21:45 '还有陷阱' / 第 65 场 22:41 '卡住了'）：
    玩家甲在游戏公屏说'这边还有陷阱''还有陷阱 2 号'或指出卡住时，【游戏那边此刻】呈现近期挖掘与失败记录，
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

    chat_line = "[21:45 minecraft 游戏公屏] 玩家 玩家甲：这边还有陷阱，还有陷阱 2 号"
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
    ref_text = captured_messages[0][-1]["content"]
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
    assert invoked_tools == ["minecraft_get_work_log", "framework_prompt", "streamer_reply"]


async def test_s66_remote_public_chat_exclamation_marks_absent_speakers_and_guards_against_self_attribution(
    prompts: Any,
) -> None:
    """第 66 场 22:55:21 回归：两名玩家远在沙漠神殿发全服游戏公屏感叹'wc'/'全是压力板'，主播独自在营地西边修压机。
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

    line_a = "[22:55 minecraft 游戏公屏] 玩家 玩家甲：wc"
    line_b = "[22:55 minecraft 游戏公屏] 玩家 玩家乙：全是压力板"
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
    ref_text = captured_messages[0][-1]["content"]
    assert "玩家 玩家甲、玩家 玩家乙 当前不在【现场速览】身边" in ref_text
    assert "未点名她的环境感叹是对方那边的遭遇" in ref_text


def test_s66_nearby_and_named_players_keep_positive_response_guardrail() -> None:
    """配对副作用护栏：不能因为防远端游戏公屏误认，压低对身边围观互动、点名主播或向主播求助搭话玩家的回应积极度。"""
    # 玩家甲就在现场速览身边围观，玩家乙在远处向主播求助：两边都明确保留积极回应与游戏内回话提醒
    overview = (
        'minecraft_glance: {"tool": "glance", "body": {"dimension": "minecraft:overworld"}, '
        '"entities": [{"type": "minecraft:player", "name": "玩家甲", "distance": 4.0}]}'
    )
    notes = (
        PlayerNote(who="玩家甲", said=True, line="[22:56 minecraft 游戏公屏] 玩家 玩家甲：这台压机转起来了"),
        PlayerNote(who="玩家乙", said=True, line="[22:56 minecraft 游戏公屏] 玩家 玩家乙：麦麦家里还有铁锭吗"),
    )
    mixed = render_game_speakers(notes, overview=overview)
    assert "在游戏里回一句" in mixed
    assert "玩家 玩家甲 当前就在身边，围观搭话照常积极回应" in mixed
    assert "玩家 玩家乙 当前不在身边" in mixed and "点名她或求助搭话的照常积极回应" in mixed

    # 全部发言玩家都在身边时，直接标明就在【现场速览】身边并积极回应
    all_here = render_game_speakers(notes[:1], overview=overview)
    assert "玩家 玩家甲 当前就在【现场速览】身边，围观互动或搭话照常积极回应" in all_here


def test_game_chat_vs_streamer_reply_role_split_in_speakers() -> None:
    """游戏公屏说话人参考段明确区分 minecraft_chat_send（往游戏公屏发字）与 streamer_reply（直播间口播收口）。"""
    note = PlayerNote(who="玩家甲", said=True, line="[21:00 minecraft 游戏公屏] 玩家 玩家甲：在忙什么")
    speakers_text = render_game_speakers((note,))
    assert speakers_text.startswith("【游戏公屏近期发言】")
    assert "调用 `minecraft_chat_send` 在游戏里回一句" in speakers_text
    assert "再用 `streamer_reply` 收口直播口播" in speakers_text
    assert "单独填 `to_player` 不会往游戏公屏发字" in speakers_text
    assert "属于旁人对话，不要强行调 `minecraft_chat_send` 插嘴或代答" in speakers_text


def test_speakers_segment_anchors_the_mention_mark() -> None:
    """提及标注判据：游戏公屏说话人段锚住行尾的 [提及…] 标注——带标注优先处理、无标注的旁人对话不插嘴。

    标注是采集器按文本判的事实：判据同时写明撞词也照标、"说她"是否等于"跟她说话"由模型按句意分辨。
    """
    note = PlayerNote(who="玩家甲", said=True, line="[21:00 minecraft 游戏公屏] 玩家 玩家甲：麦麦在吗")
    speakers_text = render_game_speakers((note,))
    assert "行尾带 [提及…] 标注的行是正文里提到了她的名字，优先处理" in speakers_text
    assert "是不是在跟她说话按那句的意思分辨" in speakers_text, "只做事实标注，行为判断留给模型"
    assert "无标注且是玩家间互相点名或一问一答聊他们自己的事的行，属于旁人对话" in speakers_text


@pytest.mark.parametrize(
    ("overview", "expected"),
    [
        ("", None),
        ('minecraft_glance: {"unavailable": "游戏未连接"}', None),
        ('minecraft_glance: {"nearby_error": "不在世界里"}', None),
        ('minecraft_glance: {"body": {"dimension": "minecraft:overworld"}, "entities": []}', None),
        ('minecraft_glance: {"body": {"dimension": "minecraft:overworld"}, "online_players": []}', frozenset()),
        (
            'minecraft_glance: {"body": {"dimension": "minecraft:overworld"}, '
            '"entities": [{"type": "minecraft:player", "name": "玩家甲", "distance": 4.0}], '
            '"online_players": ["玩家乙"]}',
            frozenset({"玩家甲", "玩家乙"}),
        ),
    ],
    ids=["empty", "unavailable", "nearby_error", "legacy_without_key", "nobody_online", "merged_online_set"],
)
def test_online_players_from_overview_parses_presence_cleanly(overview: str, expected: frozenset[str] | None) -> None:
    """全服在线名单解析：仅在有效速览快照携带 online_players 列表时返回集合，空列表与读取失败严格区分。"""
    assert online_players_from_overview(overview) == expected


def test_render_game_speakers_marks_offline_players_and_mixed_presence() -> None:
    """本回合发言过的玩家若在当刻速览里已不在 online_players 中，现场核对如实标明已下线并不必往游戏公屏空喊。"""
    offline_overview = 'minecraft_glance: {"body": {"dimension": "minecraft:overworld"}, "online_players": []}'
    note_a = PlayerNote(who="玩家甲", said=True, line="[21:10 minecraft 游戏公屏] 玩家 玩家甲：我先溜了")
    only_offline = render_game_speakers((note_a,), overview=offline_overview)
    assert "玩家 玩家甲 当前已下线" in only_offline
    assert "无需再用 `minecraft_chat_send` 往游戏公屏空喊" in only_offline
    assert "可在直播口播里顺口带过" in only_offline

    # 三种在场状态并存：玩家甲在身边、玩家乙在线但不在身边、玩家丙刚说完已下线
    mixed_overview = (
        'minecraft_glance: {"body": {"dimension": "minecraft:overworld"}, '
        '"entities": [{"type": "minecraft:player", "name": "玩家甲", "distance": 5.0}], '
        '"online_players": ["玩家甲", "玩家乙"]}'
    )
    notes = (
        note_a,
        PlayerNote(who="玩家乙", said=True, line="[21:10 minecraft 游戏公屏] 玩家 玩家乙：我在矿洞挖铁"),
        PlayerNote(who="玩家丙", said=True, line="[21:10 minecraft 游戏公屏] 玩家 玩家丙：拜拜"),
    )
    mixed = render_game_speakers(notes, overview=mixed_overview)
    assert "玩家 玩家甲 当前就在身边，围观搭话照常积极回应" in mixed
    assert "玩家 玩家乙 当前不在身边（仍在服内在线）" in mixed
    assert "玩家 玩家丙 当前已下线" in mixed and "无需再用 `minecraft_chat_send` 往游戏公屏空喊" in mixed


async def test_empty_talk_deck_hides_talk_draw_cards_and_recovers_when_cards_exist(prompts: Any) -> None:
    """话题匣可用卡为 0 时动态隐藏 talk_draw_cards，卡池有可用卡时重新暴露。"""

    async def _noop(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=inv.tool_name, success=True, structured_content={"ok": True, "cards": []})

    registry = ToolRegistry()
    registry.register_provider(
        make_provider_from_specs(
            "talk",
            [(ToolSpec(name="draw_cards", description="抽话题卡", kind="sync", provider="talk"), _noop)],
        )
    )
    registry.register_provider(
        make_provider_from_specs(
            "streamer",
            [(ToolSpec(name="reply", description="发言", kind="sync", provider="streamer"), _noop)],
        )
    )

    class _FakeDeck:
        def __init__(self, cards: list[str]) -> None:
            self.cards = cards

        async def available_cards(self, *, kind: str | None = None, limit: int | None = None) -> list[str]:
            del kind
            return self.cards[:limit] if limit is not None else list(self.cards)

    class _FakeOwner:
        def __init__(self, deck: _FakeDeck) -> None:
            self.topic_deck = deck

        async def refresh_cards(self, has_viewer_batch: bool) -> str:
            # 模拟边玩边说档或有弹幕时不预摆文本，仅依赖 deck 余量判断
            del has_viewer_batch
            return ""

    # 宿主公开 topic_deck 余量为 0：隐藏 talk_draw_cards
    deck = _FakeDeck([])
    owner = _FakeOwner(deck)
    llm_empty = FakeLLMManager()
    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm_empty,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(talk_cards_provider=owner.refresh_cards),
    )
    await planner.plan([], proactive=True)
    tools_empty = {t["name"] for t in (llm_empty.calls[0]["tools"] or [])}
    assert "talk_draw_cards" not in tools_empty
    assert "streamer_reply" in tools_empty

    # 卡池补进卡片后：即使当前窗未预摆文本，仍恢复暴露 talk_draw_cards
    deck.cards.append("card-1")
    await planner.plan([], proactive=True)
    tools_ready = {t["name"] for t in (llm_empty.calls[1]["tools"] or [])}
    assert "talk_draw_cards" in tools_ready

    # 显式 talk_cards_available_provider 返回 False 时隐藏
    async def _no_cards() -> bool:
        return False

    llm_explicit = FakeLLMManager()
    planner_explicit = Planner(
        config=PlannerConfig(),
        llm_service=llm_explicit,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(talk_cards_available_provider=_no_cards),
    )
    await planner_explicit.plan([], proactive=True)
    tools_explicit = {t["name"] for t in (llm_explicit.calls[0]["tools"] or [])}
    assert "talk_draw_cards" not in tools_explicit


async def test_idle_fallback_window_allows_silence_and_drops_forced_persona_or_card_prompt(prompts: Any) -> None:
    """轻从冷场兜底轮去除强推人设/抽卡措辞，明确无新变化且游戏平稳时保持沉默完全合适。"""
    llm = FakeLLMManager()
    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        env=PlannerEnv(game_lead_agent_provider=lambda: "minecraft"),
    )
    await planner.plan(
        [],
        proactive=True,
        trigger_cause=TriggerCause(kind=TriggerKind.GAME_LEAD_IDLE_FALLBACK),
    )
    ref_text = [m["content"] for m in llm.calls[0]["input"] if m["role"] == "user"][-1]
    assert "低频冷场兜底" in ref_text
    assert "【冷场】" in ref_text
    assert "顺着近况、话题卡或个人喜好" not in ref_text
    assert "保持沉默完全合适" in ref_text
    assert "不必硬聊或反复展开静态背景设定" in ref_text
