# Amaidesu Subtitle Plugin (Screen Display): src/plugins/subtitle/plugin.py

import contextlib
import queue  # 用于线程间通信
import threading  # 用于运行 GUI
import time
import tkinter as tk
from typing import Any, Dict, Optional

try:
    import customtkinter as ctk

    CTK_AVAILABLE = True
except ImportError:
    ctk = None
    CTK_AVAILABLE = False

from src.core.amaidesu_core import AmaidesuCore
from src.core.plugin_manager import BasePlugin


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
        logger=None,  # 添加 logger 参数
        **kwargs,
    ):
        if not CTK_AVAILABLE or ctk is None:
            raise ImportError("CustomTkinter not available")

        # 保存 logger 引用
        self.logger = logger

        # 移除描边相关参数，避免传递给父类
        kwargs.pop("outline_color", None)
        kwargs.pop("outline_width", None)
        kwargs.pop("outline_enabled", None)
        kwargs.pop("background_color", None)
        kwargs.pop("logger", None)  # 移除 logger 参数

        # 过滤掉可能导致问题的参数
        safe_kwargs = {k: v for k, v in kwargs.items() if k not in ["bg_color", "text_color"]}

        # 设置容器为透明
        safe_kwargs["fg_color"] = "transparent"
        safe_kwargs["bg_color"] = "transparent"
        safe_kwargs["border_width"] = 0

        # 使用 CTkFrame 作为容器而不是 CTkLabel 来避免布局冲突
        self.container_frame = ctk.CTkFrame(master, **safe_kwargs)

        # 保存传入的参数
        self.logger = logger  # 保存 logger 引用
        self.display_text = text
        self.text_color = text_color
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.outline_enabled = outline_enabled
        self.font_obj = font
        self._background_color = background_color

        # 创建 Canvas 来绘制带描边的文字
        # 重要：设置Canvas的初始背景和边框
        canvas_kwargs = {
            "highlightthickness": 0,
            "bd": 0,
            "relief": "flat",
            "bg": background_color,  # 直接使用配置的背景色
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

        # 获取 Canvas 尺寸
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


class SubtitlePlugin(BasePlugin):
    """
    接收语音文本并显示在自定义的置顶窗口中，支持描边和半透明背景
    专门为 OBS 窗口捕获优化，窗口长期存在并在任务栏可见
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # 从正确的配置节点读取配置
        self.config = self.plugin_config.get("subtitle_display", {})

        # 如果 subtitle_display 节点不存在，尝试使用顶级配置（向后兼容）
        if not self.config:
            self.config = self.plugin_config

        # --- 检查依赖 ---
        if not CTK_AVAILABLE:
            self.logger.error("CustomTkinter库不可用,字幕插件已禁用。")
            self.enabled = False
            return

        # --- GUI 配置 ---
        self.window_width = self.config.get("window_width", 800)
        self.window_height = self.config.get("window_height", 100)
        self.window_offset_y = self.config.get("window_offset_y", 100)
        self.font_family = self.config.get("font_family", "Microsoft YaHei UI")
        self.font_size = self.config.get("font_size", 28)
        self.font_weight = self.config.get("font_weight", "bold")
        self.text_color = self.config.get("text_color", "white")

        # --- 描边配置 ---
        self.outline_enabled = self.config.get("outline_enabled", True)
        self.outline_color = self.config.get("outline_color", "black")
        self.outline_width = self.config.get("outline_width", 2)

        # --- 背景配置 ---
        self.background_color = self.config.get("background_color", "#FFFFFF")

        # --- 行为配置 ---
        self.fade_delay_seconds = self.config.get("fade_delay_seconds", 5)
        self.auto_hide = self.config.get("auto_hide", True)
        self.window_alpha = self.config.get("window_alpha", 0.95)
        self.always_on_top = self.config.get("always_on_top", False)  # 修改默认值为 False

        # --- OBS 集成配置 ---
        self.obs_friendly_mode = self.config.get("obs_friendly_mode", True)
        self.window_title = self.config.get("window_title", "Amaidesu-Subtitle-OBS")
        self.use_chroma_key = self.config.get("use_chroma_key", False)
        self.chroma_key_color = self.config.get("chroma_key_color", "#00FF00")

        # --- 窗口显示配置 ---
        self.always_show_window = self.config.get("always_show_window", True)
        self.show_in_taskbar = self.config.get("show_in_taskbar", True)
        self.window_minimizable = self.config.get("window_minimizable", True)
        self.show_waiting_text = self.config.get("show_waiting_text", False)

        # --- 线程和状态 ---
        self.text_queue = queue.Queue()
        self.gui_thread: Optional[threading.Thread] = None
        self.root = None  # type: Optional[Any]
        self.text_label = None  # type: Optional[OutlineLabel]
        self.last_voice_time = time.time()
        self.is_running = True
        self.is_visible = False  # 窗口是否可见

        # 输出配置调试信息
        self.logger.info("SubtitlePlugin (CustomTkinter) 初始化完成。")
        self.logger.info("=== 字幕插件配置信息 ===")
        self.logger.info(f"  - OBS友好模式: {self.obs_friendly_mode}")
        self.logger.info(f"  - 始终显示窗口: {self.always_show_window}")
        self.logger.info(f"  - 任务栏显示: {self.show_in_taskbar}")
        self.logger.info(f"  - 可最小化: {self.window_minimizable}")
        self.logger.info(f"  - 显示等待文字: {self.show_waiting_text}")
        self.logger.info(f"  - 使用色度键: {self.use_chroma_key}")
        self.logger.info(f"  - 窗口标题: {self.window_title}")
        self.logger.info(f"  - 背景颜色: {self.background_color}")
        self.logger.info(f"  - 窗口透明度: {self.window_alpha}")
        self.logger.info(f"  - 始终置顶: {self.always_on_top}")
        self.logger.info("========================")

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
            # 设置 OBS 友好的窗口标题
            window_title = self.window_title if self.obs_friendly_mode else "Amaidesu Subtitle"
            self.root.title(window_title)
            self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

            # --- 窗口属性设置 ---
            self.root.attributes("-topmost", self.always_on_top)
            self.root.attributes("-alpha", self.window_alpha)

            # 确保窗口在任务栏显示且可操作
            if self.always_show_window and self.show_in_taskbar:
                # 正常窗口模式：在任务栏显示，可最小化/最大化
                self.logger.info("正常窗口模式：窗口将在任务栏显示并可正常操作")

                # 设置窗口为可调整大小（便于任务栏操作）
                if self.window_minimizable:
                    self.root.resizable(True, True)
                else:
                    self.root.resizable(False, False)
            else:
                # 工具窗口模式
                try:
                    self.root.attributes("-toolwindow", True)
                except Exception:
                    # 在不支持的系统上忽略此属性
                    self.logger.debug("系统不支持 -toolwindow 属性，将使用普通窗口模式")
                    pass

            # --- 窗口大小和位置 ---
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            x = (screen_width - self.window_width) // 2
            y = screen_height - self.window_height - self.window_offset_y
            self.root.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")

            # --- 设置窗口背景（用于透明或色度键）---
            if self.use_chroma_key:
                # 使用色度键模式 (适合 OBS 绿幕)
                try:
                    self.root.configure(fg_color=self.chroma_key_color)
                    self.logger.info(f"已启用色度键模式，背景颜色: {self.chroma_key_color}")
                except Exception as e:
                    self.logger.debug(f"设置色度键背景失败: {e}")
            else:
                # 使用配置的背景色
                try:
                    self.root.configure(fg_color=self.background_color)
                    self.logger.info(f"已设置窗口背景颜色: {self.background_color}")
                except Exception as e:
                    self.logger.debug(f"设置背景失败，使用白色背景: {e}")
                    self.root.configure(fg_color="#FFFFFF")  # 白色fallback

            # --- 直接在主窗口创建内容 ---
            parent = self.root

            # --- 创建自定义文本标签 ---
            font_tuple = (self.font_family, self.font_size, self.font_weight)
            self.text_label = OutlineLabel(
                parent,
                text="",
                font=font_tuple,
                text_color=self.text_color,
                outline_color=self.outline_color,
                outline_width=self.outline_width,
                outline_enabled=self.outline_enabled,
                background_color=self.background_color,
                logger=self.logger,  # 传递 logger 参数
            )
            self.text_label.pack(expand=True, fill="both", padx=10, pady=5)

            # --- 绑定窗口拖动和右键菜单事件 ---
            def bind_drag_events(widget):
                widget.bind("<Button-1>", self._start_move)
                widget.bind("<B1-Motion>", self._on_move)
                widget.bind("<Button-3>", self._show_context_menu)  # 右键菜单

            bind_drag_events(self.root)
            bind_drag_events(self.text_label)
            if hasattr(self.text_label, "canvas"):
                bind_drag_events(self.text_label.canvas)

            # --- 窗口初始显示状态 ---
            if self.always_show_window:
                # 始终显示窗口模式：立即显示
                self.root.deiconify()  # 确保窗口可见
                self.is_visible = True

                # 设置初始显示内容
                if self.show_waiting_text:
                    initial_text = "字幕窗口已就绪 - 等待语音/弹幕输入..."
                else:
                    initial_text = ""  # 空内容（透明效果）

                if initial_text:
                    self.root.after(500, lambda: self._update_subtitle_display(initial_text))

                self.logger.info(f"窗口始终显示模式已启用 - 标题: '{window_title}'")
                self.logger.info("窗口已在任务栏显示，OBS 可通过窗口捕获识别")
            else:
                # 传统模式：初始隐藏窗口
                self.root.withdraw()
                self.is_visible = False

            # --- 启动定时任务 ---
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
                # 显示窗口（仅在非始终显示模式下才需要deiconify）
                if not self.always_show_window and not self.is_visible and self.root:
                    self.root.deiconify()
                    self.is_visible = True

                # 更新文本
                self.text_label.configure_text(text=text)
                self.last_voice_time = time.time()
                self.logger.debug(f"已更新字幕: {text[:30]}...")
            elif not self.always_show_window and self.is_visible and self.auto_hide and self.root:
                # 只有在非始终显示模式下才隐藏窗口
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
                    # 传统模式：隐藏窗口
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

    # --- 窗口事件处理 ---
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
        if not self.root:
            return

        try:
            # 创建右键菜单
            context_menu = tk.Menu(self.root, tearoff=0)

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
            self.always_on_top = new_topmost  # 同步配置状态
            status = "置顶" if new_topmost else "取消置顶"
            self.logger.info(f"窗口已{status} (always_on_top: {self.always_on_top})")

    def _adjust_opacity(self):
        """调整窗口透明度"""
        if self.root:
            current_alpha = self.root.attributes("-alpha")
            # 循环透明度: 1.0 -> 0.8 -> 0.6 -> 0.4 -> 1.0
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
        """显示测试消息"""
        if self.root:
            self._update_subtitle_display("OBS 测试消息 - 窗口可见性检查")
            self.logger.info("已显示 OBS 测试消息，请检查窗口是否在 OBS 窗口捕获列表中出现")

    def _clear_content(self):
        """清空窗口内容"""
        if self.text_label:
            if self.always_show_window and self.show_waiting_text:
                self.text_label.configure_text(text="等待语音/弹幕输入...")
            else:
                self.text_label.configure_text(text="")  # 完全清空
            self.logger.info("已清空字幕内容")

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

    # --- Plugin Lifecycle ---
    async def setup(self):
        await super().setup()

        # 检查是否被禁用
        if not CTK_AVAILABLE or hasattr(self, "enabled") and not self.enabled:
            self.logger.warning("字幕插件被禁用，跳过设置")
            return

        # 注册服务
        self.core.register_service("subtitle_service", self)
        self.logger.info("SubtitlePlugin 已注册为 'subtitle_service' 服务。")

        # 启动 GUI 线程
        self.is_running = True
        self.gui_thread = threading.Thread(target=self._run_gui, daemon=True)
        self.gui_thread.start()
        self.logger.info("Subtitle GUI 线程已启动。")

    async def cleanup(self):
        self.logger.info("正在清理 SubtitlePlugin...")
        self.is_running = False

        # 等待线程结束 (线程会自己清理窗口)
        if self.gui_thread and self.gui_thread.is_alive():
            self.logger.debug("等待 Subtitle GUI 线程结束...")
            self.gui_thread.join(timeout=3.0)
            if self.gui_thread.is_alive():
                self.logger.warning("Subtitle GUI 线程未能及时结束。")

        await super().cleanup()
        self.logger.info("SubtitlePlugin 清理完成。")

    # --- Service Method ---
    async def record_speech(self, text: str, duration: float):
        """
        接收文本，显示字幕。保持与原接口兼容。
        """
        if not self.is_running:
            self.logger.debug("字幕服务未运行，跳过显示")
            return

        if not CTK_AVAILABLE:
            self.logger.debug("CustomTkinter 不可用，跳过字幕显示")
            return

        if not text:
            # 空文本表示结束，可以选择隐藏
            if self.auto_hide:
                try:
                    self.text_queue.put("")
                except Exception as e:
                    self.logger.debug(f"队列操作失败: {e}")
            return

        # 清理文本
        cleaned_text = text.replace("\n", " ").replace("\r", "")

        try:
            self.text_queue.put(cleaned_text)
            self.logger.debug(f"已将文本放入字幕队列: {cleaned_text[:30]}...")
        except Exception as e:
            self.logger.error(f"放入字幕队列时出错: {e}", exc_info=True)


# --- Plugin Entry Point ---
plugin_entrypoint = SubtitlePlugin
