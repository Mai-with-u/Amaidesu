"""表达侧看到要回应的原文——target 按整场历史展开成原话、游戏里有人搭话时原话随说话一起交给表达侧，
她刚说过的话随 reply 一起交给表达侧。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer import canonical
from src.agents.streamer.planner import PlannerConfig, RoundOutcome
from src.agents.streamer.decision_executor import DecisionRoundExecutor
from src.agents.streamer.session_scope import WindowMaterials
from src.agents.streamer.timeline import GameEventBatch
from src.agents.streamer.plan import DecisionPlan, ReplyRoundAttachment
from src.agents.streamer.planner import Planner, PlannerEnv
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.stats import StreamerStats
from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.agents.streamer.trigger_cause import TriggerCause, TriggerKind
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolInvocation


@dataclass(frozen=True)
class FakeTurn:
    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""


def _msg(text: str, mid: str, nickname: str) -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id=f"u_{nickname}", name=nickname),
        content=text,
        message_id=mid,
    )


# 按消息 ID 找原话


@pytest.mark.parametrize("target", ["m2", "[id:m2]", "id:m2", " [ID：m2] "])
def test_normalize_message_id_accepts_reference_forms(target: str) -> None:
    """Planner 照着 [id:…] 抄 target 时可能连括号一起抄，几种写法都归一成裸 ID。"""
    assert canonical.normalize_message_id(target) == "m2"


def test_find_target_prefers_batch_then_recent_history() -> None:
    """本批命中优先；本批没有时从近到远查历史；只认 ID，不按文本猜。"""
    batch = [_msg("第一条", "m1", "观众A"), _msg("第二条", "m2", "观众B")]
    history = [FakeTurn(role="viewer", content="早先的话", sender_name="观众C", message_id="h1")]

    assert canonical.find_target_message("[id:m2]", batch, history) == "观众B: 第二条 [id:m2]"
    assert canonical.find_target_message("h1", batch, history) == "观众C: 早先的话 [id:h1]"
    assert canonical.find_target_message("第二条", batch, history) == ""
    assert canonical.find_target_message(None, batch, history) == ""


# Replyer 本轮输入


def _replyer() -> tuple[Replyer, MagicMock]:
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(
            success=True, content="", tool_calls=[ToolCall(id="c1", name="reply", arguments={"speech": "好"})]
        )
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return Replyer(config={}, llm_service=llm, prompt_service=prompt), prompt


def _turn_kwargs(prompt: MagicMock) -> Dict[str, Any]:
    return {call.args[0]: call.kwargs for call in prompt.render.call_args_list}["amaidesu_replyer"]


@pytest.mark.parametrize(
    ("target", "expected_line"),
    [
        # 一批有好几条时直接给出 target 那条原话，表达侧不用自己对号；消息 ID 不再给她看
        ("[id:m2]", "target 原文: 观众B: 锯子修好了吗\n"),
        # 找不到原话时不出原文行，不拿别人的话凑数；Planner 填的片段照旧给出
        ("观众C", "target: 观众C\n"),
        # 填的是消息 ID 写法却查不到（ID 写错，或那条已折进前情提要）：不把 ID 给她看，按面向全体观众说
        ("[id:zzz]", "target: （无特定目标，面向全体观众）\n"),
        ("id:zzz", "target: （无特定目标，面向全体观众）\n"),
        # 没填：面向全体观众
        ("", "target: （无特定目标，面向全体观众）\n"),
    ],
    ids=["found-by-id", "nickname-kept", "unknown-bracket-id", "unknown-bare-prefix-id", "no-target"],
)
async def test_plan_carries_target_original_text(target: str, expected_line: str) -> None:
    replyer, prompt = _replyer()
    batch = [_msg("今天玩什么", "m1", "观众A"), _msg("锯子修好了吗", "m2", "观众B")]
    await replyer.generate(DecisionPlan(should_reply=True, target=target, topic_summary="锯子"), batch)

    plan_text = _turn_kwargs(prompt)["plan"]
    assert plan_text.startswith(expected_line) and "[id:" not in plan_text
    assert plan_text.count("target") == 1


async def test_target_found_in_the_whole_session_not_just_the_recent_window() -> None:
    """要回应的那条弹幕已滑出最近几条：原话按整份历史查回来，截取不影响找目标。"""
    replyer, prompt = _replyer()
    history = [FakeTurn(role="user", content="开场那个梦后来呢", sender_name="观众C", message_id="h0")]
    history += [FakeTurn(role="user", content=f"刷屏{i}", sender_name="路人", message_id=f"h{i}") for i in range(1, 40)]
    await replyer.generate(DecisionPlan(should_reply=True, target="h0", topic_summary="接着讲梦"), [], history=history)

    turn = _turn_kwargs(prompt)
    assert turn["plan"].startswith("target 原文: 观众C: 开场那个梦后来呢\n")
    assert "开场那个梦后来呢" not in turn["dialogue_prompt"]  # 聊天记录本身仍只有最近几条


async def test_game_chat_rendered_or_placeholder() -> None:
    """本窗新到的游戏聊天逐行给出；没有时用占位，模板不会出现字面变量。"""
    replyer, prompt = _replyer()
    plan = DecisionPlan(should_reply=True, topic_summary="游戏里有人问")

    await replyer.generate(plan, [], game_chat=["[minecraft] 玩家 Steve：麦麦你在干嘛呀"])
    assert _turn_kwargs(prompt)["game_chat"] == "[minecraft] 玩家 Steve：麦麦你在干嘛呀"

    prompt.render.reset_mock()
    await replyer.generate(plan, [])
    assert _turn_kwargs(prompt)["game_chat"] == "（无）"


# 原话一路交到表达侧


async def test_planner_hands_fresh_game_chat_to_reply_tool() -> None:
    """玩家搭话促发的一轮（弹幕批为空）：原话随 reply 交给表达侧，调用后槽位清空。"""
    seen: List[List[str]] = []
    replyer = MagicMock()

    async def _generate(*, game_chat: List[str], **_: Any) -> Dict[str, Any]:
        seen.append(list(game_chat))
        return {"speech": "刚有人在游戏里叫我", "emotion": {"name": "happy", "intensity": 0.5}, "metadata": {}}

    replyer.generate = AsyncMock(side_effect=_generate)
    provider = ReplyToolProvider(replyer=replyer)
    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=provider.invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(
            success=True,
            content="",
            tool_calls=[
                ToolCall(id="c1", name="streamer_reply", arguments={"topic_summary": "游戏里有人问", "is_novel": True})
            ],
        )
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config=PlannerConfig(planner_max_steps=2),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(reply_provider_factory=lambda: provider),
    )

    outcome = await planner.plan(
        [],
        proactive=True,
        game_batch=GameEventBatch(
            events=("[20:01 minecraft] 玩家 Steve：在吗",),
            fresh_chat=("[20:01 minecraft] 玩家 Steve：在吗",),
        ),
    )

    assert outcome.replied is True
    assert seen == [["[20:01 minecraft] 玩家 Steve：在吗"]]
    # 游戏聊天原话随调用单一次性传入：Provider 上不再有跨调用槽位
    assert not hasattr(provider, "_round_game_chat")


class _RecordingReplyer:
    """表达层桩：记下 reply 工具交来的已说台词，固定回一句新台词。"""

    def __init__(self) -> None:
        self.recent_speeches: List[Any] = []

    async def generate(self, **kwargs: Any) -> Dict[str, Any]:
        self.recent_speeches.append(kwargs["recent_speeches"])
        return {"speech": "这句是新的", "llm_request_id": None}


@pytest.mark.parametrize(
    ("attachment", "history", "expected"),
    [
        # Planner 路径：窗起点算好的发言闸语料随单携带，原样交给表达侧
        (ReplyRoundAttachment(speech_history=["刚说的一句"]), None, ["刚说的一句"]),
        # 裸调用：从刚读到的历史里现取她说过的话（与发言闸兜底同源）
        (
            None,
            [FakeTurn(role="user", content="在吗", sender_name="观众A"), FakeTurn(role="assistant", content="在呢")],
            ["在呢"],
        ),
    ],
    ids=["planner-attachment", "bare-call"],
)
async def test_reply_tool_hands_recent_speeches_to_the_replyer(
    attachment: Optional[ReplyRoundAttachment], history: Optional[List[Any]], expected: List[str]
) -> None:
    """她刚说过的话和发言闸查重用同一份语料：先交表达侧列出来，生成后再拿来查重。"""
    replyer = _RecordingReplyer()
    provider = ReplyToolProvider(
        replyer=cast(Replyer, replyer), history_provider=(lambda: history) if history is not None else None
    )
    result = await provider.invoke(
        ToolInvocation(
            tool_name="streamer_reply",
            arguments={"topic_summary": "接话", "is_novel": True, "emotion": "neutral", "intensity": 0.5},
            source="planner-react",
            attachment=attachment,
        )
    )
    assert result.success is True
    assert replyer.recent_speeches == [expected]


async def test_executor_splits_game_chat_view() -> None:
    """决策执行器把游戏聊天全文交给 Planner 参考段，本窗新到的原话另传。"""
    planner = MagicMock()
    planner.plan = AsyncMock(return_value=RoundOutcome(silent_reason="natural"))
    executor = DecisionRoundExecutor(
        planner=planner,
        speech=MagicMock(),
        event_bus=None,
        room_state=MagicMock(),
        proactive_trigger=MagicMock(),
        stats=StreamerStats(),
        thinking_sink=None,
        thinking_enabled=False,
        window_opener=AsyncMock(
            return_value=WindowMaterials(
                history=[],
                game_batch=GameEventBatch(
                    events=("[20:01 minecraft] 玩家 Steve：在吗",),
                    fresh_chat=("[20:01 minecraft] 玩家 Steve：在吗",),
                ),
            )
        ),
        rundown_text_provider=MagicMock(return_value=None),
    )

    await executor.execute([], forced=False, trigger_cause=TriggerCause(kind=TriggerKind.GAME), proactive=True)

    kwargs = planner.plan.await_args.kwargs
    assert kwargs["game_batch"].events == ("[20:01 minecraft] 玩家 Steve：在吗",)
    assert kwargs["game_batch"].fresh_chat == ("[20:01 minecraft] 玩家 Steve：在吗",)
