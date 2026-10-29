"""VTS 面捕驱动：通用通道换算成 VTS 输入、按范围钳制、调试覆盖、就绪判定"""

from __future__ import annotations

import pytest

from src.modules.avatar.platform.vts.tracking_driver import OVERRIDE_HOLD_MS, VtsTrackingDriver
from src.modules.avatar.tracking.channels import BROW_L, EYE_OPEN_L, EYE_WIDE, HEAD_YAW, MOUTH_SMILE, rest_frame
from tests.helpers import FakeClock
from tests.modules.avatar.platform.vts.fakes import RecordingVtsClient


def _driver(client: RecordingVtsClient, clock: FakeClock) -> VtsTrackingDriver:
    driver = VtsTrackingDriver(
        client, eye_open_rest=0.55, mouth_smile_neutral=0.5, brow_neutral=0.5, clock=clock.now_ms
    )
    driver.set_active(True)
    return driver


def test_rest_pose_maps_to_calibrated_neutral_inputs(fake_clock: FakeClock) -> None:
    inputs = _driver(RecordingVtsClient(), fake_clock).to_inputs(rest_frame())
    assert inputs["EyeOpenLeft"] == pytest.approx(0.55)  # 正常睁眼 = 校准值，不是 1.0（1.0 在有的模型上是瞪到最大）
    assert (inputs["MouthSmile"], inputs["BrowLeftY"], inputs["Brows"]) == (0.5, 0.5, 0.5)
    assert inputs["FaceAngleX"] == 0.0 and inputs["MouthOpen"] == 0.0


@pytest.mark.parametrize(
    ("channel", "value", "vts_input", "expected"),
    [
        (BROW_L, 1.0, "BrowLeftY", 1.0),
        (BROW_L, -1.0, "BrowLeftY", 0.0),
        (MOUTH_SMILE, -1.0, "MouthSmile", 0.0),
        (MOUTH_SMILE, 0.5, "MouthSmile", 0.75),
        (EYE_OPEN_L, 0.0, "EyeOpenLeft", 0.0),
        (HEAD_YAW, -12.0, "FaceAngleX", -12.0),
    ],
)
def test_channel_extremes_map_to_input_range(
    fake_clock: FakeClock, channel: str, value: float, vts_input: str, expected: float
) -> None:
    frame = dict(rest_frame(), **{channel: value})
    assert _driver(RecordingVtsClient(), fake_clock).to_inputs(frame)[vts_input] == pytest.approx(expected)


def test_wide_eyes_reach_full_input(fake_clock: FakeClock) -> None:
    frame = dict(rest_frame(), **{EYE_WIDE: 1.0})
    assert _driver(RecordingVtsClient(), fake_clock).to_inputs(frame)["EyeOpenRight"] == pytest.approx(1.0)


async def test_apply_injects_one_clamped_frame_of_known_inputs(fake_clock: FakeClock) -> None:
    client = RecordingVtsClient()
    driver = _driver(client, fake_clock)
    driver.set_input_ranges({"FaceAngleX": (-30.0, 30.0), "EyeOpenLeft": (0.0, 1.0)})
    await driver.apply(dict(rest_frame(), **{HEAD_YAW: 45.0}))
    [(message_type, data)] = client.sent
    assert message_type == "InjectParameterDataRequest" and data["faceFound"] is True
    assert {p["id"]: p["value"] for p in data["parameterValues"]} == {"FaceAngleX": 30.0, "EyeOpenLeft": 0.55}


async def test_debug_override_wins_for_a_while_then_hands_back(fake_clock: FakeClock) -> None:
    client = RecordingVtsClient()
    driver = _driver(client, fake_clock)
    driver.set_override("MouthOpen", 0.9)
    await driver.apply(rest_frame())
    fake_clock.tick(OVERRIDE_HOLD_MS)
    await driver.apply(rest_frame())
    values = [{p["id"]: p["value"] for p in data["parameterValues"]}["MouthOpen"] for _, data in client.sent]
    assert values == [0.9, 0.0]


def test_ready_needs_active_driver_and_open_client(fake_clock: FakeClock) -> None:
    client = RecordingVtsClient(is_open=False)
    driver = _driver(client, fake_clock)
    assert not driver.ready()
    client.is_open = True
    assert driver.ready()
    driver.set_active(False)
    assert not driver.ready()
