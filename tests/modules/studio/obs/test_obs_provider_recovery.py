"""OBSProvider 断连自动恢复循环单测。

mock obs-websocket-py 适配层（不发真实 WebSocket 请求），重试脚本化在
``_connect_obs`` 替身上——验证：
- invoke 捕获连接类异常摘工具后自动触发恢复循环，重试成功拉回连接
- 手动 disconnect 取消恢复循环（手动断开不是故障，不得再拉连接）
- 恢复循环期间连接已被手动路径恢复 → 让位退出，不再重连
- 重复 ``start_recovery`` 幂等，不叠任务

时序纪律：退避参数用毫秒级极小值（1ms），不写真实 sleep 等待业务发生；
轮询用 ``_wait_until``（短步进 + 超时断言），与 MCP 恢复循环测试同手法。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from src.modules.studio.obs import obs_client
from src.modules.studio.obs import obs_provider as obs_mod
from src.modules.tools.models import ToolInvocation


def _make_obs_provider(monkeypatch: pytest.MonkeyPatch) -> "obs_mod.OBSProvider":
    monkeypatch.setattr(obs_client, "ObsClient", MagicMock())
    return obs_mod.OBSProvider(
        config={
            "type": "obs_control",
            "host": "127.0.0.1",
            "port": 4455,
            "password": "",
            "text_source_name": "text",
            "typewriter_enabled": False,
            "typewriter_speed": 0.1,
            "typewriter_delay": 0.5,
            "test_on_connect": False,
        },
        event_bus=None,
    )


def _scripted_connect(provider: "obs_mod.OBSProvider", succeed_on_call: int) -> list[int]:
    """把 ``_connect_obs`` 换成计数替身：第 succeed_on_call 次起连上（含 mark_connected）。"""
    calls = [0]

    async def fake_connect() -> bool:
        calls[0] += 1
        if calls[0] >= succeed_on_call:
            provider.mark_connected()
            return True
        return False

    provider._connect_obs = fake_connect  # type: ignore[method-assign]
    return calls


def _fast_recovery(provider: "obs_mod.OBSProvider") -> None:
    """把生产代码里的 start_recovery() 默认退避（5s）替换为毫秒级，测试不等真实时间。

    仍走原方法（含幂等判定），只改退避参数——触发点本身由调用与否验证。
    """
    original = provider.start_recovery

    def fast_start(*, base_delay_ms: int = 1, cap_delay_ms: int = 2) -> None:
        original(base_delay_ms=base_delay_ms, cap_delay_ms=cap_delay_ms)

    provider.start_recovery = fast_start  # type: ignore[method-assign]


async def _wait_until(predicate: Callable[[], Any], timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "等待超时"
        await asyncio.sleep(0.005)


# invoke 断连 → 触发恢复 → 重试成功拉回连接


async def test_invoke_connection_error_triggers_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """invoke 捕获连接类异常摘工具后启动恢复循环，重试成功后连接与工具都回来。"""
    provider = _make_obs_provider(monkeypatch)
    _fast_recovery(provider)
    provider.mark_connected()
    provider._has_started = True
    # send_text 业务协程直接抛连接类异常，模拟 OBS 中途崩溃断连
    provider._send_text_to_obs = _raise_connection_error  # type: ignore[method-assign]

    result = await provider.invoke(
        ToolInvocation(tool_name=provider._connected_tools()[0].full_name, arguments={"text": "hi"})
    )
    assert not result.success
    assert provider._connected is False and provider.list_tools() == []
    assert provider.recovering

    calls = _scripted_connect(provider, succeed_on_call=3)  # 前两轮重试失败，第三轮连上
    await _wait_until(lambda: not provider.recovering)
    assert provider._connected is True
    assert len(provider.list_tools()) == 4
    assert calls[0] == 3


async def _raise_connection_error(text: str, typewriter: Any = None) -> bool:
    raise ConnectionResetError("连接被关闭")


# 手动 disconnect 抑制重连


async def test_manual_disconnect_cancels_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """手动 disconnect 取消恢复循环，之后不再发起重试（手动断开不是故障）。"""
    provider = _make_obs_provider(monkeypatch)
    _fast_recovery(provider)
    provider.mark_connected()
    provider._has_started = True
    provider._send_text_to_obs = _raise_connection_error  # type: ignore[method-assign]
    await provider.invoke(ToolInvocation(tool_name=provider._connected_tools()[0].full_name, arguments={"text": "hi"}))
    assert provider.recovering

    calls = _scripted_connect(provider, succeed_on_call=1)
    await _wait_until(lambda: calls[0] >= 1)  # 循环已在跑
    await provider.disconnect()
    assert not provider.recovering
    assert calls[0] == 1  # 取消后不再重试


async def test_cleanup_cancels_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """cleanup 同样取消恢复循环。"""
    provider = _make_obs_provider(monkeypatch)
    _fast_recovery(provider)
    provider.mark_connected()
    provider._has_started = True
    provider._send_text_to_obs = _raise_connection_error  # type: ignore[method-assign]
    await provider.invoke(ToolInvocation(tool_name=provider._connected_tools()[0].full_name, arguments={"text": "hi"}))
    assert provider.recovering

    calls = _scripted_connect(provider, succeed_on_call=1)
    await _wait_until(lambda: calls[0] >= 1)
    await provider.cleanup()
    assert not provider.recovering


# 恢复循环期间已被手动恢复 → 让位退出


async def test_recovery_yields_when_manually_reconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复循环发现连接已由手动路径恢复（_connected=True）即退出，退出后不再重试。"""
    provider = _make_obs_provider(monkeypatch)
    _fast_recovery(provider)
    provider.mark_connected()
    provider._has_started = True
    provider._send_text_to_obs = _raise_connection_error  # type: ignore[method-assign]
    await provider.invoke(ToolInvocation(tool_name=provider._connected_tools()[0].full_name, arguments={"text": "hi"}))

    calls = _scripted_connect(provider, succeed_on_call=1000)  # 替身始终失败，逼出让位语义
    await _wait_until(lambda: calls[0] >= 1)
    provider.mark_connected()  # 模拟面板手动重连先一步恢复
    await _wait_until(lambda: not provider.recovering)
    # 退出前的尝试次数是时序窗口（退避 1–2ms 快于观察粒度，满载下 mark_connected
    # 晚一拍循环就多跑一拍），不绑定具体值；契约是退出后静止——让位退出由
    # recovering 转 False 承载（重连后端恒失败时这是唯一退出路径）。
    attempts_at_exit = calls[0]
    await asyncio.sleep(0.05)  # 数个退避周期：循环若未真正退出，计数必然增长
    assert calls[0] == attempts_at_exit  # 让位退出后不再重连


# 重复 start_recovery 幂等


async def test_start_recovery_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """已有未完成恢复循环时再次 start_recovery 不叠任务。"""
    provider = _make_obs_provider(monkeypatch)
    calls = _scripted_connect(provider, succeed_on_call=1000)  # 恒失败，循环保持运行
    provider.start_recovery(base_delay_ms=1, cap_delay_ms=2)
    first_task = provider._recover_task
    provider.start_recovery(base_delay_ms=1, cap_delay_ms=2)
    provider.start_recovery(base_delay_ms=1, cap_delay_ms=2)
    assert provider._recover_task is first_task
    await _wait_until(lambda: calls[0] >= 2)
    assert calls[0] == 2  # 只有一个循环在重试


async def test_setup_failure_starts_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """setup 装配期连接失败（如开机时 OBS 还没开）进入后台恢复循环。"""
    provider = _make_obs_provider(monkeypatch)
    _fast_recovery(provider)
    calls = _scripted_connect(provider, succeed_on_call=2)
    await provider.setup()
    assert provider.recovering and calls[0] == 1
    await _wait_until(lambda: not provider.recovering)
    assert provider._connected is True
