"""记忆渲染：常驻简报、线索卡、老观众在场一行与两档写法。"""

from __future__ import annotations

from typing import Optional

import pytest

from src.agents.streamer.memory.render import (
    MAX_BRIEF_COMMITMENTS,
    MAX_PRESENCE_VIEWERS,
    PRESENCE_RECENT_MS,
    render_archive_block,
    render_archive_number,
    render_brief,
    render_commitment_number,
    render_commitment_text,
    render_cue_block,
    render_forget_notice,
    render_game_card,
    render_presence_line,
    render_retract_notice,
    render_spoken_memory,
    render_viewer_card,
)
from src.modules.storage.models.memory import ArchiveLine, Commitment

_NOW = 10_000_000_000


def _commitment(
    *,
    cid: int = 1,
    what: str = "接着讲鬼故事的结局",
    quote: str = "下次接着讲那个鬼故事的结局",
    viewer_id: Optional[int] = None,
    game: Optional[str] = None,
    created_session_id: Optional[int] = None,
) -> Commitment:
    return Commitment(
        id=cid,
        what=what,
        quote=quote,
        viewer_id=viewer_id,
        game=game,
        created_session_id=created_session_id,
        created_at_ms=1,
        updated_at_ms=1,
    )


def _archive(*, line_id: int = 41, text: str = "Minecraft：水车搭好了", quote: str = "水车搭好啦") -> ArchiveLine:
    return ArchiveLine(id=line_id, live_session_id=7, text=text, quote=quote, evidence_chat_id=3)


def test_numbers_use_the_documented_prefixes() -> None:
    assert render_commitment_number(7) == "约#7"
    assert render_archive_number(41) == "档#41"


def test_commitment_text_gives_quote_only_to_planner_and_only_for_everyone() -> None:
    everyone = _commitment(quote="下次接着讲那个鬼故事的结局")
    assert render_commitment_text(everyone, is_previous=True) == "你上一场说过「下次接着讲那个鬼故事的结局」"
    assert render_commitment_text(everyone) == "你之前说过「下次接着讲那个鬼故事的结局」"
    assert render_commitment_text(everyone, spoken=True) == "接着讲鬼故事的结局"

    # 对某位观众的承诺任何档都不给原话：原话可能夹着 ta 的私事
    for_viewer = _commitment(quote="阿杰别难过，失恋而已！下次进 MC 给你盖个小房子", viewer_id=9)
    assert "失恋" not in render_commitment_text(for_viewer)
    assert "失恋" not in render_commitment_text(for_viewer, spoken=True)


def test_archive_block_renders_title_and_quote_and_caps_lines() -> None:
    lines = [_archive(line_id=index, text=f"第 {index} 条", quote="原话") for index in range(1, 12)]
    rendered = render_archive_block(lines)
    assert len(rendered) == 8
    assert rendered[0] == "- [档#1] 第 1 条（你说过「原话」）"
    assert render_archive_block([_archive(text="", quote="")]) == []


def test_brief_has_archive_section_and_commitment_section() -> None:
    brief = render_brief(
        archive_lines=[_archive()],
        commitments=[_commitment(cid=5, created_session_id=3, quote="等我把城堡屋顶盖完，第一个请大家来参观")],
        previous_session_id=3,
    )
    assert brief.startswith("# 她记得的事")
    assert "上一场：" in brief
    assert "[档#41] Minecraft：水车搭好了（你说过「水车搭好啦」）" in brief
    assert "还没兑现、你当众说过的事：" in brief
    assert "[约#5] 「等我把城堡屋顶盖完，第一个请大家来参观」（上一场）" in brief


def test_brief_caps_commitments_and_points_to_recall_tool() -> None:
    commitments = [_commitment(cid=index, quote=f"第 {index} 件事") for index in range(1, 9)]
    brief = render_brief(commitments=commitments, earlier_commitment_count=0)
    listed = brief.count("- [约#")
    assert listed == MAX_BRIEF_COMMITMENTS
    capped = render_brief(commitments=commitments, earlier_commitment_count=3)
    assert "另有 3 件，可以用 memory_recall 查" in capped


def test_brief_is_empty_without_material() -> None:
    assert render_brief() == ""


def test_game_card_names_the_game_and_her_line() -> None:
    card = render_game_card(_commitment(cid=7, game="Minecraft"), game="Minecraft")
    assert card.startswith("[约#7·Minecraft]")
    assert "这件承诺挂在你玩的游戏 Minecraft 上" in card


def test_viewer_card_gives_action_only_and_the_responding_danmaku() -> None:
    card = render_viewer_card(
        _commitment(cid=9, what="在 Minecraft 里给 ta 盖个小房子", quote="阿杰别难过", viewer_id=9),
        viewer_label="阿杰",
        message_id="m812",
    )
    assert card.startswith("[约#9·对阿杰]")
    assert "阿杰（弹幕 m812）在" in card
    assert "你答应过 ta：在 Minecraft 里给 ta 盖个小房子" in card
    assert "阿杰别难过" not in card
    assert "别提当时的缘由" in card


def test_presence_line_uses_two_tiers_and_caps_people() -> None:
    recent = _NOW - PRESENCE_RECENT_MS // 2
    old = _NOW - PRESENCE_RECENT_MS * 4
    line = render_presence_line([("小林", recent), ("阿杰", old)], now_ms=_NOW)
    assert "老观众在场" in line
    assert "小林（前几天刚来过）" in line
    assert "阿杰（有阵子没来）" in line
    crowd = [(f"观众{index}", recent) for index in range(1, MAX_PRESENCE_VIEWERS + 3)]
    capped = render_presence_line(crowd, now_ms=_NOW)
    assert f"等 {len(crowd) - MAX_PRESENCE_VIEWERS} 位" in capped
    assert render_presence_line([], now_ms=_NOW) == ""
    # 没有上次时刻不编时长，按"有阵子没来"
    assert "有阵子没来" in render_presence_line([("无名", None)], now_ms=_NOW)


def test_command_notices_point_by_message_id_not_by_name() -> None:
    forget = render_forget_notice(message_id="m815")
    assert "【已按" in forget and "处理】" in forget and "m815" in forget
    assert "别叫 ta 的名字" in forget
    retract = render_retract_notice(message_id="m821")
    assert "【已撤回" in retract and "m821" in retract


def test_cue_block_orders_cards_presence_and_notices_under_one_header() -> None:
    block = render_cue_block(
        cards=["[约#7·Minecraft] 卡"],
        presence="老观众在场（只知道 ta 常来、多久没来）：小林（前几天刚来过）",
        notices=[render_forget_notice(message_id="m815")],
    )
    lines = block.split("\n")
    assert lines[0].startswith("【这一批想起来的事】")
    assert "[约#7·Minecraft] 卡" in lines[1]
    assert lines[2].startswith("老观众在场")
    assert lines[3].startswith("【已按")
    assert render_cue_block() == ""


def test_spoken_memory_has_header_action_and_no_quote() -> None:
    spoken = render_spoken_memory(
        target_label="阿杰",
        commitment=_commitment(what="在 Minecraft 里给 ta 盖个小房子", quote="阿杰别难过", viewer_id=9),
        is_previous=True,
    )
    assert spoken.startswith("## 你想起来的事")
    assert "你上次答应过阿杰：在 Minecraft 里给 ta 盖个小房子。" in spoken
    assert "阿杰别难过" not in spoken


@pytest.mark.parametrize("cid", [1, 12, 999])
def test_commitment_number_is_stable(cid: int) -> None:
    assert render_commitment_number(cid) == f"约#{cid}"
