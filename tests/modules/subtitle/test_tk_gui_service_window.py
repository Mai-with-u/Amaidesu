"""SubtitleGuiService 窗口几何测试。

防止回归两类问题：
1. 固定高度窗口下多行字幕居中绘制时首尾行落在窗口外被裁掉；
2. 窗口底边随字幕更新逐次下移、最终整窗跑出屏幕外。

桩 Root 模拟 CustomTkinter 的 geometry 口径——宽高参数按窗口缩放放大后
执行，位置不缩放，winfo 系列返回物理像素。早期桩把位置也按缩放处理，
与真实 CTk 不符，因而漏掉了坐标口径混读导致的漂移。
"""

import pytest

from src.modules.subtitle.backends.tk_gui_service import SubtitleGuiService


class FakeCtkRoot:
    """模拟 CTk 窗口：geometry 宽高按 scale 放大生效，位置原样生效。"""

    def __init__(self, width, height, x, y, scale, screen=(2048, 1280)):
        self._width = width
        self._height = height
        self._x = x
        self._y = y
        self._scale = scale
        self._screen = screen
        self.geometry_specs = []

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
    svc.root = FakeCtkRoot(width=1200, height=150, x=880, y=1400, scale=1.5)
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
    svc.root = FakeCtkRoot(width=1200, height=150, x=880, y=1400, scale=2.0)
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


def test_drag_uses_screen_coordinates_not_relative_offsets():
    """按住拖动期间窗口被高度调整挪走时，位移必须按屏幕坐标算。

    真实事故形态：鼠标停在字幕条上按住不放，字幕变长把窗口向上撑开，指针在
    窗口内的相对位置随之改变；按相对位移续算会把这段高度差当成拖动距离，每
    次鼠标事件都把窗口甩出一截。
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
    assert svc.root.winfo_x() == 624 + 40
    assert svc.root.winfo_y() == 874 + 50
    assert svc._window_bottom_px == svc.root.winfo_y() + svc.root.winfo_height()


def test_converges_when_reported_scale_differs(service):
    # 窗口缩放口径谎报系数时依赖实测校正收敛：实际执行 2.0 缩放，口径却报 1.5
    service.root._scale = 2.0
    service._apply_window_height(300)
    assert abs(service.root.winfo_height() - 300) <= 2
    assert abs(service.root.winfo_width() - 1200) <= 2
