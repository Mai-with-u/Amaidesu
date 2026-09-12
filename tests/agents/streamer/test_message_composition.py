"""T9 消息构成单测——对话原生化后的消息序列契约。

覆盖：
- 角色映射（live_chat 行 user/assistant；弹幕批恒 user）
- 顺序：[system] → 历史 → 本批 → 参考段（尾）；ReAct 追加在参考段之后
- 跨窗逐字稳定：相邻两决策窗的旧消息字节一致（append-only 缓存硬要求）
- canonical 映射本身：批与历史同形、类型前缀、[id:…] 目标机制
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer import canonical
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.llm.manager import LLMResponse
from src.modules.tools.models import ToolExecutionResult
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


@dataclass(frozen=True)
class FakeTurn:
    """live_chat 行的历史视图（同 _LiveChatTurn 全字段）。"""

    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""


def _batch_msg(text: str, mid: str, *, nickname: str = "小明", message_type: str = "danmaku") -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type=message_type,
        user=RoomMessageUser(id="u1", name=nickname),
        content=text, message_id=mid,
    )


def _make_planner() -> tuple[Planner, MagicMock, List[List[dict]]]:
    """构造测试 Planner；返回 (planner, llm, captured)——captured 为每次 LLM 调用时的消息快照
    （planner 原地追加消息，必须复制）。"""
    captured: List[List[dict]] = []
    llm = MagicMock()

    async def _chat(*, messages: List[dict], tools: Any = None, client_type: str = "", on_delta: Any = None) -> LLMResponse:
        captured.append([dict(m) for m in messages])
        return LLMResponse(success=True, content="不说")

    llm.chat_messages = AsyncMock(side_effect=_chat)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])

    planner = Planner(
        config={"planner_max_steps": 2},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        memory=None,
        context_enabled=True,
        reply_provider=MagicMock(),
    )
    return planner, llm, captured


# ---------------------------------------------------------------------------
# canonical 映射
# ---------------------------------------------------------------------------


def test_canonical_batch_and_history_same_shape() -> None:
    """同一弹幕经批通道与历史通道序列化结果逐字一致（批与历史同形）。"""
    batch_msg = canonical.batch_item_to_message(_batch_msg("来个落地水", "m9", nickname="小明"))
    history_msg = canonical.turn_to_message(FakeTurn(role="user", content="来个落地水", sender_name="小明", message_id="m9"))
    assert batch_msg == history_msg == {"role": "user", "content": "小明: 来个落地水 [id:m9]"}


def test_canonical_type_prefix_and_role_mapping() -> None:
    """类型前缀按登记表渲染；assistant 行原样内容、不加 id。"""
    gift = canonical.batch_item_to_message(_batch_msg("送出 小花花 x1", "m2", nickname="小红", message_type="gift"))
    assert gift == {"role": "user", "content": "[礼物] 小红: 送出 小花花 x1 [id:m2]"}
    speak = canonical.turn_to_message(FakeTurn(role="assistant", content="晚上好", message_type="speak", message_id="m3"))
    assert speak == {"role": "assistant", "content": "晚上好"}


def test_canonical_text_view() -> None:
    """文本视图：观众行渲染 canonical content，主播行加"主播:"前缀。"""
    messages = [
        canonical.turn_to_message(FakeTurn(role="user", content="大家好", sender_name="小明", message_id="m1")),
        canonical.turn_to_message(FakeTurn(role="assistant", content="晚上好", message_type="speak")),
    ]
    text = canonical.to_text_view(messages)
    assert text == "小明: 大家好 [id:m1]\n主播: 晚上好"


# ---------------------------------------------------------------------------
# 消息构成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_composition_roles_and_order() -> None:
    """[system] → 历史（user/assistant）→ 本批（user）→ 参考段（user，序列尾）。

    自然终止时循环会先追加本轮 assistant 再返回——参考段取"最后一条 user 消息"。
    """
    planner, llm, captured = _make_planner()
    history = [
        FakeTurn(role="user", content="大家好呀", sender_name="小明", message_id="m1"),
        FakeTurn(role="assistant", content="晚上好", sender_name="主播", message_type="speak", message_id="m2"),
    ]

    await planner.plan([_batch_msg("主播好", "m9")], history=history, rundown_text="## 开场\n闲聊")

    messages = captured[0]
    assert messages[0]["role"] == "system"
    assert [m["role"] for m in messages[1:4]] == ["user", "assistant", "user"]
    ref = next(m for m in reversed(messages) if m["role"] == "user")
    assert "## 环节描述" in ref["content"]  # 参考段在尾，且只含元数据段（无直播流）


@pytest.mark.asyncio
async def test_react_messages_appended_after_reference() -> None:
    """ReAct 循环的 assistant/tool 消息追加在参考段之后（append-only，不插中间）。"""
    # 快照捕获：planner 原地追加消息，必须复制每次调用时的列表
    captured: List[List[dict]] = []
    responses = [
        LLMResponse(success=True, content="", tool_calls=[{"id": "c1", "function": {"name": "tool_x", "arguments": {}}}]),
        LLMResponse(success=True, content=""),
    ]

    async def _chat(*, messages, tools=None, client_type="", on_delta=None):  # type: ignore[no-untyped-def]
        captured.append([dict(m) for m in messages])
        return responses.pop(0)

    llm = MagicMock()
    llm.chat_messages = AsyncMock(side_effect=_chat)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    registry = MagicMock()
    registry.list_tools = MagicMock(
        return_value=[MagicMock(full_name="x_tool", description="x", parameters_schema=None, provider="x")]
    )
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(tool_name="x_tool", success=True, structured_content={"ok": True})
    )
    planner = Planner(
        config={"planner_max_steps": 3},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        reply_provider=MagicMock(),
    )

    await planner.plan([_batch_msg("hi", "m1")], history=[FakeTurn(role="user", content="大家好", sender_name="小明", message_id="m0")])

    first, second = captured[0], captured[1]
    ref_content = first[-1]["content"]
    # 第二轮 = 第一轮前缀逐字保留 + 循环追加（assistant/tool 在参考段之后，参考段不重复出现）
    assert second[: len(first)] == first
    assert second[len(first)]["role"] == "assistant"
    assert second[len(first) + 1]["role"] == "tool"
    assert sum(1 for m in second if m.get("content") == ref_content) == 1


# ---------------------------------------------------------------------------
# 跨窗逐字稳定（缓存硬要求）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_window_byte_stability() -> None:
    """相邻两窗：旧消息（历史 + 上一窗本批）在新窗中逐字一致，只追加不重排。"""
    planner, llm, captured = _make_planner()
    batch_w1 = [_batch_msg("第一句", "m2"), _batch_msg("第二句", "m3")]
    history_w1: List[FakeTurn] = [FakeTurn(role="user", content="大家好", sender_name="小明", message_id="m1")]

    # 窗 1：批 [m2, m3]
    await planner.plan(batch_w1, history=history_w1)
    window1 = captured[0]

    # 窗 2：窗 1 的批已落库成为历史，新批 [m4]
    history_w2 = history_w1 + [
        FakeTurn(role="user", content="第一句", sender_name="小明", message_id="m2"),
        FakeTurn(role="user", content="第二句", sender_name="小明", message_id="m3"),
    ]
    await planner.plan([_batch_msg("第三句", "m4")], history=history_w2)
    window2 = captured[1]

    # 旧消息的期望形态（canonical 唯一映射的产物，与窗口无关）
    old_msgs = [
        canonical.turn_to_message(history_w1[0]),
        canonical.batch_item_to_message(batch_w1[0]),
        canonical.batch_item_to_message(batch_w1[1]),
    ]
    # 两窗中旧消息段逐字节一致（跨窗稳定），且其后才出现各自的新内容
    assert window1[1 : 1 + len(old_msgs)] == old_msgs
    assert window2[1 : 1 + len(old_msgs)] == old_msgs
    assert window2[1 + len(old_msgs)]["content"] == "小明: 第三句 [id:m4]"
