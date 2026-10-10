"""SubtitleGuiService 窗口几何测试。

防止回归三类问题：
1. 固定高度窗口下多行字幕居中绘制时首尾行落在窗口外被裁掉；
2. 窗口底边随字幕更新逐次下移、最终整窗跑出屏幕外；
3. 多显示器下窗口被夹在主屏边界内、拖不到副屏（或落进显示器之间的空洞）。

桩 Root 模拟 CustomTkinter 的 geometry 口径——宽高参数按窗口缩放放大后
执行，位置不缩放，winfo 系列返回物理像素。早期桩把位置也按缩放处理，
与真实 CTk 不符，因而漏掉了坐标口径混读导致的漂移。屏幕尺寸设得足够高，
让断言只考验"锚定/夹取"本身而不被夹取边界干扰。
"""

import pytest

from src.modules.subtitle.backends.tk_gui_service import SubtitleGuiService

# 假显示器布局：主屏 2048x2160（高度放大以免夹取干扰锚定断言），副屏接在主屏右侧
PRIMARY_MONITOR = (0, 0, 2048, 2160)
SECOND_MONITOR = (2048, 0, 3968, 2160)


class FakeCtkRoot:
    """模拟 CTk 窗口：geometry 宽高按 scale 放大生效，位置原样生效。

    ``monitors`` 给出各显示器矩形（物理像素），缺省视为单显示器=窗口所在屏；
    服务取屏幕范围时优先读窗口上的 ``_screen_bounds``，这里注入假桌面，免得
    断言受运行机器实际显示器布局影响。
    """

    def __init__(self, width, height, x, y, scale, screen=(2048, 1280), monitors=None):
        self._width = width
        self._height = height
        self._x = x
        self._y = y
        self._scale = scale
        self._screen = screen
        self._screen_bounds = (0, 0, screen[0], screen[1])
        self.geometry_specs = []
        self.monitors = list(monitors) if monitors else [(0, 0, screen[0], screen[1])]

    def _get_window_scaling(self):
        # 真实 CTk 由 ScalingTracker 注入窗口缩放系数（几何尺寸按它放大，
        # 位置不缩放），服务据此把物理像素尺寸折算回 geometry 的逻辑单位
        return self._scale

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

    def winfo_fpixels(self, _spec):
        return 96.0 * self._scale

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
            self._x = int(x_str)
            self._y = int(y_str)


@pytest.fixture
def service():
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    # 屏幕给足高度，让 1550 的底边仍落在屏内，便于断言锚定行为本身
    svc.root = FakeCtkRoot(
        width=1200,
        height=150,
        x=880,
        y=1400,
        scale=1.5,
        screen=(2048, 2160),
        monitors=[PRIMARY_MONITOR],
    )
    svc._default_window_height_px = 150
    svc._target_width_px = 1200
    svc._window_bottom_px = 1550
    return svc


def test_grow_keeps_bottom_anchored(service):
    service._apply_window_height(300)
    assert service.root.winfo_height() == 300
    # 底边 1400+150=1550 不动，向上扩展
    assert service.root.winfo_y() == 1550 - 300
    assert service.root.winfo_width() == 1200


def test_shrink_floors_at_default_height(service):
    service._apply_window_height(50)
    assert service.root.winfo_height() == 150
    assert service.root.winfo_y() == 1400


def test_restore_default_height_with_zero(service):
    service._apply_window_height(300)
    service._apply_window_height(0)
    assert service.root.winfo_height() == 150
    assert service.root.winfo_y() == 1400


def test_repeated_same_target_does_not_creep():
    """同样的目标高度反复下发不得让窗口逐次下移（底边锚定的漂移回归）。

    用整数缩放（2.0）排除请求逻辑单位与物理像素互为倒数换算的取整余数，
    这样位置必须逐次完全一致；有累加漂移时第一次复算就会偏出容差。
    """
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(
        width=1200, height=150, x=880, y=1400, scale=2.0, screen=(2048, 2160), monitors=[PRIMARY_MONITOR]
    )
    svc._default_window_height_px = 150
    svc._target_width_px = 1200
    svc._window_bottom_px = 1550
    svc._apply_window_height(257)
    top = svc.root.winfo_y()
    assert svc.root.winfo_height() == 256
    for _ in range(5):
        svc._apply_window_height(257)
        assert svc.root.winfo_y() == top, "窗口位置被逐次带偏"


def test_bottom_edge_drift_with_fractional_scale():
    """缩放系数非整数（真实 125% DPI 为 1.25）时同样不得漂移。"""
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(width=800, height=125, x=624, y=1055, scale=1.25)
    svc._default_window_height_px = 125
    svc._target_width_px = 800
    svc._window_bottom_px = 1180
    for target in (61, 257, 61, 257, 61):
        svc._apply_window_height(target)
        now_bottom = svc.root.winfo_y() + svc.root.winfo_height()
        assert abs(now_bottom - 1180) <= 2, f"target={target} 底边漂移到 {now_bottom}"
        assert svc.root.winfo_width() == 800, "宽度不得随高度调整被反复放大"
    # 回到短文本时窗口整体回到起始尺寸
    assert svc.root.winfo_height() == 125


def test_no_downward_creep_when_window_refuses_to_shrink():
    """窗口不缩（实测高度高于请求高度）时，反复调整不得把窗口一路往下带。

    真实事故形态：配置逻辑高度 100 在 125% DPI 下落成 125 物理像素，而请求
    的高度低于实测高度时窗口不缩；此时任何"用回读高度反推 y"的写法都会每算
    一次向下挪一截，最终整窗跑出屏幕外。
    """

    class StickyRoot(FakeCtkRoot):
        """只增不减的窗口：请求更矮的高度时保持原高。"""

        def geometry(self, spec):
            height_before = self._height
            super().geometry(spec)
            self._height = max(height_before, self._height)

    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = StickyRoot(width=800, height=125, x=624, y=1055, scale=1.25)
    svc._default_window_height_px = 125
    svc._target_width_px = 800
    svc._window_bottom_px = 1180
    for i in range(4):
        svc._apply_window_height(61)
        assert svc.root.winfo_y() == 1055, f"第{i + 1}次调整把窗口下移到 {svc.root.winfo_y()}"
        assert svc.root.winfo_y() + svc.root.winfo_height() == 1180


def test_place_window_bottoms_at_configured_offset():
    """初始摆放：底边距屏幕底部 window_offset_y，水平居中。

    位置是物理像素、geometry 尺寸是逻辑单位（实测 CTk：请求 800×100 →
    物理 1000×125）。尺寸按物理像素折算后请求，配置宽度即物理宽度。
    """
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(width=1, height=1, x=0, y=0, scale=1.25, screen=(2048, 1280))
    svc._place_window()
    assert svc.root.winfo_width() == 800
    assert svc.root.winfo_height() == 100
    assert svc.root.winfo_y() + svc.root.winfo_height() == 1280 - 100
    assert svc.root.winfo_x() == (2048 - 800) // 2
    assert svc._window_bottom_px == 1280 - 100
    # 物理宽度记为尺寸基准，后续高度调整不再重发逻辑宽度
    assert svc._target_width_px == 800


def test_measure_default_height_uses_physical_height():
    """窗口映射后按实测高度记下限，并把底边锚点校正到配置偏移处。

    配置逻辑高度 100 在缩放 2.0 下实测 200 物理像素：下限按实测值记录，锚点
    落在 1280-96=1184；若沿用配置逻辑高度算位置，底边会落到 1084。
    """
    svc = SubtitleGuiService(config={"window_width": 500, "window_height": 100, "window_offset_y": 96})
    # 映射前 winfo 系列是 Tk 初始值，_place_window 只发出请求不作定位
    svc.root = FakeCtkRoot(width=1, height=1, x=0, y=0, scale=2.0, screen=(2048, 1280))
    svc._place_window()
    # 模拟窗口映射完成：实测 1000x200
    svc.root._width, svc.root._height = 1000, 200
    svc.root._x, svc.root._y = 524, 900
    svc._measure_default_window_height()
    assert svc._default_window_height_px == 200
    assert svc._target_width_px == 1000
    assert svc._window_bottom_px == 1280 - 96
    # 顶边按"锚点 - 实测高度"重算并下发（桩的请求把逻辑单位换算回物理像素
    # 会带取整余数，位置精度由真实 tk 的端到端验证覆盖）
    assert svc.root.geometry_specs[-1].endswith(f"+{1280 - 96 - 200}")


def test_measure_skips_unmapped_window():
    """窗口尚未映射（回读仍是 Tk 初始尺寸）时不得据陈旧值挪动窗口。"""
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 96})
    svc.root = FakeCtkRoot(width=1, height=1, x=0, y=0, scale=1.0, screen=(2048, 1280))
    svc._bottom_offset_px = 96
    svc._measure_default_window_height()
    assert svc._default_window_height_px == 0, "未映射窗口的陈旧尺寸不得记为高度下限"


def test_default_height_falls_back_when_not_measured(service):
    # 窗口映射前未测到实测高度时按配置逻辑高度兜底，不得把窗口压到 0
    service._default_window_height_px = 0
    service._apply_window_height(10)
    assert service.root.winfo_height() == 100


def test_width_not_re_scaled_on_repeated_adjusts(service):
    # geometry 宽高每次调用都会被 CTk 放大，反复调整不得让窗口变宽
    for _ in range(3):
        service._apply_window_height(240)
    assert service.root.winfo_width() == 1200


def test_drag_follows_pointer_while_window_is_resized():
    """按住拖动期间字幕变长把窗口挪走时，续拖必须按目标位置累加。

    真实事故形态：鼠标停在字幕条上按住不放，字幕变长把窗口向上撑开，指针在
    窗口内的相对位置随之改变；若按窗口回读位置续算，这段高度差会被当成拖动
    距离，每来一个鼠标事件窗口就被甩出一截，快速拖动时更明显（窗口管理器
    响应 geometry 有延迟，回读到的还是旧位置，同一段位移被反复累加）。

    往下拖到底时位置被夹取接管（底边不得越过显示器下沿），横向位移与"没有把
    高度变化算成拖动距离"这两件事仍必须成立。
    """

    class Event:
        def __init__(self, x_root, y_root):
            self.x_root = x_root
            self.y_root = y_root

    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(width=800, height=100, x=624, y=1080, scale=1.0, screen=(2048, 1280))
    svc._default_window_height_px = 100
    svc._target_width_px = 800
    svc._window_bottom_px = 1180
    svc._start_move(Event(x_root=1000, y_root=1130))  # 指针落在窗口中部
    svc._apply_window_height(306)  # 字幕变长，窗口顶部上移、底边不动
    assert (svc.root.winfo_x(), svc.root.winfo_y()) == (624, 874)
    svc._on_move(Event(x_root=1040, y_root=1180))  # 指针右移 40、下移 50
    # 位置按拖动起点的窗口位置累加指针位移，中途的高度调整不参与计算
    assert svc.root.winfo_x() == 624 + 40, "横向位移被高度变化带偏"
    # 下移 50 会让底边越过显示器下沿（874+306+50 > 1280），夹取把底边钉在下沿：
    # 位移本身没有打折，折扣来自夹取
    assert svc.root.winfo_y() == 1280 - 306, "续拖把窗口高度变化当成了拖动距离"
    assert svc.root.winfo_y() + svc.root.winfo_height() == 1280
    assert svc._window_bottom_px == svc.root.winfo_y() + svc.root.winfo_height()


def test_drag_accumulates_target_position_without_double_counting():
    """窗口管理器还没把窗口挪到位时，后续鼠标事件不得重复累加同一段位移。"""

    class Event:
        def __init__(self, x_root, y_root):
            self.x_root = x_root
            self.y_root = y_root

    class LazyRoot(FakeCtkRoot):
        """位置请求延迟生效：geometry 只记录请求，update_idletasks 才应用。"""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pending = None

        def geometry(self, spec):
            if spec.startswith("+"):
                self._pending = spec
            else:
                super().geometry(spec)

        def update_idletasks(self):
            if self._pending is not None:
                super().geometry(self._pending)
                self._pending = None

    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = LazyRoot(width=800, height=100, x=624, y=1080, scale=1.0, screen=(2048, 1280))
    svc._target_width_px = 800
    svc._start_move(Event(x_root=1000, y_root=1130))
    for step in range(1, 5):
        svc._on_move(Event(x_root=1000 + 10 * step, y_root=1130))  # 每次只右移 10
    # 四次事件累计 40，而不是按未生效的回读位置重复累加
    assert svc._move_target[0] == 624 + 40, "同一段位移被重复累加"


def test_converges_when_reported_scale_differs(service):
    # 窗口缩放口径谎报系数时依赖实测校正收敛：实际执行 2.0 缩放，口径却报 1.5
    service.root._scale = 2.0
    service._apply_window_height(300)
    assert abs(service.root.winfo_height() - 300) <= 2
    assert abs(service.root.winfo_width() - 1200) <= 2


def test_configure_then_fit_sizes_window_and_draws_once():
    """换文本：算高度 → 改窗口 → 按算好的高度绘制一次，不排任何延迟重绘。

    渲染高度由文本折行自己算，窗口高度按同一个值设，因此不存在"画布还没跟上"的
    时序问题；也不需要轮询或延迟兜底。
    """

    class FakeLayout:
        def pack_info(self):
            return {"pady": 6}

    class FakeRoot(FakeCtkRoot):
        def __init__(self, label, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._label = label
            self.updates = 0

        def update(self):
            self.updates += 1

    class FakeLabel:
        def __init__(self):
            self.canvas = self
            self.container_frame = FakeLayout()
            self.display_text = "旧文本"
            self.draws = 0
            self.scheduled = []

        def winfo_height(self):
            return 147

        def required_height(self):
            return 159

        def _canvas_height_need(self, required_px):
            # 画布外的内边距：窗口 159 对应画布 147
            return required_px - 12

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

        def _draw_text(self):
            self.draws += 1

    label = FakeLabel()
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    root = FakeRoot(
        label, width=800, height=100, x=624, y=900, scale=1.0, screen=(2048, 2160), monitors=[PRIMARY_MONITOR]
    )
    svc.root = root
    svc.text_label = label
    svc._default_window_height_px = 100
    svc._target_width_px = 800
    svc._window_bottom_px = 1180

    required = svc._configure_then_fit("新文本")
    assert label.display_text == "新文本", "文本要先换上再量高度"
    assert required == 159
    assert root.winfo_height() == 159, "窗口高度按算出的高度设置"
    assert root.updates == 1, "改完窗口跑一次布局，画面尽快稳定"
    assert label.draws == 1, "只绘制一次"
    assert not label.scheduled, "不需要任何延迟重绘/轮询兜底"


def test_retries_when_height_request_keeps_missing():
    """三次请求都没把高度调到目标时，延迟重试必须把窗口补到位。

    真实事故形态：窗口管理器丢掉/半途生效一次高度请求，窗口停在中间高度，
    多行字幕只露出中间一行半——用户看到的就是"字被截断"。
    """

    class StubbornRoot(FakeCtkRoot):
        """前三次高度请求都只生效到错误高度，之后正常。"""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls = 0

        def geometry(self, spec):
            size, _, _pos = spec.partition("+")
            _w, _, h_str = size.partition("x")
            self.calls += 1
            if self.calls <= 3:
                super().geometry(spec)
                self._height = 300  # 落在错误高度上
                return
            super().geometry(spec)

        def after(self, delay, callback):
            self.pending = (delay, callback)

        def run_pending(self):
            delay, callback = self.pending
            assert delay == 120
            callback()

    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = StubbornRoot(
        width=800, height=100, x=624, y=1080, scale=1.0, screen=(2048, 2160), monitors=[PRIMARY_MONITOR]
    )
    svc._default_window_height_px = 100
    svc._target_width_px = 800
    svc._window_bottom_px = 1180
    svc._apply_window_height(306)
    assert svc.root.winfo_height() == 300, "前提：这一轮没能调到目标高度"
    svc.root.run_pending()
    assert svc.root.winfo_height() == 306, "延迟重试没把窗口补到目标高度"
    assert svc.root.winfo_y() + svc.root.winfo_height() == 1180


def test_startup_placement_leaves_user_position_alone():
    """用户已经亲手挪过窗口后，启动期的延迟定位不得把窗口搬回原处。

    真实事故形态：启动后 200~300ms 内的一次定位回调仍在途中，用户此时小幅
    拖动，回调按旧锚点重摆窗口，看起来就是"轻轻一拖就飞出去"。
    """
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(width=800, height=100, x=300, y=400, scale=1.0, screen=(2048, 1280))
    svc._bottom_offset_px = 100
    svc._user_moved_window = True
    svc._measure_default_window_height(remaining_attempts=3)
    assert (svc.root.winfo_x(), svc.root.winfo_y()) == (300, 400), "自动定位覆盖了用户摆放的位置"
    assert svc._default_window_height_px == 100, "高度下限仍要按实测记录"


def test_height_adjust_keeps_window_on_screen():
    """窗口被拖/漂到屏幕外时，高度调整必须把窗口整条拉回显示器内。

    否则底边锚点会把窗口一直固定在屏幕外，用户再也抓不回来。
    """
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(width=800, height=100, x=624, y=1500, scale=1.0, screen=(2048, 1280))
    svc._default_window_height_px = 100
    svc._target_width_px = 800
    svc._window_bottom_px = 1600  # 锚点落在屏幕外（屏高 1280）
    svc._apply_window_height(100)
    assert svc.root.winfo_y() + svc.root.winfo_height() <= 1280, "窗口底边仍在屏幕外"
    assert svc.root.winfo_y() >= 0


def test_drag_past_screen_edge_keeps_window_whole_on_screen():
    """拖到屏幕边缘时窗口必须整体留在显示器内，不得只露一角。"""

    class Event:
        def __init__(self, x_root, y_root):
            self.x_root = x_root
            self.y_root = y_root

    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(width=800, height=100, x=624, y=1080, scale=1.0, screen=(2048, 1280))
    svc._target_width_px = 800
    svc._start_move(Event(x_root=1000, y_root=1130))
    svc._on_move(Event(x_root=4000, y_root=4000))  # 指针拖到屏幕外
    # 右下角顶到显示器边界为止，窗口完整可见（旧的"一角可见"规则会让它只剩
    # 45 像素在屏内，用户看到的就是"被拖到屏幕外面了"）
    assert svc.root.winfo_x() + svc.root.winfo_width() <= 2048
    assert svc.root.winfo_y() + svc.root.winfo_height() <= 1280
    assert svc.root.winfo_x() == 2048 - 800
    assert svc.root.winfo_y() == 1280 - 100


class _DragEvent:
    """最小拖动事件桩：夹取只看屏幕坐标。"""

    def __init__(self, x_root: int, y_root: int) -> None:
        self.x_root = x_root
        self.y_root = y_root


def _multi_monitor_service() -> SubtitleGuiService:
    """主屏 2048x1280 + 右侧副屏 1920x1280 的服务实例（窗口初始在主屏右侧）。"""
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(
        width=800,
        height=100,
        x=624,
        y=1080,
        scale=1.0,
        screen=(3968, 1280),
        monitors=[(0, 0, 2048, 1280), (2048, 0, 3968, 1280)],
    )
    svc._default_window_height_px = 100
    svc._target_width_px = 800
    svc._window_bottom_px = 1180
    return svc


def test_drag_crosses_into_second_monitor():
    """窗口必须能被拖到第二台显示器上。

    真实事故形态：夹取按"主屏右沿"算边界，指针还在主屏上时窗口就先顶住了，
    再往右拖纹丝不动——副屏完全用不了。拖动只按指针位置选边界：指针落在副屏
    上，窗口就可以整条停在副屏里。
    """
    svc = _multi_monitor_service()
    svc._start_move(_DragEvent(x_root=1000, y_root=1130))
    svc._on_move(_DragEvent(x_root=3000, y_root=1130))  # 指针在主屏右沿之外（副屏上）
    assert svc.root.winfo_x() > 2048, "窗口被主屏边界挡住了，进不到副屏"
    # 落点由"上一步目标 + 指针位移"算出，不被主屏右沿截断
    assert svc.root.winfo_x() == 624 + 2000


def test_drag_into_monitor_gap_falls_back_to_visible_area():
    """显示器拼不成矩形时，拖进空洞的窗口必须被拉回到有显示器的区域。

    主屏右下角与副屏之间的空洞没有任何显示器覆盖：夹取到空洞里窗口既看不见
    也点不到，而无边框窗没有任务栏入口，等于丢失。
    """
    svc = _multi_monitor_service()
    # 副屏画在主屏上方（上/右侧拼成 L 形），主屏右下的区域没有任何显示器
    svc.root.monitors = [(0, 0, 2048, 1280), (2048, -900, 3968, 0)]
    svc._start_move(_DragEvent(x_root=1000, y_root=1130))
    svc._on_move(_DragEvent(x_root=2600, y_root=1900))  # 拖到主屏右下方的空洞
    x, y = svc.root.winfo_x(), svc.root.winfo_y()
    w, h = svc.root.winfo_width(), svc.root.winfo_height()
    fully_on_monitor = any(
        left <= x and y >= top and x + w <= right and y + h <= bottom for left, top, right, bottom in svc.root.monitors
    )
    assert fully_on_monitor, f"窗口没有完整落在任何一台显示器上: ({x},{y})"


def test_clamp_picks_monitor_under_pointer():
    """夹取按指针所在显示器算边界，而不是按落点——否则窗口会在显示器间跳。"""
    svc = _multi_monitor_service()
    # 指针在副屏、落点却已越过副屏右沿：应按副屏夹取（整窗停在副屏右沿），
    # 而不是按落点判定后弹回主屏
    svc._pointer_pos = (2500, 600)
    x, y = svc._clamp_position(5000, 600)
    assert x == 3968 - svc.root.winfo_width(), "窗口没被夹在指针所在的副屏上"
    assert y == 600


def test_shrinking_subtitle_pulls_window_back_inside():
    """字幕变少导致窗口缩短时，不得把窗口顶到屏幕外。

    真实事故形态：窗口被停在显示器下沿之外（显示器布局变过、或用户拖到边界
    附近），底边锚点落在屏外；字幕变少后窗口从底边向上缩短，顶边——也就是唯一
    还看得见的部分——跟着往下走，窗口彻底滑出屏幕。锚点越界时先收回显示器内
    再改高度，缩短只会把窗口往屏内收。
    """
    svc = _multi_monitor_service()
    svc.root._y = 1250  # 窗口停在显示器下沿之外（下沿 1280，高 100）
    svc._window_bottom_px = 1350
    svc._apply_window_height(0)  # 字幕变少，回落到默认高度
    assert svc.root.winfo_y() + svc.root.winfo_height() <= 1280, "窗口被推出屏幕外"
    assert svc.root.winfo_y() >= 0
    assert svc._window_bottom_px <= 1280, "底边锚点仍停在屏外，下一次缩短还会把窗口带走"


def test_growing_subtitle_keeps_bottom_on_screen():
    """窗口拖到显示器下沿后字幕变长，必须向上长而不是把底边顶出屏外。

    真实事故形态：窗口按"底边锚定"放在下沿，内容变高时顶边按锚点算出屏外，
    窗口就有半截露在屏幕下面，看起来就是"卡在外面了"。底边锚点不越过下沿是
    硬约束，长高的部分由顶边上移承担。
    """
    svc = _multi_monitor_service()
    svc._start_move(_DragEvent(x_root=1000, y_root=1130))
    svc._on_move(_DragEvent(x_root=1500, y_root=2000))  # 往下拖到底
    assert svc.root.winfo_y() + svc.root.winfo_height() == 1280, "前提：底边被夹在下沿"
    svc._apply_window_height(400)  # 多行字幕
    assert svc.root.winfo_y() == 1280 - 400, "长高的部分没有由顶边上移承担"
    assert svc.root.winfo_y() >= 0, "窗口顶边跑到屏幕外"
    assert svc.root.winfo_y() + svc.root.winfo_height() == 1280


class FakeLabelForAutoHide:
    """供自动隐藏用例使用：可记录文本、画布高度与重绘次数。"""

    def __init__(self, display_text: str = "") -> None:
        self.canvas = self
        self.container_frame = self
        self.display_text = display_text
        self._content_height_px = None
        self._last_lines = ["假行"]
        self.draws = 0

    def winfo_width(self) -> int:
        return 800

    def winfo_height(self) -> int:
        return 88

    def pack_info(self) -> dict:
        return {"pady": 6}

    def required_height(self) -> int:
        return 220

    def _canvas_height_need(self, required_px: int) -> int:
        return required_px - 12

    def _draw_text(self) -> None:
        self.draws += 1

    def configure_text(self, text: str | None = None, **_kwargs: object) -> None:
        if text is not None:
            self.display_text = text
        self._content_height_px = None


class FakeRootWithUpdate(FakeCtkRoot):
    """带 update()/after() 的桩：真实 CTk 在渲染期间会跑一次事件循环。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_update = None
        self.scheduled = []

    def update(self):
        if self.on_update is not None:
            self.on_update()

    def after(self, delay, callback):
        # 服务的巡检/重试排程：本用例不真的排，只记录
        self.scheduled.append((delay, callback))
        return len(self.scheduled)


def test_auto_hide_does_not_shrink_window_while_updating_text():
    """更新字幕的过程中，自动隐藏巡检不得把窗口缩回默认高度。

    真实事故形态：上一条字幕已经过去几秒，新字幕到来时先渲染再刷新计时器，而渲染
    过程会跑一次事件循环（root.update）；巡检在其中判超时、按"清空"把窗口缩回
    100 高，多行字幕于是被裁——探针实测：窗口长到 318 后 7 秒缩回 100，而落盘的贴
    图仍是 6 行 306 高，画布只有 88。
    """
    svc = SubtitleGuiService(
        config={
            "window_width": 800,
            "window_height": 100,
            "window_offset_y": 100,
            "auto_hide": True,
            "fade_delay_ms": 5000,
        }
    )
    root = FakeRootWithUpdate(
        width=800, height=100, x=624, y=1080, scale=1.0, screen=(2048, 2160), monitors=[PRIMARY_MONITOR]
    )
    svc.root = root
    label = FakeLabelForAutoHide()
    svc.text_label = label
    svc._default_window_height_px = 100
    svc._target_width_px = 800
    svc._window_bottom_px = 1160
    svc.is_visible = True
    svc.last_voice_time_ms = 0  # 上一条字幕早就过去了，巡检一跑就判超时
    # 渲染期间事件循环会跑巡检：用 update() 钩子模拟这一刻
    runs_during_update: list[int] = []

    def update_hook() -> None:
        svc._check_auto_hide()
        runs_during_update.append(root.winfo_height())

    root.on_update = update_hook

    svc._update_subtitle_display("这是一条需要占满多行的新字幕文本，用来验证窗口不会被缩回")

    assert runs_during_update, "前提：渲染期间确实跑了事件循环"
    assert root.winfo_height() == 220, "渲染期间被自动隐藏缩回了默认高度"
    assert label.display_text.startswith("这是一条"), "文本被巡检清空了"
    assert svc.last_voice_time_ms > 0, "计时器要在渲染前就刷新"


def test_clear_subtitle_enqueues_none_sentinel():
    """清空请求以 None 哨兵入队（线程安全），不触碰任何 Tk 对象。"""
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})

    svc.clear_subtitle()

    assert svc.text_queue.get_nowait() is None
    assert svc.text_queue.empty()


def test_check_queue_dispatches_text_and_clear_sentinel():
    """队列分发：None 哨兵执行清空、字符串按字幕显示，均在消费线程完成。"""
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    root = FakeRootWithUpdate(
        width=800, height=100, x=624, y=1080, scale=1.0, screen=(2048, 2160), monitors=[PRIMARY_MONITOR]
    )
    svc.root = root
    shown: list[str] = []
    cleared: list[bool] = []
    svc._update_subtitle_display = shown.append  # type: ignore[method-assign]
    svc._clear_content = lambda: cleared.append(True)  # type: ignore[method-assign]

    svc.push_subtitle("一条字幕")
    svc.clear_subtitle()
    svc._check_queue()

    assert shown == ["一条字幕"], "文本指令应分发给字幕显示"
    assert cleared == [True], "None 哨兵应分发给清空"
    assert svc.text_queue.empty()
