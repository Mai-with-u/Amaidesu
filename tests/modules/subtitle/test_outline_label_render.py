"""OutlineLabel PIL 渲染：二值合成（像素仅含背景/文字/描边三种纯色）、折行与 emoji 字体分派、窗口高度口径、绘制高度现算"""

import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PIL import ImageColor

from src.modules.subtitle.backends.tk_gui_service import OutlineLabel

BG = "#00FF00"
TEXT = "#FFFFFF"
OUTLINE = "#000000"
PHOTO = "src.modules.subtitle.backends.tk_gui_service.ImageTk.PhotoImage"
THREE_COLORS = {ImageColor.getrgb(c) for c in (BG, TEXT, OUTLINE)}


class FakeCanvas:
    """尺寸可设的画布桩；贴图时记录 (宽, 高)。"""

    def __init__(self, width: int = 800, height: int = 100) -> None:
        self.width, self.height = width, height
        self.images: list[tuple[int, int]] = []

    def pack(self, **kwargs: Any) -> None:
        pass

    def bind(self, event: str, callback: Any) -> None:
        pass

    def delete(self, _tag: str) -> None:
        pass

    def configure(self, **_kwargs: Any) -> None:
        pass

    def winfo_width(self) -> int:
        return self.width

    def winfo_height(self) -> int:
        return self.height

    def create_image(self, _x: int, _y: int, image: Any) -> None:
        self.images.append((image.width(), image.height()))


class FakePhoto:
    def __init__(self, img: Any) -> None:
        self._img = img

    def width(self) -> int:
        return self._img.width

    def height(self) -> int:
        return self._img.height


class FakeContainer:
    """画布外层容器：pack 的上下内边距就是画布外的额外高度。"""

    def winfo_height(self) -> int:
        return 112

    def pack_info(self) -> dict:
        return {"pady": 6}  # 逻辑 5 × 缩放 1.25


def _make_label(**overrides: Any) -> OutlineLabel:
    label = OutlineLabel.__new__(OutlineLabel)
    label.display_text = overrides.get("text", "测试字幕")
    label.text_color = TEXT
    label.outline_color = OUTLINE
    label.outline_width = 2
    label.outline_enabled = overrides.get("outline_enabled", True)
    label._background_color = BG
    # CI 显式提供中文字体；Windows 沿用真实字体解析路径
    label.font_obj = (os.environ.get("AMAIDESU_TEST_FONT", "Microsoft YaHei UI"), 28, "bold")
    label.font_size_px = 28
    label._font_px = round(28 * 4 / 3)
    label._photo = None
    label.canvas = FakeCanvas()
    label.container_frame = FakeContainer()
    label.logger = None
    return label


def _colors(img: Any) -> set:
    assert img is not None
    return {color for _, color in img.getcolors(maxcolors=1 << 20)}  # 取调色统计，避开已弃用的 getdata


def test_render_is_binary_three_colors_and_outline_can_be_disabled() -> None:
    colors = _colors(_make_label()._render_text(800, 100, BG))
    assert colors <= THREE_COLORS and ImageColor.getrgb(TEXT) in colors and ImageColor.getrgb(OUTLINE) in colors

    plain = _colors(_make_label(outline_enabled=False)._render_text(800, 100, BG))
    assert ImageColor.getrgb(OUTLINE) not in plain and ImageColor.getrgb(TEXT) in plain

    emoji = _colors(_make_label(text="完成✅🔥")._render_text(800, 100, BG))
    assert emoji <= THREE_COLORS and ImageColor.getrgb(TEXT) in emoji


def test_chinese_font_has_real_glyphs_not_missing_glyph_boxes() -> None:
    """非空图片仍可能全是缺字方块，必须验证中文墨迹与缺字字形不同。"""
    font = _make_label()._load_font()
    assert font is not None
    missing = bytes(font.getmask("\U0010ffff", mode="1"))
    glyphs = []
    for character in "测试字幕":
        ink = bytes(font.getmask(character, mode="1"))
        assert ink != missing and set(ink) <= {0, 255} and 255 in ink
        glyphs.append(ink)
    assert len(set(glyphs)) == 4


def test_emoji_font_renders_monochrome_ink() -> None:
    emoji_font = _make_label(text="✅")._load_emoji_font()
    if emoji_font is None:
        if sys.platform == "win32":
            pytest.fail("Windows 未加载到 Segoe UI Emoji 字体")
        pytest.skip("真实 Segoe UI Emoji 字体由 Windows CI 验证")
    bbox = emoji_font.getbbox("✅")
    assert bbox is not None and bbox[2] > bbox[0] and bbox[3] > bbox[1]
    ink = bytes(emoji_font.getmask("✅", mode="1"))
    assert set(ink) <= {0, 255} and 255 in ink and ink != bytes(emoji_font.getmask("\U0010ffff", mode="1"))


def test_split_emoji_runs_classifies_and_glues_joiners() -> None:
    label = _make_label()
    assert label._split_emoji_runs("你好🃏AB") == [("你好", False), ("🃏", True), ("AB", False)]
    assert label._split_emoji_runs("👍‍👎") == [("👍‍👎", True)]
    assert label._split_emoji_runs("纯文本") == [("纯文本", False)]


def test_wrap_lines_respects_width_including_emoji_width_and_font_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    label = _make_label(text="这是一段比较长的字幕文本，用于测试折行是否超出窗口宽度限制")
    font = label._load_font()
    assert font is not None
    lines = label._wrap_lines(font, 800)
    assert len(lines) >= 2 and all(font.getlength(line) <= 780 for line in lines)

    mixed = _make_label(text="好嘞✅🔥⛏️稍等看成品✅" * 5)
    emoji_font = mixed._load_emoji_font()
    mixed_lines = mixed._wrap_lines(font, 800)
    assert len(mixed_lines) >= 2
    for line in mixed_lines:
        width = sum(
            (emoji_font if (use_emoji and emoji_font) else font).getlength(seg)
            for seg, use_emoji in mixed._split_emoji_runs(line)
        )
        assert width <= 780

    # 受控宽度验证字体分派，不把缺字字形当作真实 emoji 验证
    controlled = _make_label(text="A✅B✅C")
    main_font = MagicMock()
    main_font.getlength.side_effect = lambda text: 10 * len(text)
    fake_emoji = MagicMock()
    fake_emoji.getlength.side_effect = lambda text: 60 * len(text)
    monkeypatch.setattr(controlled, "_load_emoji_font", lambda: fake_emoji)
    assert controlled._wrap_lines(main_font, 100) == ["A✅B", "✅C"]
    assert (main_font.getlength.call_count, fake_emoji.getlength.call_count) == (3, 2)


def test_required_height_is_window_height_scaled_by_lines_and_zero_when_unmeasurable() -> None:
    assert _make_label(text="").required_height() == 0
    unlaid = _make_label()
    unlaid.canvas = FakeCanvas(width=1, height=1)  # 画布还没布局
    assert unlaid.required_height() == 0

    single = _make_label(text="短字幕")
    canvas_only = single.required_height() - single._canvas_chrome_height()
    assert canvas_only > 0 and single.required_height() == canvas_only + 12  # 窗口口径含画布外上下内边距
    multi = _make_label(
        text="这是一段相当长的字幕文本，需要折成多行才能完整展示在画布宽度内，用来验证高度测量随行数增长"
    )
    assert multi.required_height() > single.required_height()


def test_draw_text_uses_current_text_height_not_canvas_or_stale_cache() -> None:
    """绘制高度取"按当前文本现算的内容高度"：不取画布回读高度（窗口已改高而画布尚未跟上时会裁掉半行），
    也不复用上一条文本遗留的 _content_height_px（缓存过期会让首行 y 变负、字形顶部被裁）。"""
    label = _make_label(text="这是一段需要折行的字幕文本，用来验证绘制高度取算好的内容高度" * 2)
    content_height = label.required_height() - label._canvas_chrome_height()
    assert content_height > 100  # 前提：该文本确为多行

    label._content_height_px = 30  # 上一条单行字幕遗留的过期缓存
    label.canvas = FakeCanvas(width=800, height=40)  # 画布还停留在很矮的旧尺寸
    with patch(PHOTO, FakePhoto):
        label._draw_text()
    assert label.canvas.images == [(800, content_height)]


def test_canvas_configure_reflows_when_width_really_changes() -> None:
    label = _make_label(text="折行宽度变化时要重新排版，否则行数与宽度都会对不上实际画布")
    label._canvas_height_px = None
    label.required_height()
    narrow_need = label._content_height_px

    label.canvas = FakeCanvas(width=400, height=100)
    with patch(PHOTO, FakePhoto):
        label._on_canvas_configure(None)
    assert label._content_height_px > narrow_need  # 画布变窄后折行更多，内容高度随之变大


def test_configure_text_distinguishes_empty_string_from_none() -> None:
    label = _make_label(text="原有字幕")
    with patch(PHOTO, FakePhoto):
        label.configure_text()
        assert label.display_text == "原有字幕"  # None（缺省）只改样式，不动文本
        label.configure_text(text="")
        assert label.display_text == ""  # 空串是显式清空
        label.configure_text(text="新字幕")
        assert label.display_text == "新字幕"
