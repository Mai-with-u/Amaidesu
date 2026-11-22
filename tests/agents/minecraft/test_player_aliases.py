"""Minecraft 侧玩家常用称呼锚定：公屏发言人格式化、只读别名索引与同伴防串人锚点。"""

from __future__ import annotations

from src.agents.minecraft.chat_attribution import (
    NOTE_UNVERIFIED,
    ChatAttribution,
    format_chat_sender,
    render_attributed_chat_line,
)
from src.agents.minecraft.companions import (
    build_player_alias_index as companions_build_index,
)
from src.agents.minecraft.companions import (
    format_chat_sender as companions_format_sender,
)
from src.agents.minecraft.companions import (
    format_companions_anchor as companions_format_anchor,
)
from src.agents.minecraft.player_interactions import (
    build_player_alias_index,
    format_companions_anchor,
)
from src.modules.storage.models.game_players import GamePlayer


def _player(
    player_id: int,
    uuid: str,
    name: str,
    aliases: list[str],
    *,
    forget_requested_at_ms: int | None = None,
) -> GamePlayer:
    return GamePlayer(
        id=player_id,
        game="minecraft",
        uuid=uuid,
        name=name,
        names=[name],
        aliases=aliases,
        first_seen_ms=1_000,
        last_seen_ms=2_000,
        forget_requested_at_ms=forget_requested_at_ms,
    )


def test_build_player_alias_index_skips_forgotten_and_ambiguous_names() -> None:
    """只读索引按 UUID 精确查表；同名不同 UUID 不写入按名映射，防止无 UUID 消息串人。"""
    players = [
        _player(1, "UUID-ALPHA", "AlphaPlayer", ["小阿尔", "阿法"]),
        _player(2, "uuid-dup-1", "SharedName", ["小一号"]),
        _player(3, "uuid-dup-2", "SharedName", ["小二号"]),
        _player(4, "uuid-forget", "ForgetPlayer", ["不该出现"], forget_requested_at_ms=5_000),
        _player(5, "uuid-plain", "PlainPlayer", []),
    ]
    by_uuid, by_name = build_player_alias_index(players)

    assert by_uuid == {
        "uuid-alpha": ("小阿尔", "阿法"),
        "uuid-dup-1": ("小一号",),
        "uuid-dup-2": ("小二号",),
    }
    assert by_name == {"alphaplayer": ("小阿尔", "阿法")}
    assert "sharedname" not in by_name
    assert "uuid-forget" not in by_uuid

    # UUID 优先，同名不同 UUID 依然精准区分
    assert (
        format_chat_sender("SharedName", sender_id="uuid-dup-1", aliases_by_uuid=by_uuid, aliases_by_name=by_name)
        == "SharedName（常叫：小一号）"
    )
    assert (
        format_chat_sender("SharedName", sender_id="uuid-dup-2", aliases_by_uuid=by_uuid, aliases_by_name=by_name)
        == "SharedName（常叫：小二号）"
    )
    assert (
        format_chat_sender("SharedName", sender_id="", aliases_by_uuid=by_uuid, aliases_by_name=by_name) == "SharedName"
    )
    # 唯一名称在无 UUID 时可回落按名命中（大小写不敏感）
    assert (
        format_chat_sender("alphaplayer", aliases_by_uuid=by_uuid, aliases_by_name=by_name)
        == "alphaplayer（常叫：小阿尔、阿法）"
    )
    assert format_chat_sender("PlainPlayer", aliases_by_uuid=by_uuid, aliases_by_name=by_name) == "PlainPlayer"


def test_render_attributed_chat_line_merges_alias_and_note_without_nested_parens() -> None:
    """公屏归属行把常用称呼与未核验说明合并进同一对括号，系统消息原样保留。"""
    by_uuid = {"uuid-alpha": ("小阿尔",)}
    by_name = {"alphaplayer": ("小阿尔",)}

    verified = ChatAttribution(kind="player", sender="AlphaPlayer", sender_id="uuid-alpha", content="我在仓库门口")
    assert (
        render_attributed_chat_line(verified, aliases_by_uuid=by_uuid, aliases_by_name=by_name)
        == "AlphaPlayer（常叫：小阿尔）：我在仓库门口"
    )

    unverified = ChatAttribution(
        kind="player", sender="AlphaPlayer", sender_id="", content="帮忙拿点木头", note=NOTE_UNVERIFIED
    )
    assert (
        render_attributed_chat_line(unverified, aliases_by_uuid=by_uuid, aliases_by_name=by_name)
        == f"AlphaPlayer（常叫：小阿尔；{NOTE_UNVERIFIED}）：帮忙拿点木头"
    )

    system_msg = ChatAttribution(kind="system", sender="", sender_id="", content="服务器将在 5 分钟后保存")
    assert render_attributed_chat_line(system_msg, aliases_by_uuid=by_uuid) == "服务器将在 5 分钟后保存"


def test_format_companions_anchor_and_companions_reexports() -> None:
    """同伴称呼核对锚点按主键稳定排列，跳过无别名与申请遗忘的玩家，且由 companions 统一导出。"""
    players = [
        _player(2, "uuid-beta", "BetaPlayer", ["小贝"]),
        _player(1, "uuid-alpha", "AlphaPlayer", ["小阿尔", "阿法"]),
        _player(3, "uuid-plain", "PlainPlayer", []),
        _player(4, "uuid-forget", "ForgetPlayer", ["不该出现"], forget_requested_at_ms=9_999),
    ]
    anchor = format_companions_anchor(players)
    assert anchor == (
        "同伴称呼核对（提交解说或喊人前先核对账号 ID 与常用称呼，勿串人）："
        "AlphaPlayer（常叫：小阿尔、阿法）；BetaPlayer（常叫：小贝）"
    )
    assert format_companions_anchor([_player(3, "uuid-plain", "PlainPlayer", [])]) == ""
    assert companions_format_anchor is format_companions_anchor
    assert companions_build_index is build_player_alias_index
    assert companions_format_sender is format_chat_sender
