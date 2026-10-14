"""OBSProvider 对接 obsws-python：日志被压住、失败原因翻译后经项目日志记录、半开连接被断开、来源显隐走场景项。

用 obsws ReqClient 替身，不发真实 WebSocket 请求；替身按 obsws-python 1.8 的行为在连接时以
INFO 级打印含明文密码的连接参数，只提供真实客户端也有的方法。
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

import pytest
from obsws_python.error import OBSSDKError, OBSSDKRequestError
from websocket import WebSocketConnectionClosedException

from src.modules.studio.obs import obs_provider as obs_mod
from src.modules.studio.obs.obs_errors import describe_obs_error

_PASSWORD = "s3cret-pw"


class _FakeReqClient:
    """obsws ReqClient 替身：记录调用；连接时照 obsws-python 的习惯明文打印密码，可按需让握手或请求失败。"""

    instances: List["_FakeReqClient"] = []
    # 当前节目场景及其顶层来源（来源名 → 场景项 ID）：与 obs-websocket 5 一样，显隐挂在场景项上
    program_scene = "游戏场景"
    scene_items: Dict[str, int] = {"摄像头": 7}
    fail_connect: Optional[BaseException] = None
    fail_request: Optional[BaseException] = None

    def __init__(self, **kwargs: Any) -> None:
        third_party = logging.getLogger("obsws_python.baseclient.ObsClient")
        third_party.info(
            f"Connecting with parameters: host='{kwargs['host']}' port={kwargs['port']} password='{kwargs['password']}'"
        )
        if _FakeReqClient.fail_connect is not None:
            third_party.error(f"{type(_FakeReqClient.fail_connect).__name__}: {_FakeReqClient.fail_connect}")
            raise _FakeReqClient.fail_connect
        self.kwargs = kwargs
        self.disconnected = False
        self.scenes: List[str] = []
        self.texts: List[str] = []
        self.toggles: List[tuple] = []
        _FakeReqClient.instances.append(self)

    def _check(self) -> None:
        if _FakeReqClient.fail_request is not None:
            raise _FakeReqClient.fail_request

    def set_current_program_scene(self, name: str) -> None:
        self._check()
        self.scenes.append(name)

    def set_input_settings(self, name: str, settings: Dict[str, Any], overlay: bool) -> None:
        self._check()
        self.texts.append(settings["text"])

    def get_current_program_scene(self) -> Any:
        self._check()
        return SimpleNamespace(current_program_scene_name=self.program_scene)

    def get_scene_item_id(self, scene_name: str, source_name: str, offset: Optional[int] = None) -> Any:
        self._check()
        if source_name not in self.scene_items:
            raise OBSSDKRequestError("GetSceneItemId", 600, f"No scene items were found with the name `{source_name}`.")
        return SimpleNamespace(scene_item_id=self.scene_items[source_name])

    def set_scene_item_enabled(self, scene_name: str, item_id: int, enabled: bool) -> None:
        self._check()
        self.toggles.append((scene_name, item_id, enabled))

    def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture
def fake_obs(monkeypatch: pytest.MonkeyPatch) -> type[_FakeReqClient]:
    _FakeReqClient.instances = []
    _FakeReqClient.fail_connect = None
    _FakeReqClient.fail_request = None
    monkeypatch.setattr("obsws_python.ReqClient", _FakeReqClient)
    return _FakeReqClient


@pytest.fixture
def obsws_logger() -> Iterator[logging.Logger]:
    """先把 obsws_python 日志器恢复成未压制状态（别的用例可能已压过），测完还原。"""
    third_party = logging.getLogger("obsws_python")
    level, propagate = third_party.level, third_party.propagate
    third_party.setLevel(logging.NOTSET)
    third_party.propagate = True
    yield third_party
    third_party.setLevel(level)
    third_party.propagate = propagate


def _provider(**overrides: Any) -> obs_mod.OBSProvider:
    config = {
        "host": "127.0.0.1",
        "port": 4455,
        "password": _PASSWORD,
        "text_source_name": "字幕",
        "test_on_connect": False,
        **overrides,
    }
    return obs_mod.OBSProvider(config=config)


def _errors(capture: Any) -> List[str]:
    return [record["message"] for record in capture.records if record["level"] == "ERROR"]


async def test_connect_keeps_password_out_of_logs(
    fake_obs: type[_FakeReqClient],
    obsws_logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """连接前压住 obsws_python 日志器：明文密码既不进标准库日志，也不打到控制台。"""
    caplog.set_level(logging.DEBUG)
    provider = _provider()

    assert await provider.connect() is True
    assert fake_obs.instances[0].kwargs == {"host": "127.0.0.1", "port": 4455, "password": _PASSWORD}
    assert obsws_logger.level > logging.CRITICAL and obsws_logger.propagate is False
    assert _PASSWORD not in caplog.text
    assert _PASSWORD not in capsys.readouterr().err


async def test_connect_refused_reports_how_to_enable_websocket(
    fake_obs: type[_FakeReqClient], loguru_capture: Any
) -> None:
    """OBS 没开或没启用 WebSocket 服务器：连接失败，原因告诉用户去哪打开。"""
    fake_obs.fail_connect = ConnectionRefusedError(10061, "目标计算机积极拒绝")
    provider = _provider()

    assert await provider.connect() is False
    assert provider.obs_connection is None and provider.is_connected is False
    assert any("启用 WebSocket 服务器" in message for message in _errors(loguru_capture))


async def test_missing_password_points_to_tools_toml(fake_obs: type[_FakeReqClient], loguru_capture: Any) -> None:
    """OBS 要求密码而工具没配：原因指向工具自己的配置项，不是采集器的 [live_frame]。"""
    fake_obs.fail_connect = OBSSDKError("authentication enabled but no password provided")
    provider = _provider(password="")

    assert await provider.connect() is False
    errors = _errors(loguru_capture)
    assert any("tools.toml 的 [tools.studio.obs.config].password" in message for message in errors)
    assert not any("live_frame" in message for message in errors)


async def test_failed_test_message_disconnects_half_open_client(
    fake_obs: type[_FakeReqClient], loguru_capture: Any
) -> None:
    """握手成功但测试消息写不进文本源：断开这条半开连接再报失败，原因说明文本源不存在。"""
    fake_obs.fail_request = OBSSDKRequestError("SetInputSettings", 600, "No source was found by the name of `字幕`.")
    provider = _provider(test_on_connect=True)

    assert await provider.connect() is False
    assert fake_obs.instances[0].disconnected is True
    assert provider.obs_connection is None and provider.is_connected is False
    assert any("找不到文本源「字幕」" in message for message in _errors(loguru_capture))


async def test_switch_scene_to_missing_scene_explains_name_mismatch(
    fake_obs: type[_FakeReqClient], loguru_capture: Any
) -> None:
    """切到不存在的场景（OBS 回 600）：工具返回失败，原因点名找不到的场景。"""
    provider = _provider()
    assert await provider.connect() is True
    fake_obs.fail_request = OBSSDKRequestError("SetCurrentProgramScene", 600, "No source was found")

    assert await provider.switch_scene("主场景") is False
    assert any("找不到场景「主场景」" in message for message in _errors(loguru_capture))


async def test_request_after_obs_closed_reports_lost_connection(
    fake_obs: type[_FakeReqClient], loguru_capture: Any
) -> None:
    """连上之后 OBS 关了：请求失败说成连接断开，不误报成密码不对。"""
    provider = _provider()
    assert await provider.connect() is True
    fake_obs.fail_request = WebSocketConnectionClosedException("socket is already closed.")

    assert await provider._send_text_to_obs("你好") is False
    errors = _errors(loguru_capture)
    assert any("连接断开了" in message for message in errors)
    assert not any("密码" in message for message in errors)


async def test_set_source_visibility_toggles_scene_item_in_program_scene(fake_obs: type[_FakeReqClient]) -> None:
    """显隐挂在场景项上：在当前节目场景里找到来源的场景项，再设这个场景项的显隐。"""
    provider = _provider()
    assert await provider.connect() is True

    assert await provider.set_source_visibility("摄像头", False) is True
    assert fake_obs.instances[0].toggles == [("游戏场景", 7, False)]


async def test_set_source_visibility_missing_source_names_the_scene(
    fake_obs: type[_FakeReqClient], loguru_capture: Any
) -> None:
    """来源不在当前节目场景顶层（OBS 回 600）：不改任何场景项，原因点名场景与来源。"""
    provider = _provider()
    assert await provider.connect() is True

    assert await provider.set_source_visibility("贴纸", True) is False
    assert fake_obs.instances[0].toggles == []
    assert any("当前节目场景「游戏场景」里找不到来源「贴纸」" in message for message in _errors(loguru_capture))


def test_closed_connection_reads_as_auth_rejection_only_during_handshake() -> None:
    """同一个"连接被关闭"：握手阶段是身份验证被拒，请求阶段是 OBS 断开；600 没给原因时按通用请求错误描述。"""
    where: Dict[str, Any] = {"host": "localhost", "port": 4455, "password_setting": "某处的 password"}
    closed = WebSocketConnectionClosedException("Connection to remote host was lost.")

    assert "密码不一致" in describe_obs_error(closed, **where)
    assert "连接断开了" in describe_obs_error(closed, connected=True, **where)
    assert "连接断开了" in describe_obs_error(json.JSONDecodeError("Expecting value", "", 0), connected=True, **where)
    assert "代码 600" in describe_obs_error(OBSSDKRequestError("GetSceneItemId", 600, "x"), **where)
