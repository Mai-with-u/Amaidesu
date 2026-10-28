"""OutlineLabel - PIL 二值渲染的描边字幕标签

Canvas 原生文字带抗锯齿，与色键透明（``-transparentcolor`` 打孔）不兼容，
渲染引擎由 Pillow 以 1-bit mask 完成，详见类 docstring。
"""

from __future__ import annotations

import glob
import os
import tkinter as tk
from typing import Any, Callable, List, Optional, Tuple

from PIL import Image, ImageColor, ImageFilter, ImageFont, ImageTk

from src.modules.logging import ModuleLogger, get_logger

try:
    import customtkinter as ctk

    CTK_AVAILABLE = True
except ImportError:
    ctk = None
    CTK_AVAILABLE = False

# 字体解析等不依赖实例状态的辅助路径用模块级 logger：测试会经 __new__ 构造
# 未初始化实例，self.logger 不保证可用
logger = get_logger("OutlineLabel")

# 字幕标签在窗口里的内边距（逻辑单位，CustomTkinter 会按缩放放大）。画布只占
# 窗口的一部分，高度自适应要按这份内边距把窗口算得比内容高一点
_LABEL_PADX = 10
_LABEL_PADY = 5


class OutlineLabel:
    """PIL 二值渲染的描边标签。

    Canvas 原生文字带抗锯齿：描边像素与色键背景混合后不再匹配透明色，
    ``-transparentcolor`` 打孔会残留脏边。改由 Pillow 以 1-bit mask
    （无抗锯齿）渲染文字、膨胀 mask 生成描边，合成图仅含三种纯色像素——
    背景色/描边色/文字色，打孔后零残留。
    """

    def __init__(
        self,
        master: Any,
        text: str = "",
        font: Optional[Tuple[str, int, str]] = None,
        text_color: str = "white",
        outline_color: str = "black",
        outline_width: int = 2,
        outline_enabled: bool = True,
        background_color: str = "gray15",
        logger: Optional[ModuleLogger] = None,
        **kwargs: Any,
    ) -> None:
        if not CTK_AVAILABLE or ctk is None:
            raise ImportError("CustomTkinter not available")

        self.logger = logger

        kwargs.pop("outline_color", None)
        kwargs.pop("outline_width", None)
        kwargs.pop("outline_enabled", None)
        kwargs.pop("background_color", None)
        kwargs.pop("logger", None)

        safe_kwargs = {k: v for k, v in kwargs.items() if k not in ["bg_color", "text_color"]}
        safe_kwargs["fg_color"] = "transparent"
        safe_kwargs["bg_color"] = "transparent"
        safe_kwargs["border_width"] = 0

        self.container_frame = ctk.CTkFrame(master, **safe_kwargs)
        self.display_text = text
        self.text_color = text_color
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.outline_enabled = outline_enabled
        self.font_obj = font
        # font 元组的字号是磅（point）；PIL truetype 按像素（px）渲染，
        # 换算系数 96dpi/72dpi = 4/3，对齐 Tk canvas 时代的观感字号。
        self.font_size_px = font[1] if font else 28
        self._font_px = round(self.font_size_px * 4 / 3)
        self._photo: Any = None
        # 当前文本按画布宽度折行后的内容高度（物理像素）；None 表示需要按当前
        # 画布宽度重新折行。绘制按这个值渲染，不去回读画布高度
        self._content_height_px: Optional[int] = None
        # emoji 兜底字体缓存：None=未尝试 False=不可用 FreeTypeFont=已加载
        self._emoji_font_obj: Any = None
        self._background_color = background_color

        canvas_kwargs = {
            "highlightthickness": 0,
            "bd": 0,
            "relief": "flat",
            "bg": background_color,
        }
        self.canvas = tk.Canvas(self.container_frame, **canvas_kwargs)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.container_frame.after(1, self._draw_text)

    def pack(self, **kwargs: Any) -> None:
        self.container_frame.pack(**kwargs)

    def bind(self, event: str, callback: Callable[..., Any]) -> None:
        self.container_frame.bind(event, callback)

    def cget(self, option: str) -> Optional[Any]:
        try:
            return self.container_frame.cget(option)
        except Exception:
            self.logger.exception(f"获取 Canvas 选项 '{option}' 失败")
            return None

    def after(self, delay: int, callback: Callable[[], Any]) -> Any:
        return self.container_frame.after(delay, callback)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        """画布尺寸变化（用户拖动窗口宽度等）时重新排版并绘制。

        正常换字幕不依赖这里：绘制按算好的内容高度进行，与画布何时跟上无关。
        """
        self._content_height_px = None
        self._draw_text()

    def _draw_text(self) -> None:
        """按本次折行现算的内容高度渲染并居中贴到画布上。

        不用 ``winfo_height()`` 定渲染高度：那里读的是 Tk 已经算完的布局，几何请求
        尚未处理时拿到的是旧值，据此渲染会画出被裁的图（窗口已改、画布未跟上的那
        一瞬）。渲染高度由文本折行自己算，画布何时跟上都不影响画面正确性——画布比
        内容矮的短暂瞬间里，居中贴图也不会切到字形。

        内容高度每次现算而不读 ``_content_height_px`` 缓存：缓存可能还属于上一条
        文本（``configure_text`` 换文本不经过 ``required_height``，不会刷新它），
        复用过期值会让渲染高度与实际行数错位——高度小于行高需求时首行 y 被算成
        负数，字形顶部被裁进图像外。现算结果回写缓存，供窗口高度链路读取。
        """
        self.canvas.delete("all")
        self._photo = None
        if not self.display_text:
            return
        bg_color = self._background_color if self._background_color else "gray15"
        try:
            self.canvas.configure(bg=bg_color)
        except Exception:
            self.canvas.configure(bg="gray15")
        canvas_width = self.canvas.winfo_width()
        if canvas_width <= 1:
            return
        font = self._load_font()
        if font is None:
            return
        lines = self._wrap_lines(font, canvas_width)
        if not lines:
            return
        content_height = self._content_height_px = self._content_height_for(lines)
        img = self._render_text(canvas_width, content_height, bg_color)
        if img is None:
            return
        try:
            self._photo = ImageTk.PhotoImage(img)
            # anchor 决定贴图基准点：用画布当前尺寸定位，画布比图矮时也不会切字形
            self.canvas.create_image(
                self.canvas.winfo_width() // 2,
                self.canvas.winfo_height() // 2,
                image=self._photo,
            )
        except Exception:
            self.logger.exception("PIL 字幕渲染失败（ImageTk 不可用？）")

    def _line_height(self) -> int:
        return int(self._font_px * 1.35)

    def _content_height_for(self, lines: List[str]) -> int:
        """折行结果对应的内容高度：行数 × 行高 + 描边与贴边余量。"""
        pad = 8 + (2 * self.outline_width if self.outline_enabled else 0)
        return len(lines) * self._line_height() + pad

    def required_height(self) -> int:
        """当前文本在画布宽度内折行后所需的窗口高度（物理像素）。

        供窗口高度自适应使用：窗口比内容矮时，居中绘制的首尾行会落
        在窗口外被裁掉。返回值是窗口高度口径——画布只占窗口的一个子
        区域，空白高度按需留出，避免每行字幕都把窗口撑到比内容高一截。
        画布尚未完成布局（宽度 ≤ 1）、文本为空或字体不可用时返回 0，
        由调用方回退到窗口默认高度。
        """
        if not self.display_text:
            self._content_height_px = None
            return 0
        width = self.canvas.winfo_width()
        if width <= 1:
            return 0
        font = self._load_font()
        if font is None:
            return 0
        lines = self._wrap_lines(font, width)
        if not lines:
            return 0
        self._content_height_px = self._content_height_for(lines)
        return self._content_height_px + self._canvas_chrome_height()

    def _canvas_chrome_height(self) -> int:
        """画布之外的额外高度：``OutlineLabel`` 容器在窗口里的上下内边距。

        取自 pack 配置而不是回读几何：画布与容器的高度在布局过程中会短暂读到
        中间值（甚至读到 0），据此算出的"还差多少"会误判成空间不足。取不到
        pack 配置（容器已销毁等）时返回 0，由调用方的默认高度兜底。
        """
        try:
            info = self.container_frame.pack_info()
        except Exception as e:
            self.logger.debug(f"读取字幕容器 pack 配置失败: {e}")
            return 0
        pady = info.get("pady", 0)
        if isinstance(pady, (tuple, list)):
            return int(pady[0]) + int(pady[1])
        return 2 * int(pady)

    def _canvas_height_need(self, required_height_px: int) -> int:
        """窗口口径的所需高度换算成画布需要的高度。"""
        return required_height_px - self._canvas_chrome_height()

    def _render_text(self, width: int, height: int, bg_color: str) -> Optional[Image.Image]:
        """按色键背景合成文字+描边。

        文字 mask 用 1-bit（无抗锯齿）渲染，然后膨胀生成描边 mask——
        最终图像只有三种像素（背景色/描边色/文字色），供 ``-transparentcolor``
        打孔实现零残留透明（抗锯齿灰度像素会残留成脏边）。
        """
        font = self._load_font()
        if font is None:
            return None
        lines = self._wrap_lines(font, width)
        if not lines:
            return None
        try:
            bg_rgb = ImageColor.getrgb(bg_color)
            text_rgb = ImageColor.getrgb(self.text_color)
            outline_rgb = ImageColor.getrgb(self.outline_color)
        except Exception:
            return None

        img = Image.new("RGB", (width, height), bg_rgb)
        line_h = self._line_height()
        total_h = len(lines) * line_h
        y = (height - total_h) // 2
        emoji_font = self.load_emoji_font()
        main_ascent = font.getmetrics()[0]
        emoji_ascent = emoji_font.getmetrics()[0] if emoji_font else main_ascent
        for line in lines:
            # 每段按所属字体取 1-bit ink 掩码（无抗锯齿），段间按 advance
            # 排布、按 ascent 差对齐基线；掩码膨胀/合成与单字体时一致。
            segments: List[Tuple[Image.Image, int, int]] = []
            cursor = 0.0
            for seg_text, use_emoji in self._split_emoji_runs(line):
                f = emoji_font if (use_emoji and emoji_font) else font
                advance = f.getlength(seg_text)
                bbox = f.getbbox(seg_text)
                if bbox is None or bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
                    cursor += advance
                    continue
                # getmask(mode="1") 返回 ImagingCore（无抗锯齿，字节为
                # 0/255 二值）；转 Image("L") 后膨胀/合成，像素保持纯色
                # （无灰度中间值）。
                mask_core = f.getmask(seg_text, mode="1")
                mask_w, mask_h = mask_core.size
                if mask_w <= 0 or mask_h <= 0:
                    cursor += advance
                    continue
                mask_l = Image.frombytes("L", (mask_w, mask_h), bytes(mask_core))
                ascent_diff = (main_ascent - emoji_ascent) if (use_emoji and emoji_font) else 0
                segments.append((mask_l, round(cursor + bbox[0]), y + bbox[1] + ascent_diff))
                cursor += advance
            pen_x = (width - round(cursor)) // 2
            for mask_l, seg_x, seg_y in segments:
                self._paste_mask(img, mask_l, pen_x + seg_x, seg_y, text_rgb, outline_rgb)
            y += line_h
        return img

    def _paste_mask(
        self,
        img: Image.Image,
        mask_l: Image.Image,
        x: int,
        y: int,
        text_rgb: Tuple[int, int, int],
        outline_rgb: Tuple[int, int, int],
    ) -> None:
        """把一段 ink 掩码按"先描边后填充"合成到画布。"""
        if self.outline_enabled and self.outline_width > 0:
            outline_l = mask_l.filter(ImageFilter.MaxFilter(self.outline_width * 2 + 1))
            img.paste(Image.new("RGB", outline_l.size, outline_rgb), (x, y), outline_l)
        img.paste(Image.new("RGB", mask_l.size, text_rgb), (x, y), mask_l)

    def _load_font(self) -> Optional[ImageFont.FreeTypeFont]:
        """解析字体路径：先用字体族名（Pillow Windows 走注册表），失败后退化为
        ``C:\\Windows\\Fonts`` 匹配族名关键词或常见中文兜底字体。"""
        if not self.font_obj:
            return None
        try:
            return ImageFont.truetype(self.font_obj[0], self._font_px)
        except Exception as e:
            logger.debug(f"按字体族名加载失败，退化目录匹配: {e}")
        family_key = (self.font_obj[0] or "").lower().replace(" ", "")
        try:
            for path in glob.glob(r"C:\Windows\Fonts\*.tt[cf]"):
                name = os.path.basename(path).lower().replace(" ", "")
                if family_key in name:
                    return ImageFont.truetype(path, self._font_px)
        except Exception as e:
            logger.debug(f"按 Fonts 目录匹配失败，退化内置兜底字体: {e}")
        for fallback in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
            try:
                return ImageFont.truetype(fallback, self._font_px)
            except Exception as e:
                logger.debug(f"兜底字体 {fallback} 加载失败: {e}")
                continue
        return None

    def load_emoji_font(self) -> Optional[ImageFont.FreeTypeFont]:
        """加载 Windows 内置的 Segoe UI Emoji 作为 emoji 兜底字体。

        不开 embedded_color：COLR 字体按普通轮廓渲染，得到的 1-bit 掩码
        与主文本共用描边/纯色合成管线（彩色像素会破坏 ``-transparentcolor``
        的纯色打孔约定）。字体缺失（非 Windows 环境）时返回 None，emoji
        退回主字体按现状渲染（方块）。
        """
        cached = getattr(self, "_emoji_font_obj", None)
        if cached is not None:
            return cached or None
        font = None
        try:
            font = ImageFont.truetype("seguiemj.ttf", self._font_px)
        except Exception:
            for path in glob.glob(r"C:\Windows\Fonts\seguiemj*.tt[fc]"):
                try:
                    font = ImageFont.truetype(path, self._font_px)
                    break
                except Exception as e:
                    logger.debug(f"Segoe UI Emoji 备选路径加载失败: {path}: {e}")
                    continue
        self._emoji_font_obj = font if font is not None else False
        return font

    @staticmethod
    def _is_emoji_char(ch: str) -> bool:
        """字符是否属于 emoji 字体接管的 Unicode 区段。

        只收 Segoe UI Emoji 确定覆盖的区段（含变体选择符/零宽连接符/
        键帽组合符）；箭头、©®™ 等雅黑本身有字形的符号不接管，避免把
        正常文本字形换成 emoji 风格。
        """
        cp = ord(ch)
        return (
            0x1F000 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or 0x2B00 <= cp <= 0x2BFF
            or 0x231A <= cp <= 0x231B
            or 0x23E9 <= cp <= 0x23FA
            or cp in (0x200D, 0xFE0F, 0x20E3)
        )

    def _split_emoji_runs(self, line: str) -> List[Tuple[str, bool]]:
        """把一行拆成 ``(文本段, 是否走 emoji 字体)`` 序列。

        变体选择符/零宽连接符归入 emoji 段，让多码点 emoji 尽量成段；
        段内按原字符顺序交由字体排版（无复杂整形时多码点 emoji 可能逐
        码点平铺，属可接受降级）。
        """
        segments: List[Tuple[str, bool]] = []
        for ch in line:
            is_emoji = self._is_emoji_char(ch)
            if segments and segments[-1][1] == is_emoji:
                segments[-1] = (segments[-1][0] + ch, is_emoji)
            else:
                segments.append((ch, is_emoji))
        return segments

    def _wrap_lines(self, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
        text = (self.display_text or "").strip()
        if not text:
            return []
        emoji_font = self.load_emoji_font()
        lines: List[str] = []
        current = ""
        current_w = 0.0
        for ch in text:
            f = emoji_font if (emoji_font and self._is_emoji_char(ch)) else font
            ch_w = f.getlength(ch)
            if current and current_w + ch_w > max_width - 20:
                lines.append(current)
                current = ch
                current_w = ch_w
            else:
                current += ch
                current_w += ch_w
        if current:
            lines.append(current)
        return lines

    def configure_text(self, text: Optional[str] = None, **kwargs: Any) -> None:
        """更新文本与样式属性后重绘。

        ``text=None`` 表示本次只改样式、不动文本；空串是显式清空。二者
        不能共用默认值：历史上空串兼作"未传"，清空成了空操作——超时巡检
        "清空"后旧文本继续渲染、窗口却缩回最小高度，贴图超出画布被裁并
        长驻到下一条字幕。
        """
        if text is not None:
            self.display_text = text
        if "text_color" in kwargs:
            self.text_color = kwargs["text_color"]
        if "outline_color" in kwargs:
            self.outline_color = kwargs["outline_color"]
        if "outline_width" in kwargs:
            self.outline_width = kwargs["outline_width"]
        if "outline_enabled" in kwargs:
            self.outline_enabled = kwargs["outline_enabled"]
        if "font" in kwargs:
            self.font_obj = kwargs["font"]
        self._draw_text()
