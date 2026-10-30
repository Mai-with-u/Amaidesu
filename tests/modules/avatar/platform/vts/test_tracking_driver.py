"""VTS 面捕驱动：ARKit 系数按 VBridger 规范换算成 VTS 输入、按范围钳制、调试覆盖、就绪判定、校准项实时调"""

from __future__ import annotations

import pytest

from src.modules.avatar.platform.vts.tracking_driver import OVERRIDE_HOLD_MS, VBRIDGER_INPUTS, VtsTrackingDriver
from src.modules.avatar.tracking.channels import BODY_YAW, BODY_Z, GAZE_X, GAZE_Y, HEAD_YAW, expand, rest_frame
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
    ("coefficients", "vts_input", "expected"),
    [
        ({"browOuterUpLeft": 1.0, "browInnerUp": 1.0}, "BrowLeftY", 1.0),
        ({"browDownLeft": 1.0}, "BrowLeftY", 0.0),
        ({"mouthFrown": 1.0}, "MouthSmile", 0.0),
        ({"mouthSmile": 0.5}, "MouthSmile", 0.75),
        ({"eyeBlinkLeft": 1.0}, "EyeOpenLeft", 0.0),
        ({"eyeWideRight": 1.0}, "EyeOpenRight", 1.0),
        ({HEAD_YAW: -12.0}, "FaceAngleX", -12.0),
        ({"jawOpen": 0.6, "mouthClose": 0.2}, "MouthOpen", 0.4),  # 下巴张开但嘴唇合着的那部分不算张嘴
        ({"browDown": 1.0, "noseSneer": 1.0}, "FaceAngry", 1.0),
        ({"aa": 0.7}, "VoiceA", 0.7),
    ],
)
def test_coefficients_map_to_vts_inputs(
    fake_clock: FakeClock, coefficients: dict, vts_input: str, expected: float
) -> None:
    frame = {**rest_frame(), **expand(coefficients)}
    assert _driver(RecordingVtsClient(), fake_clock).to_inputs(frame)[vts_input] == pytest.approx(expected)


@pytest.mark.parametrize(
    ("coefficients", "vts_input", "expected"),
    [
        ({"jawOpen": 0.6}, "JawOpen", 0.6),
        ({"mouthFunnel": 0.8}, "MouthFunnel", 0.8),
        ({"mouthPucker": 0.9}, "MouthPucker", -0.9),  # 撅嘴在负半轴
        ({"mouthStretch": 0.5, "mouthDimple": 0.3}, "MouthPucker", 0.8),  # 咧开在正半轴
        ({"mouthUpperUp": 0.6, "mouthLowerDown": 0.4}, "MouthPressLipOpen", 1.0),  # 露牙
        ({"mouthPress": 0.5, "mouthRollLower": 0.4}, "MouthPressLipOpen", -0.7),  # 抿唇
        ({"mouthShrugUpper": 0.6, "mouthShrugLower": 0.2}, "MouthShrug", 0.4),
        ({"browInnerUp": 0.7}, "BrowInnerUp", 0.7),
        ({"eyeSquintLeft": 0.8}, "EyeSquintL", 0.8),
        ({BODY_YAW: 5.0}, "BodyAngleX", 5.0),
        ({BODY_Z: 1.0}, "BodyPositionZ", 5.0),
    ],
)
def test_vbridger_standard_inputs(fake_clock: FakeClock, coefficients: dict, vts_input: str, expected: float) -> None:
    frame = {**rest_frame(), **expand(coefficients)}
    inputs = _driver(RecordingVtsClient(), fake_clock).to_inputs(frame)
    assert set(VBRIDGER_INPUTS) <= set(inputs)  # VBridger 那组自建输入每帧都写
    assert inputs[vts_input] == pytest.approx(expected)


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


def test_eyes_turn_the_same_way_as_the_head(fake_clock: FakeClock) -> None:
    # VTS 的眼球左右输入与头部转向输入方向相反：看向右边时两者要异号，模型上才朝同一边
    frame = {**rest_frame(), HEAD_YAW: 8.0, GAZE_X: 0.8, GAZE_Y: -0.4}
    inputs = _driver(RecordingVtsClient(), fake_clock).to_inputs(frame)
    assert inputs["FaceAngleX"] == 8.0 and inputs["EyeLeftX"] == inputs["EyeRightX"] == -0.8
    assert inputs["EyeLeftY"] == inputs["EyeRightY"] == -0.4  # 上下同向，不取反


def test_calibration_changes_neutral_mapping_live(fake_clock: FakeClock) -> None:
    driver = _driver(RecordingVtsClient(), fake_clock)
    assert driver.calibration() == {"eye_open_rest": 0.55, "mouth_smile_neutral": 0.5, "brow_neutral": 0.5}
    driver.calibrate({"eye_open_rest": 0.7, "mouth_smile_neutral": 0.6})
    inputs = driver.to_inputs(rest_frame())
    assert inputs["EyeOpenLeft"] == pytest.approx(0.7) and inputs["MouthSmile"] == pytest.approx(0.6)
    with pytest.raises(ValueError):
        driver.calibrate({"eye_open_rest": 0.5, "nose": 1.0})
    assert driver.calibration()["eye_open_rest"] == 0.7  # 有不认识的键时整批不改


def _marker_driver(client: RecordingVtsClient, clock: FakeClock) -> VtsTrackingDriver:
    driver = VtsTrackingDriver(
        client,
        eye_open_rest=0.55,
        mouth_smile_neutral=0.5,
        brow_neutral=0.5,
        markers={"tears": "EyesCry.exp3.json", "blush": "Blush", "anger": "SignAngry.exp3.json"},
        clock=clock.now_ms,
    )
    driver.set_active(True)
    return driver


def _activations(client: RecordingVtsClient) -> list:
    return [(d["expressionFile"], d["active"]) for t, d in client.sent if t == "ExpressionActivationRequest"]


def test_input_markers_are_written_every_frame(fake_clock: FakeClock) -> None:
    driver = _marker_driver(RecordingVtsClient(), fake_clock)
    assert driver.to_inputs({**rest_frame(), "marker_blush": 0.6})["Blush"] == pytest.approx(0.6)
    assert driver.marker_inputs() == {"Blush": "风格化标记：脸红"}  # 适配器据此在 VTS 里补建


async def test_expression_file_markers_switch_with_hysteresis(fake_clock: FakeClock) -> None:
    client = RecordingVtsClient()
    driver = _marker_driver(client, fake_clock)
    for tears in (0.0, 0.3, 0.4, 0.3, 0.25, 0.1, 0.1):
        await driver.apply({**rest_frame(), "marker_tears": tears})
    # 第一帧把开关状态对齐一次（关）；到 0.35 才开，回落到 0.2 以下才关，中间抖动不反复开关
    assert _activations(client) == [
        ("EyesCry.exp3.json", False),
        ("SignAngry.exp3.json", False),
        ("EyesCry.exp3.json", True),
        ("EyesCry.exp3.json", False),
    ]


async def test_expression_files_missing_on_the_model_are_skipped(fake_clock: FakeClock) -> None:
    client = RecordingVtsClient()
    driver = _marker_driver(client, fake_clock)
    driver.set_available_expressions(["EyesCry.exp3.json"])
    assert driver.missing_marker_files() == {"anger": "SignAngry.exp3.json"}
    await driver.apply({**rest_frame(), "marker_anger": 1.0, "marker_tears": 1.0})
    assert _activations(client) == [("EyesCry.exp3.json", True)]


async def test_reconnect_realigns_expression_file_state(fake_clock: FakeClock) -> None:
    client = RecordingVtsClient()
    driver = _marker_driver(client, fake_clock)
    driver.set_available_expressions(["EyesCry.exp3.json"])
    await driver.apply({**rest_frame(), "marker_tears": 1.0})
    await driver.apply({**rest_frame(), "marker_tears": 1.0})
    driver.set_active(False)
    driver.set_active(True)  # 断线期间 VTS 那边的状态不可知：重连后按目标状态再发一次
    await driver.apply({**rest_frame(), "marker_tears": 1.0})
    assert _activations(client) == [("EyesCry.exp3.json", True), ("EyesCry.exp3.json", True)]
