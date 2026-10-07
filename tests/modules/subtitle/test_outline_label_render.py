"""OutlineLabel PIL 渲染测试：二值合成，像素仅含三种纯色。

真实中文字体覆盖字形渲染，受控字体度量单独覆盖混排分支。
"""

import os
import sys
from typing import Any
from unittest.mock import MagicMock

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

    class FakeContainer:
        """画布外层容器：比画布高出的部分是窗口的上下内边距。"""

        def winfo_height(self) -> int:
            return 112

    label = OutlineLabel.__new__(OutlineLabel)
    label.display_text = overrides.get("text", "测试字幕")
    label.text_color = overrides.get("text_color", TEXT)
    label.outline_color = overrides.get("outline_color", OUTLINE)
    label.outline_width = overrides.get("outline_width", 2)
    label.outline_enabled = overrides.get("outline_enabled", True)
    label._background_color = overrides.get("background_color", BG)
    # CI 显式提供中文字体；Windows 沿用真实字体解析路径。
    label.font_obj = (os.environ.get("AMAIDESU_TEST_FONT", "Microsoft YaHei UI"), 28, "bold")
    label.font_size_px = overrides.get("font_size_px", 28)
    label._font_px = round(label.font_size_px * 4 / 3)
    label._photo = None
    label.canvas = FakeCanvas()
    label.container_frame = FakeContainer()
    label.logger = None
    return label


def ImageColorToTuple(color_hex: str) -> tuple[int, int, int]:
    return ImageColor.getrgb(color_hex)


def test_render_pixels_only_three_colors() -> None:
    img = _make_label()._render_text(800, 100, BG)
    assert img is not None
    colors = set(img.getdata())
    assert colors <= {ImageColorToTuple(BG), ImageColorToTuple(OUTLINE), ImageColorToTuple(TEXT)}, colors
    assert ImageColorToTuple(TEXT) in colors
    assert ImageColorToTuple(OUTLINE) in colors


def test_render_no_outline_when_disabled() -> None:
    img = _make_label(outline_enabled=False)._render_text(800, 100, BG)
    assert img is not None
    colors = set(img.getdata())
    assert ImageColorToTuple(OUTLINE) not in colors
    assert ImageColorToTuple(TEXT) in colors


def test_chinese_font_has_real_glyphs_and_binary_masks() -> None:
    """非空图片仍可能全是缺字方块，必须验证中文墨迹与缺字字形不同。"""
    font = _make_label()._load_font()
    assert font is not None
    missing = bytes(font.getmask("\U0010ffff", mode="1"))
    glyphs = []
    for character in "测试字幕":
        ink = bytes(font.getmask(character, mode="1"))
        assert ink != missing
        assert set(ink) <= {0, 255}
        assert 255 in ink
        glyphs.append(ink)
    assert len(set(glyphs)) == 4


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

        def winfo_height(self) -> int:
            return 1

    label = _make_label(text="测试字幕")
    label.canvas = NarrowCanvas()
    assert label.required_height() == 0


def test_required_height_includes_container_chrome() -> None:
    """返回值是窗口高度口径：含画布外的上下内边距，避免每行都撑高窗口。"""
    label = _make_label(text="短字幕")
    canvas_only = label.required_height() - label._canvas_chrome_height()
    assert canvas_only > 0
    assert label.required_height() == canvas_only + 12


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
    """真实 Segoe Emoji 由 Windows CI 验证，不能以缺字方块代替。"""
    emoji_font = _make_label(text="✅")._load_emoji_font()
    if emoji_font is None:
        if sys.platform == "win32":
            pytest.fail("Windows 未加载到 Segoe UI Emoji 字体")
        pytest.skip("真实 Segoe UI Emoji 字体由 Windows CI 验证")
    bbox = emoji_font.getbbox("✅")
    assert bbox is not None and bbox[2] > bbox[0] and bbox[3] > bbox[1]
    ink = bytes(emoji_font.getmask("✅", mode="1"))
    assert set(ink) <= {0, 255}
    assert 255 in ink
    assert ink != bytes(emoji_font.getmask("\U0010ffff", mode="1"))


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
            selected = emoji_font if (use_emoji and emoji_font) else font
            width += selected.getlength(seg)
        assert width <= 780


def test_wrap_lines_uses_distinct_emoji_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """受控宽度验证字体分派，不把缺字字形当作真实 emoji 验证。"""
    label = _make_label(text="A✅B✅C")
    main_font = MagicMock()
    main_font.getlength.side_effect = lambda text: 10 * len(text)
    emoji_font = MagicMock()
    emoji_font.getlength.side_effect = lambda text: 60 * len(text)
    monkeypatch.setattr(label, "_load_emoji_font", lambda: emoji_font)

    assert label._wrap_lines(main_font, 100) == ["A✅B", "✅C"]
    assert main_font.getlength.call_count == 3
    assert emoji_font.getlength.call_count == 2
