"""VRChat 面捕驱动：眼睛走官方眼动输入（方向与幅度）、脸按 VRCFaceTracking 参数名换算且只发变化、风格化标记写形象参数"""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from src.modules.avatar.platform.vrchat.tracking_driver import (
    EYE_PITCH_YAW,
    EYES_CLOSED,
    GAZE_YAW_DEG,
    VrchatTrackingDriver,
    face_parameters,
)
from src.modules.avatar.tracking.channels import GAZE_X, GAZE_Y, expand, rest_frame


def _driver(**kwargs: Any) -> Tuple[VrchatTrackingDriver, List[Tuple[str, Any]]]:
    sent: List[Tuple[str, Any]] = []
    driver = VrchatTrackingDriver(lambda address, value: sent.append((address, value)), lambda: True, **kwargs)
    return driver, sent


def test_face_parameters_follow_the_vrcft_simplified_names() -> None:
    frame = {**rest_frame(), **expand({"mouthSmile": 0.6, "eyeBlink": 1.0, "eyeWideRight": 0.0}), "jawOpen": 0.4}
    params = face_parameters(frame)
    assert params["JawOpen"] == 0.4 and params["MouthSmileLeft"] == pytest.approx(0.6)
    assert params["SmileFrownRight"] == pytest.approx(0.6)  # 笑减撇
    assert params["EyeLidLeft"] == 0.0  # 闭眼
    assert face_parameters(rest_frame())["EyeLidLeft"] == 0.75  # 正常睁眼是 0.75，往上是睁大


async def test_eyes_go_to_the_official_osc_inputs_every_frame() -> None:
    driver, sent = _driver(face_prefix=None)
    frame = {**rest_frame(), **expand({"eyeBlink": 0.5}), GAZE_X: 0.4, GAZE_Y: 0.5}
    await driver.apply(frame)
    await driver.apply(frame)
    assert [address for address, _ in sent] == [EYES_CLOSED, EYE_PITCH_YAW] * 2  # 没有面捕参数时只发眼睛
    closed, (pitch, yaw) = sent[0][1], sent[1][1]
    assert closed == 0.5
    # 俯仰正数往下：视线往上是负；镜头在正前方，画面右边是形象自己的左边
    assert pitch == -10.0 and yaw == pytest.approx(-0.4 * GAZE_YAW_DEG)


async def test_face_parameters_and_markers_are_sent_only_when_they_change() -> None:
    driver, sent = _driver(markers={"tears": "Tears"})
    await driver.apply({**rest_frame(), "jawOpen": 0.3, "marker_tears": 0.5})
    first = dict(sent)
    assert first["/avatar/parameters/FT/v2/JawOpen"] == 0.3 and first["/avatar/parameters/Tears"] == 0.5
    sent.clear()
    await driver.apply({**rest_frame(), "jawOpen": 0.305, "marker_tears": 0.5})  # 变化不到 0.01：不重发
    assert [a for a, _ in sent] == [EYES_CLOSED, EYE_PITCH_YAW]
    sent.clear()
    await driver.apply({**rest_frame(), "jawOpen": 0.0, "marker_tears": 0.5})
    assert ("/avatar/parameters/FT/v2/JawOpen", 0.0) in sent
    driver.reset()  # 换形象或重连后全部重发
    sent.clear()
    await driver.apply({**rest_frame(), "marker_tears": 0.5})
    assert ("/avatar/parameters/Tears", 0.5) in sent and len(sent) > 30
