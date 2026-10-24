"""SubtitleGuiService 窗口几何：底边锚定不漂移、多行字幕不被裁、窗口始终整条落在显示器内（含多屏拖动）、自动隐藏与队列分发

桩 Root 模拟 CustomTkinter 的 geometry 口径：宽高参数按窗口缩放放大后执行，位置不缩放，winfo 系列返回物理像素。
早期桩把位置也按缩放处理，与真实 CTk 不符，因而漏掉了坐标口径混读导致的漂移。
"""

from dataclasses import dataclass

import pytest

from src.modules.subtitle.backends.tk_gui_service import SubtitleGuiService

# 假显示器布局：主屏 2048x2160（高度放大以免夹取干扰锚定断言），副屏接在主屏右侧
PRIMARY_MONITOR = (0, 0, 2048, 2160)
CONFIG = {"window_width": 800, "window_height": 100, "window_offset_y": 100}


class FakeCtkRoot:
    """geometry 宽高按 scale 放大生效，位置原样生效；monitors 缺省视为单显示器=窗口所在屏。"""

    def __init__(self, width, height, x, y, scale, screen=(2048, 1280), monitors=None):
        self._width, self._height, self._x, self._y = width, height, x, y
        self._scale = scale
        self._screen = screen
        self._screen_bounds = (0, 0, screen[0], screen[1])  # 服务优先读窗口上的屏幕范围，注入假桌面避免依赖运行机显示器
        self.geometry_specs = []
        self.monitors = list(monitors) if monitors else [(0, 0, screen[0], screen[1])]

    def _get_window_scaling(self):
        return self._scale  # 真实 CTk 由 ScalingTracker 注入；服务据此把物理像素折算回 geometry 的逻辑单位

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def winfo_screenwidth(self):
        return self._screen[0]

    def winfo_screenheight(self):
        return self._screen[1]

    def update_idletasks(self):
        pass

    def geometry(self, spec):
        self.geometry_specs.append(spec)
        size, _, pos = spec.partition("+")
        if size:
            w_str, _, h_str = size.partition("x")
            self._width = round(int(w_str) * self._scale)
            self._height = round(int(h_str) * self._scale)
        if pos:
            x_str, _, y_str = pos.partition("+")
            self._x, self._y = int(x_str), int(y_str)


@dataclass
class Pointer:
    x_root: int
    y_root: int


def _service(root=None, *, default_h=100, target_w=800, bottom=1180, **cfg) -> SubtitleGuiService:
    """带窗口桩的服务；默认窗口 800x100 位于 (624,1080)，屏 2048x1280。"""
    svc = SubtitleGuiService(config={**CONFIG, **cfg})
    svc.root = root or FakeCtkRoot(800, 100, 624, 1080, 1.0)
    svc._default_window_height_px = default_h
    svc._target_width_px = target_w
    svc._window_bottom_px = bottom
    return svc


@pytest.fixture
def scaled() -> SubtitleGuiService:
    """缩放 1.5 的窗口：物理 1200x150，底边 1550；屏幕给足高度，让断言只考验锚定本身。"""
    root = FakeCtkRoot(1200, 150, 880, 1400, 1.5, screen=(2048, 2160), monitors=[PRIMARY_MONITOR])
    return _service(root, default_h=150, target_w=1200, bottom=1550)


def _multi_monitor() -> SubtitleGuiService:
    """主屏 2048x1280 + 右侧副屏 1920x1280，窗口初始在主屏右侧。"""
    root = FakeCtkRoot(
        800, 100, 624, 1080, 1.0, screen=(3968, 1280), monitors=[(0, 0, 2048, 1280), (2048, 0, 3968, 1280)]
    )
    return _service(root)


# 高度调整：底边锚定


def test_height_changes_keep_bottom_anchored_and_width_stable(scaled: SubtitleGuiService) -> None:
    root = scaled.root
    scaled._apply_window_height(300)
    assert (root.winfo_height(), root.winfo_y(), root.winfo_width()) == (300, 1550 - 300, 1200)  # 向上扩展，底边不动
    scaled._apply_window_height(50)
    assert (root.winfo_height(), root.winfo_y()) == (150, 1400)  # 缩短不低于默认高度
    scaled._apply_window_height(300)
    scaled._apply_window_height(0)  # 0 = 恢复默认高度
    assert (root.winfo_height(), root.winfo_y()) == (150, 1400)
    for _ in range(3):
        scaled._apply_window_height(240)  # geometry 宽高每次都会被 CTk 放大，反复调整不得让窗口变宽
    assert root.winfo_width() == 1200

    scaled._default_window_height_px = 0  # 映射前未测到实测高度：按配置逻辑高度兜底，不压到 0
    scaled._apply_window_height(10)
    assert root.winfo_height() == 100

    scaled.root._scale = 2.0  # 口径谎报系数时依赖实测校正收敛
    scaled._apply_window_height(300)
    assert abs(root.winfo_height() - 300) <= 2 and abs(root.winfo_width() - 1200) <= 2


@pytest.mark.parametrize(
    ("scale", "geometry", "bottom", "targets"),
    [
        (2.0, (1200, 150, 880, 1400), 1550, (257,) * 6),  # 整数缩放排除取整余数，位置必须逐次完全一致
        (1.25, (800, 125, 624, 1055), 1180, (61, 257, 61, 257, 61)),  # 真实 125% DPI
    ],
)
def test_repeated_targets_do_not_creep_the_window(scale, geometry, bottom, targets) -> None:
    root = FakeCtkRoot(*geometry, scale, screen=(2048, 2160), monitors=[PRIMARY_MONITOR])
    svc = _service(root, default_h=geometry[1], target_w=geometry[0], bottom=bottom)
    for target in targets:
        svc._apply_window_height(target)
        assert abs(root.winfo_y() + root.winfo_height() - bottom) <= 2, f"target={target} 底边漂移"
        assert root.winfo_width() == geometry[0]  # 宽度不得随高度调整被反复放大


def test_window_that_refuses_to_shrink_is_not_dragged_downwards() -> None:
    """配置逻辑高度 100 在 125% DPI 下落成 125 物理像素，请求更矮时窗口不缩；用回读高度反推 y 会每算一次下移一截。"""

    class StickyRoot(FakeCtkRoot):
        def geometry(self, spec):
            before = self._height
            super().geometry(spec)
            self._height = max(before, self._height)  # 只增不减

    svc = _service(StickyRoot(800, 125, 624, 1055, 1.25), default_h=125, bottom=1180)
    for i in range(4):
        svc._apply_window_height(61)
        assert svc.root.winfo_y() == 1055, f"第{i + 1}次调整把窗口下移"
        assert svc.root.winfo_y() + svc.root.winfo_height() == 1180


def test_missed_height_requests_are_retried_until_window_reaches_target() -> None:
    """窗口管理器丢掉/半途生效高度请求会让多行字幕只露一行半，延迟重试必须补到位。"""

    class StubbornRoot(FakeCtkRoot):
        calls = 0
        pending = None

        def geometry(self, spec):
            self.calls += 1
            super().geometry(spec)
            if self.calls <= 3:
                self._height = 300  # 前三次请求落在错误高度

        def after(self, delay, callback):
            self.pending = (delay, callback)

    root = StubbornRoot(800, 100, 624, 1080, 1.0, screen=(2048, 2160), monitors=[PRIMARY_MONITOR])
    svc = _service(root)
    svc._apply_window_height(306)
    assert root.winfo_height() == 300
    delay, callback = root.pending
    assert delay == 120
    callback()
    assert root.winfo_height() == 306 and root.winfo_y() + root.winfo_height() == 1180


# 初始摆放与测量


def test_initial_placement_measurement_and_user_position() -> None:
    placed = _service(FakeCtkRoot(1, 1, 0, 0, 1.25, screen=(2048, 1280)))
    placed._place_window()
    root = placed.root
    # 位置是物理像素、geometry 尺寸是逻辑单位：按物理像素折算后请求，配置宽度即物理宽度
    assert (root.winfo_width(), root.winfo_height()) == (800, 100)
    assert root.winfo_y() + root.winfo_height() == 1280 - 100 and root.winfo_x() == (2048 - 800) // 2
    assert placed._window_bottom_px == 1280 - 100 and placed._target_width_px == 800

    svc = _service(FakeCtkRoot(1, 1, 0, 0, 2.0, screen=(2048, 1280)), window_width=500, window_offset_y=96)
    svc._place_window()
    svc.root._width, svc.root._height, svc.root._x, svc.root._y = 1000, 200, 524, 900  # 模拟映射完成：实测 1000x200
    svc._measure_default_window_height()
    assert (svc._default_window_height_px, svc._target_width_px, svc._window_bottom_px) == (200, 1000, 1280 - 96)
    assert svc.root.geometry_specs[-1].endswith(f"+{1280 - 96 - 200}")  # 顶边按"锚点 - 实测高度"重算并下发

    unmapped = _service(FakeCtkRoot(1, 1, 0, 0, 1.0, screen=(2048, 1280)), default_h=0, window_offset_y=96)
    unmapped._bottom_offset_px = 96
    unmapped._measure_default_window_height()
    assert unmapped._default_window_height_px == 0  # 未映射窗口的陈旧尺寸不得记为高度下限

    moved = _service(FakeCtkRoot(800, 100, 300, 400, 1.0), default_h=0)
    moved._bottom_offset_px = 100
    moved._user_moved_window = True  # 启动后 200~300ms 的定位回调不得把用户刚拖走的窗口搬回原处
    moved._measure_default_window_height(remaining_attempts=3)
    assert (moved.root.winfo_x(), moved.root.winfo_y()) == (300, 400) and moved._default_window_height_px == 100


# 拖动与夹取


def test_drag_follows_pointer_target_not_readback_position() -> None:
    """按住拖动时字幕变长把窗口向上撑开，这段高度差不能被当成拖动距离。"""
    svc = _service()
    svc._start_move(Pointer(1000, 1130))
    svc._apply_window_height(306)  # 顶部上移、底边不动
    assert (svc.root.winfo_x(), svc.root.winfo_y()) == (624, 874)
    svc._on_move(Pointer(1040, 1180))  # 右移 40、下移 50
    assert svc.root.winfo_x() == 624 + 40, "横向位移被高度变化带偏"
    # 下移 50 会让底边越过显示器下沿（874+306+50 > 1280），夹取把底边钉在下沿
    assert svc.root.winfo_y() == 1280 - 306 and svc._window_bottom_px == 1280


def test_drag_does_not_double_count_displacement_while_window_manager_lags() -> None:
    class LazyRoot(FakeCtkRoot):
        """位置请求延迟生效：geometry 只记录，update_idletasks 才应用。"""

        _pending = None

        def geometry(self, spec):
            if spec.startswith("+"):
                self._pending = spec
            else:
                super().geometry(spec)

        def update_idletasks(self):
            if self._pending is not None:
                super().geometry(self._pending)
                self._pending = None

    svc = _service(LazyRoot(800, 100, 624, 1080, 1.0))
    svc._start_move(Pointer(1000, 1130))
    for step in range(1, 5):
        svc._on_move(Pointer(1000 + 10 * step, 1130))  # 每次只右移 10
    assert svc._move_target[0] == 624 + 40, "同一段位移被重复累加"


def test_drag_past_screen_edge_keeps_window_whole_on_screen() -> None:
    svc = _service()
    svc._start_move(Pointer(1000, 1130))
    svc._on_move(Pointer(4000, 4000))  # 指针拖到屏幕外
    assert (svc.root.winfo_x(), svc.root.winfo_y()) == (2048 - 800, 1280 - 100)  # 右下角顶到边界，窗口完整可见


def test_multi_monitor_drag_crosses_clamps_by_pointer_and_escapes_gaps() -> None:
    svc = _multi_monitor()
    svc._start_move(Pointer(1000, 1130))
    svc._on_move(Pointer(3000, 1130))  # 指针在主屏右沿之外（副屏上）
    assert svc.root.winfo_x() == 624 + 2000  # 夹取只按指针选边界，不被主屏右沿截断

    svc._pointer_pos = (2500, 600)
    x, y = svc._clamp_position(5000, 600)  # 指针在副屏、落点已越过副屏右沿：按副屏夹取，不弹回主屏
    assert (x, y) == (3968 - svc.root.winfo_width(), 600)

    gap = _multi_monitor()
    gap.root.monitors = [(0, 0, 2048, 1280), (2048, -900, 3968, 0)]  # L 形拼接，主屏右下是空洞
    gap._start_move(Pointer(1000, 1130))
    gap._on_move(Pointer(2600, 1900))
    x, y, w, h = gap.root.winfo_x(), gap.root.winfo_y(), gap.root.winfo_width(), gap.root.winfo_height()
    assert any(
        left <= x and top <= y and x + w <= right and y + h <= bottom for left, top, right, bottom in gap.root.monitors
    ), "窗口落进显示器空洞"


# 窗口越界时的拉回


def test_height_changes_pull_an_off_screen_window_back() -> None:
    off = _service(FakeCtkRoot(800, 100, 624, 1500, 1.0), bottom=1600)  # 锚点落在屏外（屏高 1280）
    off._apply_window_height(100)
    assert off.root.winfo_y() + off.root.winfo_height() <= 1280 and off.root.winfo_y() >= 0

    shrinking = _multi_monitor()
    shrinking.root._y, shrinking._window_bottom_px = 1250, 1350  # 窗口停在显示器下沿之外
    shrinking._apply_window_height(0)  # 字幕变少：缩短只能把窗口往屏内收，不能顶出屏外
    assert shrinking.root.winfo_y() + shrinking.root.winfo_height() <= 1280 and shrinking._window_bottom_px <= 1280

    growing = _multi_monitor()
    growing._start_move(Pointer(1000, 1130))
    growing._on_move(Pointer(1500, 2000))  # 往下拖到底
    assert growing.root.winfo_y() + growing.root.winfo_height() == 1280
    growing._apply_window_height(400)  # 字幕变长：长高的部分由顶边上移承担，底边锚点不越过下沿
    assert growing.root.winfo_y() == 1280 - 400 and growing.root.winfo_y() + growing.root.winfo_height() == 1280


# 渲染、自动隐藏与队列


class FakeLabel:
    """记录文本与重绘次数的字幕标签桩。"""

    def __init__(self, display_text: str = "") -> None:
        self.canvas = self
        self.container_frame = self
        self.display_text = display_text
        self._content_height_px = None
        self._last_lines = ["假行"]
        self.draws = 0
        self.scheduled: list = []

    def winfo_width(self) -> int:
        return 800

    def winfo_height(self) -> int:
        return 88

    def pack_info(self) -> dict:
        return {"pady": 6}

    def required_height(self) -> int:
        return 220

    def _canvas_height_need(self, required_px: int) -> int:
        return required_px - 12  # 画布外的内边距

    def after(self, delay, callback):
        self.scheduled.append((delay, callback))

    def _draw_text(self) -> None:
        self.draws += 1

    def configure_text(self, text: str | None = None, **_kwargs: object) -> None:
        if text is not None:
            self.display_text = text
        self._content_height_px = None


class UpdatingRoot(FakeCtkRoot):
    """带 update()/after() 的桩：真实 CTk 渲染期间会跑一次事件循环。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_update = None
        self.updates = 0
        self.scheduled = []

    def update(self):
        self.updates += 1
        if self.on_update is not None:
            self.on_update()

    def after(self, delay, callback):
        self.scheduled.append((delay, callback))
        return len(self.scheduled)


def _updating_service(**cfg) -> tuple[SubtitleGuiService, UpdatingRoot, FakeLabel]:
    root = UpdatingRoot(800, 100, 624, 900, 1.0, screen=(2048, 2160), monitors=[PRIMARY_MONITOR])
    svc = _service(root, **cfg)
    label = FakeLabel("旧文本")
    svc.text_label = label
    return svc, root, label


def test_configure_then_fit_sizes_window_and_draws_exactly_once() -> None:
    svc, root, label = _updating_service()
    assert svc._configure_then_fit("新文本") == 220  # 渲染高度由文本折行自己算，窗口按同一个值设
    assert label.display_text == "新文本"  # 文本先换上再量高度
    assert root.winfo_height() == 220 and root.updates == 1  # 改完窗口跑一次布局，画面尽快稳定
    assert label.draws == 1 and not label.scheduled  # 只绘制一次，不排任何延迟重绘/轮询兜底


def test_auto_hide_check_during_render_does_not_shrink_the_window() -> None:
    """渲染期间会跑事件循环，巡检若在其中判超时清空，多行字幕会被缩回默认高度裁掉。"""
    svc, root, label = _updating_service(auto_hide=True, fade_delay_ms=5000)
    svc.is_visible = True
    svc.last_voice_time_ms = 0  # 上一条字幕早已过去，巡检一跑就判超时
    during_update: list[int] = []

    def hook() -> None:
        svc._check_auto_hide()
        during_update.append(root.winfo_height())

    root.on_update = hook
    svc._update_subtitle_display("这是一条需要占满多行的新字幕文本，用来验证窗口不会被缩回")

    assert during_update and root.winfo_height() == 220
    assert label.display_text.startswith("这是一条") and svc.last_voice_time_ms > 0  # 计时器要在渲染前刷新


def test_clear_enqueues_none_sentinel_and_queue_dispatch_runs_in_consumer_thread() -> None:
    svc = _service(UpdatingRoot(800, 100, 624, 1080, 1.0, screen=(2048, 2160), monitors=[PRIMARY_MONITOR]))
    shown: list[str] = []
    cleared: list[bool] = []
    svc._update_subtitle_display = shown.append  # type: ignore[method-assign]
    svc._clear_content = lambda: cleared.append(True)  # type: ignore[method-assign]

    svc.push_subtitle("一条字幕")
    svc.clear_subtitle()  # 清空以 None 哨兵入队（线程安全），不触碰任何 Tk 对象
    svc._check_queue()

    assert shown == ["一条字幕"] and cleared == [True] and svc.text_queue.empty()
