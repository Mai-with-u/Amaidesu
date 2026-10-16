"""B 站官方弹幕 game_id 检查点与残留清理测试

覆盖：
- GameIdCheckpoint 读写删与损坏容错（读失败按无残留处理）
- 启动前按检查点幂等清残留（end 用传入 game_id、失败不阻塞、成功后清盘）
- app/start 成功写检查点；7002（房间重复游戏）失败时回调告警、不写检查点
- 采集器侧启动失败经事件总线发 core.error（WebUI 告警通道）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
    BiliDanmakuOfficialCollector,
)
from src.modules.collectors.bilibili.official.client.game_checkpoint import GameIdCheckpoint
from src.modules.collectors.bilibili.official.client.websocket_client import BiliWebSocketClient
from src.modules.events.names import CoreEvents


def _make_checkpoint(tmp_path: Path) -> GameIdCheckpoint:
    return GameIdCheckpoint(path=tmp_path / "bili_game_id.json")


def _make_client(
    checkpoint: GameIdCheckpoint,
    start_error_callback: Any = None,
) -> BiliWebSocketClient:
    return BiliWebSocketClient(
        id_code="test-code",
        app_id=123,
        access_key="ak",
        access_key_secret="sk",
        api_host="https://mock.biliapi.test",
        game_id_checkpoint=checkpoint,
        start_error_callback=start_error_callback,
    )


class _FakeResponse:
    """替代 requests.Response：只暴露 json() / raise_for_status()。"""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


def _install_post_spy(client: BiliWebSocketClient, responses: list[_FakeResponse]) -> list[dict[str, Any]]:
    """替换 _post_openapi 捕获请求体，按序返回预设响应。"""
    calls: list[dict[str, Any]] = []

    def fake_post(*, url: str, headers: dict[str, str], body: str, timeout: int) -> _FakeResponse:
        calls.append({"url": url, "body": json.loads(body)})
        return responses.pop(0)

    client._post_openapi = fake_post  # type: ignore[method-assign]
    return calls


# ---------------- GameIdCheckpoint ----------------


async def test_checkpoint_save_load_clear(tmp_path: Path) -> None:
    """写入的 game_id 可读回，clear 后归零"""
    cp = _make_checkpoint(tmp_path)
    assert cp.load() == ""

    cp.save("game-123")
    assert cp.load() == "game-123"

    cp.clear()
    assert cp.load() == ""


async def test_checkpoint_corrupt_file_treated_as_empty(tmp_path: Path) -> None:
    """损坏的检查点文件读失败按无残留处理，不抛异常"""
    cp = _make_checkpoint(tmp_path)
    cp.path.parent.mkdir(parents=True, exist_ok=True)
    cp.path.write_text("not-json{{", encoding="utf-8")

    assert cp.load() == ""


# ---------------- 启动前清残留 ----------------


async def test_clear_stale_game_ends_and_clears_checkpoint(tmp_path: Path) -> None:
    """存在残留检查点：用传入的 game_id 调 end 并清空检查点"""
    cp = _make_checkpoint(tmp_path)
    cp.save("stale-game-1")
    client = _make_client(cp)
    client.game_id = ""  # 实例无状态：end 必须走传入参数而非实例属性

    calls = _install_post_spy(client, [_FakeResponse({"code": 0, "message": "0"})])
    await client._clear_stale_game()

    assert len(calls) == 1
    assert calls[0]["url"].endswith("/v2/app/end")
    assert calls[0]["body"]["game_id"] == "stale-game-1"
    assert cp.load() == ""


async def test_clear_stale_game_end_failure_not_blocking(tmp_path: Path) -> None:
    """残留已失效等 end 错误一律忽略，检查点照常清空，不抛异常"""
    cp = _make_checkpoint(tmp_path)
    cp.save("stale-game-2")
    client = _make_client(cp)
    # 服务端报错（如残留已失效）
    _install_post_spy(client, [_FakeResponse({"code": 7002, "message": "房间重复游戏"})])

    await client._clear_stale_game()

    assert cp.load() == ""


async def test_clear_stale_game_no_checkpoint_is_noop(tmp_path: Path) -> None:
    """无残留检查点：不发任何请求"""
    cp = _make_checkpoint(tmp_path)
    client = _make_client(cp)
    calls = _install_post_spy(client, [])

    await client._clear_stale_game()

    assert calls == []


# ---------------- app/start 与检查点、告警回调 ----------------


def _start_ok_response(game_id: str) -> _FakeResponse:
    return _FakeResponse(
        {
            "code": 0,
            "data": {
                "game_info": {"game_id": game_id},
                "websocket_info": {"wss_link": ["wss://mock"], "auth_body": "auth"},
            },
        }
    )


async def test_start_success_writes_checkpoint(tmp_path: Path) -> None:
    """app/start 成功拿到 game_id 即写检查点"""
    cp = _make_checkpoint(tmp_path)
    client = _make_client(cp)
    _install_post_spy(client, [_start_ok_response("game-live-9")])

    wss_link, auth_body = await client._get_websocket_info()

    assert wss_link == "wss://mock"
    assert auth_body == "auth"
    assert cp.load() == "game-live-9"


async def test_start_7002_notifies_callback_without_checkpoint(tmp_path: Path) -> None:
    """app/start 撞 7002：启动失败经回调上抛，且不写检查点"""
    cp = _make_checkpoint(tmp_path)
    notified: list[str] = []

    async def on_start_error(message: str) -> None:
        notified.append(message)

    client = _make_client(cp, start_error_callback=on_start_error)
    _install_post_spy(client, [_FakeResponse({"code": 7002, "message": "房间重复游戏"})])

    wss_link, auth_body = await client._get_websocket_info()

    assert wss_link is None
    assert auth_body is None
    assert len(notified) == 1
    assert "7002" in notified[0]
    assert cp.load() == ""


async def test_start_callback_exception_swallowed(tmp_path: Path) -> None:
    """回调自身异常不外溢，不影响重试循环"""

    async def broken_callback(message: str) -> None:
        raise RuntimeError("callback boom")

    cp = _make_checkpoint(tmp_path)
    client = _make_client(cp, start_error_callback=broken_callback)
    _install_post_spy(client, [_FakeResponse({"code": 7002, "message": "房间重复游戏"})])

    wss_link, _ = await client._get_websocket_info()
    assert wss_link is None


async def test_end_app_success_clears_checkpoint(tmp_path: Path) -> None:
    """正常 end 成功后清空检查点；end 失败则保留供下次启动再清"""
    cp = _make_checkpoint(tmp_path)
    client = _make_client(cp)
    client.game_id = "game-live-9"
    cp.save("game-live-9")

    _install_post_spy(client, [_FakeResponse({"code": 0, "message": "0"})])
    await client._end_app()
    assert cp.load() == ""

    cp.save("game-live-10")
    _install_post_spy(client, [_FakeResponse({"code": 1, "message": "err"})])
    await client._end_app()
    assert cp.load() == "game-live-10"


# ---------------- 采集器侧告警外显 ----------------


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def emit(self, event_name: str, payload: Any, **kwargs: Any) -> None:
        self.events.append((event_name, payload))


async def test_collector_start_error_emits_core_error() -> None:
    """采集器把客户端启动失败经事件总线发 core.error（WebUI 通道）"""
    collector = BiliDanmakuOfficialCollector(
        config={"id_code": "x", "app_id": "y", "access_key": "z", "access_key_secret": "w"},
        event_bus=_FakeBus(),
    )

    await collector._emit_start_error_alert("B 站应用启动失败（code=7002）: 房间重复游戏")

    assert len(collector._event_bus.events) == 1  # type: ignore[attr-defined]
    event_name, payload = collector._event_bus.events[0]  # type: ignore[attr-defined]
    assert event_name == CoreEvents.CORE_ERROR
    assert "7002" in payload.message
    assert payload.data == {"collector": collector.name}


@pytest.mark.parametrize("stale", ["", None])
async def test_end_app_noop_without_game_id(tmp_path: Path, stale: Any) -> None:
    """无 game_id（实例与传入皆空）时 end 直接跳过"""
    cp = _make_checkpoint(tmp_path)
    client = _make_client(cp)
    client.game_id = ""
    calls = _install_post_spy(client, [])

    await client._end_app(game_id=stale)

    assert calls == []


# ---------------- 清残留与首次启动之间的冷却间隔 ----------------


async def _run_first_attempt_and_stop(
    client: BiliWebSocketClient,
    monkeypatch: pytest.MonkeyPatch,
) -> list[float]:
    """让 run() 走完清残留与首次连接尝试即退出，收集其间每一次 sleep 时长。"""
    import asyncio as _asyncio

    sleeps: list[float] = []
    real_sleep = _asyncio.sleep

    async def fake_sleep(delay: Any, *args: Any, **kwargs: Any) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)

    async def fake_connect() -> None:
        client.is_started = False  # 首次连接尝试后即退出运行循环
        return None

    client._connect = fake_connect  # type: ignore[method-assign]
    await client.run(message_handler=lambda *a, **k: None)
    return sleeps


async def test_run_waits_after_clearing_stale_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真清了残留：首次连接前等待冷却间隔，躲 B 站 end→start 冷却（code=7001）。"""
    from src.modules.collectors.bilibili.official.client.websocket_client import STALE_CLEAR_COOLDOWN_S

    cp = _make_checkpoint(tmp_path)
    cp.save("stale-game-3")
    client = _make_client(cp)
    _install_post_spy(client, [_FakeResponse({"code": 0, "message": "0"})])

    sleeps = await _run_first_attempt_and_stop(client, monkeypatch)

    assert sleeps[0] == STALE_CLEAR_COOLDOWN_S, "清残留后必须先等冷却再首次连接"


async def test_run_starts_immediately_without_stale_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """无残留：不做冷却等待，照常立即启动。"""
    from src.modules.collectors.bilibili.official.client.websocket_client import STALE_CLEAR_COOLDOWN_S

    client = _make_client(_make_checkpoint(tmp_path))
    _install_post_spy(client, [])

    sleeps = await _run_first_attempt_and_stop(client, monkeypatch)

    assert STALE_CLEAR_COOLDOWN_S not in sleeps, "没清残留不得引入冷却等待"
