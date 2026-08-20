"""MessageBuffer.render_batch_text 渲染测试。

覆盖 Task 4 落地后渲染输出与现状字节级一致——以"快照值"作为断言基准，确保未来
改动不会偏离现状。
"""

from __future__ import annotations

# 先导入 config.schemas 种子，规避 deciders/__init__ 的预存在循环导入：
#   deciders/__init__ → llm → llm_decider → schemas → decision_schemas → llm_decider(未完成)
# 先让 schemas 入口完成 llm_decider 的完整初始化，再进入 deciders 包即不再死锁。
import src.modules.config.schemas  # noqa: F401  # isort:skip

import pytest

from src.modules.types.base.normalized_message import NormalizedMessage
from src.stages.decision.deciders.amaidesu.message_buffer import MessageBuffer


def _msg(data_type: str, text: str, *, source: str, nickname: str | None = None, user_id: str | None = None):
    return NormalizedMessage(
        text=text,
        source=source,
        data_type=data_type,
        user_nickname=nickname,
        user_id=user_id,
    )


def test_render_text_no_prefix():
    """text 类型：无前缀，格式 `{nickname}: {text}`。"""
    msgs = [_msg("text", "hello", source="console", nickname="千石可乐")]
    assert MessageBuffer.render_batch_text(msgs) == "千石可乐: hello"


def test_render_gift_with_prefix():
    """gift 类型：前缀 `[礼物] `，格式 `[礼物] {nickname}: {text}`。"""
    msgs = [_msg("gift", "送出了 1 个辣条", source="bili", nickname="千石可乐")]
    assert MessageBuffer.render_batch_text(msgs) == "[礼物] 千石可乐: 送出了 1 个辣条"


def test_render_super_chat_with_prefix():
    """super_chat 类型：前缀 `[醒目留言] `，格式 `[醒目留言] {nickname}: {text}`。"""
    msgs = [_msg("super_chat", "支持主播", source="bili", nickname="路人甲")]
    assert MessageBuffer.render_batch_text(msgs) == "[醒目留言] 路人甲: 支持主播"


def test_render_guard_with_prefix():
    """guard 类型：前缀 `[上舰] `，格式 `[上舰] {nickname}: {text}`。"""
    msgs = [_msg("guard", "开通了总督", source="bili", nickname="舰长A")]
    assert MessageBuffer.render_batch_text(msgs) == "[上舰] 舰长A: 开通了总督"


def test_render_enter_with_prefix():
    """enter 类型：前缀 `[入场] `，格式 `[入场] {nickname}: {text}`。"""
    msgs = [_msg("enter", "进入了直播间", source="bili", nickname="新人")]
    assert MessageBuffer.render_batch_text(msgs) == "[入场] 新人: 进入了直播间"


def test_render_mainosaba_game_text_no_nickname():
    """game.text 类型：源前缀 `[游戏] `，**无**昵称——与原 source_prefix 特判输出一致。"""
    msgs = [_msg("game.text", "游戏剧情台词", source="mainosaba_game", nickname="Mainosaba游戏")]
    assert MessageBuffer.render_batch_text(msgs) == "[游戏] 游戏剧情台词"


def test_render_unknown_falls_back_to_text_style():
    """unknown 类型：无前缀，同 text 格式 `{nickname}: {text}`。"""
    msgs = [_msg("unknown", "兜底内容", source="bili", nickname="路人")]
    assert MessageBuffer.render_batch_text(msgs) == "路人: 兜底内容"


def test_render_nickname_fallback_chain():
    """昵称回退链：user_nickname → user_id → "观众"。"""
    msgs = [
        _msg("text", "a", source="x", nickname="昵称A"),
        _msg("text", "b", source="x", nickname=None, user_id="userB"),
        _msg("text", "c", source="x", nickname=None, user_id=None),
    ]
    assert MessageBuffer.render_batch_text(msgs) == "昵称A: a\nuserB: b\n观众: c"


def test_render_multiple_messages_newline_joined():
    """多条消息用 `\n` 连接（与现状一致）。"""
    msgs = [
        _msg("text", "msg1", source="x", nickname="A"),
        _msg("gift", "msg2", source="x", nickname="B"),
        _msg("super_chat", "msg3", source="x", nickname="C"),
    ]
    assert MessageBuffer.render_batch_text(msgs) == "A: msg1\n[礼物] B: msg2\n[醒目留言] C: msg3"


def test_render_empty_batch():
    """空批次返回空字符串。"""
    assert MessageBuffer.render_batch_text([]) == ""


def test_render_mainosaba_batch_mixed():
    """mainosaba_game 消息混在批次中——只有 mainosaba_game 走游戏前缀路径，其它走通用路径。"""
    msgs = [
        _msg("game.text", "剧情", source="mainosaba_game", nickname="Mainosaba游戏"),
        _msg("text", "弹幕", source="console", nickname="观众A"),
    ]
    assert MessageBuffer.render_batch_text(msgs) == "[游戏] 剧情\n观众A: 弹幕"