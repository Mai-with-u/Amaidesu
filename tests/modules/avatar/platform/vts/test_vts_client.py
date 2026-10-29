"""VTS 客户端：令牌登录与申请、按请求编号对回复、发完就走、断开收尾（假连接，不连真实 VTS）"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.modules.avatar.platform.vts.vts_client import VtsApiError, VtsClient
from tests.modules.avatar.platform.vts.fakes import FakeVtsSocket, api_error, socket_connector


def _client(socket: FakeVtsSocket, token_path: Path) -> VtsClient:
    return VtsClient(
        host="localhost",
        port=8001,
        plugin_name="Amaidesu",
        plugin_developer="test",
        token_path=token_path,
        connector=socket_connector(socket),
    )


async def test_existing_token_logs_in_without_popup(tmp_path: Path) -> None:
    token = tmp_path / "vts_token.txt"
    token.write_text("saved-token", encoding="utf-8")
    socket = FakeVtsSocket()
    client = _client(socket, token)
    await client.connect()
    assert client.authenticated and client.is_open and socket.url == "ws://localhost:8001"
    assert socket.sent_types() == ["AuthenticationRequest"]
    assert socket.sent[0]["data"]["authenticationToken"] == "saved-token"
    await client.close()
    assert socket.closed and not client.is_open


@pytest.mark.parametrize("saved", [None, "revoked-token"])
async def test_missing_or_revoked_token_requests_a_new_one_and_saves_it(tmp_path: Path, saved: str | None) -> None:
    token = tmp_path / "data" / "vts_token.txt"
    if saved is not None:
        token.parent.mkdir(parents=True)
        token.write_text(saved, encoding="utf-8")
    socket = FakeVtsSocket(
        {"AuthenticationRequest": lambda data: {"authenticated": data["authenticationToken"] == "new-token"}}
    )
    client = _client(socket, token)
    await client.connect()
    expected = ["AuthenticationTokenRequest", "AuthenticationRequest"]
    assert socket.sent_types() == (expected if saved is None else ["AuthenticationRequest", *expected])
    assert token.read_text(encoding="utf-8") == "new-token" and client.authenticated
    await client.close()


async def test_denied_popup_fails_connect_and_closes(tmp_path: Path) -> None:
    socket = FakeVtsSocket({"AuthenticationTokenRequest": api_error(50, "用户拒绝了授权")})
    client = _client(socket, tmp_path / "vts_token.txt")
    with pytest.raises(VtsApiError, match="50"):
        await client.connect()
    assert not client.is_open and socket.closed


async def test_replies_are_matched_by_request_id_even_out_of_order(tmp_path: Path) -> None:
    socket = FakeVtsSocket({"EchoRequest": lambda data: {"echo": data["n"]}})
    client = _client(socket, tmp_path / "t.txt")
    await client.connect()
    socket.hold = True
    first = asyncio.create_task(client.request("EchoRequest", {"n": 1}))
    second = asyncio.create_task(client.request("EchoRequest", {"n": 2}))
    while len(socket.held) < 2:
        await asyncio.sleep(0)
    await socket.release([1, 0])  # 后发的先回
    assert (await first, await second) == ({"echo": 1}, {"echo": 2})
    await client.close()


async def test_send_does_not_wait_and_rejected_injection_is_only_logged(tmp_path: Path) -> None:
    socket = FakeVtsSocket({"InjectParameterDataRequest": api_error(453, "参数不存在")})
    client = _client(socket, tmp_path / "t.txt")
    await client.connect()
    socket.hold = True
    await client.send("InjectParameterDataRequest", {"parameterValues": []})  # 回复还扣着也立刻返回
    socket.hold = False
    await socket.release([0])
    assert await client.request("AuthenticationRequest", {}) == {"authenticated": True}  # 报错的注入不影响后续请求
    with pytest.raises(VtsApiError):
        await client.request("InjectParameterDataRequest", {})
    await client.close()


async def test_remote_drop_fails_pending_request_and_marks_closed(tmp_path: Path) -> None:
    socket = FakeVtsSocket()
    client = _client(socket, tmp_path / "t.txt")
    await client.connect()
    socket.hold = True
    pending = asyncio.create_task(client.request("AuthenticationRequest", {}))
    while not socket.held:
        await asyncio.sleep(0)
    await socket.drop()
    with pytest.raises(ConnectionError):
        await pending
    assert not client.is_open
    await client.close()
