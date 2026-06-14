"""
STTCollector 测试
"""

from src.domains.input.registry import list_collectors


def test_stt_collector_registered():
    """测试 STT Collector 已注册"""
    collectors = list_collectors()
    assert "stt" in collectors


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-s"])
