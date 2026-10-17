"""B 站官方弹幕应用心跳告警节流与会话失效自动重开测试

覆盖：
- 连续同因失败只在首条记 warning（附失败计数），重复降 debug，恢复成功记 info 并复位
- 失败原因变化后重新 warning
- 会话级失效（7003 心跳过期/GameId 错误）：自动重开开启时抛 AppSessionExpiredError
  穿出心跳循环触发既有 end → 重连路径；关闭时按普通失败节流告警、不抛
- 失败期间心跳间隔指数退避，恢复后回到正常节奏
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List

import pytest

from src.modules.collectors.bilibili.official.client.game_checkpoint import GameIdCheckpoint
from src.modules.collectors.bilibili.official.client.websocket_client import (
    AppSessionExpiredError,
    BiliWebSocketClient,
)


class _FakeResponse:
    """替代 requests.Response：只暴露 json() / raise_for_status()。"""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


def _make_client(reopen: bool = True) -> BiliWebSocketClient:
    client = BiliWebSocketClient(
        id_code="test-code",
        app_id=123,
        access_key="ak",
        access_key_secret="sk",
        api_host="https://mock.biliapi.test",
        game_id_checkpoint=GameIdCheckpoint(),
        reopen_on_session_expired=reopen,
    )
    client.game_id = "game-hb-1"  # 无 game_id 时心跳直接跳过，测试前置一个
    return client


def _install_responses(client: BiliWebSocketClient, bodies: List[dict[str, Any]]) -> None:
    """按序返回预设心跳响应；耗尽后复用最后一个（供退避循环连续失败用）。"""

    state = {"index": 0}

    def fake_post(*, url: str, headers: dict[str, str], body: str, timeout: int) -> _FakeResponse:
        index = min(state["index"], len(bodies) - 1)
        state["index"] += 1
        assert json.loads(body)["game_id"] == "game-hb-1"
        return _FakeResponse(bodies[index])

    client._post_openapi = fake_post  # type: ignore[method-assign]


def _records(loguru_capture: Any, level: str, keyword: str) -> list[Any]:
    return [r for r in loguru_capture.records if r["level"] == level and keyword in r["message"]]


async def test_consecutive_failures_warn_once_then_debug_and_recover(loguru_capture: Any) -> None:
    """连续同因失败只 warning 一次，重复降 debug；恢复成功记 info 并复位状态。"""
    client = _make_client()
    _install_responses(client, [{"code": 500, "message": "server busy"}])

    for _ in range(5):
        await client._send_app_heartbeat()

    assert len(_records(loguru_capture, "WARNING", "应用心跳失败")) == 1
    assert "第 1 次" in _records(loguru_capture, "WARNING", "应用心跳失败")[0]["message"]
    assert len(_records(loguru_capture, "DEBUG", "仍失败（同因")) == 4

    _install_responses(client, [{"code": 0, "message": "0"}])
    await client._send_app_heartbeat()

    assert len(_records(loguru_capture, "INFO", "应用心跳恢复成功")) == 1
    assert "连续失败 5 次" in _records(loguru_capture, "INFO", "应用心跳恢复成功")[0]["message"]
    assert client._app_hb_fail_streak == 0 and client._app_hb_fail_reason == ""


async def test_reason_change_rewarns(loguru_capture: Any) -> None:
    """失败原因变化（含瞬时网络异常形态）视为新因，重新 warning。"""
    client = _make_client()
    _install_responses(client, [{"code": 500, "message": "server busy"}])
    await client._send_app_heartbeat()
    await client._send_app_heartbeat()
    assert len(_records(loguru_capture, "WARNING", "应用心跳失败")) == 1

    _install_responses(client, [{"code": 7001, "message": "请求冷却期"}])
    await client._send_app_heartbeat()

    assert len(_records(loguru_capture, "WARNING", "应用心跳失败")) == 2
    assert "7001" in _records(loguru_capture, "WARNING", "应用心跳失败")[-1]["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 7003, "message": "心跳过期或GameId错误"},
        {"code": -99, "message": "心跳过期"},  # 兜底按 message 判别，不依赖单一 code
    ],
    ids=["code-7003", "message-expired"],
)
async def test_session_expired_raises_to_trigger_reopen(loguru_capture: Any, payload: dict[str, Any]) -> None:
    """自动重开开启：会话级失效抛 AppSessionExpiredError，穿出任务组触发既有重连路径。"""
    client = _make_client(reopen=True)
    _install_responses(client, [payload])

    with pytest.raises(AppSessionExpiredError):
        await client._send_app_heartbeat()

    # 明确告知重开意图，且不落入普通失败节流通道
    assert len(_records(loguru_capture, "INFO", "触发自动重开会话")) == 1
    assert _records(loguru_capture, "WARNING", "应用心跳失败") == []


async def test_session_expired_without_reopen_only_throttles(loguru_capture: Any) -> None:
    """自动重开关闭：会话级失效按普通失败节流告警，不抛错。"""
    client = _make_client(reopen=False)
    _install_responses(client, [{"code": 7003, "message": "心跳过期或GameId错误"}])

    for _ in range(3):
        await client._send_app_heartbeat()

    assert len(_records(loguru_capture, "WARNING", "应用心跳失败")) == 1
    assert len(_records(loguru_capture, "DEBUG", "仍失败（同因")) == 2


async def test_heartbeat_interval_backs_off_on_failures(loguru_capture: Any) -> None:
    """失败期间心跳间隔指数退避（封顶 120s），恢复后回到正常节奏。"""
    client = _make_client()
    _install_responses(
        client,
        [
            {"code": 500, "message": "server busy"},
            {"code": 500, "message": "server busy"},
            {"code": 0, "message": "0"},
        ],
    )
    sleeps: List[float] = []
    client.is_started = True

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 3:
            client.is_started = False

    monkey_sleep = pytest.MonkeyPatch()
    monkey_sleep.setattr(asyncio, "sleep", fake_sleep)
    try:
        await asyncio.wait_for(client._app_heartbeat_loop(), timeout=5)
    finally:
        monkey_sleep.undo()

    assert sleeps == [40.0, 80.0, 20.0]  # 两次失败翻倍封顶前递增，恢复后回 20s
