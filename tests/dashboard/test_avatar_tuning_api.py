"""形象调校 API：静态信息取自 Schema、改参立刻生效且越界拒绝、配方改与恢复、预览与动作、手动摆姿、校准、保存写回"""

from __future__ import annotations

import random
import tomllib
from pathlib import Path
from typing import Dict, Mapping

import httpx
import pytest

from src.modules.avatar.tracking import TrackingConfig, TrackingRuntime
from src.modules.config.service import ConfigService
from tests.helpers import FakeClock
from tests.modules.avatar.tracking.fakes import RecordingDriver

BASE = "/api/v1/avatar/tuning"


class VtsLikeDriver(RecordingDriver):
    """名叫 vts、带校准项的驱动替身（说明与范围应取自 VTS 配置段 Schema）。"""

    def __init__(self) -> None:
        super().__init__("vts")
        self.values = {"eye_open_rest": 0.55, "mouth_smile_neutral": 0.5, "brow_neutral": 0.5}

    def calibration(self) -> Dict[str, float]:
        return dict(self.values)

    def calibrate(self, values: Mapping[str, float]) -> None:
        if set(values) - set(self.values):
            raise ValueError("未知校准项")
        self.values.update(values)


@pytest.fixture
async def tuning(api, fake_clock: FakeClock, initialized_config: ConfigService):
    # 调参用默认值：和首启生成的 avatar.toml 一致，"有没有未保存的修改"才从干净状态起算
    runtime = TrackingRuntime(TrackingConfig(), clock=fake_clock.now_ms, rng=random.Random(0), run_loop=False)
    await runtime.start()
    driver = VtsLikeDriver()
    await runtime.attach(driver)
    client: httpx.AsyncClient = await api(avatar_tracking=runtime, config_service=initialized_config)
    yield client, runtime, driver, Path(initialized_config.base_dir) / "config" / "avatar.toml"
    await runtime.stop()


async def test_endpoints_503_without_tracking(api) -> None:
    client = await api()
    resp = await client.get(BASE)
    assert resp.status_code == 503 and "面捕合成器未装配" in resp.json()["detail"]


async def test_profile_reads_titles_and_ranges_from_schema(tuning) -> None:
    client, _, _, _ = tuning
    body = (await client.get(BASE)).json()
    fields = {f["key"]: f for f in body["fields"]}
    assert "enabled" not in fields and "recipes" not in fields
    assert fields["camera_gaze_x"]["title"] == "镜头方向（左右）" and fields["camera_gaze_x"]["min"] == -1.0
    assert body["recipes"]["happy"]["overridden"] is False and len(body["recipes"]) == 16
    [vts] = body["calibrations"]
    items = {item["key"]: item for item in vts["items"]}
    assert vts["driver"] == "vts" and items["eye_open_rest"]["title"] == "正常睁眼的输入值"
    assert items["eye_open_rest"]["min"] == 0.05 and items["eye_open_rest"]["value"] == 0.55
    assert any(action["name"] == "点头" for action in body["actions"])
    assert body["unsaved"] is False  # 刚启动：运行中的值就是文件里的值


async def test_config_patch_applies_live_and_rejects_bad_values(tuning) -> None:
    client, runtime, _, _ = tuning
    resp = await client.patch(f"{BASE}/config", json={"changes": {"rest_smile": 0.3, "thinking_gaze_mirror": False}})
    assert resp.status_code == 200 and runtime.config.rest_smile == 0.3
    assert runtime.rest_frame()["mouthSmileLeft"] == pytest.approx(0.3)  # 静止姿态立刻换上
    for changes in ({"rest_smile": 5}, {"enabled": False}, {"nose": 1}):
        assert (await client.patch(f"{BASE}/config", json={"changes": changes})).status_code == 422
    assert runtime.config.rest_smile == 0.3 and runtime.config.enabled is True  # 失败时原样不动


async def test_recipe_override_and_reset(tuning) -> None:
    client, runtime, _, _ = tuning
    resp = await client.put(f"{BASE}/recipes/happy", json={"offsets": {"eyeSquint": 0.85, "mouthSmile": 1.0}})
    assert resp.status_code == 200 and resp.json()["overridden"] is True
    assert dict(runtime.recipes()["happy"].offsets) == {"eyeSquint": 0.85, "mouthSmile": 1.0}
    bad = await client.put(f"{BASE}/recipes/happy", json={"offsets": {"nose": 1.0}})
    assert bad.status_code == 422
    assert (await client.put(f"{BASE}/recipes/sleepy", json={"offsets": {}})).status_code == 422
    reset = await client.delete(f"{BASE}/recipes/happy")
    assert reset.json()["overridden"] is False and runtime.config.recipes == {}


async def test_preview_look_act_and_manual_pose(tuning, fake_clock: FakeClock) -> None:
    client, runtime, _, _ = tuning
    assert (await client.post(f"{BASE}/preview", json={"emotion": "happy"})).json()["outcome"] == "started"
    assert runtime.current_emotion() == "happy"
    assert (await client.post(f"{BASE}/preview", json={"emotion": "sleepy"})).status_code == 422
    assert (await client.post(f"{BASE}/look", json={"x": 0.5, "y": 0.2})).status_code == 200
    assert (await client.post(f"{BASE}/act", json={"action": "点头"})).json()["success"] is True
    unknown = await client.post(f"{BASE}/act", json={"action": "后空翻"})
    assert unknown.status_code == 404 and "点头" in unknown.json()["detail"]

    assert (await client.put(f"{BASE}/manual", json={"values": {"head_yaw": 15.0}})).status_code == 200
    for _ in range(20):
        fake_clock.tick(33)
        await runtime.tick_once()
    state = (await client.get(f"{BASE}/state")).json()
    assert state["manual"] is True and state["frame"]["head_yaw"] == pytest.approx(15.0)
    assert (await client.put(f"{BASE}/manual", json={"values": {"nose": 1.0}})).status_code == 422
    await client.delete(f"{BASE}/manual")
    assert (await client.get(f"{BASE}/state")).json()["manual"] is False


async def test_calibration_patch_validates_against_platform_schema(tuning) -> None:
    client, _, driver, _ = tuning
    resp = await client.patch(f"{BASE}/calibration/vts", json={"values": {"eye_open_rest": 0.6}})
    assert resp.status_code == 200 and driver.values["eye_open_rest"] == 0.6
    out_of_range = await client.patch(f"{BASE}/calibration/vts", json={"values": {"eye_open_rest": 2.0}})
    assert out_of_range.status_code == 422 and "正常睁眼的输入值" in out_of_range.json()["detail"]
    assert (await client.patch(f"{BASE}/calibration/vts", json={"values": {"nose": 1.0}})).status_code == 422
    assert (await client.patch(f"{BASE}/calibration/warudo", json={"values": {}})).status_code == 404


async def test_save_writes_tuning_back_to_avatar_toml(tuning) -> None:
    client, _, _, avatar_toml = tuning
    await client.patch(f"{BASE}/config", json={"changes": {"rest_smile": 0.3, "camera_gaze_x": 0.2}})
    await client.put(f"{BASE}/recipes/happy", json={"offsets": {"eyeSquint": 0.85}, "motion": 1.2})
    await client.patch(f"{BASE}/calibration/vts", json={"values": {"eye_open_rest": 0.6}})
    assert (await client.get(BASE)).json()["unsaved"] is True  # 刷新页面也知道有没保存的修改
    resp = await client.post(f"{BASE}/save")
    assert resp.status_code == 200 and resp.json()["success"] is True
    assert (await client.get(BASE)).json()["unsaved"] is False

    saved = tomllib.loads(avatar_toml.read_text(encoding="utf-8"))
    assert saved["tracking"]["rest_smile"] == 0.3 and saved["tracking"]["camera_gaze_x"] == 0.2
    assert saved["tracking"]["recipes"] == {"happy": {"eyeSquint": 0.85, "motion": 1.2}}
    assert saved["platform"]["vts"]["eye_open_rest"] == 0.6

    await client.delete(f"{BASE}/recipes/happy")
    await client.post(f"{BASE}/save")
    assert tomllib.loads(avatar_toml.read_text(encoding="utf-8"))["tracking"]["recipes"] == {}  # 恢复默认也写回
