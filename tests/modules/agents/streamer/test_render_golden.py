"""直播对话统一映射的字节契约：批次、历史和文本视图保留完整正文与目标标识。"""

from __future__ import annotations

from dataclasses import dataclass

from src.agents.streamer import canonical
from src.agents.streamer.message_buffer import MessageBuffer
from src.agents.streamer.replyer import _batch_prompt_text
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


@dataclass(frozen=True)
class FakeTurn:
    """历史 turn 的鸭子类型替身（role/content/sender_name/message_type/message_id）。"""

    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""


def _batch_msg(
    text: str, mid: str = "", *, nickname: str = "小明", message_type: str = "danmaku"
) -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type=message_type,
        user=RoomMessageUser(id="u1", name=nickname),
        content=text,
        message_id=mid,
    )


# ---------------------------------------------------------------------------
# golden：单条 canonical content（批/历史/摘要三通道共用）
# ---------------------------------------------------------------------------


def test_golden_canonical_content_bytes() -> None:
    """观众行带类型前缀与 [id:] 后缀；主播行原样、无 id、无前缀。"""
    assert (
        canonical.canonical_content(role="user", nickname="小明", text="来个落地水", message_id="m9")
        == "小明: 来个落地水 [id:m9]"
    )
    assert (
        canonical.canonical_content(
            role="user", nickname="小红", text="送出 小花花 x1", message_type="gift", message_id="m2"
        )
        == "[礼物] 小红: 送出 小花花 x1 [id:m2]"
    )
    assert canonical.canonical_content(role="user", nickname="小红", text="来个落地水") == "小红: 来个落地水"
    assert canonical.canonical_content(role="assistant", text="晚上好", message_id="m3") == "晚上好"
    # 未知/空类型按普通弹幕渲染
    assert canonical.canonical_content(role="user", nickname="小明", text="hi", message_type="") == "小明: hi"


def test_golden_long_message_is_complete() -> None:
    """长弹幕保留完整正文与消息标识，历史和本批映射一致。"""
    long_text = "哇" * 2500
    out = canonical.canonical_content(role="user", nickname="小明", text=long_text, message_id="m1")
    assert out == "小明: " + long_text + " [id:m1]"


# ---------------------------------------------------------------------------
# golden：文本视图（replyer 历史注入 / 后台摘要共用 to_text_view）
# ---------------------------------------------------------------------------


def test_golden_text_view_bytes() -> None:
    """观众行为 canonical content 本身，主播行加"主播:"前缀。"""
    messages = [
        canonical.turn_to_message(FakeTurn(role="user", content="大家好", sender_name="小明", message_id="m1")),
        canonical.turn_to_message(FakeTurn(role="assistant", content="晚上好", message_type="speak", message_id="m2")),
    ]
    assert canonical.to_text_view(messages) == "小明: 大家好 [id:m1]\n主播: 晚上好"


def test_golden_text_view_proactive_placeholder_annotated() -> None:
    """dict 直传路径：user 行 content 以"（主动发言"开头时标 [系统]。"""
    messages = [{"role": "user", "content": "（主动发言，主题：测试）"}]
    assert canonical.to_text_view(messages) == "[系统] （主动发言，主题：测试）"


def test_golden_text_view_turn_channel_placeholder_prefixed() -> None:
    """turn 通道：user 占位先被加昵称前缀，[系统] 分支不触发。"""
    messages = [canonical.turn_to_message(FakeTurn(role="user", content="（主动发言，主题：测试）"))]
    assert canonical.to_text_view(messages) == "观众: （主动发言，主题：测试）"


def test_golden_text_view_assistant_placeholder_not_annotated() -> None:
    """同文内容出现在 assistant 行不加 [系统]——标注只针对 user 占位。"""
    messages = [canonical.turn_to_message(FakeTurn(role="assistant", content="（主动发言，主题：测试）"))]
    assert canonical.to_text_view(messages) == "主播: （主动发言，主题：测试）"


# ---------------------------------------------------------------------------
# golden：批渲染（MessageBuffer / replyer 包装）
# ---------------------------------------------------------------------------


def test_golden_render_batch_text_bytes() -> None:
    """批渲染逐行 canonical content；空批为空串。"""
    msgs = [
        _batch_msg("hello", "m1"),
        _batch_msg("送出 小花花 x1", "m2", nickname="小红", message_type="gift"),
    ]
    assert MessageBuffer.render_batch_text(msgs) == ("小明: hello [id:m1]\n[礼物] 小红: 送出 小花花 x1 [id:m2]")
    assert MessageBuffer.render_batch_text([]) == ""


def test_golden_render_batch_text_missing_id_no_suffix() -> None:
    """message_id 缺失时不渲染空 id 后缀。"""
    assert MessageBuffer.render_batch_text([_batch_msg("hello")]) == "小明: hello"


def test_golden_replyer_batch_wrapper_bytes() -> None:
    """replyer 批包装：空批占位文案原样保留；非空委托 MessageBuffer。"""
    assert _batch_prompt_text([]) == "（本批无弹幕）"
    assert _batch_prompt_text([_batch_msg("hi", "m1")]) == "小明: hi [id:m1]"


# ---------------------------------------------------------------------------
# golden：批与历史同形（收敛的核心等价断言）
# ---------------------------------------------------------------------------


def test_golden_batch_and_history_equivalence() -> None:
    """同一条弹幕经批通道与历史通道渲染逐字一致（单一实现的根契约）。"""
    via_batch = MessageBuffer.render_batch_text([_batch_msg("来个落地水", "m9")])
    via_history = canonical.to_text_view(
        [canonical.turn_to_message(FakeTurn(role="user", content="来个落地水", sender_name="小明", message_id="m9"))]
    )
    assert via_batch == via_history == "小明: 来个落地水 [id:m9]"
