"""记忆渲染：常驻简报、线索卡、老观众在场一行与两档写法。"""

from __future__ import annotations

from typing import Optional

import pytest

from src.agents.streamer.memory.render import (
    MAX_BRIEF_ARCHIVE_LINES,
    MAX_BRIEF_COMMITMENTS,
    MAX_PRESENCE_VIEWERS,
    PRESENCE_RECENT_MS,
    brief_archive_ids,
    format_memory_refs,
    memory_ref_archive_numbers,
    memory_ref_numbers,
    render_archive_block,
    render_archive_card,
    render_archive_number,
    render_brief,
    render_commitment_number,
    render_commitment_text,
    render_cue_block,
    render_forget_notice,
    render_game_card,
    render_presence_line,
    render_retract_notice,
    render_spoken_archive,
    render_spoken_memory,
    render_viewer_card,
)
from src.modules.storage.models.memory import ArchiveLine, Commitment
from src.modules.types.game_save import GameSave

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
    lines = [
        _archive(line_id=index, text=f"第 {index} 条", quote="原话") for index in range(1, MAX_BRIEF_ARCHIVE_LINES + 4)
    ]
    rendered = render_archive_block(lines)
    assert len(rendered) == MAX_BRIEF_ARCHIVE_LINES == 12, "一份滚动档案至多 12 行，简报照这个数列"
    assert rendered[0] == "- [档#1] 第 1 条（你说过「原话」）", "档案行带编号：要提这件事就点名"
    assert render_archive_block([_archive(text="", quote="")]) == []


def test_archive_block_marks_lines_cited_this_session_and_keeps_them() -> None:
    """本场点名用过的档案行留着、末尾标"（本场已提过）"：她知道的事实不能一场里忘掉。"""
    lines = [_archive(line_id=41), _archive(line_id=42, text="Minecraft：女仆取名团子", quote="")]

    rendered = render_archive_block(lines, used_ids={41})

    assert rendered == [
        "- [档#41] Minecraft：水车搭好了（你说过「水车搭好啦」）（本场已提过）",
        "- [档#42] Minecraft：女仆取名团子",
    ]


def test_memory_refs_keep_commitment_and_archive_numbers_apart() -> None:
    """记忆引用里约#与档#是两个编号空间，同号不同义。"""
    raw = '["约#7", "档#41", "档#7", "乱写#3"]'
    assert memory_ref_numbers(raw) == {7}
    assert memory_ref_archive_numbers(raw) == {41, 7}
    assert memory_ref_archive_numbers(format_memory_refs([5])) == set()
    assert memory_ref_archive_numbers("不是 JSON") == set()


def test_viewer_sourced_lines_say_they_were_decided_with_viewers_without_naming_anyone() -> None:
    """人批准的观众候选写成的行：简报、线索卡、说话档都注明和观众一起定的，别点名是哪一位。"""
    line = ArchiveLine(
        id=43, live_session_id=7, text="和观众一起给小羊取名雪球", quote="就叫雪球", evidence_chat_id=3, source="viewer"
    )
    assert render_archive_block([line]) == [
        "- [档#43] 和观众一起给小羊取名雪球（你说过「就叫雪球」；和观众一起定的，不记得是哪一位，别点名）"
    ]
    assert "和观众一起定的，不记得是哪一位，别点名" in render_archive_card(line, why="弹幕提到了雪球")
    assert render_spoken_archive(line).endswith("和观众一起给小羊取名雪球（和观众一起定的，别说成是哪一位定的）。")
    assert "观众" not in render_archive_card(_archive(), why="弹幕提到了水车"), "她自己说的不加注记"


def test_spoken_archive_gives_title_only() -> None:
    """说话档只给标题：不带编号，不带原话（原话是 Planner 对口径用的）。"""
    spoken = render_spoken_archive(_archive())
    assert "水车搭好了" in spoken and "档#" not in spoken and "水车搭好啦" not in spoken
    assert render_spoken_archive(_archive(text=" ")) == ""


def test_brief_has_archive_section_and_commitment_section() -> None:
    brief = render_brief(
        archive_lines=[_archive()],
        commitments=[_commitment(cid=5, created_session_id=7, quote="等我把城堡屋顶盖完，第一个请大家来参观")],
        previous_session_id=7,
    )
    assert brief.startswith("# 她记得的事")
    assert "上一场：" in brief
    assert "- [档#41] Minecraft：水车搭好了（你说过「水车搭好啦」）" in brief
    assert "还没兑现、你当众说过的事：" in brief
    assert "[约#5] 「等我把城堡屋顶盖完，第一个请大家来参观」（上一场）" in brief


def test_brief_splits_snapshot_into_previous_and_earlier_blocks() -> None:
    """快照里上一场新写的行列在"上一场："下，从之前几场接住的行列在"之前："下（按每行写在哪一场分）。"""
    lines = [
        ArchiveLine(
            id=1, live_session_id=9, text="Minecraft：炉子点火烤海带", quote="炉子点上火了", evidence_chat_id=11
        ),
        ArchiveLine(id=2, live_session_id=4, text="Minecraft：女仆取名团子", quote="就叫团子", evidence_chat_id=12),
    ]

    brief = render_brief(archive_lines=lines, previous_session_id=9)

    assert (
        "上一场：\n- [档#1] Minecraft：炉子点火烤海带（你说过「炉子点上火了」）\n"
        "之前：\n- [档#2] Minecraft：女仆取名团子（你说过「就叫团子」）"
    ) in brief
    only_earlier = render_brief(archive_lines=lines[1:], previous_session_id=9)
    assert "上一场：" not in only_earlier and "之前：\n- [档#2] Minecraft：女仆取名团子" in only_earlier


def test_brief_lists_one_thing_once_when_archive_and_commitment_share_a_line() -> None:
    """同一句台词既整理成档案行、又登记成承诺：简报只留承诺那一条，别的档案行照旧。"""
    promise = Commitment(
        id=1,
        what="接着救墓碑女仆",
        quote="下次接着救",
        evidence_chat_id=1226,
        created_session_id=53,
        created_at_ms=1,
        updated_at_ms=1,
    )
    same_line = ArchiveLine(
        id=1, live_session_id=53, text="墓碑女仆救援留待下次", quote="下次接着救", evidence_chat_id=1226
    )
    other_line = ArchiveLine(id=2, live_session_id=53, text="墓碑归属悬念未解", quote="我偏不信", evidence_chat_id=1225)

    brief = render_brief(archive_lines=[same_line, other_line], commitments=[promise], previous_session_id=53)

    assert "墓碑女仆救援留待下次" not in brief
    assert "墓碑归属悬念未解" in brief
    assert brief.count("下次接着救") == 1
    assert brief_archive_ids([same_line, other_line], [promise]) == [2], "没列出的行不能点名"


def test_brief_caps_commitments_and_points_to_recall_tool() -> None:
    commitments = [_commitment(cid=index, quote=f"第 {index} 件事") for index in range(1, 9)]
    brief = render_brief(commitments=commitments, earlier_commitment_count=0)
    listed = brief.count("- [约#")
    assert listed == MAX_BRIEF_COMMITMENTS
    capped = render_brief(commitments=commitments, earlier_commitment_count=3)
    assert "另有 3 件，可以用 memory_recall 查" in capped


def test_brief_commitment_section_ends_with_a_fixed_line_on_how_to_close() -> None:
    """承诺段末尾固定一行：做不到的当众说清楚、没做的别说成做完了；只在有承诺段时出现，逐字不变。"""
    one = render_brief(commitments=[_commitment(cid=5, quote="下次给大家唱首歌")])
    more = render_brief(
        commitments=[_commitment(cid=index, quote=f"第 {index} 件事") for index in range(1, 9)],
        earlier_commitment_count=3,
    )
    hint = "（确实做不到的，找个合适的时机当众说清楚；没做的别说成做完了）"
    assert one.endswith("\n" + hint) and more.endswith("另有 3 件，可以用 memory_recall 查\n" + hint)
    assert one.count(hint) == 1
    assert hint not in render_brief(archive_lines=[_archive()]), "没有承诺段时不出现"


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
    assert lines[0].startswith("【这一批想起来的事】") and "以眼前为准" in lines[0], "和常驻简报一样以眼前为准"
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


def _game_line(
    text: str, *, game: Optional[str], save_id: str = "", save_name: str = "", chat_id: int = 3
) -> ArchiveLine:
    return ArchiveLine(
        id=chat_id,
        live_session_id=7,
        text=text,
        quote="",
        evidence_chat_id=chat_id,
        game=game,
        save_id=save_id,
        save_name=save_name,
    )


def test_brief_folds_other_save_lines_and_marks_unsure_ones() -> None:
    """上一场在老档里做的事折成一行；本存档的、没挂游戏的照列；没记存档的游戏行注明不确定；开头写眼下在哪。"""
    lines = [
        _game_line("Minecraft：北边的桥修好了", game="minecraft", save_id="a", save_name="老档", chat_id=1),
        _game_line("猜谜环节", game=None, chat_id=2),
        _game_line("Minecraft：麦田翻了一遍", game="minecraft", save_id="a", save_name="老档", chat_id=3),
        _game_line("Minecraft：出生点旁边有村子", game="minecraft", save_id="b", save_name="新档", chat_id=4),
        _game_line("Minecraft：挖到钻石", game="minecraft", chat_id=5),
    ]
    brief = render_brief(archive_lines=lines, current_saves={"minecraft": GameSave(id="b", name="新档")})

    assert brief.splitlines()[1:] == [
        "眼下 minecraft 开着存档「新档」。",
        "上一场：",
        "- 还在 minecraft「老档」里做过 2 件事（现在开着「新档」，那边的建筑和进度这里没有；要细节用 memory_recall 查）",
        "- [档#2] 猜谜环节",
        "- [档#4] Minecraft：出生点旁边有村子",
        "- [档#5] Minecraft：挖到钻石（不确定是 minecraft 哪个存档里的事）",
    ]
    # 折起来的老档行不带编号，也不在点名名单里
    assert brief_archive_ids(lines, current_saves={"minecraft": GameSave(id="b", name="新档")}) == [2, 4, 5]
    # 不分存档时与原来逐字节相同
    assert render_brief(archive_lines=lines) == render_brief(archive_lines=lines, current_saves={})
    plain = render_brief(archive_lines=lines)
    assert "- [档#1] Minecraft：北边的桥修好了" in plain and "做过 2 件事" not in plain
    assert brief_archive_ids(lines) == [1, 2, 3, 4, 5]


def test_brief_before_entering_a_save_folds_every_saved_line_and_notes_unsaved_session() -> None:
    """还没进存档：记着存档的一律折起来；上一场玩时还没记存档的，块尾注一句。"""
    lines = [
        _game_line("Minecraft：北边的桥修好了", game="minecraft", save_id="a", save_name="老档", chat_id=1),
        _game_line("聊了最近的动画", game=None, chat_id=2),
    ]
    brief = render_brief(archive_lines=lines, current_saves={"minecraft": GameSave()}, unsaved_games=["minecraft"])

    assert "minecraft 眼下还没进存档，先别把游戏里的进度当成眼前的事。" in brief
    assert "还在 minecraft「老档」里做过 1 件事（现在还没进 minecraft 的存档" in brief
    assert brief.endswith("（那场玩 minecraft 时还没记存档，上面游戏里的事未必是现在这个存档的）")


def test_cards_mark_unsure_save_and_where_a_viewer_promise_was_made() -> None:
    unsure = render_game_card(_commitment(cid=7, game="minecraft"), game="minecraft", unsure_save=True)
    assert "不确定是在哪个存档里答应的，先对一对眼前的世界；时机对再提" in unsure
    card = render_viewer_card(
        _commitment(cid=9, what="给 ta 盖个小房子", viewer_id=9, game="minecraft"),
        viewer_label="阿杰",
        message_id="m812",
        place="在 minecraft「老档」里，不是眼下这个存档",
    )
    assert "你答应过 ta：给 ta 盖个小房子（之前；在 minecraft「老档」里，不是眼下这个存档）。" in card
