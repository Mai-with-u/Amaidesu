"""
测试 LocalLLMDecisionProvider 统计功能

运行: uv run pytest tests/domains/decision/providers/test_local_llm_decision_provider.py -v
"""

import pytest

from src.domains.decision.providers.local_llm.local_llm_decision_provider import LocalLLMDecisionProvider


class TestLocalLLMStatistics:
    """测试运行时统计功能"""

    @pytest.mark.asyncio
    async def test_get_statistics_initial(self):
        """测试初始统计状态"""
        provider = LocalLLMDecisionProvider({"backend": "llm"})

        stats = provider.get_statistics()

        assert stats["total_requests"] == 0
        assert stats["successful_requests"] == 0
        assert stats["failed_requests"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["client_type"] == "llm"

    @pytest.mark.asyncio
    async def test_get_statistics_after_requests(self):
        """测试请求后的统计"""
        provider = LocalLLMDecisionProvider({"backend": "llm"})

        # 模拟统计
        provider._total_requests = 10
        provider._successful_requests = 8
        provider._failed_requests = 2

        stats = provider.get_statistics()

        assert stats["total_requests"] == 10
        assert stats["successful_requests"] == 8
        assert stats["failed_requests"] == 2
        assert stats["success_rate"] == 80.0

    @pytest.mark.asyncio
    async def test_get_info_does_not_include_stats(self):
        """测试 get_info() 不包含统计数据"""
        provider = LocalLLMDecisionProvider({"backend": "llm"})

        # 设置一些统计数据
        provider._total_requests = 10
        provider._successful_requests = 8

        info = provider.get_info()

        # 验证：get_info() 只返回配置信息，不包含统计数据
        assert "total_requests" not in info
        assert "successful_requests" not in info
        assert "failed_requests" not in info
        assert "client_type" in info
        assert "fallback_mode" in info
