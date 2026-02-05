"""
Subtitle Output Provider

字幕输出Provider，使用CustomTkinter显示字幕窗口。
"""

import contextlib
import time
import threading
import queue
import tkinter as tk
from typing import Optional

try:
    import customtkinter as ctk

    CTK_AVAILABLE = True
except ImportError:
    ctk = None
    CTK_AVAILABLE = False

from src.core.base.output_provider import OutputProvider
from src.core.base.base import RenderParameters
from src.core.utils.logger import get_logger


class OutlineLabel:
    """自定义标签，支持文字描边效果"""

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

        # 移除描边相关参数
        kwargs.pop("outline_color", None)
        kwargs.pop("outline_width", None)
        kwargs.pop("outline_enabled", None)
        kwargs.pop("background_color", None)
        kwargs.pop("logger", None)

        # 过滤掉可能导致问题的参数
        safe_kwargs = {k: v for k, v in kwargs.items() if k not in ["bg_color", "text_color"]}

        # 设置容器为透明
        safe_kwargs["fg_color"] = "transparent"
        safe_kwargs["bg_color"] = "transparent"
        safe_kwargs["border_width"] = 0

        # 使用 CTkFrame 作为容器
        self.container_frame = ctk.CTkFrame(master, **safe_kwargs)

        self.display_text = text
        self.text_color = text_color
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.outline_enabled = outline_enabled
        self.font_obj = font
        self._background_color = background_color

        # 创建 Canvas 来绘制带描边的文字
        canvas_kwargs = {
            "highlightthickness": 0,
            "bd": 0,
            "relief": "flat",
            "bg": background_color,
        }

        self.canvas = tk.Canvas(self.container_frame, **canvas_kwargs)
        self.canvas.pack(fill="both", expand=True)

        # 绑定重绘事件
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 初始绘制
        self.container_frame.after(1, self._draw_text)

    def pack(self, **kwargs):
        """包装 pack 方法"""
        self.container_frame.pack(**kwargs)

    def bind(self, event, callback):
        """包装 bind 方法"""
        self.container_frame.bind(event, callback)

    def cget(self, option):
        """包装 cget 方法"""
        try:
            return self.container_frame.cget(option)
        except Exception:
            return None

    def after(self, delay, callback):
        """包装 after 方法"""
        return self.container_frame.after(delay, callback)

    def _on_canvas_configure(self, event):
        """Canvas 尺寸改变时重绘文字"""
        self._draw_text()

    def _draw_text(self):
        """绘制带描边的文字"""
        if not self.display_text:
            return

        self.canvas.delete("all")

        # 设置背景色
        bg_color = self._background_color if self._background_color else "gray15"
        try:
            self.canvas.configure(bg=bg_color)
        except Exception:
            self.canvas.configure(bg="gray15")

        # 获取 Canvas 尺寸
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        if canvas_width <= 1 or canvas_height <= 1:
            return

        # 计算文字位置 (居中)
        x = canvas_width // 2
        y = canvas_height // 2

        # 绘制描边
        if self.outline_enabled and self.outline_width > 0:
            for dx in range(-self.outline_width, self.outline_width + 1):
                for dy in range(-self.outline_width, self.outline_width + 1):
                    if dx == 0 and dy == 0:
                        continue
                    if dx * dx + dy * dy <= self.outline_width * self.outline_width:
                        if self.font_obj:
                            self.canvas.create_text(
                                x + dx,
                                y + dy,
                                text=self.display_text,
                                font=self.font_obj,
                                fill=self.outline_color,
                                anchor="center",
                                width=canvas_width - 20,
                            )
                        else:
                            self.canvas.create_text(
                                x + dx,
                                y + dy,
                                text=self.display_text,
                                fill=self.outline_color,
                                anchor="center",
                                width=canvas_width - 20,
                            )

        # 绘制主文字
        if self.font_obj:
            self.canvas.create_text(
                x,
                y,
                text=self.display_text,
                font=self.font_obj,
                fill=self.text_color,
                anchor="center",
                width=canvas_width - 20,
            )
        else:
            self.canvas.create_text(
                x, y, text=self.display_text, fill=self.text_color, anchor="center", width=canvas_width - 20
            )

    def configure_text(self, text="", **kwargs):
        """更新文字内容和样式"""
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


class SubtitleOutputProvider(OutputProvider):
    """
    字幕输出Provider

    使用CustomTkinter显示字幕窗口，支持描边和半透明背景。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("SubtitleOutputProvider")

        if not CTK_AVAILABLE:
            self.logger.error("CustomTkinter库不可用,字幕插件已禁用。")
            self.enabled = False
            return

        # GUI 配置
        self.window_width = self.config.get("window_width", 800)
        self.window_height = self.config.get("window_height", 100)
        self.window_offset_y = self.config.get("window_offset_y", 100)
        self.font_family = self.config.get("font_family", "Microsoft YaHei UI")
        self.font_size = self.config.get("font_size", 28)
        self.font_weight = self.config.get("font_weight", "bold")
        self.text_color = self.config.get("text_color", "white")

        # 描边配置
        self.outline_enabled = self.config.get("outline_enabled", True)
        self.outline_color = self.config.get("outline_color", "black")
        self.outline_width = self.config.get("outline_width", 2)

        # 背景配置
        self.background_color = self.config.get("background_color", "#FFFFFF")

        # 行为配置
        self.fade_delay_seconds = self.config.get("fade_delay_seconds", 5)
        self.auto_hide = self.config.get("auto_hide", True)
        self.window_alpha = self.config.get("window_alpha", 0.95)
        self.always_on_top = self.config.get("always_on_top", False)

        # OBS 集成配置
        self.obs_friendly_mode = self.config.get("obs_friendly_mode", True)
        self.window_title = self.config.get("window_title", "Amaidesu-Subtitle-OBS")
        self.use_chroma_key = self.config.get("use_chroma_key", False)
        self.chroma_key_color = self.config.get("chroma_key_color", "#00FF00")

        # 窗口显示配置
        self.always_show_window = self.config.get("always_show_window", True)
        self.show_in_taskbar = self.config.get("show_in_taskbar", True)
        self.window_minimizable = self.config.get("window_minimizable", True)
        self.show_waiting_text = self.config.get("show_waiting_text", False)

        # 线程和状态
        self.text_queue = queue.Queue()
        self.gui_thread: Optional[threading.Thread] = None
        self.root = None
        self.text_label = None
        self.last_voice_time = time.time()
        self.is_running = True
        self.is_visible = False

    async def _setup_internal(self):
        """内部设置"""
        # 注册事件监听器
        if self.event_bus:
            self.event_bus.on("render.subtitle", self._handle_render_request, priority=50)
            self.logger.info("已注册 subtitle 渲染事件监听器")

    async def _render_internal(self, parameters: RenderParameters):
        """
        内部渲染逻辑

        Args:
            parameters: 渲染参数，包含content(文本内容)
        """
        text = parameters.content if parameters.content else ""
        self.logger.debug(f"收到字幕渲染请求: {text[:30]}...")

        # 将文本放入队列
        try:
            self.text_queue.put(text)
        except Exception as e:
            self.logger.error(f"放入字幕队列时出错: {e}", exc_info=True)

    async def _cleanup_internal(self):
        """内部清理"""
        self.logger.info("正在清理 SubtitleOutputProvider...")
        self.is_running = False

        # 等待线程结束
        if self.gui_thread and self.gui_thread.is_alive():
            self.logger.debug("等待 Subtitle GUI 线程结束...")
            self.gui_thread.join(timeout=3.0)
            if self.gui_thread.is_alive():
                self.logger.warning("Subtitle GUI 线程未能及时结束。")

        self.logger.info("SubtitleOutputProvider 清理完成。")

    def _run_gui(self):
        """运行 GUI 线程"""
        if not CTK_AVAILABLE or ctk is None:
            self.logger.error("CustomTkinter not available")
            return

        try:
            # 设置 CustomTkinter 外观
            ctk.set_appearance_mode("dark")
            ctk.set_default_color_theme("blue")

            self.root = ctk.CTk()
            window_title = self.window_title if self.obs_friendly_mode else "Amaidesu Subtitle"
            self.root.title(window_title)
            self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

            # 窗口属性设置
            self.root.attributes("-topmost", self.always_on_top)
            self.root.attributes("-alpha", self.window_alpha)

            # 任务栏显示
            if self.always_show_window and self.show_in_taskbar:
                if self.window_minimizable:
                    self.root.resizable(True, True)
                else:
                    self.root.resizable(False, False)
            else:
                try:
                    self.root.attributes("-toolwindow", True)
                except Exception:
                    pass

            # 窗口大小和位置
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            x = (screen_width - self.window_width) // 2
            y = screen_height - self.window_height - self.window_offset_y
            self.root.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")

            # 背景设置
            if self.use_chroma_key:
                try:
                    self.root.configure(fg_color=self.chroma_key_color)
                except Exception:
                    pass
            else:
                try:
                    self.root.configure(fg_color=self.background_color)
                except Exception:
                    self.root.configure(fg_color="#FFFFFF")

            # 创建文本标签
            font_tuple = (self.font_family, self.font_size, self.font_weight)
            self.text_label = OutlineLabel(
                self.root,
                text="",
                font=font_tuple,
                text_color=self.text_color,
                outline_color=self.outline_color,
                outline_width=self.outline_width,
                outline_enabled=self.outline_enabled,
                background_color=self.background_color,
                logger=self.logger,
            )
            self.text_label.pack(expand=True, fill="both", padx=10, pady=5)

            # 初始显示状态
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

            # 启动定时任务
            self.root.after(100, self._check_queue)
            self.root.after(100, self._check_auto_hide)

            self.logger.info("Subtitle GUI 启动成功。")
            self.root.mainloop()

        except Exception as e:
            self.logger.error(f"运行 Subtitle GUI 时出错: {e}", exc_info=True)
        finally:
            self.logger.info("Subtitle GUI 线程结束。")
            if self.root:
                with contextlib.suppress(Exception):
                    self.root.quit()
            self.is_running = False

    def _check_queue(self):
        """检查队列中的新文本"""
        if not self.is_running:
            return

        try:
            while not self.text_queue.empty():
                text = self.text_queue.get_nowait()
                self._update_subtitle_display(text)
        except queue.Empty:
            pass
        except Exception as e:
            self.logger.warning(f"检查字幕队列时出错: {e}", exc_info=True)

        if self.is_running and self.root:
            self.root.after(100, self._check_queue)

    def _update_subtitle_display(self, text: str):
        """更新字幕显示"""
        if not self.text_label or not self.is_running:
            return

        try:
            if text:
                # 显示窗口
                if not self.always_show_window and not self.is_visible and self.root:
                    self.root.deiconify()
                    self.is_visible = True

                # 更新文本
                self.text_label.configure_text(text=text)
                self.last_voice_time = time.time()
                self.logger.debug(f"已更新字幕: {text[:30]}...")
            elif not self.always_show_window and self.is_visible and self.auto_hide and self.root:
                # 隐藏窗口
                self.root.withdraw()
                self.is_visible = False

        except Exception as e:
            self.logger.warning(f"更新字幕显示时出错: {e}", exc_info=True)

    def _check_auto_hide(self):
        """检查是否需要自动隐藏"""
        if not self.is_running:
            return

        try:
            if (
                self.auto_hide
                and self.is_visible
                and self.root
                and self.fade_delay_seconds > 0
                and time.time() - self.last_voice_time > self.fade_delay_seconds
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

            if self.is_running and self.root:
                self.root.after(100, self._check_auto_hide)

        except Exception as e:
            self.logger.warning(f"检查自动隐藏时出错: {e}", exc_info=True)
            if self.is_running and self.root:
                self.root.after(100, self._check_auto_hide)

    def _on_closing(self):
        """处理窗口关闭事件"""
        self.logger.info("Subtitle 窗口关闭请求...")
        self.is_running = False
        if self.root:
            try:
                self.root.destroy()
            except Exception as e:
                self.logger.warning(f"销毁 subtitle 窗口时出错: {e}", exc_info=True)
        self.root = None

    async def _handle_render_request(self, event_name: str, data: RenderParameters, source: str):
        """处理字幕渲染请求"""
        await self.render(data)
