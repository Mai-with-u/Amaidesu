"""
SubtitleGuiService - 字幕 GUI 长驻 Tk 线程服务

CustomTkinter 窗口、长驻线程、文本队列、自动隐藏、右键菜单、拖动。
该服务不在 Tool 系统内，由 main.py 在应用启动时直接实例化并调用
``start()``，字幕文本通过 ``push_subtitle(text)`` 入队、清空通过
``clear_subtitle()`` 入队（均线程安全，Tk 调用全部收敛在 GUI 线程内）。

渲染引擎（``OutlineLabel``）见 ``outline_label.py``，窗口摆位状态机
（``WindowPlacer``）见 ``window_placer.py``。
"""

from __future__ import annotations

import contextlib
import queue
import threading
import tkinter as tk
from typing import Any, Dict, Optional

from src.modules.config.core_schemas import SubtitleGuiParams
from src.modules.logging import get_logger
from src.modules.subtitle.backends.outline_label import OutlineLabel
from src.modules.subtitle.backends.window_placer import WindowPlacer
from src.modules.utils.time_utils import now_ms

try:
    import customtkinter as ctk

    CTK_AVAILABLE = True
except ImportError:
    ctk = None
    CTK_AVAILABLE = False

# 字幕标签在窗口里的内边距（逻辑单位，CustomTkinter 会按缩放放大）。画布只占
# 窗口的一部分，高度自适应要按这份内边距把窗口算得比内容高一点
_LABEL_PADX = 10
_LABEL_PADY = 5


class SubtitleGuiService(WindowPlacer):
    """字幕 GUI 服务（长驻 Tk 线程）

    集成方式：
    - 字幕后端通过 ``service`` 依赖注入调用 ``push_subtitle(text)`` /
      ``clear_subtitle()``，两者都只入队，Tk 调用由 GUI 线程统一执行
    - ``start()`` / ``stop()`` 在组合根中管理生命周期
    - 字幕渲染走 GUI 服务，不走 ToolResult 反馈给 LLM
    """

    # 参数 Schema 单一事实源在 config 侧（core_schemas），此处仅别名引用
    ConfigSchema = SubtitleGuiParams

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
        # 摆位域状态字段（窗口尺寸收敛、显示器夹取、拖动增量等）归 WindowPlacer
        # 所有，由其 _init_placement_state() 声明
        self._init_placement_state()

    def _gui_alive(self) -> bool:
        """WindowPlacer 的生命期询问钩子：GUI 线程仍在运行即摆位回调可执行。"""
        return self._gui_running

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
