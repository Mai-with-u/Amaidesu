"""第二批决策与提示词接缝：去口癖词表、收窄静默禁问、无游戏干净工具面、解开冷场死锁与话头参数透传。"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List

import pytest

from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.talk.workbench import TANGENT_TTL_MS
from src.agents.streamer.tools.reply_tool import ReplyToolProvider, build_reply_tool_spec
from src.modules.agents.base import BaseAgent
from src.modules.agents.control import build_agent_control_provider
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.storage.models.rundown import Rundown, RundownSegment
from src.modules.tools.models import ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import make_streamer_agent
from tests.modules.llm.fakes import FakeLLMManager


@pytest.fixture
def prompts() -> Iterator[Any]:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


class _AvatarGuideStub(BaseToolProvider):
    """带使用说明的舞台形象工具桩（模拟 VTube Studio 的 avatar_act）。"""

    category = "avatar"

    @property
    def name(self) -> str:
        return "avatar"

    def list_tools(self) -> List[ToolSpec]:
        return [ToolSpec(name="act", description="做动作", kind="sync", provider="avatar")]

    def guide_for(self, agent_name: str) -> str:
        return "舞台形象：用 avatar_act 做点头或挥手动作。"

    async def invoke(self, invocation: Any) -> Any:
        raise AssertionError("本测试不应调用 avatar_act")


class _StreamerStubAgent(BaseAgent):
    """仅覆写 receive_prompt（模拟主播自身接收控制台递话），不覆写 receive_delegation。"""

    name = "streamer"
    description = "主播"

    def list_tools(self) -> List[ToolSpec]:
        return []

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        return True


class _GameStubAgent(BaseAgent):
    """覆写 receive_delegation 的游戏 Agent 桩。"""

    name = "minecraft"
    description = "MC 游戏"

    def list_tools(self) -> List[ToolSpec]:
        return []

    def receive_delegation(self, *, instruction: str, task_id: str) -> str:
        return ""


def _last_user_text(call: Dict[str, Any]) -> str:
    return [m["content"] for m in call["input"] if m["role"] == "user"][-1]


class _EchoReplyer:
    """记录 generate 入参并回传纯文本台词的回复器替身。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        return {"speech": "观众甲你好呀，说到四亿年前那段历史嘛。", "llm_request_id": "req_talk_1"}


def test_mouth_prompt_removes_named_filler_words(prompts: Any) -> None:
    """嘴提示词不再点名列举 5 个口癖词，用开放边界特征描述反应方式。"""
    rendered = prompts.render(
        "amaidesu_mouth",
        bot_name="麦麦",
        personality="活泼",
        style_constraints="口语自然",
        max_chars="40",
        emotions="happy, surprised",
        boundaries_rule="",
    )
    for filler in ("诶？", "啊这", "完了完了", "好家伙", "行吧", "口癖"):
        assert filler not in rendered
    assert "突发状况" in rendered and "随处境轻重自然变化" in rendered


def test_planner_prompt_narrows_quiet_rule_and_adds_talk_increment(prompts: Any) -> None:
    """决策提示词：静默禁问只约束无 target 独白轮；有 target 鼓励接话反问；支持杂谈话头推进增量。"""
    rendered = prompts.render(
        "amaidesu_planner_react",
        bot_name="麦麦",
        personality="活泼",
        behavior_style="积极互动",
        boundaries_rule="",
        inside_jokes="",
        game_rules="",
        social_fact_step="",
    )
    assert "只约束没有 `target` 的主动独白轮" in rendered
    assert "当 `target` 非空" in rendered and "把话递回去接住聊天" in rendered
    assert "进展、卡点和下一步" not in rendered
    assert "话头推进增量" in rendered and "beat_done" in rendered and "use_card_id" in rendered

    props = build_reply_tool_spec().parameters_schema["properties"]
    assert "beat_done" in props and "use_card_id" in props
    assert "话头推进增量" in props["is_novel"]["description"]


def test_framework_delegation_tools_hidden_from_planner_when_no_game_agent_enabled() -> None:
    """纯杂谈场没有能接收委派的游戏 Agent 时，Planner 工具表不含委派四件；接上游戏即恢复。"""
    mgr = AgentManager()
    mgr.register(_StreamerStubAgent())
    reg = ToolRegistry()
    reg.register_provider(build_agent_control_provider(mgr))

    # 运营全集仍看得见，但主播的 LLM 工具表里隐藏委派四件
    assert len(reg.list_tools(for_agent=None)) == 4
    assert reg.list_tools(for_agent="streamer") == []

    # 一旦注册了能接收委派的游戏 Agent，四件立即对主播可见
    mgr.register(_GameStubAgent())
    names = {spec.full_name for spec in reg.list_tools(for_agent="streamer")}
    assert {"framework_delegate", "framework_prompt", "framework_task_status", "framework_task_settle"} <= names


async def test_no_game_stage_mode_keeps_avatar_guide_and_cleans_proactive_reference(prompts: Any) -> None:
    """只挂舞台形象不挂游戏时：系统提示词仍带舞台杂谈模式说明与形象说明，主动窗不提委派与游戏日志。"""
    mgr = AgentManager()
    mgr.register(_StreamerStubAgent())
    reg = ToolRegistry()
    reg.register_provider(build_agent_control_provider(mgr))
    reg.register_provider(_AvatarGuideStub())

    llm = FakeLLMManager()
    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=reg,
        bot_name="麦麦",
        personality="活泼",
        persona_profile="### 身份与来历\n四亿年前萨卡班甲鱼混橙子。",
        env=PlannerEnv(
            speech_mode_provider=lambda: "talk",
            talk_workbench_provider=lambda _has_batch: "【当前话头与本节提纲】\n当前要点：名字与招呼（还没开口，最多 3 轮）",
            talk_cards_provider=self_async_text("【可抽的话题卡 / 提问箱】\n#1［提问箱］腰上的旋钮能拧吗"),
        ),
    )
    await planner.plan([], proactive=True)

    sent_messages = llm.calls[0]["input"]
    sent_tools = {t["name"] for t in (llm.calls[0]["tools"] or [])}
    assert sent_tools == {"avatar_act"}
    system_text = sent_messages[0]["content"]
    assert "舞台杂谈模式" in system_text and "avatar_act" in system_text
    assert "四亿年前萨卡班甲鱼混橙子" in system_text

    ref_text = _last_user_text(llm.calls[0])
    assert "framework_delegate" not in ref_text and "查游戏那边的工作日志" not in ref_text
    assert "【冷场】当前为杂谈节奏" in ref_text
    assert "【当前话头与本节提纲】" in ref_text
    assert "【可抽的话题卡 / 提问箱】" in ref_text


def self_async_text(value: str):
    async def _fn(_has_batch: bool) -> str:
        return value

    return _fn


async def test_play_mode_unlocks_cold_material_after_one_silent_proactive_round(prompts: Any) -> None:
    """边玩边说档游戏平静且上一轮主动窗已沉默过一次时，不必卡满 45 秒即可点亮冷场找话提示。"""
    silent_flag = [False]
    llm = FakeLLMManager()
    room = RoomState()
    room.record_speech(now_ms())
    planner = Planner(
        config=PlannerConfig(cold_silence_ms=45_000),
        llm_service=llm,
        prompt_service=prompts,
        room_state=room,
        env=PlannerEnv(
            speech_mode_provider=lambda: "play",
            proactive_silent_provider=lambda: silent_flag[0],
        ),
    )
    # 还没沉默过且距上次开口不足 45 秒：不点亮【冷场】
    await planner.plan([], proactive=True)
    ref_first = _last_user_text(llm.calls[0])
    assert "【冷场】" not in ref_first

    # 上一轮主动窗已沉默过一次且本轮无游戏事件：立刻点亮【冷场】解开死锁
    silent_flag[0] = True
    await planner.plan([], proactive=True)
    ref_second = _last_user_text(llm.calls[1])
    assert "【冷场】游戏那边暂时没有新动静" in ref_second


async def test_planner_passes_talk_mode_and_collects_beat_done_and_card_id(prompts: Any) -> None:
    """Planner 在杂谈档把 speech_mode=talk 传给回复器，并在 RoundOutcome 收回 beat_done 与 use_card_id。"""
    replyer = _EchoReplyer()
    reg = ToolRegistry()
    reg.register_provider(ReplyToolProvider(replyer=replyer))

    llm = FakeLLMManager(
        responses=[
            Response(
                success=True,
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="streamer_reply",
                        arguments={
                            "topic_summary": "展开四亿年前萨卡班甲鱼与橙子混合的来历",
                            "emotion": "happy",
                            "intensity": 0.7,
                            "is_novel": True,
                            "beat_done": True,
                            "use_card_id": 7,
                        },
                    )
                ],
            )
        ]
    )
    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=reg,
        env=PlannerEnv(speech_mode_provider=lambda: "talk"),
    )

    outcome = await planner.plan([], proactive=True)
    assert outcome.replied is True
    assert replyer.calls[0]["speech_mode"] == "talk"
    assert outcome.reply_payload is not None and outcome.reply_payload.get("speech_mode") == "talk"
    assert outcome.beat_done is True
    assert outcome.use_card_id == 7


def test_planner_and_settle_prompts_carry_off_game_works_and_quote_anti_pollution_rules(prompts: Any) -> None:
    """方案 A 场外作品边界、刚聊开话头增量与下播整理引述弹幕防污染条款对齐守护。"""
    planner_text = prompts.render(
        "amaidesu_planner_react",
        bot_name="麦麦",
        personality="活泼",
        behavior_style="积极互动",
        boundaries_rule="",
        inside_jokes="",
        game_rules="",
        social_fact_step="",
    )
    assert "存在【刚聊开的话头】" in planner_text
    assert "围绕该话头顺延这一轮讲出本场未说过的延伸看法或设定喜好算有增量" in planner_text
    assert "观众问起当前未在玩的场外作品或别的游戏时" in planner_text
    assert "禁止在发言要点里编写无出处的亲身游玩经历" in planner_text

    reply_props = build_reply_tool_spec().parameters_schema["properties"]
    assert "【刚聊开的话头】" in reply_props["is_novel"]["description"]

    archive_text = prompts.render("memory_archive_system")
    assert '标着"回复观众N「……」"的台词开头顺口念出或复述的观众弹幕问题' in archive_text
    assert "不算她自己陈述的事实" in archive_text
    assert "按创意源头划分：只要名字、口号、设定或梗是那条观众弹幕先提出来的" in archive_text
    assert "绝不写进第二节正式 archive" in archive_text

    commitment_text = prompts.render("memory_commitment_system")
    assert '标着"回复观众N"的台词开头把弹幕或醒目留言里的请求顺口念出来' in commitment_text
    assert "仅念出问题本身或婉拒、打哈哈的不算承诺" in commitment_text


class _MinecraftGuideStub(BaseToolProvider):
    """暴露游戏玩法说明的 Minecraft 工具桩，使 StreamerAgent 识别本场挂有游戏而进入 play 档。"""

    category = "game"

    @property
    def name(self) -> str:
        return "minecraft"

    def list_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="glance",
                description="查看现场",
                kind="sync",
                provider="minecraft",
            )
        ]

    def guide_for(self, agent_name: str) -> str:
        return "Minecraft 沙盒：通过委派安排角色探索与建造。"

    async def invoke(self, invocation: Any) -> Any:
        raise AssertionError("本测试不应调用 minecraft_glance")


class _PlayRundownRepoStub:
    """默认提供两个 play 档环节的流程单仓储替身，避免落入内置 opening 杂谈环节。"""

    async def get_rundown(self, rundown_id: str) -> Rundown:
        return Rundown(
            rundown_id=rundown_id,
            title="游玩编排",
            segments=[
                RundownSegment(id="seg_a", title="挖矿阶段", task_description="挖矿", expected_ms=300_000, mode="play"),
                RundownSegment(id="seg_b", title="建房阶段", task_description="建房", expected_ms=300_000, mode="play"),
            ],
        )


def _play_agent_with_fake_clock(
    prompts: Any,
    responses: List[Response],
    clock_box: List[int],
    *,
    rundown_repo: Any = None,
    rundown_id: str = "rd_play",
) -> tuple[Any, FakeLLMManager, EventBus]:
    """装配挂有游戏工具面（处于 play 档）并注入假时钟的真实 StreamerAgent。"""
    bus = EventBus(enable_stats=False)
    mgr = AgentManager()
    mgr.register(_StreamerStubAgent())
    mgr.register(_GameStubAgent())
    reg = ToolRegistry(event_bus=bus)
    reg.register_provider(build_agent_control_provider(mgr))
    reg.register_provider(_MinecraftGuideStub())
    llm = FakeLLMManager(responses=responses)
    sections: Dict[str, Any] = {
        "rundown_id": rundown_id,
        "proactive": {"enabled": False},
        "background": {"enabled": False},
        "mouth": {"enabled": False},
        "word_filter": {"enabled": False},
    }
    agent = make_streamer_agent(
        sections,
        llm=llm,
        prompt=prompts,
        event_bus=bus,
        tool_registry=reg,
        rundown_repo=rundown_repo or _PlayRundownRepoStub(),
        speech_config={"enabled": False},
        clock=lambda: clock_box[0],
    )
    return agent, llm, bus


async def test_play_mode_viewer_tangent_extends_one_round_then_closes_or_consumes_on_silence(prompts: Any) -> None:
    """play 档回应弹幕启动余温 -> 下个主动窗渲染【刚聊开的话头】并放行增量 -> 1 轮主动发声或沉默后自动收束清空。"""
    clock_box = [100_000]
    responses = [
        # 1) 回应阿杰弹幕（启动余温）
        Response(
            success=True,
            content="",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="streamer_reply",
                    arguments={
                        "topic_summary": "阿杰问平时爱喝什么饮料，回答偏爱冰镇柑橘汽水",
                        "topic_tag": "柑橘汽水喜好",
                        "target": "debug_阿杰",
                        "emotion": "happy",
                        "intensity": 0.6,
                        "is_novel": True,
                    },
                )
            ],
        ),
        Response(success=True, content="阿杰问平时爱喝啥呀？那肯定是冰镇柑橘汽水啦！", request_id="r1"),
        # 2) 无弹幕主动窗 #1：看到【刚聊开的话头】，顺延讲一拍延伸看法
        Response(
            success=True,
            content="",
            tool_calls=[
                ToolCall(
                    id="c2",
                    name="streamer_reply",
                    arguments={
                        "topic_summary": "顺着柑橘汽水话头补充气泡太冲会呛鼻子的设定细节，随后回看手头游戏",
                        "topic_tag": "柑橘汽水喜好",
                        "emotion": "happy",
                        "intensity": 0.5,
                        "is_novel": True,
                    },
                )
            ],
        ),
        Response(
            success=True, content="不过汽水刚拧开第一口最冲，喝急了鼻子都发酸，好啦继续看咱们的矿道。", request_id="r2"
        ),
        # 3) 无弹幕主动窗 #2：余温已消费完，不再渲染【刚聊开的话头】，本轮自然沉默
        Response(success=True, content="专注挖矿中", tool_calls=[]),
        # 4) 回应小夏弹幕（再次启动余温，测主动轮沉默消费路径）
        Response(
            success=True,
            content="",
            tool_calls=[
                ToolCall(
                    id="c4",
                    name="streamer_reply",
                    arguments={
                        "topic_summary": "小夏聊起复古街机配乐，回应很喜欢八位机鼓点",
                        "topic_tag": "街机配乐",
                        "target": "debug_小夏",
                        "emotion": "happy",
                        "intensity": 0.6,
                        "is_novel": True,
                    },
                )
            ],
        ),
        Response(success=True, content="小夏说复古街机配乐？八位机的鼓点听着可带劲了！", request_id="r4"),
        # 5) 无弹幕主动窗 #3：看到【刚聊开的话头】但模型决定不延伸、直接沉默收尾
        Response(success=True, content="没有更多延伸，继续看面前石头", tool_calls=[]),
        # 6) 无弹幕主动窗 #4：上一主动轮虽沉默也已消费当次余温，不再重复提示
        Response(success=True, content="继续安静做事", tool_calls=[]),
    ]
    agent, llm, bus = _play_agent_with_fake_clock(prompts, responses, clock_box)
    await agent.start()
    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=1, source="manual", title="余温接线测试"),
            source="test",
        )
        await bus.wait_until_drained()
        assert agent.current_speech_mode == "play"

        # 首答阿杰弹幕 -> 启动余温
        res1 = await agent.debug_test_decision(batch=[{"nickname": "阿杰", "text": "平时最爱喝什么饮料呀？"}])
        assert res1["speech"].startswith("阿杰问")
        assert agent.talk_workbench.has_active_tangent(now_ms=clock_box[0] + 1_000) is True

        # 主动轮 #1：参考段出现【刚聊开的话头】且【已聊话题】同步带出余温增量说明，开口后余温清空
        clock_box[0] += 15_000
        res2 = await agent.debug_test_decision(proactive=True)
        assert res2["speech"] is not None
        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        ref_proactive_1 = _last_user_text(planner_calls[1])
        assert "【刚聊开的话头】" in ref_proactive_1
        assert "柑橘汽水喜好" in ref_proactive_1
        assert "当参考段存在【刚聊开的话头】时，围绕该话头顺延这一轮" in ref_proactive_1
        assert agent.talk_workbench.has_active_tangent(now_ms=clock_box[0]) is False

        # 主动轮 #2：不再出现【刚聊开的话头】
        clock_box[0] += 15_000
        await agent.debug_test_decision(proactive=True)
        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        assert "【刚聊开的话头】" not in _last_user_text(planner_calls[2])

        # 回应小夏 -> 主动轮 #3 沉默收尾 -> 主动轮 #4 余温已被消费清空
        clock_box[0] += 5_000
        await agent.debug_test_decision(batch=[{"nickname": "小夏", "text": "你喜欢复古街机配乐吗？"}])
        assert agent.talk_workbench.has_active_tangent(now_ms=clock_box[0]) is True

        clock_box[0] += 10_000
        res_silent = await agent.debug_test_decision(proactive=True)
        assert res_silent["speech"] is None
        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        assert "【刚聊开的话头】" in _last_user_text(planner_calls[4])
        assert agent.talk_workbench.has_active_tangent(now_ms=clock_box[0]) is False

        clock_box[0] += 10_000
        await agent.debug_test_decision(proactive=True)
        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        assert "【刚聊开的话头】" not in _last_user_text(planner_calls[5])
    finally:
        await agent.cleanup()
        await bus.cleanup()


async def test_play_mode_viewer_tangent_followup_refresh_ttl_urgent_game_and_segment_clear(prompts: Any) -> None:
    """假时钟验证：观众继续追问刷新 90s 计时；满 90s 超时、突发游戏遭遇事件与切换流程单环节均立即清空余温。"""
    clock_box = [500_000]

    def _viewer_reply(call_id: str, tag: str, summary: str, line: str) -> List[Response]:
        return [
            Response(
                success=True,
                content="",
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        name="streamer_reply",
                        arguments={
                            "topic_summary": summary,
                            "topic_tag": tag,
                            "target": "debug_阿杰",
                            "emotion": "happy",
                            "intensity": 0.5,
                            "is_novel": True,
                        },
                    )
                ],
            ),
            Response(success=True, content=line, request_id=f"req_{call_id}"),
        ]

    responses = [
        *_viewer_reply(
            "v1", "像素游戏印象", "阿杰聊起经典横版像素游戏", "阿杰提到横版像素游戏？那种精致点阵画风看着很舒服呢。"
        ),
        *_viewer_reply(
            "v2", "像素游戏印象", "小夏追问像素动画帧率印象", "小夏说点阵动画呀？流畅的像素动作确实很考究手艺！"
        ),
        # 距追问仅 50s（距首答已 110s > 90s）的主动窗：仍应看到余温（本轮沉默消费）
        Response(success=True, content="看回矿道", tool_calls=[]),
        # 再次启动余温后直接推进假时钟满 90s TTL -> 过期不渲染
        *_viewer_reply("v3", "解谜机关", "阿杰聊起推箱子机关", "阿杰说推箱子解谜？一步错步步错最磨人了！"),
        Response(success=True, content="超时后已无余温", tool_calls=[]),
        # 启动余温后收到突发游戏遭遇事件（attacked）-> 主动窗按 urgent_game 清空余温
        *_viewer_reply("v4", "复古手柄", "小夏聊十字键手感", "小夏说十字键手感呀？清脆微动按着确实利落。"),
        Response(success=True, content="处理被苦力怕袭击的突发处境", tool_calls=[]),
        # 启动余温后切换流程单环节 -> 立即清空余温
        *_viewer_reply("v5", "像素字体", "阿杰聊点阵字体", "阿杰说点阵字体？方方正正的边缘特别清楚！"),
        Response(success=True, content="换入建房环节后已无上一环节余温", tool_calls=[]),
    ]
    agent, llm, bus = _play_agent_with_fake_clock(prompts, responses, clock_box)
    await agent.start()
    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=2, source="manual", title="余温边界测试"),
            source="test",
        )
        await bus.wait_until_drained()

        # 1) t=500s 阿杰开话头 -> t=560s 小夏追问刷新 -> t=610s（距首答 110s，距追问 50s < 90s）主动窗仍有效
        await agent.debug_test_decision(batch=[{"nickname": "阿杰", "text": "横版像素游戏画风好看吗？"}])
        clock_box[0] += 60_000
        await agent.debug_test_decision(batch=[{"nickname": "小夏", "text": "那种逐帧点阵动画是不是很费功夫？"}])
        clock_box[0] += 50_000
        await agent.debug_test_decision(proactive=True)
        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        assert "【刚聊开的话头】" in _last_user_text(planner_calls[2])
        assert "小夏追问像素动画帧率印象" in _last_user_text(planner_calls[2])

        # 2) 重新开启余温后推进假时钟 90_000ms 整 -> TTL 到期自动失效
        clock_box[0] += 5_000
        await agent.debug_test_decision(batch=[{"nickname": "阿杰", "text": "推箱子机关好玩吗？"}])
        clock_box[0] += TANGENT_TTL_MS
        await agent.debug_test_decision(proactive=True)
        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        assert "【刚聊开的话头】" not in _last_user_text(planner_calls[4])

        # 3) 重新开启余温后游戏侧推入突发遭遇（attacked）-> 主动窗 urgent_game=True 清空余温
        clock_box[0] += 5_000
        await agent.debug_test_decision(batch=[{"nickname": "小夏", "text": "老手柄十字键按着舒服吗？"}])
        assert agent.talk_workbench.has_active_tangent(now_ms=clock_box[0]) is True
        await bus.emit(
            CoreEvents.GAME_BODY_ATTACKED,
            BodyEventPayload(
                game="minecraft",
                kind="attacked",
                summary="矿道拐角被骷髅射中一箭",
                timestamp_ms=clock_box[0] + 1_000,
            ),
            source="test",
        )
        await bus.wait_until_drained()
        clock_box[0] += 2_000
        await agent.debug_test_decision(proactive=True)
        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        assert "【刚聊开的话头】" not in _last_user_text(planner_calls[6])
        assert agent.talk_workbench.has_active_tangent(now_ms=clock_box[0]) is False

        # 4) 重新开启余温后切换流程单环节 -> 下个窗口同步环节即清空余温
        clock_box[0] += 5_000
        await agent.debug_test_decision(batch=[{"nickname": "阿杰", "text": "点阵字体是不是很耐看？"}])
        assert agent.talk_workbench.has_active_tangent(now_ms=clock_box[0]) is True
        ok, _, _ = await agent.rundown_control("next", now_ms=clock_box[0] + 1_000)
        assert ok is True
        clock_box[0] += 2_000
        await agent.debug_test_decision(proactive=True)
        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        assert "【刚聊开的话头】" not in _last_user_text(planner_calls[8])
        assert agent.talk_workbench.has_active_tangent(now_ms=clock_box[0]) is False
    finally:
        await agent.cleanup()
        await bus.cleanup()
