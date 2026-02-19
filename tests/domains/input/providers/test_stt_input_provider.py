"""
STTInputProvider 测试
"""


def test_get_registration_info():
    """测试注册信息"""
    from src.domains.input.providers.stt import STTInputProvider

    info = STTInputProvider.get_registration_info()

    assert info["layer"] == "input"
    assert info["name"] == "stt"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-s"])
