"""
SubtitleGuiService - 字幕 GUI 长驻 Tk 线程服务

CustomTkinter 窗口、长驻线程、文本队列、自动隐藏、右键菜单、拖动。
该服务不在 Tool 系统内，由 main.py 在应用启动时直接实例化并调用
``start()``，字幕文本通过 ``push_subtitle(text)`` 入队（线程安全）。
"""

from __future__ import annotations

import contextlib
import glob
import os
import queue
import threading
import tkinter as tk
from typing import Any, Dict, List, Optional

from PIL import Image, ImageColor, ImageFilter, ImageFont, ImageTk
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

try:
    import customtkinter as ctk

    CTK_AVAILABLE = True
except ImportError:
    ctk = None
    CTK_AVAILABLE = False


class OutlineLabel:
    """PIL 二值渲染的描边标签。

    Canvas 原生文字带抗锯齿：描边像素与色键背景混合后不再匹配透明色，
    ``-transparentcolor`` 打孔会残留脏边。改由 Pillow 以 1-bit mask
    （无抗锯齿）渲染文字、膨胀 mask 生成描边，合成图仅含三种纯色像素——
    背景色/描边色/文字色，打孔后零残留。
    """

    def __init__(
        self,
        master,
        text="",
        font=None,
        text_color="white",
        outline_color="black",
        outline_width=2,
        outline_enabled=True,
        background_color="gray15",
        logger=None,
        **kwargs,
    ):
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

    def pack(self, **kwargs):
        self.container_frame.pack(**kwargs)

    def bind(self, event, callback):
        self.container_frame.bind(event, callback)

    def cget(self, option):
        try:
            return self.container_frame.cget(option)
        except Exception:
            self.logger.error(f"获取 Canvas 选项 '{option}' 失败", exc_info=True)
            return None

    def after(self, delay, callback):
        return self.container_frame.after(delay, callback)

    def _on_canvas_configure(self, event):
        self._draw_text()

    def _draw_text(self):
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
        canvas_height = self.canvas.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        img = self._render_text(canvas_width, canvas_height, bg_color)
        if img is None:
            return
        try:
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.create_image(canvas_width // 2, canvas_height // 2, image=self._photo)
        except Exception:
            self.logger.error("PIL 字幕渲染失败（ImageTk 不可用？）", exc_info=True)

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
        line_h = int(self._font_px * 1.35)
        total_h = len(lines) * line_h
        y = (height - total_h) // 2
        for line in lines:
            # getmask(mode="1") 返回 ImagingCore（无抗锯齿，字节为 0/255 二值）；
            # 转 Image("L") 后膨胀/合成，像素保持纯色（无灰度中间值）。
            mask_core = font.getmask(line, mode="1")
            mask_w, mask_h = mask_core.size
            if mask_w <= 0 or mask_h <= 0:
                continue
            mask_l = Image.frombytes("L", (mask_w, mask_h), bytes(mask_core))
            x = (width - mask_w) // 2
            if self.outline_enabled and self.outline_width > 0:
                outline_l = mask_l.filter(ImageFilter.MaxFilter(self.outline_width * 2 + 1))
                img.paste(Image.new("RGB", outline_l.size, outline_rgb), (x, y), outline_l)
            img.paste(Image.new("RGB", mask_l.size, text_rgb), (x, y), mask_l)
            y += line_h
        return img

    def _load_font(self) -> Optional[ImageFont.FreeTypeFont]:
        """解析字体路径：先用字体族名（Pillow Windows 走注册表），失败后退化为
        ``C:\\Windows\\Fonts`` 匹配族名关键词或常见中文兜底字体。"""
        if not self.font_obj:
            return None
        try:
            return ImageFont.truetype(self.font_obj[0], self._font_px)
        except Exception:
            pass
        family_key = (self.font_obj[0] or "").lower().replace(" ", "")
        try:
            for path in glob.glob(r"C:\Windows\Fonts\*.tt[cf]"):
                name = os.path.basename(path).lower().replace(" ", "")
                if family_key in name:
                    return ImageFont.truetype(path, self._font_px)
        except Exception:
            pass
        for fallback in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
            try:
                return ImageFont.truetype(fallback, self._font_px)
            except Exception:
                continue
        return None

    def _wrap_lines(self, font, max_width: int) -> List[str]:
        text = (self.display_text or "").strip()
        if not text:
            return []
        lines: List[str] = []
        current = ""
        for ch in text:
            if font.getlength(current + ch) > max_width - 20 and current:
                lines.append(current)
                current = ch
            else:
                current += ch
        if current:
            lines.append(current)
        return lines

    def configure_text(self, text="", **kwargs):
        if text != "":
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

    与新架构的集成方式：
    - ``SubtitleProvider`` 通过 ``service`` 依赖注入调用 ``push_subtitle(text)``
    - ``start()`` / ``stop()`` 在 main.py 组合根中管理生命周期
    - 字幕渲染走 GUI 服务，不走 ToolResult 反馈给 LLM
    """

    class ConfigSchema(BaseConfig):
        """字幕 GUI 配置（verbatim 自旧 SubtitleHandler.ConfigSchema）"""

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

    def __init__(self, config: Dict[str, Any]):
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

        self.text_queue: "queue.Queue[str]" = queue.Queue()
        self.gui_thread: Optional[threading.Thread] = None
        self.root: Any = None
        self.text_label: Any = None
        self.last_voice_time_ms = now_ms()
        self._gui_running = True
        self.is_visible = False
        self._started = False

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
        except Exception as e:
            self.logger.error(f"放入字幕队列时出错: {e}", exc_info=True)

    def _run_gui(self) -> None:
        """verbatim 自旧 SubtitleHandler._run_gui()"""
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
                    self.logger.error("设置透明背景失败（-transparentcolor 不可用）", exc_info=True)
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
                    self.logger.error("设置工具窗口属性失败", exc_info=True)

            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            x = (screen_width - self.window_width) // 2
            y = screen_height - self.window_height - self.window_offset_y
            self.root.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")

            # 背景：OBS 友好模式或色度键开启时用色度键颜色作"透明打孔色"，
            # 否则用配置的背景色。
            effective_background = (
                self.chroma_key_color if (self.obs_friendly_mode or self.use_chroma_key) else self.background_color
            )
            try:
                self.root.configure(fg_color=effective_background)
            except Exception:
                self.logger.error("设置背景颜色失败", exc_info=True)

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
            self.text_label.pack(expand=True, fill="both", padx=10, pady=5)

            def bind_drag_events(widget):
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
            self.logger.error(f"运行 Subtitle GUI 时出错: {e}", exc_info=True)
        finally:
            self.logger.info("Subtitle GUI 线程结束")
            if self.root:
                with contextlib.suppress(Exception):
                    self.root.quit()
            self._gui_running = False

    def _check_queue(self):
        if not self._gui_running:
            return
        try:
            while not self.text_queue.empty():
                text = self.text_queue.get_nowait()
                self._update_subtitle_display(text)
        except queue.Empty:
            pass
        except Exception as e:
            self.logger.warning(f"检查字幕队列时出错: {e}", exc_info=True)
        if self._gui_running and self.root:
            self.root.after(100, self._check_queue)

    def _update_subtitle_display(self, text: str):
        if not self.text_label or not self._gui_running:
            return
        try:
            if text:
                if not self.always_show_window and not self.is_visible and self.root:
                    self.root.deiconify()
                    self.is_visible = True
                self.text_label.configure_text(text=text)
                self.last_voice_time_ms = now_ms()
                self.logger.debug(f"已更新字幕: {text[:30]}...")
            elif not self.always_show_window and self.is_visible and self.auto_hide and self.root:
                self.root.withdraw()
                self.is_visible = False
        except Exception as e:
            self.logger.warning(f"更新字幕显示时出错: {e}", exc_info=True)

    def _check_auto_hide(self):
        if not self._gui_running:
            return
        try:
            if (
                self.auto_hide
                and self.is_visible
                and self.root
                and self.fade_delay_ms > 0
                and now_ms() - self.last_voice_time_ms > self.fade_delay_ms
            ):
                if self.always_show_window:
                    if self.text_label:
                        if self.show_waiting_text:
                            self.text_label.configure_text(text="等待语音/弹幕输入...")
                        else:
                            self.text_label.configure_text(text="")
                else:
                    self.logger.debug("自动隐藏字幕窗口")
                    self.root.withdraw()
                    self.is_visible = False
                    if self.text_label:
                        self.text_label.configure_text(text="")
            if self._gui_running and self.root:
                self.root.after(100, self._check_auto_hide)
        except Exception as e:
            self.logger.warning(f"检查自动隐藏时出错: {e}", exc_info=True)
            if self._gui_running and self.root:
                self.root.after(100, self._check_auto_hide)

    def _on_closing(self):
        self.logger.info("Subtitle 窗口关闭请求...")
        self._gui_running = False
        if self.root:
            try:
                self.root.destroy()
            except Exception as e:
                self.logger.warning(f"销毁 subtitle 窗口时出错: {e}", exc_info=True)
        self.root = None

    def _start_move(self, event):
        self._move_x = event.x
        self._move_y = event.y

    def _on_move(self, event):
        if self.root:
            deltax = event.x - self._move_x
            deltay = event.y - self._move_y
            x = self.root.winfo_x() + deltax
            y = self.root.winfo_y() + deltay
            self.root.geometry(f"+{x}+{y}")

    def _show_context_menu(self, event):
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

    def _minimize_window(self):
        if self.root and self.always_show_window:
            if self.obs_friendly_mode:
                # 无边框窗口没有任务栏图标，iconify 无法恢复——改为隐藏，
                # 右键菜单"显示窗口"可以恢复。
                self.root.withdraw()
                self.is_visible = False
            else:
                self.root.iconify()

    def _show_window(self):
        if self.root:
            self.root.deiconify()
            self.is_visible = True

    def _toggle_topmost(self):
        if self.root:
            current = self.root.attributes("-topmost")
            new_topmost = not current
            self.root.attributes("-topmost", new_topmost)
            self.always_on_top = new_topmost
            status = "置顶" if new_topmost else "取消置顶"
            self.logger.info(f"窗口已{status} (always_on_top: {self.always_on_top})")

    def _adjust_opacity(self):
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

    def _show_test_message(self):
        if self.root:
            self._update_subtitle_display("OBS 测试消息 - 窗口可见性检查")
            self.logger.info("已显示 OBS 测试消息，请检查窗口是否在 OBS 窗口捕获列表中出现")

    def _clear_content(self):
        if self.text_label:
            if self.always_show_window and self.show_waiting_text:
                self.text_label.configure_text(text="等待语音/弹幕输入...")
            else:
                self.text_label.configure_text(text="")
            self.logger.info("已清空字幕内容")
