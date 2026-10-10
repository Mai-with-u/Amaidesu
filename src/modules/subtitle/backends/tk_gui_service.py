"""
SubtitleGuiService - 字幕 GUI 长驻 Tk 线程服务

CustomTkinter 窗口、长驻线程、文本队列、自动隐藏、右键菜单、拖动。
该服务不在 Tool 系统内，由 main.py 在应用启动时直接实例化并调用
``start()``，字幕文本通过 ``push_subtitle(text)`` 入队、清空通过
``clear_subtitle()`` 入队（均线程安全，Tk 调用全部收敛在 GUI 线程内）。
"""

from __future__ import annotations

import contextlib
import ctypes
import glob
import os
import queue
import sys
import threading
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageColor, ImageFilter, ImageFont, ImageTk
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.logging import ModuleLogger, get_logger
from src.modules.time_utils import now_ms

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


class _MonitorRect(ctypes.Structure):
    """``MONITORINFO`` 里的显示器矩形（物理像素）。"""

    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MonitorInfo(ctypes.Structure):
    """``GetMonitorInfoW`` 的出参结构；``rcMonitor`` 为显示器整体矩形。

    取整体矩形而非 ``rcWork``：隐藏任务栏的区域仍属显示器，字幕窗停在那里
    依然可见可抓，按工作区夹取会平白多出一条够不着的带。
    """

    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _MonitorRect),
        ("rcWork", _MonitorRect),
        ("dwFlags", ctypes.c_ulong),
    ]


def _virtual_screen_bounds() -> Optional[Tuple[int, int, int, int]]:
    """Windows 虚拟桌面范围 (left, top, right, bottom)；取不到返回 None。

    口径是物理像素，与 Tk 的窗口位置参数（``geometry`` 的 ``+x+y``、事件里的
    ``x_root``）同口径——前提是进程为 per-monitor DPI 感知，见
    ``src.modules.windows_dpi``。
    """
    try:
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if width > 0 and height > 0:
            return left, top, left + width, top + height
    except Exception as e:
        # 非 Windows 或 ctypes 不可用时退回 Tk 报告的屏幕尺寸，不留异常路径
        logger.debug(f"读取虚拟桌面范围失败: {e}")
    return None


def _enumerate_monitors() -> Optional[List[Tuple[int, int, int, int]]]:
    """枚举显示器矩形 (left, top, right, bottom)，物理像素；失败返回 None。

    逐台枚举而不是只看虚拟桌面包围盒：显示器拼不成矩形时（L 形、上下错位），
    包围盒里存在没有任何显示器覆盖的空洞，把窗口夹取到包围盒内会把它放进
    空洞——窗口既看不见也点不到，而无边框窗没有任务栏入口，等于丢失。
    """
    if sys.platform != "win32":
        return None
    rects: List[Tuple[int, int, int, int]] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_MonitorRect),
        ctypes.c_double,
    )

    def _collect(hmonitor: Any, _hdc: Any, _rect: Any, _data: Any) -> int:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(hmonitor), ctypes.byref(info)):
            mon = info.rcMonitor
            if mon.right > mon.left and mon.bottom > mon.top:
                rects.append((mon.left, mon.top, mon.right, mon.bottom))
        return 1

    try:
        if not ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(_collect), 0):
            logger.debug("EnumDisplayMonitors 未枚举到显示器")
    except Exception as e:
        logger.debug(f"枚举显示器矩形失败: {e}")
        return None
    return rects or None


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
        emoji_font = self._load_emoji_font()
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

    def _load_emoji_font(self) -> Optional[ImageFont.FreeTypeFont]:
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
        emoji_font = self._load_emoji_font()
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


class SubtitleGuiService:
    """字幕 GUI 服务（长驻 Tk 线程）

    集成方式：
    - 字幕后端通过 ``service`` 依赖注入调用 ``push_subtitle(text)`` /
      ``clear_subtitle()``，两者都只入队，Tk 调用由 GUI 线程统一执行
    - ``start()`` / ``stop()`` 在组合根中管理生命周期
    - 字幕渲染走 GUI 服务，不走 ToolResult 反馈给 LLM
    """

    class ConfigSchema(BaseConfig):
        """字幕 GUI 配置"""

        type: str = "subtitle"

        window_width: int = Field(default=800, ge=100, le=3840, description="字幕窗口宽度")
        window_height: int = Field(default=100, ge=50, le=2160, description="字幕窗口高度")
        window_offset_y: int = Field(default=100, ge=0, le=2160, description="字幕窗口距离底部的偏移")
        font_family: str = Field(default="Microsoft YaHei UI", description="字体名称")
        font_size: int = Field(default=28, ge=10, le=100, description="字体大小")
        font_weight: str = Field(default="bold", description="字体粗细")
        text_color: str = Field(default="white", pattern=r"^[a-zA-Z#]+$", description="文字颜色")
        outline_enabled: bool = Field(default=True, description="是否启用描边")
        outline_color: str = Field(default="black", pattern=r"^[a-zA-Z#]+$", description="描边颜色")
        outline_width: int = Field(default=2, ge=0, le=10, description="描边宽度")
        background_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$", description="背景颜色")
        fade_delay_ms: int = Field(default=5000, ge=0, le=300000, description="淡出延迟（毫秒）")
        auto_hide: bool = Field(default=True, description="是否自动隐藏")
        window_alpha: float = Field(default=0.95, ge=0.0, le=1.0, description="窗口透明度")
        always_on_top: bool = Field(default=False, description="是否置顶")
        obs_friendly_mode: bool = Field(default=True, description="OBS 友好模式")
        window_title: str = Field(default="Amaidesu-Subtitle-OBS", description="窗口标题")
        use_chroma_key: bool = Field(default=False, description="是否使用色度键")
        chroma_key_color: str = Field(default="#00FF00", pattern=r"^#[0-9A-Fa-f]{6}$", description="色度键颜色")
        always_show_window: bool = Field(default=True, description="是否始终显示窗口")
        show_in_taskbar: bool = Field(default=True, description="是否在任务栏显示")
        window_minimizable: bool = Field(default=True, description="窗口是否可最小化")
        show_waiting_text: bool = Field(default=False, description="是否显示等待文字")

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.logger = get_logger("SubtitleGuiService")

        if not CTK_AVAILABLE:
            self.logger.error("CustomTkinter 库不可用，字幕 GUI 已禁用")
            self._enabled = False
            return

        self._enabled = True
        self.typed_config = self.ConfigSchema.from_dict(config)
        self.window_width = self.typed_config.window_width
        self.window_height = self.typed_config.window_height
        self.window_offset_y = self.typed_config.window_offset_y
        self.font_family = self.typed_config.font_family
        self.font_size = self.typed_config.font_size
        self.font_weight = self.typed_config.font_weight
        self.text_color = self.typed_config.text_color
        self.outline_enabled = self.typed_config.outline_enabled
        self.outline_color = self.typed_config.outline_color
        self.outline_width = self.typed_config.outline_width
        self.background_color = self.typed_config.background_color
        self.fade_delay_ms = self.typed_config.fade_delay_ms
        self.auto_hide = self.typed_config.auto_hide
        self.window_alpha = self.typed_config.window_alpha
        self.always_on_top = self.typed_config.always_on_top
        self.obs_friendly_mode = self.typed_config.obs_friendly_mode
        self.window_title = self.typed_config.window_title
        self.use_chroma_key = self.typed_config.use_chroma_key
        self.chroma_key_color = self.typed_config.chroma_key_color
        self.always_show_window = self.typed_config.always_show_window
        self.show_in_taskbar = self.typed_config.show_in_taskbar
        self.window_minimizable = self.typed_config.window_minimizable
        self.show_waiting_text = self.typed_config.show_waiting_text

        # 队列元素：字幕文本 str，或清空哨兵 None（clear_subtitle 入队，
        # GUI 线程消费到即执行清空）。Tk 方法跨线程调用会在与推流并发的
        # 时序下死锁 GUI 线程（窗口"未响应"、字幕停更），所以对 GUI 的
        # 全部操作都以指令形式入队、由 GUI 线程独占执行
        self.text_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self.gui_thread: Optional[threading.Thread] = None
        self.root: Any = None
        self.text_label: Any = None
        self.last_voice_time_ms = now_ms()
        self._gui_running = True
        self.is_visible = False
        self._started = False
        # 正在更新字幕文本：更新过程中会跑一次事件循环，自动隐藏巡检不得在此期间
        # 清空文本/缩回窗口
        self._updating_text = False
        # 窗口尺寸自持：geometry 的宽高参数按窗口缩放放大后交给 Tk，位置不
        # 缩放；winfo 系列返回物理像素，而 geometry 回读返回逻辑单位。位置
        # 锚定底边——字幕窗贴底展示，内容变高只向上扩展，底边必须钉在屏幕
        # 底部；顶边由"底边减目标高度"算出，但目标高度与实际高度不等时（窗口
        # 不缩、校正未收敛）回读高度里含未收敛的放大残差，直接拿它反推会让
        # 窗口每次调整都向下爬一截，所以只在高度确实被改到位时才挪顶边。
        self._target_width_px = 0
        self._window_bottom_px = 0
        self._default_window_height_px = 0
        # 底边距屏幕底部的偏移（物理像素），窗口映射后据此校正摆放位置
        self._bottom_offset_px = 0
        # 上一步期望的窗口高度（物理像素），供未到位时的延迟重试用
        self._last_target_height_px = 0
        # 已经为哪个目标高度重试过，避免"重试仍不到位→再重试"的自我重排
        self._height_retry_target = 0
        # 屏幕可用范围缓存 (left, top, right, bottom)，位置夹取与摆放都用它
        self._screen_bounds: Optional[Tuple[int, int, int, int]] = None
        # 显示器矩形缓存（物理像素）；None 表示尚未枚举，空列表在 _monitor_rects
        # 里统一按"枚举不到"处理
        self._monitors: Optional[List[Tuple[int, int, int, int]]] = None
        # 当前指针位置：拖动夹取按指针所在显示器选边界，窗口才不会在相邻显示器
        # 之间跳来跳去。非拖动期间等于窗口左上角，夹取退回按窗口位置判定
        self._pointer_pos: Tuple[int, int] = (0, 0)
        # 用户是否亲手挪过窗口：启动后的自动定位会重摆窗口，一旦用户动过就不再
        # 插手，否则拖动刚落下就被定位回调按旧锚点搬回去（看起来像被甩飞）
        self._user_moved_window = False
        # 拖动中的指针位置与窗口目标位置，见 _start_move/_on_move
        self._move_pointer: Tuple[int, int] = (0, 0)
        self._move_target: Tuple[int, int] = (0, 0)
        # 最近一次请求的高度（逻辑单位，0 表示无未收敛请求），用于在没有窗口
        # 缩放口径时按"请求 → 回读"实测换算出缩放系数
        self._last_requested_height_px = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        """启动 GUI 线程"""
        if not self._enabled:
            return
        if self._started:
            return
        if not self.gui_thread or not self.gui_thread.is_alive():
            self.gui_thread = threading.Thread(target=self._run_gui, daemon=True)
            self.gui_thread.start()
            self.logger.info("字幕 GUI 线程已启动")
            self._started = True

    def stop(self) -> None:
        """停止 GUI 线程"""
        self._gui_running = False
        if self.gui_thread and self.gui_thread.is_alive():
            self.logger.debug("等待字幕 GUI 线程结束...")
            self.gui_thread.join(timeout=3.0)
            if self.gui_thread.is_alive():
                self.logger.warning("字幕 GUI 线程未能及时结束")
        self._started = False

    def push_subtitle(self, text: str) -> None:
        """外部调用方入队字幕文本（线程安全）"""
        if not self._enabled:
            return
        if not text:
            return
        try:
            self.text_queue.put(text)
            # 文本一到就刷新空闲计时器：自动隐藏按"最后一条字幕"计时，而不是按
            # 上一次语音输入——TTS 引擎在位时字幕由播放事件直接推进来
            # （SubtitleService.show → 本方法），不经过任何语音时间戳，计时器会
            # 一直停留在很久以前，巡检就会在文本还显示着的时候判超时、把多行
            # 窗口缩回默认高度，字幕于是被裁成中间一行
            self.last_voice_time_ms = now_ms()
        except Exception as e:
            self.logger.exception(f"放入字幕队列时出错: {e}")

    def clear_subtitle(self) -> None:
        """外部调用方请求清空字幕（线程安全）。

        入队 ``None`` 哨兵，由 GUI 线程消费后执行 ``_clear_content``。
        与 ``push_subtitle`` 一样只入队不动 Tk——调用方线程（asyncio
        事件循环）直接调 Tk 方法会死锁 GUI 线程，禁止回退到跨线程直调。
        """
        if not self._enabled:
            return
        try:
            self.text_queue.put(None)
        except Exception as e:
            self.logger.exception(f"放入清空指令时出错: {e}")

    def _run_gui(self) -> None:
        """Tk GUI 主循环（长驻线程入口）"""
        if not CTK_AVAILABLE or ctk is None:
            return
        try:
            ctk.set_appearance_mode("dark")
            ctk.set_default_color_theme("blue")
            self.root = ctk.CTk()
            window_title = self.window_title if self.obs_friendly_mode else "Amaidesu Subtitle"
            self.root.title(window_title)
            self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

            # OBS 友好模式：无边框窗口（去掉标题栏）+ 色度键颜色整窗打孔透明，
            # -transparentcolor 已承担背景透明——若再叠加 -alpha（window_alpha）
            # 会导致整窗（含文字）不可见（Windows Tk 已知合成冲突），故强制 alpha=1.0。
            if self.obs_friendly_mode:
                self.root.overrideredirect(True)
                try:
                    self.root.attributes("-transparentcolor", self.chroma_key_color)
                except Exception:
                    self.logger.exception("设置透明背景失败（-transparentcolor 不可用）")
                self.root.attributes("-alpha", 1.0)
            else:
                self.root.attributes("-alpha", self.window_alpha)

            self.root.attributes("-topmost", self.always_on_top)

            if self.always_show_window and self.show_in_taskbar:
                if self.window_minimizable:
                    self.root.resizable(True, True)
                else:
                    self.root.resizable(False, False)
            else:
                try:
                    self.root.attributes("-toolwindow", True)
                except Exception:
                    self.logger.exception("设置工具窗口属性失败")

            self._place_window()
            self.root.after(200, lambda: self._measure_default_window_height(remaining_attempts=3))

            # 背景：OBS 友好模式或色度键开启时用色度键颜色作"透明打孔色"，
            # 否则用配置的背景色。
            effective_background = (
                self.chroma_key_color if (self.obs_friendly_mode or self.use_chroma_key) else self.background_color
            )
            try:
                self.root.configure(fg_color=effective_background)
            except Exception:
                self.logger.exception("设置背景颜色失败")

            font_tuple = (self.font_family, self.font_size, self.font_weight)
            self.text_label = OutlineLabel(
                self.root,
                text="",
                font=font_tuple,
                text_color=self.text_color,
                outline_color=self.outline_color,
                outline_width=self.outline_width,
                outline_enabled=self.outline_enabled,
                background_color=effective_background,
                logger=self.logger,
            )
            self.text_label.pack(expand=True, fill="both", padx=_LABEL_PADX, pady=_LABEL_PADY)

            def bind_drag_events(widget: Any) -> None:
                widget.bind("<Button-1>", self._start_move)
                widget.bind("<B1-Motion>", self._on_move)
                widget.bind("<Button-3>", self._show_context_menu)

            bind_drag_events(self.root)
            bind_drag_events(self.text_label)
            if hasattr(self.text_label, "canvas"):
                bind_drag_events(self.text_label.canvas)

            if self.always_show_window:
                self.root.deiconify()
                self.is_visible = True
                initial_text = ""
                if self.show_waiting_text:
                    initial_text = "字幕窗口已就绪 - 等待语音/弹幕输入..."
                if initial_text:
                    self.root.after(500, lambda: self._update_subtitle_display(initial_text))
            else:
                self.root.withdraw()
                self.is_visible = False

            self.root.after(100, self._check_queue)
            self.root.after(100, self._check_auto_hide)

            self.logger.info("Subtitle GUI 启动成功")
            self.root.mainloop()
        except Exception as e:
            self.logger.exception(f"运行 Subtitle GUI 时出错: {e}")
        finally:
            self.logger.info("Subtitle GUI 线程结束")
            if self.root:
                with contextlib.suppress(Exception):
                    self.root.quit()
            self._gui_running = False

    def _check_queue(self) -> None:
        if not self._gui_running:
            return
        try:
            while not self.text_queue.empty():
                text = self.text_queue.get_nowait()
                if text is None:
                    self._clear_content()
                else:
                    self._update_subtitle_display(text)
        except queue.Empty:
            pass
        except Exception as e:
            self.logger.warning(f"检查字幕队列时出错: {e}", exc=True)
        if self._gui_running and self.root:
            self.root.after(100, self._check_queue)

    def _logical_size(self, size_px: int) -> int:
        """把物理像素尺寸折算成 geometry 期望的逻辑单位。"""
        return max(1, round(size_px / self._size_scale()))

    def _size_scale(self) -> float:
        """geometry 尺寸相对 winfo 物理像素的缩放系数（CustomTkinter 窗口缩放）。

        优先用窗口自身的缩放口径（CTk 以 ``_get_window_scaling`` 暴露，私有
        属性名按类名改写、跨版本可能变），取不到时退回"请求 → 回读"实测比值，
        再取不到按 1.0 处理，由高度调整的收敛闭环下一轮纠正。
        """
        if not self.root:
            return 1.0
        try:
            scale = self.root._get_window_scaling()
        except Exception:
            scale = None
        if isinstance(scale, (int, float)) and scale > 0:
            return float(scale)
        if self._last_requested_height_px > 1:
            try:
                requested = self._parse_requested_size(self.root.geometry())[1]
            except Exception:
                requested = None
            if requested:
                measured = self.root.winfo_height() / requested
                if measured > 0:
                    return measured
        return 1.0

    @staticmethod
    def _parse_requested_size(geometry_string: str) -> Tuple[int, int]:
        """解析 geometry 串中的宽高（逻辑单位）；无尺寸段时返回 (0, 0)。"""
        size = geometry_string.partition("+")[0]
        width_str, _, height_str = size.partition("x")
        width = int(width_str) if width_str.isdigit() else 0
        height = int(height_str) if height_str.isdigit() else 0
        return width, height

    def _sync_window_metrics(self) -> None:
        """以当前窗口几何重设尺寸基准与底边锚点。

        用户拖动后调用：之后高度调整只改高度、不动底边，锚点取当前底边即可。
        锚点只在摆放/拖动时更新，不随高度调整回写——请求的逻辑高度换算回物理
        像素必然带取整余数（实测高度可能比目标差 1 像素），把回读到的底边当作
        新锚点，这点余数就会每次调整累加一次，窗口会持续单向漂移。
        """
        if not self.root:
            return
        self._target_width_px = max(self.root.winfo_width(), 1)
        self._window_bottom_px = self.root.winfo_y() + self.root.winfo_height()
        # 非拖动期间指针基准跟随窗口：夹取按指针位置选显示器，若留着上一次拖动
        # 的旧坐标，窗口会被按早已离开的那台显示器夹取
        self._pointer_pos = (self.root.winfo_x(), self.root.winfo_y())

    def _place_window(self) -> None:
        """按配置摆放窗口：水平居中、底边距屏幕底部 ``window_offset_y``。

        位置是物理像素（屏幕尺寸与 geometry 的位置参数同口径），尺寸交给
        CustomTkinter 缩放；配置的逻辑高度与缩放后的物理高度不等，先按逻辑
        高度估一个顶边，窗口映射后由 ``_measure_default_window_height`` 按实测
        高度把底边校正到位。
        """
        if not self.root:
            return
        screen_height = self.root.winfo_screenheight()
        self._bottom_offset_px = self.window_offset_y
        self._window_bottom_px = screen_height - self.window_offset_y
        self._target_width_px = self.window_width
        self.root.geometry(
            self._size_spec(self.window_width, self.window_height, self._window_bottom_px - self.window_height)
        )
        self.root.update_idletasks()
        self._last_requested_height_px = self._logical_size(self.window_height)
        self._pointer_pos = (self.root.winfo_x(), self.root.winfo_y())

    def _size_spec(self, width_px: int, height_px: int, y_px: int) -> str:
        """拼 geometry 尺寸串：宽高按物理像素传入并折算成逻辑单位，位置原样。"""
        return f"{self._logical_size(width_px)}x{self._logical_size(height_px)}+{self._window_x()}+{y_px}"

    def _window_x(self) -> int:
        """水平居中所需的位置，由屏幕宽度与物理窗口宽度算出。"""
        if not self.root:
            return 0
        return max((self.root.winfo_screenwidth() - self.window_width) // 2, 0)

    def _measure_default_window_height(self, remaining_attempts: int = 1) -> None:
        """窗口映射后按实测高度定位：记高度下限并把底边校正到配置偏移处。

        配置的逻辑高度与实测物理高度在 DPI 缩放下不等，而且窗口管理器响应
        geometry 请求有延迟：回读高度已经生效、位置还是旧值时按实测值定位会
        把底边算错，所以没把握时留一次重试机会。用户已经亲手挪过窗口时只记
        高度下限，不再重摆位置——用户的位置优先。
        """
        if not self.root or not self._gui_running:
            return
        measured = self.root.winfo_height()
        if measured <= 1:
            # 窗口尚未映射（回读仍是 Tk 初始尺寸），高度与位置都不作数
            if remaining_attempts > 1:
                self.root.after(100, lambda: self._measure_default_window_height(remaining_attempts - 1))
            return
        self._default_window_height_px = measured
        self._target_width_px = max(self.root.winfo_width(), 1)
        if self._user_moved_window:
            self.logger.debug(f"用户已挪动字幕窗口，跳过自动定位（实测高度 {measured}）")
            return
        self._window_bottom_px = self.root.winfo_screenheight() - self._bottom_offset_px
        # 顶边过位置夹取：配置偏移大于窗口高度时底边会越过显示器下沿，不夹取
        # 启动摆放就把窗口放进屏外
        desired_top = self._clamp_position(self._window_x(), self._window_bottom_px - measured)[1]
        self.root.geometry(self._size_spec(self.window_width, self.window_height, desired_top))
        self.root.update_idletasks()
        self.logger.info(
            f"字幕窗口已摆放: 屏幕 {self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}"
            f" 位置 ({self.root.winfo_x()},{self.root.winfo_y()})"
            f" 尺寸 {self.root.winfo_width()}x{self.root.winfo_height()}"
        )
        if remaining_attempts > 1 and self.root.winfo_y() != desired_top:
            # 位置还没落到目标处，说明上一步请求仍在途中，下一轮按新回读校准
            self.root.after(100, lambda: self._measure_default_window_height(remaining_attempts - 1))
        self.logger.info(
            f"窗口定位结果: 实测高度 {measured} 目标顶边 {desired_top}"
            f" 实际位置 y={self.root.winfo_y()} 高度 {self.root.winfo_height()}"
        )

    def _apply_window_height(self, target_height_px: int) -> None:
        """把窗口高度调到 ``target_height_px``（物理像素），底边锚定不动。

        字幕窗口贴底展示，内容变高时只向上扩展；目标高度低于窗口默认高度时
        取默认高度（清空/短文本回落到常规条幅尺寸）。geometry 的宽高会被
        CustomTkinter 按窗口缩放放大，所以尺寸先在物理像素空间算好再折算成
        逻辑单位请求，缩放系数用"请求 → 回读实测 → 校正"收敛。

        每次调整都从窗口实际底边重新取锚点（``_anchor_bottom_px``，并收回所在
        显示器内），而不是沿用上一轮记下的值：锚点越界时缩短会把窗口顶出屏幕。
        顶边由锚点推算后按显示器上下沿夹取——锚点贴在显示器下沿时内容变高会把
        顶边算到屏外，不夹取窗口就有半截露在外面；窗口高于显示器时以顶边可见
        优先。夹取只用显示器边界与目标高度这类绝对量，不与上一轮回读位置叠加，
        因此不会像"按回读高度反推"那样逐次漂移。
        """
        if not self.root or not self._gui_running:
            return
        if self._default_window_height_px <= 0:
            # 窗口映射前（或 _measure_default_window_height 未及执行）先按配置
            # 逻辑高度兜底，映射后由实测值覆盖
            self._default_window_height_px = max(self.window_height, 1)
        if self._target_width_px <= 0:
            self._measure_default_window_height()
        # 锚点按窗口实际底边取，并收回所在显示器内：锚点越界时缩短会把窗口推出屏幕
        self._window_bottom_px = self._anchor_bottom_px()
        target_h = max(target_height_px, self._default_window_height_px)
        target_w = self._target_width_px
        top = self._clamp_top_to_monitor(self._window_bottom_px - target_h, target_h)
        if (
            abs(self.root.winfo_height() - target_h) <= 2
            and abs(self.root.winfo_width() - target_w) <= 2
            and self.root.winfo_y() == top
        ):
            # 尺寸与位置都已是目标值：不发无意义的请求（空字幕时每 100ms 巡检一次）
            return
        h_scale = self._size_scale()
        w_scale = h_scale
        for _ in range(3):
            w_req = max(1, round(target_w / w_scale))
            h_req = max(1, round(target_h / h_scale))
            self._last_requested_height_px = h_req
            self.root.geometry(f"{w_req}x{h_req}+{self.root.winfo_x()}+{top}")
            self.root.update_idletasks()
            actual_h = self.root.winfo_height()
            actual_w = self.root.winfo_width()
            if not abs(actual_w - target_w) <= 2:
                w_scale = actual_w / max(w_req, 1)
            if not abs(actual_h - target_h) <= 2:
                h_scale = actual_h / max(h_req, 1)
            # 顶边按"本次实际高度"重算：请求高度经逻辑单位取整后与实测高度差 1 像素
            # 是常态，顶边若一直按目标高度算，底边就每次都随这点差值挪一像素。
            # 高度没到位时窗口可能只是"缩不下去"，此时取实测高度与目标高度的较小
            # 值，只把窗口确实长高的那一侧计入位置
            top = self._clamp_top_to_monitor(self._window_bottom_px - min(actual_h, target_h), target_h)
            if abs(actual_h - target_h) <= 2 and abs(actual_w - target_w) <= 2 and self.root.winfo_y() == top:
                break
        self._last_requested_height_px = 0
        self._target_width_px = self.root.winfo_width()
        self._put_window_back_in_monitor()
        self.logger.info(
            f"窗口高度调整: 目标 {target_h} 实测 {self.root.winfo_height()}"
            f" 顶边 {self.root.winfo_y()} 比例 {h_scale:.3f}"
        )
        if not (abs(self.root.winfo_height() - target_h) <= 2 and abs(self.root.winfo_width() - target_w) <= 2):
            # 三次请求都没到位：窗口管理器可能把某次请求丢了，隔一拍再校正一次，
            # 否则窗口会停在中间高度，多行字幕只露出中间一行半
            self._last_target_height_px = target_h
            self.root.after(120, self._retry_window_height)

    def _put_window_back_in_monitor(self) -> None:
        """尺寸落定后按实际回读尺寸再夹一次位置，越界才补发一次 geometry。

        尺寸调整与位置是同一条 geometry 请求里的两个参数，但窗口管理器可能只
        认了尺寸、顺手把窗口按新尺寸重排（跨显示器时还会按目标屏幕 DPI 放大），
        于是"夹取时按旧尺寸算出的位置"配上"新尺寸"就探出显示器外。回读一次实际
        尺寸再夹，越界时补发位置请求；已经是目标位置时什么都不做，不引入漂移。
        """
        if not self.root or not self._gui_running:
            return
        rect = self._monitor_under_window()
        if rect is None:
            return
        x, y = self._clamp_into(rect, self.root.winfo_x(), self.root.winfo_y())
        if (x, y) == (self.root.winfo_x(), self.root.winfo_y()):
            return
        self.root.geometry(f"+{x}+{y}")
        self.root.update_idletasks()
        self._window_bottom_px = self.root.winfo_y() + self.root.winfo_height()
        self.logger.debug(f"窗口尺寸落定后按实际尺寸回夹位置: ({x},{y})")

    def _configure_then_fit(self, text: str) -> int:
        """换文本：算高度、改窗口、按算好的高度绘制一次。

        渲染高度由文本折行自己算（``required_height`` 顺手记下内容高度），窗口高度
        按同一个值设；两者一致就不存在"画布还没跟上"的问题，布局何时落定都不影响
        画面正确性，因此不需要延迟重绘或轮询兜底。

        改窗口后跑一次 ``update()`` 只是让画面尽快稳定，不是正确性依赖——即使跳过，
        贴图也已是按正确尺寸渲染的。这里用 ``update()`` 而非 ``update_idletasks()``，
        后者不做几何重算。
        """
        self.text_label.display_text = text
        required = self.text_label.required_height()
        self._apply_window_height(required)
        self.root.update()
        self.text_label._draw_text()
        return required

    def _log_subtitle_fit(self, required_height_px: int) -> None:
        """把本次文本的渲染空间记到调试日志，供排查截断时对照。"""
        if not self.root or not self.text_label:
            return
        canvas_h = self.text_label.canvas.winfo_height()
        canvas_need = self.text_label._canvas_height_need(required_height_px)
        self.logger.debug(f"字幕渲染空间: 窗口高 {self.root.winfo_height()} 画布高 {canvas_h} 画布需要 {canvas_need}")

    def _retry_window_height(self) -> None:
        """校正上一步没到位的窗口高度（延迟一拍重试一次）。"""
        if not self.root or not self._gui_running:
            return
        if abs(self.root.winfo_height() - self._last_target_height_px) <= 2:
            return
        if self._height_retry_target == self._last_target_height_px:
            # 同一目标已经重试过且仍不到位，不再自我重排（窗口管理器或 DPI 口径
            # 决定的偏差，重发请求也改不动），只记录事实
            self.logger.warning(
                f"字幕窗口高度无法到位: 实测 {self.root.winfo_height()}，目标 {self._last_target_height_px}"
            )
            return
        self._height_retry_target = self._last_target_height_px
        self.logger.warning(
            f"字幕窗口高度未到位（实测 {self.root.winfo_height()}，目标 {self._last_target_height_px}），重试一次"
        )
        self._apply_window_height(self._last_target_height_px)

    def _update_subtitle_display(self, text: str) -> None:
        """显示一条字幕（GUI 线程内调用）。

        空闲计时器在这里统一刷新：自动隐藏应当从"这条字幕被显示"开始计时，而不是
        从上次语音输入开始。TTS 引擎在位时字幕由播放事件推进来
        （SubtitleService.show → push_subtitle → 队列 → 本方法），与语音时间戳没有
        关系，不刷新就会让巡检在文本还显示着的时候判超时，把多行窗口缩回默认高度。
        """
        if not self.text_label or not self._gui_running:
            return
        try:
            if text:
                if not self.always_show_window and not self.is_visible and self.root:
                    self.root.deiconify()
                    self.is_visible = True
                self.last_voice_time_ms = now_ms()
                self._updating_text = True
                try:
                    self._configure_then_fit(text)
                    self._log_subtitle_fit(self.text_label.required_height())
                finally:
                    self._updating_text = False
                self.logger.debug(f"已更新字幕: {text[:30]}...")
            elif not self.always_show_window and self.is_visible and self.auto_hide and self.root:
                self.root.withdraw()
                self.is_visible = False
        except Exception as e:
            self.logger.warning(f"更新字幕显示时出错: {e}", exc=True)

    def _check_auto_hide(self) -> None:
        if not self._gui_running:
            return
        try:
            if (
                self.auto_hide
                and self.is_visible
                and self.root
                and not self._updating_text
                and self.fade_delay_ms > 0
                and now_ms() - self.last_voice_time_ms > self.fade_delay_ms
            ):
                if self.always_show_window:
                    if self.text_label:
                        if self.show_waiting_text:
                            waiting_text = "等待语音/弹幕输入..."
                        else:
                            waiting_text = ""
                        if self.text_label.display_text:
                            # 已经清空过就不用重复清空/重算高度：空字幕期间这个
                            # 巡检每 100ms 跑一次，重复下发会刷爆日志与布局
                            self.text_label.configure_text(text=waiting_text)
                        self._apply_window_height(0)
                else:
                    self.logger.debug("自动隐藏字幕窗口")
                    self.root.withdraw()
                    self.is_visible = False
                    if self.text_label:
                        self.text_label.configure_text(text="")
                        self._apply_window_height(0)
            if self._gui_running and self.root:
                self.root.after(100, self._check_auto_hide)
        except Exception as e:
            self.logger.warning(f"检查自动隐藏时出错: {e}", exc=True)
            if self._gui_running and self.root:
                self.root.after(100, self._check_auto_hide)

    def _on_closing(self) -> None:
        self.logger.info("Subtitle 窗口关闭请求...")
        self._gui_running = False
        if self.root:
            try:
                self.root.destroy()
            except Exception as e:
                self.logger.warning(f"销毁 subtitle 窗口时出错: {e}", exc=True)
        self.root = None

    def _window_bounds(self) -> Tuple[int, int, int, int]:
        """窗口可停留的屏幕范围 (left, top, right, bottom)，物理像素。

        虚拟桌面外围范围：Windows 允许窗口停在物理屏幕外的区域，按主屏尺寸
        夹取会把窗口硬拽回主屏、副屏用不了。窗口对象上的同名属性优先——测试
        用它注入屏幕尺寸，免得断言受运行机器的实际桌面影响。
        """
        injected = getattr(self.root, "_screen_bounds", None)
        if injected is not None:
            return injected
        if self._screen_bounds is None:
            self._screen_bounds = _virtual_screen_bounds() or (
                0,
                0,
                self.root.winfo_screenwidth(),
                self.root.winfo_screenheight(),
            )
        return self._screen_bounds

    def _clamp_top_to_monitor(self, top: int, height_px: int) -> int:
        """把顶边夹到当前显示器的上下沿内（横向不动，由拖动夹取负责）。

        高度自适应只改高度与顶边，横向位置保持原样；这里也只回收纵向越界——
        锚点贴在显示器下沿时内容变高会把顶边算到屏外，窗口高于显示器时则优先
        保住顶边可见，否则用户再也点不到它。
        """
        rect = self._monitor_under_window()
        if rect is None:
            return top
        _, mon_top, _, mon_bottom = rect
        if height_px > mon_bottom - mon_top:
            return mon_top
        return min(max(top, mon_top), mon_bottom - height_px)

    def _monitor_under_window(self) -> Optional[Tuple[int, int, int, int]]:
        """窗口所在（或离窗口最近）的显示器矩形；无显示器信息时返回 None。"""
        if not self.root:
            return None
        rects = self._monitor_rects()
        if not rects:
            return None
        x, y = self.root.winfo_x(), self.root.winfo_y()
        for rect in rects:
            left, top, right, bottom = rect
            if left <= x < right and top <= y < bottom:
                return rect
        return self._nearest_monitor(x, y)

    def _anchor_bottom_px(self) -> int:
        """本次高度调整要用的底边锚点：窗口实际底边，且不越过所在显示器下沿。

        锚点越界只可能来自显示器布局变化（副屏被拔掉、分辨率改了）或用户把窗口
        停在了屏外。此时必须先把锚点收回显示器内再改高度：字幕变少时窗口是从
        底边向上缩短，顶边（唯一还看得见的部分）跟着往下走，锚点留在屏外就等于
        把窗口彻底推出屏幕——用户看到的就是"字幕一变少窗口就不见了"。
        """
        current_bottom = self.root.winfo_y() + self.root.winfo_height()
        rect = self._monitor_under_window()
        if rect is None:
            return current_bottom
        _, _, _, mon_bottom = rect
        return min(current_bottom, mon_bottom)

    def _monitor_rects(self) -> List[Tuple[int, int, int, int]]:
        """各显示器的矩形（物理像素），枚举不到时退化成单个虚拟桌面矩形。

        窗口要停在某一台显示器上，只有虚拟桌面包围盒不够用：多显示器拼成
        L 形时包围盒含没有显示器的空洞，夹取到洞里窗口就彻底消失。窗口对象
        上的同名属性优先——测试用它注入显示器布局；真实窗口上没有该属性。
        """
        injected = getattr(self.root, "monitors", None)
        if injected:
            return list(injected)
        if self._monitors is None:
            self._monitors = _enumerate_monitors()
        if self._monitors:
            return self._monitors
        return [self._window_bounds()]

    def _primary_monitor_rect(self) -> Tuple[int, int, int, int]:
        """主显示器矩形（虚拟桌面原点所在的那台）；取不到时退回虚拟桌面范围。"""
        rects = self._monitor_rects()
        for rect in rects:
            left, top, right, bottom = rect
            if left <= 0 < right and top <= 0 < bottom:
                return rect
        return self._window_bounds()

    @staticmethod
    def _keep_visible_px(size_px: int) -> int:
        """窗口在某个方向上至少要留在显示器内的长度。

        取窗口在该方向尺寸的一半：窗口被拖到显示器边缘时仍有一半可见，标题与
        字幕都还能看见、也点得到；窗口比显示器还大时这条下限按显示器尺寸收窄，
        保证窗口不会因为"要求可见一半"而被完全推出屏外。
        """
        return max(size_px // 2, 40)

    def _clamp_position(self, x: int, y: int) -> Tuple[int, int]:
        """把窗口位置夹到指针所在显示器内（横向不出屏、底边不出下沿）。

        窗口一旦被拖到显示器外就再也抓不回来，而字幕窗是无边框透明窗、没有
        任务栏入口；位置改动一律过这道闸。夹取按指针位置选显示器，而不是落点
        所在的那台：拖到显示器边缘时落点可能已经越过边界（甚至落进多显示器
        拼不成矩形时的空洞），按落点选会让窗口在相邻显示器间跳来跳去。指针与
        落点都没落在任何显示器上时，夹到离落点最近的那台——按虚拟桌面包围盒
        夹取可能把窗口放进空洞，那和无显示器覆盖是一回事。
        """
        pointer_x, pointer_y = self._pointer_pos
        # 优先指针所在的那台，其次落点所在的那台
        for px, py in ((pointer_x, pointer_y), (x, y)):
            for rect in self._monitor_rects():
                mon_left, mon_top, mon_right, mon_bottom = rect
                if mon_left <= px < mon_right and mon_top <= py < mon_bottom:
                    return self._clamp_into(rect, x, y)
        return self._clamp_into(self._nearest_monitor(x, y), x, y)

    def _nearest_monitor(self, x: int, y: int) -> Tuple[int, int, int, int]:
        """离给定点最近的显示器矩形：到各显示器矩形的距离取最小。

        点到矩形的距离为两轴方向"超出矩形范围"的分量平方和；点在矩形内时
        距离为 0（正常情况下走不到这里，作为兜底语义保留）。
        """
        best_rect: Optional[Tuple[int, int, int, int]] = None
        best_distance = 0
        for rect in self._monitor_rects():
            left, top, right, bottom = rect
            dx = max(left - x, 0, x - right)
            dy = max(top - y, 0, y - bottom)
            distance = dx * dx + dy * dy
            if best_rect is None or distance < best_distance:
                best_rect, best_distance = rect, distance
        return best_rect if best_rect is not None else self._window_bounds()

    def _clamp_into(
        self,
        rect: Tuple[int, int, int, int],
        x: int,
        y: int,
    ) -> Tuple[int, int]:
        """把窗口位置夹进单台显示器：横向整体可见、纵向底边不出下沿。

        位置是窗口左上角，右探出的部分按**窗口实际尺寸**回收：跨显示器拖动时
        窗口管理器会按目标屏幕的 DPI 重排窗口，尺寸与拖动开始时不同，按配置
        尺寸夹取会留下"程序以为在屏内、实际探出屏外"的偏差。底边同样不越过
        下沿；顶边允许探出上沿——内容变高时窗口只向上长，把整条窗口往下拽会
        让字幕条离开用户放的位置。窗口高/宽大于显示器时（装不下）改为保住
        顶边与至少一半可见，否则用户再也点不到它。
        """
        mon_left, mon_top, mon_right, mon_bottom = rect
        mon_width = mon_right - mon_left
        mon_height = mon_bottom - mon_top
        win_w = max(self._target_width_px, 1)
        win_h = max(self.root.winfo_height(), 1)
        keep_x = self._keep_visible_px(win_w)
        keep_y = self._keep_visible_px(win_h)
        # 横向：放得下就整体可见；放不下则至少留 keep 像素在显示器内
        x_min = mon_left if win_w <= mon_width else mon_right - keep_x
        x_max = mon_right - win_w if win_w <= mon_width else mon_left - win_w + keep_x
        # 纵向：底边锚定是"字幕条停在哪"的事实源——窗口放得下时保证底边落在显示器
        # 内，顶边可以探出上沿（内容变高只向上长，不把整条窗口往下拽）；窗口高于
        # 显示器时才反过来保住顶边可见，否则用户点不到它
        y_min = mon_top if win_h <= mon_height else mon_bottom - keep_y
        y_max = mon_bottom - win_h if win_h <= mon_height else mon_top
        return min(max(x, x_min), x_max), min(max(y, y_min), y_max)

    def _start_move(self, event: tk.Event) -> None:
        """记录拖动起点：按下时的指针屏幕坐标与窗口位置。

        后续每步位移按"上一步的目标位置 + 指针位移增量"累加，而不是按窗口回读
        位置续算：窗口管理器响应 geometry 有上百毫秒延迟，快速拖动时回读到的
        还是旧位置，同一段位移会被反复累加，窗口越拖越远、最后飞出屏幕。
        """
        if not self.root:
            return
        self._user_moved_window = True
        self._move_pointer = (event.x_root, event.y_root)
        self._pointer_pos = self._move_pointer
        self._move_target = (self.root.winfo_x(), self.root.winfo_y())

    def _on_move(self, event: tk.Event) -> None:
        if not self.root:
            return
        deltax = event.x_root - self._move_pointer[0]
        deltay = event.y_root - self._move_pointer[1]
        # 指针没动就不是真拖动：窗口可能只是被高度自适应挪了位置，此时若照旧
        # 下发位置请求，会平白多一次挪窗并污染底边锚点
        if deltax == 0 and deltay == 0:
            return
        self._move_pointer = (event.x_root, event.y_root)
        self._pointer_pos = self._move_pointer
        # 位移增量按屏幕坐标算，与 geometry 的位置参数同口径，不掺缩放换算：
        # 指针落在窗口内，而窗口只有底边可能探出屏幕下沿，y 方向不会把整窗带走
        x, y = self._clamp_position(self._move_target[0] + deltax, self._move_target[1] + deltay)
        self._move_target = (x, y)
        self.root.geometry(f"+{x}+{y}")
        # 窗口被挪动后底边锚点跟着走，后续高度调整不再拉回原处
        self._window_bottom_px = y + self.root.winfo_height()
        self.logger.debug(
            f"拖动字幕窗口: 指针位移 ({deltax},{deltay}) → 位置 ({x},{y}) 底边锚点 {self._window_bottom_px}"
        )

    def _show_context_menu(self, event: tk.Event) -> None:
        if not self.root:
            return
        try:
            context_menu = tk.Menu(self.root, tearoff=0)
            if self.always_show_window:
                if self.is_visible:
                    context_menu.add_command(label="最小化窗口", command=self._minimize_window)
                else:
                    context_menu.add_command(label="显示窗口", command=self._show_window)
            context_menu.add_separator()
            context_menu.add_command(label="置顶/取消置顶", command=self._toggle_topmost)
            context_menu.add_command(label="调整透明度", command=self._adjust_opacity)
            context_menu.add_separator()
            context_menu.add_command(label="测试显示", command=self._show_test_message)
            context_menu.add_command(label="清空内容", command=self._clear_content)
            context_menu.add_separator()
            context_menu.add_command(label="关闭窗口", command=self._on_closing)
            context_menu.post(event.x_root, event.y_root)
        except Exception as e:
            self.logger.debug(f"显示右键菜单时出错: {e}")

    def _minimize_window(self) -> None:
        if self.root and self.always_show_window:
            if self.obs_friendly_mode:
                # 无边框窗口没有任务栏图标，iconify 无法恢复——改为隐藏，
                # 右键菜单"显示窗口"可以恢复。
                self.root.withdraw()
                self.is_visible = False
            else:
                self.root.iconify()

    def _show_window(self) -> None:
        if self.root:
            self.root.deiconify()
            self.is_visible = True

    def _toggle_topmost(self) -> None:
        if self.root:
            current = self.root.attributes("-topmost")
            new_topmost = not current
            self.root.attributes("-topmost", new_topmost)
            self.always_on_top = new_topmost
            status = "置顶" if new_topmost else "取消置顶"
            self.logger.info(f"窗口已{status} (always_on_top: {self.always_on_top})")

    def _adjust_opacity(self) -> None:
        if self.root:
            current_alpha = self.root.attributes("-alpha")
            alpha_values = [1.0, 0.8, 0.6, 0.4]
            try:
                current_index = alpha_values.index(current_alpha)
                new_index = (current_index + 1) % len(alpha_values)
            except ValueError:
                new_index = 0
            new_alpha = alpha_values[new_index]
            self.root.attributes("-alpha", new_alpha)
            self.logger.info(f"窗口透明度已调整为: {new_alpha}")

    def _show_test_message(self) -> None:
        if self.root:
            self._update_subtitle_display("OBS 测试消息 - 窗口可见性检查")
            self.logger.info("已显示 OBS 测试消息，请检查窗口是否在 OBS 窗口捕获列表中出现")

    def _clear_content(self) -> None:
        if self.text_label:
            if self.always_show_window and self.show_waiting_text:
                self.text_label.configure_text(text="等待语音/弹幕输入...")
            else:
                self.text_label.configure_text(text="")
            self._apply_window_height(0)
            self.logger.info("已清空字幕内容")
