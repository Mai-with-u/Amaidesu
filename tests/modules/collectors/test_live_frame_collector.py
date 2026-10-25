"""直播画面采集器：每次采样覆盖暂存最新帧、变化才刷新比对基线、取帧失败不中断采样。"""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace
from typing import List, Optional

import pytest
from PIL import Image

from src.modules.collectors.frame.live_frame_collector import (
    GrabbedFrame,
    LiveFrameCollector,
    ObsFrameGrabber,
    describe_live_frame_obs_error,
    frame_change,
)
from src.modules.studio.obs import obs_client
from src.modules.studio.obs.obs_client import OBSRequestError
from src.modules.vision.image_store import VisionImageStore, set_image_store


def _frame(shade: int) -> GrabbedFrame:
    """一张纯色 PNG（灰度 shade）。"""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), (shade, shade, shade)).save(buffer, format="PNG")
    data = buffer.getvalue()
    return GrabbedFrame(f"data:image/png;base64,{base64.b64encode(data).decode()}", data, 64, 36)


class _Grabber:
    """按脚本吐帧的取帧后端；脚本项为 None 表示取不到，为异常表示来源不可用。"""

    def __init__(self, script: List[object]) -> None:
        self.script = list(script)
        self.closed = False

    async def grab(self) -> Optional[GrabbedFrame]:
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]

    async def close(self) -> None:
        self.closed = True

    def describe(self, error: BaseException) -> str:
        return f"替身失败：{error}"


@pytest.fixture(autouse=True)
def _store() -> VisionImageStore:
    store = VisionImageStore()
    set_image_store(store)
    yield store
    set_image_store(VisionImageStore())


def _collector(script: List[object], **config: object) -> LiveFrameCollector:
    return LiveFrameCollector(config={"refresh_ms": 0, **config}, grabber=_Grabber(script))


def test_frame_change_measures_mean_gray_difference() -> None:
    assert frame_change(None, [0, 0]) == 1.0
    assert frame_change([10, 10], [10, 10]) == 0.0
    assert frame_change([0, 0], [255, 255]) == 1.0


async def test_first_and_changed_frames_hit_the_change_gate(_store: VisionImageStore) -> None:
    """首帧必中闸；画面没明显变化不中；明显变化再中，暂存里始终是刚截的画面。"""
    collector = _collector([_frame(10), _frame(12), _frame(200)])
    assert await collector.sample_once() is True
    assert await collector.sample_once() is False
    assert await collector.sample_once() is True
    latest = _store.latest()
    assert latest is not None and latest.change > 0.5
    assert _store.get(latest.ref).startswith("data:image/png;base64,")


async def test_unchanged_frame_still_updates_latest_slot(_store: VisionImageStore) -> None:
    """低于变化阈值的帧不刷新基线，但暂存的最新帧槽位每次采样都覆盖更新——
    决策窗查询式挂帧看的必须是刚截的画面，而不是上一张稳定画面。"""
    collector = _collector([_frame(10), _frame(11), _frame(200)], refresh_ms=0)
    assert await collector.sample_once() is True
    assert await collector.sample_once() is False
    latest = _store.latest()
    assert latest is not None and _store.get(latest.ref).startswith("data:image/png;base64,")
    assert await collector.sample_once() is True


async def test_unchanged_frame_refreshes_baseline_after_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """画面一直没变，隔 refresh_ms 仍触发一次基线刷新（补刷周期到）。"""
    clock = {"now": 1_000_000}
    monkeypatch.setattr("src.modules.collectors.frame.live_frame_collector.now_ms", lambda: clock["now"])
    collector = _collector([_frame(10), _frame(10), _frame(10)], refresh_ms=60_000)
    assert await collector.sample_once() is True
    clock["now"] += 30_000
    assert await collector.sample_once() is False
    clock["now"] += 30_000
    assert await collector.sample_once() is True


async def test_grab_failure_does_not_stop_sampling() -> None:
    """来源不可用（OBS 没开）只记日志，恢复后照常采样。"""
    collector = _collector([ConnectionRefusedError("OBS 未开"), None, _frame(10)])
    assert await collector.sample_once() is False
    assert await collector.sample_once() is False
    assert await collector.sample_once() is True


async def test_stop_closes_grabber() -> None:
    collector = _collector([])
    await collector.stop()
    assert collector._grabber.closed  # type: ignore[attr-defined]


def test_config_defaults_are_constructible() -> None:
    """物化按注册表补缺段依赖无参构造：默认截 OBS 当前节目场景、5 秒采样、768 宽。"""
    config = LiveFrameCollector.ConfigSchema()
    assert (config.source, config.obs_source, config.interval_ms, config.max_width) == ("obs", "", 5000, 768)


class _FakeObsClient:
    """ObsClient 替身：记录调用，按需在截图时失败。"""

    instances: List["_FakeObsClient"] = []
    fail_screenshot: Optional[Exception] = None

    def __init__(self, *, host: str, port: int, password: str, timeout: int) -> None:
        self.kwargs = {"host": host, "port": port, "password": password, "timeout": timeout}
        self.disconnected = False
        self.shots: List[tuple] = []
        _FakeObsClient.instances.append(self)

    def connect(self) -> None:
        return None

    def call(self, request: object) -> object:
        name = type(request).__name__
        if name == "GetVersion":
            return SimpleNamespace(getSupportedImageFormats=lambda: ["bmp", "jpeg", "jpg", "png"])
        if name == "GetVideoSettings":
            return SimpleNamespace(
                getBaseWidth=lambda: 2560,
                getBaseHeight=lambda: 1440,
                getOutputWidth=lambda: 1920,
                getOutputHeight=lambda: 1080,
            )
        if name == "GetCurrentProgramScene":
            return SimpleNamespace(getCurrentProgramSceneName=lambda: "游戏场景")
        if name == "GetSourceScreenshot":
            if _FakeObsClient.fail_screenshot is not None:
                raise _FakeObsClient.fail_screenshot
            args = request.data()
            shot = (
                args["sourceName"],
                args["imageFormat"],
                args["imageWidth"],
                args["imageHeight"],
                args["imageCompressionQuality"],
            )
            self.shots.append(shot)
            width, height = args["imageWidth"], args["imageHeight"]
            buffer = io.BytesIO()
            Image.new("RGB", (width, height), (90, 90, 90)).save(buffer, format="JPEG")
            encoded = base64.b64encode(buffer.getvalue()).decode()
            return SimpleNamespace(getImageData=lambda: "data:image/jpg;base64," + encoded)
        raise AssertionError(f"替身未实现的请求：{name}")

    def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture
def fake_obs(monkeypatch: pytest.MonkeyPatch) -> type:
    _FakeObsClient.instances = []
    _FakeObsClient.fail_screenshot = None
    monkeypatch.setattr(obs_client, "ObsClient", _FakeObsClient)
    return _FakeObsClient


async def test_obs_grabber_shoots_program_scene_as_jpeg(fake_obs: type) -> None:
    """截当前节目场景：按 OBS 支持的格式选 JPEG、按输出分辨率等比算高，MIME 修正为 image/jpeg。"""
    grabber = ObsFrameGrabber(host="localhost", port=4455, password="", source="", max_width=768)
    frame = await grabber.grab()
    client = fake_obs.instances[0]
    assert client.kwargs == {"host": "localhost", "port": 4455, "password": "", "timeout": 3}
    assert client.shots == [("游戏场景", "jpg", 768, 432, 70)]
    assert frame is not None and (frame.width, frame.height) == (768, 432)
    assert frame.data_url.startswith("data:image/jpeg;base64,")


async def test_obs_grabber_drops_client_on_failure_and_reconnects(fake_obs: type) -> None:
    """截图失败时断开并丢掉客户端，下次取帧重新连接。"""
    grabber = ObsFrameGrabber(host="localhost", port=4455, password="pw", source="游戏画面", max_width=768)
    fake_obs.fail_screenshot = ConnectionResetError("OBS 重启")
    with pytest.raises(ConnectionResetError):
        await grabber.grab()
    assert fake_obs.instances[0].disconnected
    fake_obs.fail_screenshot = None
    assert await grabber.grab() is not None
    assert len(fake_obs.instances) == 2 and fake_obs.instances[1].shots[0][0] == "游戏画面"


def test_obs_errors_are_translated_to_fixes() -> None:
    """常见失败翻译成照着改的原因：没开、要密码、密码不对、找不到来源。"""

    def describe(error: BaseException) -> str:
        return describe_live_frame_obs_error(error, host="localhost", port=4455, source="游戏画面")

    assert "启用 WebSocket 服务器" in describe(ConnectionRefusedError())
    assert "obs_password" in describe(Exception("Empty response to Identify, password may be inconnect."))
    assert "密码不一致" in describe(Exception("failed to identify client with the server"))
    assert "找不到要截的场景或来源「游戏画面」" in describe(OBSRequestError("GetSourceScreenshot", 600, "x"))
    assert "超时" in describe(TimeoutError())
