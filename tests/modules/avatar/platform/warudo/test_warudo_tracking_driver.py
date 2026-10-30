"""Warudo 面捕驱动：iFacialMocap 包的名字、取值与方向，发送端就绪判定，风格化标记经蓝图通道限频补发"""

from __future__ import annotations

from typing import Dict, List, Tuple

import pytest

from src.modules.avatar.platform.warudo.ifacialmocap import GAZE_YAW_DEG, encode_frame, wire_name
from src.modules.avatar.platform.warudo.tracking_driver import MARKER_MIN_GAP_MS, WarudoTrackingDriver
from src.modules.avatar.tracking.channels import (
    ARKIT_BLENDSHAPES,
    GAZE_X,
    HEAD_PITCH,
    HEAD_YAW,
    expand,
    rest_frame,
)
from tests.helpers import FakeClock


def _parse(packet: bytes) -> Tuple[Dict[str, int], Dict[str, List[float]]]:
    """按接收端的读法拆包：表情系数 名字-整数，其余 名字#逗号分隔的度数。"""
    shapes: Dict[str, int] = {}
    poses: Dict[str, List[float]] = {}
    for part in packet.decode("ascii").strip("|").split("|"):
        if "#" in part:
            name, values = part.split("#")
            poses[name] = [float(v) for v in values.split(",")]
        else:
            name, value = part.rsplit("-", 1)
            shapes[name] = int(value)
    return shapes, poses


def test_names_follow_the_ifacialmocap_spelling() -> None:
    assert wire_name("eyeBlinkLeft") == "eyeBlink_L" and wire_name("mouthSmileRight") == "mouthSmile_R"
    assert wire_name("eyeLookInLeft") == "eyeLookIn_L"
    # 说方向的名字不是哪一侧，照旧
    assert (wire_name("jawLeft"), wire_name("mouthRight"), wire_name("browInnerUp")) == (
        "jawLeft",
        "mouthRight",
        "browInnerUp",
    )


def test_packet_carries_all_52_coefficients_in_percent_plus_head_and_eyes() -> None:
    frame = {**rest_frame(), **expand({"mouthSmile": 0.734, "jawOpen": 1.4}), HEAD_YAW: 10.0, HEAD_PITCH: 5.0}
    frame[GAZE_X] = 0.5
    shapes, poses = _parse(encode_frame(frame))
    assert len(shapes) == len(ARKIT_BLENDSHAPES) == 52
    assert shapes["mouthSmile_L"] == 73 and shapes["jawOpen"] == 100  # 0~1 写成 0~100，越界钳住
    pitch, yaw, roll = poses["=head"][:3]
    assert (pitch, yaw, roll) == (-5.0, 10.0, 0.0)  # ARKit 俯仰正数是低头：抬头取反
    assert poses["rightEye"][1] == pytest.approx(0.5 * GAZE_YAW_DEG) and poses["leftEye"] == poses["rightEye"]
    assert "-0.000" not in encode_frame(rest_frame()).decode("ascii")


class FakeTransport:
    def __init__(self) -> None:
        self.packets: List[bytes] = []
        self.closing = False

    def sendto(self, data: bytes) -> None:
        self.packets.append(data)

    def is_closing(self) -> bool:
        return self.closing


def test_ready_follows_the_transport() -> None:
    driver = WarudoTrackingDriver()
    assert not driver.ready()
    transport = FakeTransport()
    driver.attach_transport(transport)
    assert driver.ready()
    transport.closing = True
    assert not driver.ready()


async def test_markers_go_over_the_blueprint_channel_quantized_and_rate_limited(fake_clock: FakeClock) -> None:
    sent: List[Tuple[str, float]] = []
    connected = {"ok": False}

    async def send_marker(action: str, value: float) -> bool:
        if connected["ok"]:
            sent.append((action, value))
        return connected["ok"]

    driver = WarudoTrackingDriver(markers={"tears": "Tears"}, send_marker=send_marker, clock=fake_clock.now_ms)
    driver.attach_transport(FakeTransport())
    await driver.apply({**rest_frame(), "marker_tears": 0.31})
    assert sent == []  # 蓝图通道没连上：不记成已发，连上后补发
    connected["ok"] = True
    await driver.apply({**rest_frame(), "marker_tears": 0.31})
    fake_clock.tick(33)
    await driver.apply({**rest_frame(), "marker_tears": 0.42})  # 不到 100 毫秒：先不发
    await driver.apply({**rest_frame(), "marker_tears": 0.42})
    fake_clock.tick(MARKER_MIN_GAP_MS)
    await driver.apply({**rest_frame(), "marker_tears": 0.42})
    await driver.apply({**rest_frame(), "marker_tears": 0.0})  # 归零立刻发
    assert sent == [("marker_tears", 0.3), ("marker_tears", 0.4), ("marker_tears", 0.0)]
