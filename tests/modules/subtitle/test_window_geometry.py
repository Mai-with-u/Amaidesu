"""window_geometry 纯函数直测：夹取边界、显示器选屏、缩放口径解析。

几何数学与 Tk 无关，直接以矩形原语与数字驱动，不需要窗口桩。
"""

import pytest

from src.modules.subtitle.backends.window_geometry import (
    clamp_into_rect,
    clamp_position,
    clamp_top_to_monitor,
    keep_visible_px,
    monitor_under_point,
    nearest_monitor,
    parse_requested_size,
    primary_monitor_rect,
)

# 主屏 2048x1280 + 右侧副屏 1920x1280；L 形布局主屏右下是空洞
PRIMARY = (0, 0, 2048, 1280)
SECONDARY = (2048, 0, 3968, 1280)
DUAL = [PRIMARY, SECONDARY]
L_SHAPED = [PRIMARY, (2048, -900, 3968, 0)]


# geometry 口径解析


@pytest.mark.parametrize(
    ("geometry_string", "expected"),
    [
        ("800x100+10+20", (800, 100)),
        ("800x100", (800, 100)),
        ("+624+1080", (0, 0)),  # 纯位置串无尺寸段
        ("x100+0+0", (0, 100)),  # 缺宽按 0，高度段照解析
        ("axb+0+0", (0, 0)),  # 非数字
        ("", (0, 0)),
    ],
)
def test_parse_requested_size(geometry_string: str, expected: tuple[int, int]) -> None:
    assert parse_requested_size(geometry_string) == expected


# 可见下限


@pytest.mark.parametrize(
    ("size_px", "expected"),
    [(800, 400), (100, 50), (10, 40), (0, 40)],  # 小窗口按 40 像素下限
)
def test_keep_visible_px(size_px: int, expected: int) -> None:
    assert keep_visible_px(size_px) == expected


# 顶边纵向夹取


def test_clamp_top_to_monitor() -> None:
    rect = PRIMARY
    assert clamp_top_to_monitor(600, 100, rect) == 600  # 屏内不动
    assert clamp_top_to_monitor(-50, 100, rect) == 0  # 越上沿收到上沿
    assert clamp_top_to_monitor(2000, 100, rect) == 1280 - 100  # 底边不出下沿
    assert clamp_top_to_monitor(500, 2000, rect) == 0  # 窗口高于显示器时保顶边


# 显示器选屏


def test_monitor_under_point_picks_containing_then_nearest() -> None:
    assert monitor_under_point(100, 100, DUAL) == PRIMARY
    assert monitor_under_point(3000, 600, DUAL) == SECONDARY
    assert monitor_under_point(2100, 1500, DUAL) == SECONDARY  # 点不在任何屏内：取最近


def test_nearest_monitor_takes_min_squared_distance() -> None:
    assert nearest_monitor(2500, -500, L_SHAPED) == L_SHAPED[1]
    assert nearest_monitor(100, 100, DUAL) == PRIMARY  # 点在屏内距离为 0
    with pytest.raises(ValueError):
        nearest_monitor(0, 0, [])


def test_primary_monitor_rect_contains_origin() -> None:
    assert primary_monitor_rect(DUAL) == PRIMARY
    assert primary_monitor_rect([(100, 100, 300, 300)]) is None  # 无屏含原点


# 单屏位置夹取


def test_clamp_into_rect_keeps_window_inside_monitor() -> None:
    win_w, win_h = 800, 100
    assert clamp_into_rect(PRIMARY, 624, 1080, win_w, win_h) == (624, 1080)  # 屏内不动
    assert clamp_into_rect(PRIMARY, 2000, 600, win_w, win_h) == (2048 - 800, 600)  # 右探出按实际宽度回收
    assert clamp_into_rect(PRIMARY, 100, 2000, win_w, win_h) == (100, 1280 - 100)  # 底边不出下沿
    assert clamp_into_rect(PRIMARY, 100, -50, win_w, win_h) == (100, 0)  # 顶边不出上沿（放得下时）


def test_clamp_into_rect_oversized_window_keeps_part_visible() -> None:
    # 窗口 3000x2000 装不进 2048x1280：横向至少留一半在屏内（窗左沿退到屏左之外、
    # 右半可见），纵向保顶边可见
    x, y = clamp_into_rect(PRIMARY, 5000, 5000, 3000, 2000)
    assert x == -3000 + 1500  # 窗右沿 1500，屏内可见 1500 = 一半
    assert y == 0  # 保顶边可见


# 拖动位置夹取（指针选屏）


def test_clamp_position_selects_monitor_by_pointer() -> None:
    # 指针在副屏、落点越过了副屏右沿：按副屏夹取，不弹回主屏
    x, y = clamp_position(5000, 600, 2500, 600, DUAL, 800, 100)
    assert (x, y) == (3968 - 800, 600)


def test_clamp_position_escapes_monitor_gaps() -> None:
    # L 形拼接空洞（主屏右下）：落点与指针都落进空洞时夹到最近显示器内
    x, y = clamp_position(2600, 600, 2600, 600, L_SHAPED, 800, 100)
    inside = any(
        left <= x and top <= y and x + 800 <= right and y + 100 <= bottom for left, top, right, bottom in L_SHAPED
    )
    assert inside, "窗口落进显示器空洞"


def test_clamp_position_prefers_pointer_over_drop_point() -> None:
    # 指针在主屏、落点已在副屏：按指针所在主屏夹取
    x, _ = clamp_position(3000, 600, 1000, 600, DUAL, 800, 100)
    assert x == 2048 - 800
