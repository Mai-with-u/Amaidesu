"""McpClient 连接失败日志的同因去重。

常驻调用方（如注意流采集器）会周期性重连，"Mod 没开"是常态而不是事故：
同一个原因只该 warning 一次，理由变了或连上一次之后再失败才重新报，
否则日志会被重复行淹没，真正的信号反而看不见。
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List

import pytest

from src.modules.mcp.client import McpClient
from src.modules.mcp.config import McpServerConfig


class _FakeFastmcpClient:
    """fastmcp.Client 替身：只满足 connect() 用到的上下文管理协议。"""

    def __init__(self, transport: Any, message_handler: Any = None, init_timeout: float | None = None) -> None:
        self.session = None

    async def __aenter__(self) -> "_FakeFastmcpClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


def _client() -> McpClient:
    return McpClient(
        name="probe",
        config=McpServerConfig(enabled=True, transport="http", url="http://127.0.0.1:9/mcp"),
    )


def _raise_with(message: str):
    def _raise() -> Any:
        raise RuntimeError(message)

    return _raise


def _connect_failures(cap: Any) -> List[Dict[str, Any]]:
    return [r for r in cap.records if r["level"] == "WARNING" and "连接失败" in r["message"]]


@pytest.mark.asyncio
async def test_same_failure_warns_once_and_resets_after_success(monkeypatch, loguru_capture) -> None:
    """同因失败只报首次；理由变化或连上一次后再次失败，重新 warning。"""
    client = _client()
    cap = loguru_capture  # fixture 已进入捕获，不要再 with（会重复计数）
    monkeypatch.setattr(client, "_build_transport", _raise_with("boom"))

    assert await client.connect() is False
    assert await client.connect() is False
    assert len(_connect_failures(cap)) == 1, "同因失败重复重试不该重复 warning"
    assert any(r["level"] == "DEBUG" and "同因" in r["message"] for r in cap.records), "重复失败降 debug 留痕"

    monkeypatch.setattr(client, "_build_transport", _raise_with("another"))
    assert await client.connect() is False
    assert len(_connect_failures(cap)) == 2, "失败理由变了要重新报"

    # 连上一次：fake fastmcp.Client 让成功路径可达，失败特征串随之复位
    monkeypatch.setattr(McpClient, "_build_message_handler", staticmethod(lambda subscriptions: None))
    monkeypatch.setitem(sys.modules, "fastmcp", types.SimpleNamespace(Client=_FakeFastmcpClient))
    monkeypatch.setattr(client, "_build_transport", lambda: object())
    assert await client.connect() is True

    monkeypatch.setattr(client, "_build_transport", _raise_with("boom"))
    client._connected = False  # 模拟已连接的服务再次断开，才会进入重连分支。
    assert await client.connect() is False
    assert len(_connect_failures(cap)) == 3, "连上过之后再断，属新事故，要重新 warning"
