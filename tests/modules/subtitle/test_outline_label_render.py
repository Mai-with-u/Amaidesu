"""OutlineLabel PIL 渲染测试：二值（无抗锯齿）合成，像素仅含三种纯色。

防止回归：Canvas 原生抗锯齿文字在 ``-transparentcolor`` 打孔后残留脏边
（如描边与色键背景混合的绿色中间色）。
"""

import os
from typing import Any

import pytest
from PIL import ImageColor

from src.modules.subtitle.backends.tk_gui_service import OutlineLabel

BG = "#00FF00"
TEXT = "#FFFFFF"
OUTLINE = "#000000"


def _make_label(**overrides: Any) -> OutlineLabel:
    class FakeCanvas:
        def pack(self, **kwargs: Any) -> None:
            pass

        def bind(self, event: str, callback: Any) -> None:
            pass

        def winfo_width(self) -> int:
            return 800

        def winfo_height(self) -> int:
            return 100

    label = OutlineLabel.__new__(OutlineLabel)
    label.display_text = overrides.get("text", "测试字幕")
    label.text_color = overrides.get("text_color", TEXT)
    label.outline_color = overrides.get("outline_color", OUTLINE)
    label.outline_width = overrides.get("outline_width", 2)
    label.outline_enabled = overrides.get("outline_enabled", True)
    label._background_color = overrides.get("background_color", BG)
    # CI 显式提供覆盖中文的字体；本地 Windows 沿用真实字体解析路径。
    # 不使用 Pillow 默认西文字体，避免中文缺字方块造成假通过。
    label.font_obj = (os.environ.get("AMAIDESU_TEST_FONT", "Microsoft YaHei UI"), 28, "bold")
    label.font_size_px = overrides.get("font_size_px", 28)
    label._font_px = round(label.font_size_px * 4 / 3)
    label._photo = None
    label.canvas = FakeCanvas()
    label.logger = None
    return label


def test_render_pixels_only_three_colors() -> None:
    img = _make_label()._render_text(800, 100, BG)
    assert img is not None
    colors = set(img.getdata())
    assert colors <= {ImageColorToTuple(BG), ImageColorToTuple(OUTLINE), ImageColorToTuple(TEXT)}, colors
    assert ImageColorToTuple(TEXT) in colors


def ImageColorToTuple(color_hex: str) -> tuple[int, int, int]:
    return ImageColor.getrgb(color_hex)


def test_render_no_outline_when_disabled() -> None:
    img = _make_label(outline_enabled=False)._render_text(800, 100, BG)
    assert img is not None
    colors = set(img.getdata())
    assert ImageColorToTuple(OUTLINE) not in colors
    assert ImageColorToTuple(TEXT) in colors


def test_wrap_lines_respects_width() -> None:
    label = _make_label(text="这是一段比较长的字幕文本，用于测试折行是否超出窗口宽度限制")
    font = label._load_font()
    assert font is not None
    lines = label._wrap_lines(font, 800)
    assert len(lines) >= 2
    for line in lines:
        assert font.getlength(line) <= 780


def test_required_height_empty_text_is_zero() -> None:
    assert _make_label(text="").required_height() == 0


def test_required_height_zero_before_canvas_layout() -> None:
    class NarrowCanvas:
        def winfo_width(self) -> int:
            return 1

    label = _make_label(text="测试字幕")
    label.canvas = NarrowCanvas()
    assert label.required_height() == 0


def test_required_height_grows_with_wrapped_lines() -> None:
    single = _make_label(text="短字幕")
    multi = _make_label(
        text="这是一段相当长的字幕文本，需要折成多行才能完整展示在画布宽度内，用来验证高度测量随行数增长"
    )
    single_h = single.required_height()
    multi_h = multi.required_height()
    assert single_h > 0
    assert multi_h > single_h


def test_split_emoji_runs_classifies_and_glues_joiners() -> None:
    label = _make_label(text="你好🃏AB")
    assert label._split_emoji_runs("你好🃏AB") == [("你好", False), ("🃏", True), ("AB", False)]
    assert label._split_emoji_runs("👍‍👎") == [("👍‍👎", True)]
    assert label._split_emoji_runs("纯文本") == [("纯文本", False)]


def test_emoji_font_renders_monochrome_ink() -> None:
    label = _make_label(text="✅")
    emoji_font = label._load_emoji_font()
    if emoji_font is None:
        pytest.skip("缺少 Segoe UI Emoji 字体（非 Windows 环境）")
    bbox = emoji_font.getbbox("✅")
    assert bbox is not None and bbox[2] > bbox[0] and bbox[3] > bbox[1]


def test_render_emoji_pixels_only_three_colors() -> None:
    img = _make_label(text="完成✅🔥")._render_text(800, 100, BG)
    assert img is not None
    colors = set(img.getdata())
    assert colors <= {ImageColorToTuple(BG), ImageColorToTuple(OUTLINE), ImageColorToTuple(TEXT)}, colors
    assert ImageColorToTuple(TEXT) in colors


def test_wrap_lines_counts_emoji_width() -> None:
    label = _make_label(text="好嘞✅🔥⛏️稍等看成品✅" * 5)
    font = label._load_font()
    assert font is not None
    lines = label._wrap_lines(font, 800)
    assert len(lines) >= 2
    emoji_font = label._load_emoji_font()
    for line in lines:
        width = 0.0
        for seg, use_emoji in label._split_emoji_runs(line):
            f = emoji_font if (use_emoji and emoji_font) else font
            width += f.getlength(seg)
        assert width <= 780
