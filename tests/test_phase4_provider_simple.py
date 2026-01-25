"""
Phase 4 Provider验证测试（简化版）

重点：验证Provider基本功能，不涉及复杂的类型检查
"""

import asyncio
from typing import Dict, Any

from src.core.event_bus import EventBus
from src.core.output_provider_manager import OutputProviderManager
from src.expression.expression_generator import ExpressionGenerator
from src.understanding.intent import Intent, EmotionType


class SimpleMockProvider:
    """简化的Mock Provider用于测试"""

    def __init__(self, config: Dict[str, Any], event_bus=None, core=None):
        self.config = config
        self.event_bus = event_bus
        self.core = core
        self.is_setup = False
        self.render_called_count = 0
        self.render_called_params = []

    async def setup(self, event_bus):
        """设置Mock Provider"""
        self.event_bus = event_bus
        self.is_setup = True
        return True

    async def cleanup(self):
        """清理Mock Provider"""
        self.is_setup = False

    async def render(self, parameters):
        """渲染（简化版）"""
        self.render_called_count += 1
        self.render_called_params.append(parameters)
        # 模拟渲染耗时
        await asyncio.sleep(0.01)
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "SimpleMockProvider",
            "type": "test",
            "is_setup": self.is_setup,
        }


class TestProviderBasicFunctionality:
    """测试Provider基本功能"""

    @pytest.mark.asyncio
    async def test_provider_setup_and_render(self):
        """测试Provider的setup和render功能"""
        event_bus = EventBus(enable_stats=False)

        provider = SimpleMockProvider({}, event_bus)

        # 测试setup
        assert provider.is_setup is False
        result = await provider.setup(event_bus)
        assert result is True
        assert provider.is_setup is True

        # 测试render
        assert provider.render_called_count == 0
        await provider.render(None)
        assert provider.render_called_count == 1

    @pytest.mark.asyncio
    async def test_multiple_providers_concurrent_render(self):
        """测试多个Provider并发渲染"""
        event_bus = EventBus(enable_stats=False)
        expression_generator = ExpressionGenerator()
        output_manager = OutputProviderManager()

        # 创建3个Mock Provider
        providers = [
            SimpleMockProvider({}, event_bus),
            SimpleMockProvider({}, event_bus),
            SimpleMockProvider({}, event_bus),
        ]

        # 注册并setup
        for provider in providers:
            await output_manager.register_provider(provider)
        await provider.setup(event_bus)

        # 创建Intent
        intent = Intent(
            original_text="测试并发",
            emotion=EmotionType.HAPPY,
            response_text="并发测试响应",
            actions=[],
            metadata={},
        )

        params = await expression_generator.generate(intent)

        # 并发渲染
        await output_manager.render_all(params)

        # 验证所有Provider都被调用
        for provider in providers:
            assert provider.render_called_count == 1
            assert len(provider.render_called_params) == 1

    @pytest.mark.asyncio
    async def test_provider_lifecycle(self):
        """测试Provider生命周期"""
        event_bus = EventBus(enable_stats=False)
        provider = SimpleMockProvider({}, event_bus)

        # 1. Setup
        assert provider.is_setup is False
        await provider.setup(event_bus)
        assert provider.is_setup is True

        # 2. Cleanup
        await provider.cleanup()
        assert provider.is_setup is False


class TestOutputProviderManagerConfig:
    """测试OutputProviderManager配置加载"""

    @pytest.mark.asyncio
    async def test_create_provider_factory(self):
        """测试Provider工厂方法"""
        manager = OutputProviderManager()

        # 测试创建不存在的Provider类型
        provider = manager._create_provider("invalid_type", {}, None)
        assert provider is None

        # 测试创建SimpleMockProvider
        # 注意：SimpleMockProvider不在provider_classes中，所以会返回None
        provider = manager._create_provider("simple_mock", {}, None)
        assert provider is None

    @pytest.mark.asyncio
    async def test_load_from_config_empty_outputs(self):
        """测试加载空配置"""
        config = {
            "enabled": True,
            "outputs": [],
        }

        manager = OutputProviderManager()
        await manager.load_from_config(config, core=None)

        assert len(manager.providers) == 0


class TestExpressionGeneratorIntegration:
    """测试ExpressionGenerator集成"""

    @pytest.mark.asyncio
    async def test_expression_parameters_creation(self):
        """测试ExpressionParameters创建"""
        expression_generator = ExpressionGenerator()

        intent = Intent(
            original_text="测试",
            emotion=EmotionType.HAPPY,
            response_text="测试响应",
            actions=[],
            metadata={},
        )

        params = await expression_generator.generate(intent)

        # 验证基本属性
        assert params.tts_text == "测试响应"
        assert params.subtitle_text == "测试响应"
        assert params.tts_enabled is True
        assert params.subtitle_enabled is True
        assert params.expressions_enabled is True

    @pytest.mark.asyncio
    async def test_expression_with_empty_response(self):
        """测试空响应的Expression生成"""
        expression_generator = ExpressionGenerator()

        intent = Intent(
            original_text="测试",
            emotion=EmotionType.NEUTRAL,
            response_text="",  # 空响应
            actions=[],
            metadata={},
        )

        params = await expression_generator.generate(intent)

        # 验证空响应时的处理
        assert params.tts_enabled is False
        assert params.subtitle_enabled is False


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])
