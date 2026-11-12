"""第二批决策与提示词接缝：去口癖词表、收窄静默禁问、无游戏干净工具面、解开冷场死锁与话头参数透传。"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List

import pytest

from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.tools.reply_tool import ReplyToolProvider, build_reply_tool_spec
from src.modules.agents.base import BaseAgent
from src.modules.agents.control import build_agent_control_provider
from src.modules.agents.manager import AgentManager
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.models import ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry
from src.modules.utils.time_utils import now_ms
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
    """记录 generate 入参并回传纯文本台词的表达引擎替身。"""

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
    assert {"framework_delegate", "framework_prompt", "framework_task_status", "framework_task_cancel"} <= names


async def test_no_game_stage_mode_keeps_avatar_guide_and_cleans_proactive_reference(prompts: Any) -> None:
    """只挂舞台形象不挂游戏时：系统提示词仍带舞台杂谈模式说明与形象说明，主动窗不提派单与游戏日志。"""
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
    """Planner 在杂谈档把 speech_mode=talk 传给表达层，并在 RoundOutcome 收回 beat_done 与 use_card_id。"""
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
