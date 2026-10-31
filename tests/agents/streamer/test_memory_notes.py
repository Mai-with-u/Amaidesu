"""长期笔记条目格式：解析与渲染、注入视图、合并清单、人手保存的标记、两份各自改过的合并。

纯函数，不碰文件与模型：
- 标签读写往返；旧格式（整份没标签）按模型条目读并认出约定状态；有标签的文件里
  没标签的行算人工；人自己加的节与说明文字原样保留
- 注入只给四个注入节，去标签；对某位观众的条目只在点名时给；按开着的游戏筛
- 别的程序写进来的条目（``来自`` 标签）不算人改的，注入与清单里注明来处
- 三方合并：只有一边动过的取那一边，两边都动过以本地为准，对方新加的排到节末
"""

from __future__ import annotations

from typing import Any

from src.agents.streamer.memory.notes import (
    SECTION_PROMISES,
    SECTION_SHELVED,
    STATUS_OPEN,
    NoteDocument,
    mark_manual_edits,
    merge_three_way,
    parse_notes,
    person_lines,
    render_for_injection,
    render_for_merge,
    render_notes,
)
from tests.agents.streamer.helpers import SAMPLE_NOTES


def _doc() -> NoteDocument:
    return parse_notes(SAMPLE_NOTES)


def _find(doc: NoteDocument, text: str) -> Any:
    return next(item for item in doc.items if item.text == text)


# ---------------------------------------------------------------------------
# 解析与渲染
# ---------------------------------------------------------------------------


def test_tagged_file_roundtrips_and_untagged_lines_are_manual() -> None:
    doc = _doc()
    promise = _find(doc, "答应老王下次玩恐怖游戏")
    assert (promise.status, promise.viewer_id, promise.viewer_name, promise.since, promise.seen) == (
        STATUS_OPEN, 12, "老王", 3, 5,
    )  # fmt: skip
    assert _find(doc, "翻车时坚称是节目效果").extra == ["不认识的标签"]
    assert _find(doc, "床名叫咕咕床").manual  # 有标签的文件里手写的行
    assert _find(doc, "周三不播").manual  # 人自己加的节
    shelved = _find(doc, "答应小明画头像")
    assert (shelved.section, shelved.origin, shelved.shelved_at, shelved.shelve_reason) == (
        SECTION_SHELVED, SECTION_PROMISES, 4, "过期",
    )  # fmt: skip

    rendered = render_notes(doc)
    assert rendered.startswith("运营写在最前面的说明\n\n## 答应过的事")
    assert parse_notes(rendered).items == doc.items  # 写回再读一字不差
    assert rendered.index("## 搁置") < rendered.index("## 运营自己的节")
    assert "- 床名叫咕咕床〔人工〕" in rendered


def test_legacy_file_reads_as_model_items_with_status() -> None:
    """整份没标签的旧笔记：条目都算模型写的，"（未兑现）"读成状态，还没有场次标签。"""
    doc = parse_notes("## 答应过的事\n- 我答应老王挖钻石（未兑现）\n## 老梗与口头禅\n- 嘴硬")
    promise = _find(doc, "我答应老王挖钻石")
    assert (promise.status, promise.manual, promise.seen) == (STATUS_OPEN, False, None)
    assert not _find(doc, "嘴硬").manual


def test_injection_view_hides_tags_shelved_uncertain_and_viewer_items() -> None:
    doc = _doc()
    text = render_for_injection(doc, games=("minecraft",))
    assert text.startswith("运营写在最前面的说明")
    assert "- 答应全场周末更新整活视频（已兑现）" in text
    assert "- 给苦力怕盖海景房（minecraft）" in text
    assert "## 运营自己的节\n- 周三不播" in text
    for hidden in ("〔", "答应老王下次玩恐怖游戏", "好像怕黑", "答应小明画头像"):
        assert hidden not in text
    assert "给苦力怕盖海景房" not in render_for_injection(doc, games=())  # 这个游戏没开
    assert "答应老王下次玩恐怖游戏（还没兑现，对 老王）" in render_for_injection(doc, include_viewer_items=True)
    assert person_lines(doc, 12) == ["答应过的事：答应老王下次玩恐怖游戏（还没兑现）"]
    assert person_lines(doc, 7) == []  # 搁置了的不给


def test_merge_listing_numbers_every_item_and_marks_manual() -> None:
    listing, numbered = render_for_merge(_doc())
    assert listing.splitlines()[0] == "[1] 〔答应过的事〕答应老王下次玩恐怖游戏（未兑现；对 老王（观众 #12））"
    assert "〔老梗与口头禅〕床名叫咕咕床（人工写的，只读）" in listing
    assert "〔搁置〕答应小明画头像（未兑现；对 小明（观众 #7）；原在 答应过的事）" in listing
    assert len(numbered) == len(_doc().items)


def test_manual_edit_marking_keeps_untouched_lines() -> None:
    """对照上一次合并写下的版本：改过正文或新加的行记成人工，没动的行保持模型条目。"""
    old = "## 老梗与口头禅\n- 嘴硬〔始于 #1 · 最近 #1〕\n- 怕黑〔始于 #1 · 最近 #1〕"
    new = "## 老梗与口头禅\n- 嘴硬〔始于 #1 · 最近 #1〕\n- 其实不怕黑〔始于 #1 · 最近 #1〕\n- 新加一条"
    text, marked = mark_manual_edits(old, new)
    doc = parse_notes(text)
    assert not _find(doc, "嘴硬").manual
    assert _find(doc, "其实不怕黑").manual and _find(doc, "新加一条").manual
    assert marked == ["其实不怕黑"]  # 没带标签的新行本来就算人工，不重复报告
    legacy_text, _marked = mark_manual_edits("## 老梗与口头禅\n- 嘴硬", "## 老梗与口头禅\n- 嘴硬\n- 手加的")
    legacy = parse_notes(legacy_text)
    assert not _find(legacy, "嘴硬").manual and _find(legacy, "手加的").manual


# ---------------------------------------------------------------------------
# 别处写进来的条目与三方合并
# ---------------------------------------------------------------------------

_QQ_LINE = "- 答应群友小明下次直播唱歌〔未兑现 · 来自 QQ群〕"


def test_source_tag_roundtrips_and_is_not_a_manual_edit() -> None:
    """QQ 群里记下的约定：读写往返不丢来处，注入与清单注明来处，对照基线时不记成人工。"""
    old = "## 答应过的事\n- 答应全场周末更新整活视频〔未兑现 · 始于 #2 · 最近 #5〕"
    new = old + "\n" + _QQ_LINE
    doc = parse_notes(new)
    promise = _find(doc, "答应群友小明下次直播唱歌")
    assert (promise.source, promise.status, promise.manual) == ("QQ群", STATUS_OPEN, False)
    assert _QQ_LINE in render_notes(doc)
    assert "- 答应群友小明下次直播唱歌（还没兑现，来自 QQ群）" in render_for_injection(doc)
    assert "〔答应过的事〕答应群友小明下次直播唱歌（未兑现；来自 QQ群）" in render_for_merge(doc)[0]

    text, marked = mark_manual_edits(old, new)
    assert marked == [] and not _find(parse_notes(text), "答应群友小明下次直播唱歌").manual


def test_three_way_takes_each_side_once_and_local_wins_conflicts() -> None:
    base = (
        "## 答应过的事\n"
        "- 答应挖钻石〔未兑现 · 始于 #1 · 最近 #1〕\n"
        "- 答应唱歌〔未兑现 · 始于 #1 · 最近 #1〕\n"
        "## 老梗与口头禅\n"
        "- 嘴硬〔始于 #1 · 最近 #1〕\n"
        "- 怕黑〔始于 #1 · 最近 #1〕"
    )
    # 本地：收播整理把挖钻石标成已兑现、删了怕黑
    local = base.replace("答应挖钻石〔未兑现", "答应挖钻石〔已兑现").replace("\n- 怕黑〔始于 #1 · 最近 #1〕", "")
    # 对方：也改了挖钻石（冲突）、删了唱歌、改了嘴硬的标签、改了怕黑（本地已删）、新加一条约定
    remote = (
        base.replace("答应挖钻石〔未兑现 · 始于 #1", "答应挖钻石〔放弃 · 始于 #1")
        .replace("\n- 答应唱歌〔未兑现 · 始于 #1 · 最近 #1〕", "")
        .replace("- 嘴硬〔始于 #1 · 最近 #1〕", "- 嘴硬〔游戏 minecraft · 始于 #1 · 最近 #1〕")
        .replace("- 怕黑〔始于 #1 · 最近 #1〕", "- 怕黑〔始于 #1 · 最近 #2〕")
        + "\n## 答应过的事\n" + _QQ_LINE
    )  # fmt: skip

    text, changes = merge_three_way(base, local, remote)
    doc = parse_notes(text)
    assert _find(doc, "答应挖钻石").status == "已兑现"  # 两边都改：按本地
    assert "答应唱歌" not in text  # 只有对方删：采纳
    assert _find(doc, "嘴硬").game == "minecraft"  # 只有对方改：采纳
    assert "怕黑" not in text  # 本地删、对方改：按本地
    assert text.splitlines()[:3] == [
        "## 答应过的事",
        "- 答应挖钻石〔已兑现 · 始于 #1 · 最近 #1〕",
        _QQ_LINE,  # 对方新加的排到所在节末尾
    ]
    assert changes == [
        "两边都改过，按本地：答应挖钻石",
        "采纳对方删除：答应唱歌",
        "采纳对方改动：嘴硬",
        "两边都改过，按本地（本地已删）：怕黑",
        "采纳对方新增：〔答应过的事〕答应群友小明下次直播唱歌",
    ]


def test_three_way_with_only_one_side_changed_is_that_side() -> None:
    base = "说明\n\n## 老梗与口头禅\n- 嘴硬〔始于 #1 · 最近 #1〕"
    local = base + "\n- 床名叫咕咕床〔人工〕"
    assert merge_three_way(base, local, base) == (local, [])
    remote = "新的说明\n\n## 老梗与口头禅\n- 嘴硬〔始于 #1 · 最近 #1〕"
    assert merge_three_way(base, base, remote)[0] == remote  # 说明文字只有对方改：采纳
    # 还没同步过（没有共同版本）：两边各自的条目都留下
    text, _changes = merge_three_way("", local, "## 答应过的事\n" + _QQ_LINE)
    assert "床名叫咕咕床" in text and "答应群友小明下次直播唱歌" in text
