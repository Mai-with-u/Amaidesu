"""长期笔记合并操作与生命周期：模型给的编号操作逐条执行、按场次淡出。

纯函数，不碰文件与模型：
- 合并操作逐条执行，没动到的行不变；人工条目只读；编号不存在、观众不在名单里都不硬上
- 生命周期按"隔了几场写过回顾的同类场次"算：没新证据的淡出到搁置、了结的约定留一场、
  超篇幅先搁置最久没被提起的；人工条目永远不动
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.agents.streamer.memory.note_merge import apply_lifecycle, apply_ops, parse_ops, resolve_ops
from src.agents.streamer.memory.notes import (
    SECTION_GOALS,
    SECTION_JOKES,
    SECTION_PROMISES,
    SECTION_SELF,
    SECTION_SHELVED,
    SECTION_UNCERTAIN,
    STATUS_DONE,
    STATUS_DROPPED,
    STATUS_OPEN,
    NoteDocument,
    parse_notes,
    render_for_merge,
    render_notes,
)
from tests.agents.streamer.helpers import SAMPLE_NOTES


def _doc() -> NoteDocument:
    return parse_notes(SAMPLE_NOTES)


def _apply(doc: NoteDocument, *raw: Dict[str, Any], current: int = 6, **kwargs: Any) -> List[str]:
    _listing, numbered = render_for_merge(doc)
    ops, rejected = resolve_ops(raw, numbered)
    return rejected + apply_ops(doc, ops, current=current, viewers=kwargs.get("viewers", {12: "老王"}),
                                games=kwargs.get("games", ("minecraft",)))  # fmt: skip


def _find(doc: NoteDocument, text: str) -> Any:
    return next(item for item in doc.items if item.text == text)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('```json\n{"ops": [{"op": "seen", "id": 1}]}\n```', [{"op": "seen", "id": 1}]),
        ('[{"op": "seen", "id": 2}, "垃圾"]', [{"op": "seen", "id": 2}]),
        ("我觉得这场没什么要记的", None),
    ],
    ids=["fenced_object", "bare_list", "prose"],
)
def test_parse_ops(content: str, expected: Any) -> None:
    assert parse_ops(content) == expected


# ---------------------------------------------------------------------------
# 合并操作
# ---------------------------------------------------------------------------


def test_untouched_lines_stay_byte_identical() -> None:
    doc = _doc()
    before = render_notes(doc).splitlines()
    changes = _apply(doc, {"op": "seen", "id": 1})
    after = render_notes(doc).splitlines()
    assert changes == ["续上：答应老王下次玩恐怖游戏"]
    assert [line for line in after if line not in before] == [
        "- 答应老王下次玩恐怖游戏〔未兑现 · 对 老王#12 · 始于 #3 · 最近 #6〕"
    ]


def test_add_links_known_viewer_and_game_only() -> None:
    doc = _doc()
    changes = _apply(
        doc,
        {"op": "add", "section": SECTION_PROMISES, "text": "答应老王下场挖钻石", "viewer_id": 12, "game": "minecraft"},
        {"op": "add", "section": SECTION_PROMISES, "text": "答应路人唱歌", "viewer_id": 999},
        {"op": "add", "section": SECTION_JOKES, "text": "翻车时坚称是节目效果"},
        {"op": "add", "section": SECTION_SHELVED, "text": "不能直接塞进搁置"},
    )
    added = _find(doc, "答应老王下场挖钻石")
    assert (added.status, added.viewer_id, added.game, added.since, added.seen) == (STATUS_OPEN, 12, "minecraft", 6, 6)
    assert _find(doc, "答应路人唱歌").viewer_id is None
    assert "观众 #999 不在本场名单里，没挂对象" in changes[1]
    assert changes[2] == "续上（新增与已有条目相同）：翻车时坚称是节目效果"
    assert _find(doc, "翻车时坚称是节目效果").seen == 6
    assert changes[3].startswith("没采纳")


def test_status_edit_move_remove_and_revive() -> None:
    doc = _doc()
    changes = _apply(
        doc,
        {"op": "done", "id": 1, "reason": "这场玩了"},
        {"op": "drop", "id": 3, "reason": "不是约定"},  # 老梗不能标兑现
        {"op": "edit", "id": 3, "text": "翻车时坚称是节目效果和风险预案"},
        {"op": "move", "id": 6, "section": SECTION_SELF},
        {"op": "seen", "id": 7},
        {"op": "remove", "id": 2, "reason": "记错了"},
    )
    assert _find(doc, "答应老王下次玩恐怖游戏").status == STATUS_DONE
    assert changes[1].startswith("没采纳：只有约定能标兑现或放弃")
    assert _find(doc, "翻车时坚称是节目效果和风险预案").seen == 6
    assert _find(doc, "好像怕黑").section == SECTION_SELF
    revived = _find(doc, "答应小明画头像")
    assert (revived.section, revived.origin, revived.shelved_at, revived.seen) == (SECTION_PROMISES, "", None, 6)
    assert all(item.text != "答应全场周末更新整活视频" for item in doc.items)
    assert changes[-1] == "删除：答应全场周末更新整活视频（记错了）"


def test_manual_items_are_read_only_and_bad_ids_rejected() -> None:
    doc = _doc()
    changes = _apply(doc, {"op": "remove", "id": 4}, {"op": "seen", "id": 99}, {"op": "rewrite_all"})
    assert any("不存在的第 99 条" in change for change in changes)
    assert any("不认识的操作 rewrite_all" in change for change in changes)
    assert any(change.startswith("没采纳（人工条目只读）") for change in changes)
    assert _find(doc, "床名叫咕咕床").manual


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def test_items_without_new_evidence_fade_to_shelf() -> None:
    """没有新证据的条目隔满 N 场写过回顾的场次就搁置；人工条目不动；本场续上的留着。"""
    doc = parse_notes("## 老梗与口头禅\n- 老梗〔始于 #1 · 最近 #2〕\n- 新梗〔始于 #8 · 最近 #8〕\n- 手写梗〔人工〕")
    changes = apply_lifecycle(doc, current=8, settled_ids=[2, 4, 6, 8], stale_sessions=3, max_chars=2000)
    assert changes == ["搁置（过期）：〔老梗与口头禅〕老梗"]
    assert _find(doc, "老梗").section == SECTION_SHELVED and _find(doc, "老梗").origin == SECTION_JOKES
    assert _find(doc, "新梗").section == SECTION_JOKES
    assert _find(doc, "手写梗").section == SECTION_JOKES


def test_closed_promise_stays_one_session_then_shelves() -> None:
    doc = parse_notes(
        "## 答应过的事\n- 刚兑现的〔已兑现 · 最近 #8〕\n- 上场放弃的〔放弃 · 最近 #6〕\n- 没兑现的〔未兑现 · 最近 #6〕"
    )
    apply_lifecycle(doc, current=8, settled_ids=[6, 8], stale_sessions=3, max_chars=2000)
    assert _find(doc, "刚兑现的").section == SECTION_PROMISES
    assert (_find(doc, "上场放弃的").section, _find(doc, "上场放弃的").status) == (SECTION_SHELVED, STATUS_DROPPED)
    assert _find(doc, "没兑现的").section == SECTION_PROMISES


def test_over_budget_shelves_least_recently_seen_first_and_trims_shelf() -> None:
    doc = parse_notes(
        "## 老梗与口头禅\n"
        f"- {'旧' * 30}〔最近 #2〕\n- {'新' * 30}〔最近 #8〕\n- {'人' * 30}〔人工〕\n"
        "## 搁置\n- 很早搁置的〔搁置 #1 过期〕\n- 后来搁置的〔搁置 #5 过期〕"
    )
    changes = apply_lifecycle(doc, current=8, settled_ids=[2, 8], stale_sessions=5, max_chars=90, shelved_keep=2)
    assert changes[0] == f"搁置（篇幅）：〔老梗与口头禅〕{'旧' * 30}"
    assert _find(doc, "新" * 30).section == SECTION_JOKES and _find(doc, "人" * 30).section == SECTION_JOKES
    assert [item.text for item in doc.section_items(SECTION_SHELVED)] == ["后来搁置的", "旧" * 30]
    assert changes[-1] == "清出搁置区：很早搁置的"


def test_late_merge_counts_only_sessions_up_to_the_merged_one() -> None:
    """补合并一场旧场次时，之后已写好回顾的场次不算"隔了几场"：那几场还没合并，不知道有没有人提。"""
    doc = parse_notes("## 老梗与口头禅\n- 老梗〔最近 #4〕\n- 本场改过的梗")
    changes = apply_lifecycle(doc, current=5, settled_ids=[4, 5, 6, 7, 8], stale_sessions=3, max_chars=2000)
    assert changes == []
    assert _find(doc, "老梗").section == SECTION_JOKES and _find(doc, "本场改过的梗").section == SECTION_JOKES


def test_game_items_age_only_in_sessions_that_played_the_game() -> None:
    """只跟某个游戏有关的目标与老梗只数玩过那个游戏的场次；约定不管玩没玩照常数，宁可早点放下。"""
    doc = parse_notes(
        "## 长线目标\n- 搭水车〔游戏 minecraft · 最近 #5〕\n- 攒钱买新麦〔最近 #5〕\n"
        "## 答应过的事\n- 下次继续炸TNT〔未兑现 · 游戏 minecraft · 最近 #5〕"
    )
    changes = apply_lifecycle(
        doc, current=9, settled_ids=[5, 6, 7, 8, 9], stale_sessions=3, max_chars=2000,
        session_games={5: {"minecraft"}, 9: {"minecraft"}},
    )  # fmt: skip
    # #6～#8 是杂谈场：水车目标只隔了一场 minecraft，留着；不挂游戏的目标与约定都隔满了
    assert _find(doc, "搭水车").section == SECTION_GOALS
    assert sorted(changes) == ["搁置（过期）：〔答应过的事〕下次继续炸TNT", "搁置（过期）：〔长线目标〕攒钱买新麦"]


def test_game_items_count_every_session_when_play_history_unknown() -> None:
    doc = parse_notes("## 长线目标\n- 搭水车〔游戏 minecraft · 最近 #5〕")
    apply_lifecycle(doc, current=9, settled_ids=[5, 6, 7, 8, 9], stale_sessions=3, max_chars=2000)
    assert _find(doc, "搭水车").section == SECTION_SHELVED


def test_self_items_never_go_stale_and_go_last_under_budget() -> None:
    """关于自己的条目没人再提也不淡出（她是谁不靠观众复述）；超篇幅时也排在最后搁置。"""
    doc = parse_notes(f"## 关于我自己\n- {'我' * 30}〔最近 #1〕\n## 老梗与口头禅\n- {'梗' * 30}〔最近 #8〕")
    changes = apply_lifecycle(doc, current=8, settled_ids=list(range(1, 9)), stale_sessions=3, max_chars=60)
    assert changes == [f"搁置（篇幅）：〔老梗与口头禅〕{'梗' * 30}"]
    assert _find(doc, "我" * 30).section == SECTION_SELF


def test_legacy_items_start_counting_from_this_session() -> None:
    doc = parse_notes("## 不确定\n- 好像怕黑")
    assert apply_lifecycle(doc, current=9, settled_ids=[3, 5, 9], stale_sessions=1, max_chars=2000) == []
    assert _find(doc, "好像怕黑").seen == 9 and _find(doc, "好像怕黑").section == SECTION_UNCERTAIN
