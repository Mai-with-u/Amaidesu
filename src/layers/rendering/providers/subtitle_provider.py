"""
Subtitle Provider - Layer 7 渲染呈现层实现

职责:
- 在窗口中显示字幕文本
- 支持文字描边效果
- 支持OBS友好的窗口管理
- 支持自动隐藏功能
- 集成subtitle_service服务（向后兼容）
"""

import queue
import threading
import time
from typing import Dict, Any

from src.core.base.output_provider import OutputProvider
from src.layers.parameters.render_parameters import ExpressionParameters
from src.utils.logger import get_logger

# 检查依赖
CTK_AVAILABLE = False
try:
    import customtkinter as ctk

    CTK_AVAILABLE = True
except ImportError:
    ctk = None


class OutlineLabel:
    """
    自定义标签，支持文字描边效果
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

        # 移除描边相关参数，避免传递给父类
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

        # 保存参数
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

        self.canvas = ctk.CTkCanvas(self.container_frame, **canvas_kwargs)
        self.canvas.pack(fill="both", expand=True)

        # 绑定重绘事件
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 初始绘制
        self.container_frame.after(1, self._draw_text)

    def pack(self, **kwargs):
        self.container_frame.pack(**kwargs)

    def bind(self, event, callback):
        self.container_frame.bind(event, callback)

    def cget(self, option):
        try:
            return self.container_frame.cget(option)
        except Exception:
            return None

    def after(self, delay, callback):
        return self.container_frame.after(delay, callback)

    def _on_canvas_configure(self, event):
        self._draw_text()

    def _draw_text(self):
        if not self.display_text:
            return

        self.canvas.delete("all")

        # 确保使用最新的背景色并应用到Canvas
        bg_color = self._background_color if self._background_color else "gray15"
        try:
            self.canvas.configure(bg=bg_color)
            if self.logger:
                self.logger.debug(f"Canvas背景色已设置为: {bg_color}")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"设置Canvas背景色失败: {e}")
            self.canvas.configure(bg="gray15")  # fallback

        # 获取Canvas尺寸
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        if canvas_width <= 1 or canvas_height <= 1:
            return

        # 计算文字位置 (居中)
        x = canvas_width // 2
        y = canvas_height // 2

        # 绘制描边 (如果启用)
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
                x,
                y,
                text=self.display_text,
                fill=self.text_color,
                anchor="center",
                width=canvas_width - 20,
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
        if "background_color" in kwargs:
            self._background_color = kwargs["background_color"]

        self._draw_text()


class SubtitleProvider(OutputProvider):
    """
    Subtitle Provider实现

    核心功能:
    - 在窗口中显示字幕文本
    - 支持文字描边和半透明背景
    - 支持OBS友好的窗口管理
    - 智能显示和隐藏
    - 向后兼容subtitle_service服务
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化Subtitle Provider

        Args:
            config: Provider配置（来自[rendering.outputs.subtitle]）
        """
        super().__init__(config)
        self.logger = get_logger("SubtitleProvider")

        # GUI配置
        gui_config = self.config.get("gui", {})
        self.window_width = gui_config.get("window_width", 800)
        self.window_height = gui_config.get("window_height", 100)
        self.window_offset_y = gui_config.get("window_offset_y", 100)
        self.font_family = gui_config.get("font_family", "Microsoft YaHei UI")
        self.font_size = gui_config.get("font_size", 28)
        self.font_weight = gui_config.get("font_weight", "bold")
        self.text_color = gui_config.get("text_color", "white")

        # 描边配置
        self.outline_enabled = gui_config.get("outline_enabled", True)
        self.outline_color = gui_config.get("outline_color", "black")
        self.outline_width = gui_config.get("outline_width", 2)

        # 背景配置
        self.background_color = gui_config.get("background_color", "white")

        # 行为配置
        self.fade_delay_seconds = gui_config.get("fade_delay_seconds", 5)
        self.auto_hide = gui_config.get("auto_hide", True)
        self.window_alpha = gui_config.get("window_alpha", 0.95)
        self.always_on_top = gui_config.get("always_on_top", False)

        # OBS集成配置
        self.obs_friendly_mode = gui_config.get("obs_friendly_mode", True)
        self.window_title = gui_config.get("window_title", "Amaidesu-Subtitle-OBS")
        self.use_chroma_key = gui_config.get("use_chroma_key", False)
        self.chroma_key_color = gui_config.get("chroma_key_color", "#00FF00")

        # 窗口显示配置
        self.always_show_window = gui_config.get("always_show_window", True)
        self.show_in_taskbar = gui_config.get("show_in_taskbar", True)
        self.window_minimizable = gui_config.get("window_minimizable", True)
        self.show_waiting_text = gui_config.get("show_waiting_text", False)

        # 线程和状态
        self.text_queue = queue.Queue()
        self.gui_thread: threading.Thread = None
        self.root = None
        self.text_label = None
        self.last_voice_time = time.time()
        self.is_running = True
        self.is_visible = False

        self.logger.info("SubtitleProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        # 检查依赖
        if not CTK_AVAILABLE:
            raise RuntimeError("CustomTkinter库不可用，无法使用Subtitle Provider")

        self.logger.info("SubtitleProvider设置完成")

    async def _render_internal(self, parameters: ExpressionParameters):
        """
        渲染字幕输出

        Args:
            parameters: ExpressionParameters对象
        """
        if not parameters.subtitle_enabled or not parameters.subtitle_text:
            self.logger.debug("字幕未启用或文本为空，跳过渲染")
            return

        text = parameters.subtitle_text
        self.logger.info(f"显示字幕: '{text[:30]}...'")

        # 将文本放入队列
        try:
            self.text_queue.put(text)
            self.logger.debug(f"字幕已放入队列: {text[:30]}...")
        except Exception as e:
            self.logger.error(f"放入字幕队列失败: {e}")

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("SubtitleProvider清理中...")
        self.is_running = False

        # 等待线程结束
        if self.gui_thread and self.gui_thread.is_alive():
            self.logger.debug("等待GUI线程结束...")
            self.gui_thread.join(timeout=3.0)
            if self.gui_thread.is_alive():
                self.logger.warning("GUI线程未能及时结束")

        self.text_queue = queue.Queue()
        self.root = None
        self.text_label = None
        self.subtitle_service = None

        self.logger.info("SubtitleProvider清理完成")

    def _run_gui(self):
        """运行GUI线程"""
        if not CTK_AVAILABLE or ctk is None:
            self.logger.error("CustomTkinter not available")
            return

        try:
            # 设置CustomTkinter外观
            ctk.set_appearance_mode("dark")
            ctk.set_default_color_theme("blue")

            self.root = ctk.CTk()
            # 设置OBS友好的窗口标题
            window_title = self.window_title if self.obs_friendly_mode else "Amaidesu Subtitle"
            self.root.title(window_title)
            self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

            # 窗口属性设置
            self.root.attributes("-topmost", self.always_on_top)
            self.root.attributes("-alpha", self.window_alpha)

            # 确保窗口在任务栏显示且可操作
            if self.always_show_window and self.show_in_taskbar:
                self.logger.info("正常窗口模式：窗口将在任务栏显示并可正常操作")

                if self.window_minimizable:
                    self.root.resizable(True, True)
                else:
                    self.root.resizable(False, False)
            else:
                # 工具窗口模式
                try:
                    self.root.attributes("-toolwindow", True)
                except Exception:
                    self.logger.debug("系统不支持 -toolwindow 属性，将使用普通窗口模式")
                    pass

            # 窗口大小和位置
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            x = (screen_width - self.window_width) // 2
            y = screen_height - self.window_height - self.window_offset_y
            self.root.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")

            # 设置窗口背景（用于透明或色度键）
            if self.use_chroma_key:
                try:
                    self.root.configure(fg_color=self.chroma_key_color)
                    self.logger.info(f"已启用色度键模式，背景颜色: {self.chroma_key_color}")
                except Exception as e:
                    self.logger.debug(f"设置色度键背景失败: {e}")
                    self.root.configure(fg_color=self.background_color)  # fallback
            else:
                try:
                    self.root.configure(fg_color=self.background_color)
                    self.logger.info(f"已设置窗口背景颜色: {self.background_color}")
                except Exception as e:
                    self.logger.warning(f"设置窗口背景失败，使用白色fallback: {e}")
                    self.root.configure(fg_color="white")  # final fallback

            # 创建自定义文本标签
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

            # 绑定窗口拖动和右键菜单事件
            def bind_drag_events(widget):
                widget.bind("<Button-1>", self._start_move)
                widget.bind("<B1-Motion>", self._on_move)
                widget.bind("<Button-3>", self._show_context_menu)

            bind_drag_events(self.root)
            bind_drag_events(self.text_label)
            if hasattr(self.text_label, "canvas"):
                bind_drag_events(self.text_label.canvas)

            # 窗口初始显示状态
            if self.always_show_window:
                self.root.deiconify()
                self.is_visible = True

                if self.show_waiting_text:
                    initial_text = "字幕窗口已就绪 - 等待语音/弹幕输入..."
                else:
                    initial_text = ""
                self.text_label.configure_text(text=initial_text)

            else:
                self.root.withdraw()
                self.is_visible = False

            # 启动定时任务
            self.root.after(100, self._check_queue)
            self.root.after(100, self._check_auto_hide)

            self.logger.info("Subtitle GUI启动成功")
            self.root.mainloop()

        except Exception as e:
            self.logger.error(f"运行Subtitle GUI时出错: {e}", exc_info=True)
        finally:
            self.is_running = False
            if self.root:
                with Exception:
                    pass

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
                # 显示窗口（仅在非始终显示模式下才需要deiconify）
                if not self.always_show_window and not self.is_visible and self.root:
                    self.root.deiconify()
                    self.is_visible = True

                # 更新文本
                self.text_label.configure_text(text=text)
                self.last_voice_time = time.time()
                self.logger.debug(f"已更新字幕: {text[:30]}...")
            elif not self.always_show_window and self.is_visible and self.root:
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
                    # 始终显示模式：只清空文本，不隐藏窗口
                    if self.text_label:
                        if self.show_waiting_text:
                            self.text_label.configure_text(text="等待语音/弹幕输入...")
                        else:
                            self.text_label.configure_text(text="")  # 完全清空，实现透明效果
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

    def _start_move(self, event):
        """记录鼠标按下位置"""
        self._move_x = event.x
        self._move_y = event.y

    def _on_move(self, event):
        """拖动窗口"""
        if self.root:
            deltax = event.x - self._move_x
            deltay = event.y - self._move_y
            x = self.root.winfo_x() + deltax
            y = self.root.winfo_y() + deltay
            self.root.geometry(f"+{x}+{y}")

    def _show_context_menu(self, event):
        """显示右键菜单"""
        if not self.root or not ctk:
            return

        try:
            # 创建右键菜单
            context_menu = ctk.Menu(self.root, tearoff=0)

            # 添加菜单项
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

            # 显示菜单
            context_menu.post(event.x_root, event.y_root)

        except Exception as e:
            self.logger.debug(f"显示右键菜单时出错: {e}")

    def _minimize_window(self):
        """最小化窗口"""
        if self.root and self.always_show_window:
            self.root.iconify()

    def _show_window(self):
        """显示窗口"""
        if self.root:
            self.root.deiconify()
            self.is_visible = True

    def _toggle_topmost(self):
        """切换窗口置顶状态"""
        if self.root:
            current = self.root.attributes("-topmost")
            new_topmost = not current
            self.root.attributes("-topmost", new_topmost)
            status = "置顶" if new_topmost else "取消置顶"
            self.logger.info(f"窗口已{status}")

    def _adjust_opacity(self):
        """调整窗口透明度"""
        if self.root:
            current_alpha = self.root.attributes("-alpha")
            alpha_values = [1.0, 0.8, 0.6, 0.4]
            try:
                current_index = alpha_values.index(current_alpha)
                new_index = (current_index + 1) % len(alpha_values)
                new_alpha = alpha_values[new_index]
                self.root.attributes("-alpha", new_alpha)
                self.logger.info(f"窗口透明度已调整为: {new_alpha}")
            except ValueError:
                # 找不到当前值，使用默认
                self.root.attributes("-alpha", 1.0)

    def _show_test_message(self):
        """显示测试消息"""
        if self.root:
            self._update_subtitle_display("OBS 测试消息 - 窗口可见性检查")
            self.logger.info("已显示OBS测试消息，请检查窗口是否在OBS窗口捕获列表中出现")

    def _clear_content(self):
        """清空窗口内容"""
        if self.text_label:
            if self.always_show_window and self.show_waiting_text:
                self.text_label.configure_text(text="等待语音/弹幕输入...")
            else:
                self.text_label.configure_text(text="")
            self.logger.info("已清空字幕内容")

    def _on_closing(self):
        """处理窗口关闭事件"""
        self.logger.info("Subtitle窗口关闭请求...")
        self.is_running = False
        if self.root:
            try:
                self.root.destroy()
            except Exception as e:
                self.logger.warning(f"销毁subtitle窗口时出错: {e}")
        self.root = None
