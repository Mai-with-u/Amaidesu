"""OutlineLabel PIL 渲染测试：二值合成，像素仅含三种纯色。

真实中文字体覆盖字形渲染，受控字体度量单独覆盖混排分支。
"""

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
        """画布外层容器：pack 的上下内边距就是画布外的额外高度。"""

        def winfo_height(self) -> int:
            return 112

        def pack_info(self) -> dict:
            return {"pady": 6}  # 逻辑 5 × 缩放 1.25

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


def test_draw_text_renders_at_computed_content_height_not_canvas_height() -> None:
    """绘制高度取"算好的内容高度"，不取画布回读高度。

    画布比内容矮的瞬间（窗口已改高、Tk 尚未把画布尺寸交上来）如果按画布高度渲染，
    画出来的就是被裁掉上下半行的图；按算好的内容高度渲染则始终完整。
    """

    class RecordingCanvas:
        def __init__(self, width: int, height: int) -> None:
            self.width = width
            self.height = height
            self.images: list[tuple[int, int]] = []
            self.deleted = 0

        def delete(self, _tag: str) -> None:
            self.deleted += 1

        def configure(self, **_kwargs: Any) -> None:
            pass

        def winfo_width(self) -> int:
            return self.width

        def winfo_height(self) -> int:
            return self.height

        def create_image(self, x: int, y: int, image: Any) -> None:
            self.images.append((image.width(), image.height()))

    class FakePhoto:
        def __init__(self, img: Any) -> None:
            self._img = img

        def width(self) -> int:
            return self._img.width

        def height(self) -> int:
            return self._img.height

    label = _make_label(text="这是一段需要折行的字幕文本，用来验证绘制高度取算好的内容高度")
    required = label.required_height()
    content_height = required - label._canvas_chrome_height()
    assert content_height > 0

    # 画布还停留在很矮的旧尺寸
    canvas = RecordingCanvas(width=800, height=40)
    label.canvas = canvas
    with patch("src.modules.subtitle.backends.tk_gui_service.ImageTk.PhotoImage", FakePhoto):
        label._draw_text()

    assert canvas.images, "应当把渲染结果贴到画布上"
    drawn_w, drawn_h = canvas.images[0]
    assert drawn_h == content_height, f"应按内容高度 {content_height} 渲染，实际 {drawn_h}"
    assert drawn_w == 800


def test_canvas_configure_reflows_and_reredraws() -> None:
    """画布尺寸真的变了（窗口被拖宽）时要重新折行并重绘。"""

    class RecordingCanvas:
        def __init__(self, width: int, height: int) -> None:
            self.width = width
            self.height = height
            self.images: list[tuple[int, int]] = []

        def delete(self, _tag: str) -> None:
            pass

        def configure(self, **_kwargs: Any) -> None:
            pass

        def winfo_width(self) -> int:
            return self.width

        def winfo_height(self) -> int:
            return self.height

        def create_image(self, x: int, y: int, image: Any) -> None:
            self.images.append((image.width(), image.height()))

    class FakePhoto:
        def __init__(self, img: Any) -> None:
            self._img = img

        def width(self) -> int:
            return self._img.width

        def height(self) -> int:
            return self._img.height

    label = _make_label(text="折行宽度变化时要重新排版，否则行数与宽度都会对不上实际画布")
    label._canvas_height_px = None
    label.required_height()
    narrow_need = label._content_height_px

    label.canvas = RecordingCanvas(width=400, height=100)
    with patch("src.modules.subtitle.backends.tk_gui_service.ImageTk.PhotoImage", FakePhoto):
        label._on_canvas_configure(None)

    wide_need = label._content_height_px
    assert wide_need is not None and narrow_need is not None
    assert wide_need > narrow_need, "画布变窄后折行更多，内容高度应随之变大"


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


def test_draw_text_ignores_stale_content_height_cache() -> None:
    """渲染高度不得复用上一条文本的 ``_content_height_px`` 缓存。

    事故形态：超时巡检把长文本窗口缩回最小高度后，下一条短字幕换入时缓存
    仍是多行旧值（或反之），渲染高度与实际行数错位——首行 y 被算成负数，
    字形顶部被裁进图像外。绘制必须按当前文本现算折行。
    """

    class RecordingCanvas:
        def __init__(self, width: int, height: int) -> None:
            self.width = width
            self.height = height
            self.images: list[tuple[int, int]] = []

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

    label = _make_label(text="这是一条很长很长的字幕内容需要折成三行来展示窗口高度自适应" * 2)
    font = label._load_font()
    assert font is not None
    true_height = label._content_height_for(label._wrap_lines(font, 800))
    assert true_height > 100, "前提：该文本确为多行"

    # 模拟上一条单行字幕遗留的过期缓存
    label._content_height_px = label._content_height_for(label._wrap_lines(font, 800)[:0] or ["x"])
    label.canvas = RecordingCanvas(width=800, height=true_height)
    with patch("src.modules.subtitle.backends.tk_gui_service.ImageTk.PhotoImage", FakePhoto):
        label._draw_text()

    assert canvas_images_height(label) == true_height, "贴图高度应按当前文本现算，而非过期缓存"


def canvas_images_height(label: Any) -> int:
    canvas = label.canvas
    assert canvas.images, "应当把渲染结果贴到画布上"
    return canvas.images[-1][1]


def test_configure_text_empty_string_clears_and_none_keeps_text() -> None:
    """空串是显式清空，None 表示不动文本——两者不能混用同一默认值。"""

    class StubCanvas:
        def delete(self, _tag: str) -> None:
            pass

        def configure(self, **_kwargs: Any) -> None:
            pass

        def winfo_width(self) -> int:
            return 800

        def winfo_height(self) -> int:
            return 100

        def create_image(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    class FakePhoto:
        def __init__(self, img: Any) -> None:
            self._img = img

        def width(self) -> int:
            return self._img.width

        def height(self) -> int:
            return self._img.height

    label = _make_label(text="原有字幕")
    label.canvas = StubCanvas()

    with patch("src.modules.subtitle.backends.tk_gui_service.ImageTk.PhotoImage", FakePhoto):
        label.configure_text()
        assert label.display_text == "原有字幕", "只改样式时不得动文本"

        label.configure_text(text="")
        assert label.display_text == "", "空串必须真的清空文本"

        label.configure_text(text="新字幕")
        assert label.display_text == "新字幕"
