"""ScreenSession：区域换算、窗口护栏原语、稳定帧降级、廉价帧采样（采集语义单点的契约）。

假件注入（窗口/采集/读屏），不触真机；处置（停循环/回执错误）归调用方，
本文件只验证会话侧返回的结构化结果与降级语义。
"""

from __future__ import annotations

from typing import Optional, Tuple

from src.agents.text_adv import MonitorGeometry
from src.agents.text_adv.config import TextAdvConfig
from src.agents.text_adv.session import ScreenSession
from src.agents.text_adv.vlm import FakeVisionReader
from src.agents.text_adv.window import FakeWindowBackend
from src.modules.vision.capture_types import ScreenCaptureResult


class BoomCapture:
    def capture(self, monitor_index: int, region=None, max_width=None):
        raise RuntimeError("screen unavailable")


class NoneImageCapture:
    """采集调用本身成功但快照无图像（capture 后端的失败形态）。"""

    def capture(self, monitor_index: int, region=None, max_width=None):
        return ScreenCaptureResult(image=None)


def fast_config(**overrides) -> TextAdvConfig:
    config = TextAdvConfig(stability_sample_ms=10, stability_consecutive=2, stability_timeout_ms=100)
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def make_session(*, config=None, window=None, capture=None) -> ScreenSession:
    return ScreenSession(
        config or fast_config(),
        window_backend=window or FakeWindowBackend(),
        capture=capture,  # type: ignore[arg-type]
        vision_reader=FakeVisionReader("正文：\n测试。"),
    )


def test_capture_region_and_coords_region_convert_the_single_region_config() -> None:
    session = make_session(config=fast_config(region=[100, 50, 320, 240]))
    assert session.capture_region() == (100, 50, 420, 290)
    geometry = MonitorGeometry(1920, 0, 1280, 720)
    assert session.coords_region(geometry) == (100, 50, 320, 240)

    bare = make_session()  # 未配置 region：capture 侧 None、坐标侧退回整台显示器
    assert bare.capture_region() is None
    assert bare.coords_region(geometry) == (0, 0, 1280, 720)


def test_window_guard_primitives_return_structured_reasons() -> None:
    missing = make_session(window=FakeWindowBackend(found=False))
    outcome = missing.find_window()
    assert outcome.win is None and outcome.ok is False and "未找到游戏窗口" in (outcome.reason or "")

    window = FakeWindowBackend(focus_ok=False)
    session = make_session(window=window)
    found = session.find_window()
    assert found.ok is True and found.win is not None
    assert "夺焦失败" in (session.focus_window(found.win) or "")

    assert session.check_watchable(found.win) is None  # 前台 + 几何都正常
    window.set_foreground(False)
    assert session.check_watchable(found.win) == "游戏窗口失去前台焦点"
    window.set_foreground(True)

    class ClosedWindowBackend(FakeWindowBackend):
        """模拟窗口已关闭：几何读取返回 None。"""

        def rect(self, win):
            return None

    closed = make_session(window=ClosedWindowBackend())
    reopened = closed.find_window()
    assert reopened.win is not None
    assert closed.check_watchable(reopened.win) == "窗口几何不可得（窗口可能已关闭）"


async def test_stable_frame_degrades_capture_exception_to_none() -> None:
    session = make_session(capture=BoomCapture())
    assert await session.stable_frame() is None  # 采集异常按感知失败降级，不外抛


async def test_sample_frame_returns_none_for_failed_snapshot_and_propagates_boom() -> None:
    region: Optional[Tuple[int, int, int, int]] = None
    assert make_session(capture=NoneImageCapture()).sample_frame(region) is None
    try:
        make_session(capture=BoomCapture()).sample_frame(region)
    except RuntimeError as exc:
        assert "screen unavailable" in str(exc)  # 采集异常上抛，处置归调用方
    else:
        raise AssertionError("sample_frame 应透传采集异常")
