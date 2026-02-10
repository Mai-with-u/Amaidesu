"""
测试 NormalizerRegistry（pytest）

运行: uv run pytest tests/domains/normalization/test_normalizer_registry.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from src.domains.input.normalization.normalizers import NormalizerRegistry
from src.domains.input.normalization.normalizers.base import DataNormalizer
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.base.raw_data import RawData

# =============================================================================
# Mock Normalizer 类（用于测试）
# =============================================================================


class MockNormalizer(DataNormalizer):
    """Mock DataNormalizer for testing"""

    def can_handle(self, data_type: str) -> bool:
        return data_type == "mock"

    @property
    def priority(self) -> int:
        return 50

    async def normalize(self, raw_data: RawData):
        from src.domains.input.normalization.content import TextContent

        content = TextContent(text="mock")
        return NormalizedMessage(
            text="mock",
            content=content,
            source=raw_data.source,
            data_type=raw_data.data_type,
            importance=0.5,
            metadata=raw_data.metadata.copy(),
            timestamp=raw_data.timestamp,
        )


class AnotherMockNormalizer(DataNormalizer):
    """Another Mock DataNormalizer for testing"""

    def can_handle(self, data_type: str) -> bool:
        return data_type == "another"

    @property
    def priority(self) -> int:
        return 75

    async def normalize(self, raw_data: RawData):
        from src.domains.input.normalization.content import TextContent

        content = TextContent(text="another")
        return NormalizedMessage(
            text="another",
            content=content,
            source=raw_data.source,
            data_type=raw_data.data_type,
            importance=0.5,
            metadata=raw_data.metadata.copy(),
            timestamp=raw_data.timestamp,
        )


class InvalidNormalizer:
    """Not a valid normalizer (doesn't inherit from DataNormalizer)"""

    pass


# =============================================================================
# 注册功能测试
# =============================================================================


def test_register_normalizer():
    """测试注册 Normalizer"""
    # 清理注册表
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        NormalizerRegistry.register(MockNormalizer, "mock")

        assert "mock" in NormalizerRegistry._normalizers
        assert NormalizerRegistry._normalizers["mock"] == MockNormalizer
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


def test_register_normalizer_duplicate():
    """测试重复注册 Normalizer（应覆盖）"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        # 第一次注册
        NormalizerRegistry.register(MockNormalizer, "test_type")
        assert NormalizerRegistry._normalizers["test_type"] == MockNormalizer

        # 重复注册（应该覆盖）
        NormalizerRegistry.register(AnotherMockNormalizer, "test_type")
        assert NormalizerRegistry._normalizers["test_type"] == AnotherMockNormalizer
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


def test_register_multiple_normalizers():
    """测试注册多个 Normalizer"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        NormalizerRegistry.register(MockNormalizer, "mock")
        NormalizerRegistry.register(AnotherMockNormalizer, "another")

        assert "mock" in NormalizerRegistry._normalizers
        assert "another" in NormalizerRegistry._normalizers
        assert len(NormalizerRegistry._normalizers) == 2
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


# =============================================================================
# 获取 Normalizer 测试
# =============================================================================


def test_get_normalizer_registered():
    """测试获取已注册的 Normalizer"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        NormalizerRegistry.register(MockNormalizer, "mock")

        normalizer = NormalizerRegistry.get_normalizer("mock")

        assert normalizer is not None
        assert isinstance(normalizer, MockNormalizer)
        assert normalizer.can_handle("mock") is True
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


def test_get_normalizer_not_registered():
    """测试获取未注册的 Normalizer（应返回 None）"""
    normalizer = NormalizerRegistry.get_normalizer("nonexistent")

    assert normalizer is None


def test_get_normalizer_returns_new_instance():
    """测试每次 get_normalizer 返回新实例"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        NormalizerRegistry.register(MockNormalizer, "mock")

        normalizer1 = NormalizerRegistry.get_normalizer("mock")
        normalizer2 = NormalizerRegistry.get_normalizer("mock")

        # 应该是不同的实例
        assert normalizer1 is not normalizer2
        assert isinstance(normalizer1, MockNormalizer)
        assert isinstance(normalizer2, MockNormalizer)
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


# =============================================================================
# 获取所有 Normalizer 测试
# =============================================================================


def test_get_all_empty():
    """测试获取所有 Normalizer（空注册表）"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        all_normalizers = NormalizerRegistry.get_all()

        assert isinstance(all_normalizers, dict)
        assert len(all_normalizers) == 0
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


def test_get_all_with_registered():
    """测试获取所有 Normalizer（有注册的 Normalizer）"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        NormalizerRegistry.register(MockNormalizer, "mock")
        NormalizerRegistry.register(AnotherMockNormalizer, "another")

        all_normalizers = NormalizerRegistry.get_all()

        assert isinstance(all_normalizers, dict)
        assert len(all_normalizers) == 2
        assert "mock" in all_normalizers
        assert "another" in all_normalizers
        assert all_normalizers["mock"] == MockNormalizer
        assert all_normalizers["another"] == AnotherMockNormalizer
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


def test_get_all_returns_copy():
    """测试 get_all 返回的是副本（修改不影响原注册表）"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        NormalizerRegistry.register(MockNormalizer, "mock")

        all_normalizers = NormalizerRegistry.get_all()
        all_normalizers["new_type"] = AnotherMockNormalizer

        # 原注册表不应该被修改
        assert "new_type" not in NormalizerRegistry._normalizers
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


# =============================================================================
# 内置 Normalizer 自动注册测试
# =============================================================================


def test_builtin_normalizers_auto_registered():
    """测试内置 Normalizer 自动注册"""
    all_normalizers = NormalizerRegistry.get_all()

    # 验证预期的内置 Normalizer 已注册
    assert "text" in all_normalizers
    assert "gift" in all_normalizers
    assert "superchat" in all_normalizers
    assert "guard" in all_normalizers


def test_builtin_normalizers_can_handle():
    """测试内置 Normalizer 的 can_handle 方法"""
    # 测试 TextNormalizer
    text_normalizer = NormalizerRegistry.get_normalizer("text")
    assert text_normalizer is not None
    assert text_normalizer.can_handle("text") is True
    assert text_normalizer.can_handle("gift") is False

    # 测试 GiftNormalizer
    gift_normalizer = NormalizerRegistry.get_normalizer("gift")
    assert gift_normalizer is not None
    assert gift_normalizer.can_handle("gift") is True
    assert gift_normalizer.can_handle("text") is False

    # 测试 SuperChatNormalizer
    superchat_normalizer = NormalizerRegistry.get_normalizer("superchat")
    assert superchat_normalizer is not None
    assert superchat_normalizer.can_handle("superchat") is True
    assert superchat_normalizer.can_handle("text") is False

    # 测试 GuardNormalizer
    guard_normalizer = NormalizerRegistry.get_normalizer("guard")
    assert guard_normalizer is not None
    assert guard_normalizer.can_handle("guard") is True
    assert guard_normalizer.can_handle("text") is False


def test_builtin_normalizers_priority():
    """测试内置 Normalizer 的 priority 属性"""
    # 所有内置 Normalizer 的 priority 应该是 100
    text_normalizer = NormalizerRegistry.get_normalizer("text")
    gift_normalizer = NormalizerRegistry.get_normalizer("gift")
    superchat_normalizer = NormalizerRegistry.get_normalizer("superchat")
    guard_normalizer = NormalizerRegistry.get_normalizer("guard")

    assert text_normalizer.priority == 100
    assert gift_normalizer.priority == 100
    assert superchat_normalizer.priority == 100
    assert guard_normalizer.priority == 100


# =============================================================================
# 边界情况和错误处理测试
# =============================================================================


def test_register_with_empty_data_type():
    """测试使用空字符串作为 data_type 注册"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        # 应该允许空字符串作为 key（虽然不推荐）
        NormalizerRegistry.register(MockNormalizer, "")

        assert "" in NormalizerRegistry._normalizers
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


def test_get_normalizer_case_sensitive():
    """测试 data_type 区分大小写"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        NormalizerRegistry.register(MockNormalizer, "MockType")

        # 大小写不同，应该返回 None
        normalizer = NormalizerRegistry.get_normalizer("mocktype")
        assert normalizer is None

        # 大小写完全匹配才能获取
        normalizer = NormalizerRegistry.get_normalizer("MockType")
        assert normalizer is not None
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


@pytest.mark.asyncio
async def test_normalizer_normalize_method():
    """测试 Normalizer 的 normalize 方法"""
    original_normalizers = NormalizerRegistry._normalizers.copy()
    NormalizerRegistry._normalizers.clear()

    try:
        NormalizerRegistry.register(MockNormalizer, "mock")

        normalizer = NormalizerRegistry.get_normalizer("mock")

        raw_data = RawData(content="test content", source="test", data_type="mock", metadata={})

        result = await normalizer.normalize(raw_data)

        assert result is not None
        assert isinstance(result, NormalizedMessage)
        assert result.text == "mock"
        assert result.source == "test"
        assert result.data_type == "mock"
    finally:
        # 恢复原始注册表
        NormalizerRegistry._normalizers.clear()
        NormalizerRegistry._normalizers.update(original_normalizers)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
