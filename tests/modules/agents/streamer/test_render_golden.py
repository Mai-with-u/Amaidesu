"""渲染去重合并的 golden 等价测试——单一实现（canonical）字节级契约。

历史三份渲染（planner._render_history / replyer._render_history_text /
background._format_history）与多份批渲染（planner._render_batch /
message_buffer.render_batch_text / replyer._render_batch_text）已收敛到
``src/agents/streamer/canonical.py`` 单一映射；本文件把各调用面的渲染输出
逐字节钉死，防止后续改动漂移。

差异清单（合并前逐份对比的结论与处置）：
1. 批渲染 ``[id:]`` 后缀：旧 message_buffer.render_batch_text 无后缀，
   旧 planner._render_batch 有 → 对齐统一为"有"（reply target 引用机制
   依赖编号，批必须带 id）。
2. 历史/摘要 ``[系统]`` 标注：旧 background._format_history 无、
   planner/replyer 历史渲染有 → 参数化保留：标注只存在于文本视图
   （to_text_view），且仅命中"（主动发言"前缀的 user 占位；后台摘要只取
   viewer 行（sender_role='viewer'），天然不触发该分支。
3. 角色标签：旧 replyer._render_history_text / background._format_history
   用原始 role_str（"user"/"assistant"），旧 planner 用中文标签 → 对齐统一
   为中文标签（观众/主播，canonical.ROLE_LABELS）。
4. 主播行前缀：文本视图中 assistant 行渲染为 ``主播: 内容``；消息数组通道
   （Planner）不加前缀，角色由 role 字段承载 → 参数化保留（两个通道各有
   消费方，语义等价）。
5. 批渲染字段来源：旧实现读 ``msg.text`` / ``user_nickname`` / ``data_type``，
   新实现读 ``msg.content`` / ``user.name|id`` / ``message_type`` → 对齐统一
   到统一事件载荷字段（NormalizedMessage 删除后的形状）。
6. 单项 2000 字符截断（truncate_item）为合并时统一加入的护栏，各通道一致；
   截断作用于拼装后的整行（含 [id:] 后缀），超长行的 id 后缀会被一并裁掉。
7. ``[系统]`` 标注的可达性：turn_to_message 通道会先给 user 行加昵称前缀
   （空昵称补"观众"），content 不再以"（主动发言"开头，故标注在该通道不可达；
   仅 dict 直传 to_text_view 的路径可触发。现网 src 内无"（主动发言，主题：…）"
   占位的生产者（旧实现同样只有渲染检查、无写入点），属防御分支——参数化保留现状。
"""

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


def test_golden_truncation_cap() -> None:
    """单项超 2000 字符截断并追加统一标记；截断作用于整行，id 后缀被裁掉（差异清单第 6 条）。"""
    long_text = "哇" * 2500
    out = canonical.canonical_content(role="user", nickname="小明", text=long_text, message_id="m1")
    assert out == "小明: " + "哇" * 1996 + "…（截断）"


# ---------------------------------------------------------------------------
# golden：文本视图（replyer 历史注入 / 后台摘要共用 to_text_view）
# ---------------------------------------------------------------------------


def test_golden_text_view_bytes() -> None:
    """观众行为 canonical content 本身，主播行加"主播:"前缀（差异清单第 3/4 条）。"""
    messages = [
        canonical.turn_to_message(FakeTurn(role="user", content="大家好", sender_name="小明", message_id="m1")),
        canonical.turn_to_message(FakeTurn(role="assistant", content="晚上好", message_type="speak", message_id="m2")),
    ]
    assert canonical.to_text_view(messages) == "小明: 大家好 [id:m1]\n主播: 晚上好"


def test_golden_text_view_proactive_placeholder_annotated() -> None:
    """dict 直传路径：user 行 content 以"（主动发言"开头时标 [系统]（差异清单第 2/7 条）。"""
    messages = [{"role": "user", "content": "（主动发言，主题：测试）"}]
    assert canonical.to_text_view(messages) == "[系统] （主动发言，主题：测试）"


def test_golden_text_view_turn_channel_placeholder_prefixed() -> None:
    """turn 通道：user 占位先被加昵称前缀，[系统] 分支不触发（差异清单第 7 条）。"""
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
    """批渲染逐行 canonical content；空批为空串（差异清单第 1 条：批带 [id:]）。"""
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
