"""vlm_xy_to_screen 坐标三步反算的单元测试。"""

from src.agents.text_adv.coords import vlm_xy_to_screen


def test_no_scaling() -> None:
    """未缩放（sent_width 等于 region 宽度）时坐标原样透传加偏移。"""
    result = vlm_xy_to_screen((10, 20), 800, (0, 0, 800, 600), (0, 0))
    assert result == (10, 20)


def test_scaled() -> None:
    """sent_width 小于 region 宽度时按比例反除缩放。"""
    # 缩放系数 0.5：VLM 坐标 (200, 300) → region 内 (400, 600)
    result = vlm_xy_to_screen((200, 300), 640, (100, 50, 1280, 720), (0, 0))
    assert result == (500, 650)


def test_multi_monitor_offset() -> None:
    """显示器原点非零时叠加 monitor_origin。"""
    result = vlm_xy_to_screen((200, 300), 640, (100, 50, 1280, 720), (1920, 0))
    assert result == (2420, 650)


def test_out_of_bounds_returns_none() -> None:
    """结果落在该显示器矩形外时返回 None。"""
    # 反算 x = 5000*2 + 0 = 10000，超出显示器宽度 1920
    assert vlm_xy_to_screen((5000, 5000), 960, (0, 0, 1920, 1080), (0, 0)) is None
    # 恰好在右/下边界上（半开区间）视为越界
    assert vlm_xy_to_screen((1920, 1080), 1920, (0, 0, 1920, 1080), (0, 0)) is None
    # 反算落在显示器左侧之外（region 在显示器右半边，VLM 坐标 0 → x=2000 超出）
    assert vlm_xy_to_screen((0, 0), 1920, (2000, 0, 1920, 1080), (0, 0)) is None
