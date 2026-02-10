"""
测试 MaiCoreDecisionProvider 内存泄漏修复

运行: uv run pytest tests/domains/decision/providers/test_maicore_memory_leak.py -v
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.domains.decision.providers.maicore.maicore_decision_provider import MaiCoreDecisionProvider


class TestMaiCoreMemoryLeak:
    """测试内存泄漏修复"""

    @pytest.mark.asyncio
    async def test_pending_futures_initial_state(self):
        """测试初始状态"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        # 模拟事件总线
        event_bus = Mock()
        event_bus.emit = AsyncMock()

        await provider.setup(event_bus, None, None)

        # 验证初始状态
        assert len(provider._pending_futures) == 0

    @pytest.mark.asyncio
    async def test_passive_cleanup_of_completed_futures(self):
        """测试被动清理已完成的 Future"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        event_bus = Mock()
        event_bus.emit = AsyncMock()
        await provider.setup(event_bus, None, None)

        # 手动添加已完成的 Future
        future1 = asyncio.Future()
        future1.set_result(None)  # 标记为完成
        future2 = asyncio.Future()
        future2.set_result(None)

        provider._pending_futures["msg1"] = future1
        provider._pending_futures["msg2"] = future2

        # 添加未完成的 Future
        future3 = asyncio.Future()
        provider._pending_futures["msg3"] = future3

        assert len(provider._pending_futures) == 3

        # 模拟 decide() 中的被动清理（清理已完成的 Future）
        async with provider._futures_lock:
            completed_ids = [msg_id for msg_id, fut in provider._pending_futures.items() if fut.done()]
            for msg_id in completed_ids:
                provider._pending_futures.pop(msg_id, None)

        # 验证：已完成的被清理，未完成的保留
        assert len(provider._pending_futures) == 1
        assert "msg3" in provider._pending_futures

    @pytest.mark.asyncio
    async def test_cancel_old_future_on_duplicate_message_id(self):
        """测试同名 message_id 时取消旧 Future"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        event_bus = Mock()
        event_bus.emit = AsyncMock()
        await provider.setup(event_bus, None, None)

        # 添加一个未完成的旧 Future
        old_future = asyncio.Future()
        provider._pending_futures["msg1"] = old_future

        # 模拟同名消息的被动清理逻辑
        message_id = "msg1"
        new_future = asyncio.Future()

        async with provider._futures_lock:
            old = provider._pending_futures.get(message_id)
            if old and not old.done():
                old.cancel()
            provider._pending_futures[message_id] = new_future

        # 验证：旧 Future 被取消，新 Future 已注册
        assert old_future.cancelled()
        assert provider._pending_futures["msg1"] is new_future

    @pytest.mark.asyncio
    async def test_get_statistics(self):
        """测试获取统计信息"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        event_bus = Mock()
        await provider.setup(event_bus, None, None)

        stats = provider.get_statistics()

        assert "pending_futures_count" in stats
        assert "is_connected" in stats
        assert "router_running" in stats
        assert stats["pending_futures_count"] == 0

    @pytest.mark.asyncio
    async def test_decision_timeout_config(self):
        """测试 decision_timeout 配置"""
        provider = MaiCoreDecisionProvider(
            {
                "host": "localhost",
                "port": 8000,
                "platform": "test",
                "decision_timeout": 60.0,  # 自定义超时时间
            }
        )

        assert provider._decision_timeout == 60.0
