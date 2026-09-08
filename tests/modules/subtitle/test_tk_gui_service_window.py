"""SubtitleGuiService 窗口高度自适应测试。

防止回归：固定高度窗口下多行字幕居中绘制时首尾行落在窗口外被裁掉。
桩 Root 模拟 CustomTkinter 的 geometry 缩放口径——宽高参数按 DPI 缩放
执行，位置不缩放，winfo 系列返回缩放后的物理像素。
"""

import pytest

from src.modules.subtitle.backends.tk_gui_service import SubtitleGuiService


class FakeCtkRoot:
    """模拟 CTk 窗口：geometry 宽高按 scale 缩放生效，位置原样生效。"""

    def __init__(self, width, height, x, y, scale):
        self._width = width
        self._height = height
        self._x = x
        self._y = y
        self._scale = scale
        self.geometry_specs = []

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def winfo_fpixels(self, _spec):
        return 96.0 * self._scale

    def update_idletasks(self):
        pass

    def geometry(self, spec):
        self.geometry_specs.append(spec)
        size, _, pos = spec.partition("+")
        w_str, _, _h_str = size.partition("x")
        x_str, _, y_str = pos.partition("+")
        self._width = round(int(w_str) * self._scale)
        self._height = round(int(_h_str) * self._scale)
        self._x = int(x_str)
        self._y = int(y_str)


@pytest.fixture
def service():
    svc = SubtitleGuiService(config={"window_width": 800, "window_height": 100, "window_offset_y": 100})
    svc.root = FakeCtkRoot(width=1200, height=150, x=880, y=1400, scale=1.5)
    svc._default_window_height_px = 150
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


def test_height_clamped_at_screen_top(service):
    # 窗口贴近屏幕顶部时向上扩展空间不足，y 不得为负
    service.root._y = 20
    service._apply_window_height(300)
    assert service.root.winfo_y() == 0
    assert service.root.winfo_height() == 300


def test_lazy_default_derived_from_config_scale(service):
    # 默认高度未记录时按"配置逻辑高度 × 实测缩放"推算（100 × 1.5 = 150）
    service._default_window_height_px = None
    service._apply_window_height(50)
    assert service._default_window_height_px == 150
    assert service.root.winfo_height() == 150
    assert service.root.winfo_y() == 1400


def test_width_not_re_scaled_on_repeated_adjusts(service):
    # geometry 宽高每次调用都会被 CTk 缩放，反复调整不得让窗口变宽
    for _ in range(3):
        service._apply_window_height(240)
    assert service.root.winfo_width() == 1200


def test_converges_when_reported_scale_differs(service):
    # winfo_fpixels 谎报缩放系数时依赖实测校正收敛：
    # 实际执行 2.0 缩放，探测接口却报 1.5
    service.root._scale = 2.0
    service.root.winfo_fpixels = lambda _spec: 96.0 * 1.5
    service._apply_window_height(300)
    assert abs(service.root.winfo_height() - 300) <= 2
    assert abs(service.root.winfo_width() - 1200) <= 2
