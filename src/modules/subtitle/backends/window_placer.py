"""WindowPlacer - 字幕窗口摆位状态机（混入 SubtitleGuiService）

DPI 缩放收敛、底边锚定、多显示器夹取与拖动增量的全部方法、状态字段与口径
注释集中在此。混入而非组合：方法直接读写 ``self.root``/``self.logger`` 等
窗口与日志依赖，拆成独立对象需要为这些依赖造一层窄接口，属行为外的额外
抽象；用混入保持行为与测试不变。

摆位状态字段由 ``_init_placement_state()`` 声明，由宿主构造链调用；宿主的
GUI 线程生命期不在此管辖——摆位方法经 ``_gui_alive()`` 钩子询问"GUI 还活着
吗"，宿主覆写为回报其线程运行标志。
"""

from __future__ import annotations

import tkinter as tk
from typing import List, Optional, Tuple

from src.modules.subtitle.backends.window_geometry import (
    MonitorRect,
    clamp_into_rect,
    clamp_position,
    clamp_top_to_monitor,
    enumerate_monitors,
    monitor_under_point,
    parse_requested_size,
    primary_monitor_rect,
    virtual_screen_bounds,
)


class WindowPlacer:
    """窗口摆位方法集：供 ``SubtitleGuiService`` 混入，依赖 ``root``/``logger``
    等窗口与日志面（由宿主构造），摆位状态字段归本类所有。"""

    def _init_placement_state(self) -> None:
        """声明摆位域的全部状态字段；由宿主构造链调用（本类无 ``__init__``）。"""
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

    def _gui_alive(self) -> bool:
        """GUI 是否仍在运行：摆位回调（after 定时器）在窗口销毁后仍可能触发。

        单独跑摆位逻辑（如测试直接构造本类）默认视为存活；宿主覆写为回报
        其 GUI 线程运行标志。
        """
        return True

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
            measured = 0.0
            try:
                requested = parse_requested_size(self.root.geometry())[1]
            except Exception:
                requested = None
            if requested:
                measured = self.root.winfo_height() / requested
            if measured > 0:
                return measured
        return 1.0

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
        if not self.root or not self._gui_alive():
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
        if not self.root or not self._gui_alive():
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
        if not self.root or not self._gui_alive():
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

    def _retry_window_height(self) -> None:
        """校正上一步没到位的窗口高度（延迟一拍重试一次）。"""
        if not self.root or not self._gui_alive():
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
            self._screen_bounds = virtual_screen_bounds() or (
                0,
                0,
                self.root.winfo_screenwidth(),
                self.root.winfo_screenheight(),
            )
        return self._screen_bounds

    def _clamp_top_to_monitor(self, top: int, height_px: int) -> int:
        """顶边纵向夹取：窗口所在显示器的上下沿为界（数学见 window_geometry）。"""
        rect = self._monitor_under_window()
        if rect is None:
            return top
        return clamp_top_to_monitor(top, height_px, rect)

    def _monitor_under_window(self) -> Optional[MonitorRect]:
        """窗口所在（或离窗口最近）的显示器矩形；无显示器信息时返回 None。"""
        if not self.root:
            return None
        rects = self._monitor_rects()
        if not rects:
            return None
        return monitor_under_point(self.root.winfo_x(), self.root.winfo_y(), rects)

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
            self._monitors = enumerate_monitors()
        if self._monitors:
            return self._monitors
        return [self._window_bounds()]

    def _primary_monitor_rect(self) -> MonitorRect:
        """主显示器矩形（虚拟桌面原点所在的那台）；取不到时退回虚拟桌面范围。"""
        rect = primary_monitor_rect(self._monitor_rects())
        return rect if rect is not None else self._window_bounds()

    def _clamp_position(self, x: int, y: int) -> Tuple[int, int]:
        """把窗口位置夹到指针所在显示器内（选屏策略见 window_geometry.clamp_position）。"""
        pointer_x, pointer_y = self._pointer_pos
        return clamp_position(
            x,
            y,
            pointer_x,
            pointer_y,
            self._monitor_rects(),
            self._target_width_px,
            self.root.winfo_height(),
        )

    def _clamp_into(
        self,
        rect: MonitorRect,
        x: int,
        y: int,
    ) -> Tuple[int, int]:
        """把窗口位置夹进单台显示器；窗口实际尺寸由此处从窗口回读取供。"""
        return clamp_into_rect(rect, x, y, self._target_width_px, self.root.winfo_height())

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
