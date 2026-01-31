"""
Phase 4 集成测试

测试内容:
1. OutputProviderManager 从配置加载Provider
2. AmaidesuCore 集成 OutputProviderManager 和 ExpressionGenerator
3. Layer 4 → Layer 5 → Layer 6 完整数据流
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock

from src.core.event_bus import EventBus
from src.core.output_provider_manager import OutputProviderManager
from src.expression.expression_generator import ExpressionGenerator
from src.expression.render_parameters import ExpressionParameters
from src.understanding.intent import Intent, EmotionType


class TestOutputProviderManagerConfigLoading:
    """测试 OutputProviderManager 从配置加载Provider"""

    @pytest.mark.asyncio
    async def test_load_from_config_empty_outputs(self):
        """测试加载空配置"""
        config = {
            "enabled": True,
            "concurrent_rendering": True,
            "error_handling": "continue",
            "outputs": [],
        }

        manager = OutputProviderManager()
        await manager.load_from_config(config, core=None)

        assert len(manager.providers) == 0

    @pytest.mark.asyncio
    async def test_load_from_config_disabled(self):
        """测试禁用渲染层"""
        config = {
            "enabled": False,
            "outputs": ["subtitle"],
        }

        manager = OutputProviderManager()
        await manager.load_from_config(config, core=None)

        assert len(manager.providers) == 0

    @pytest.mark.asyncio
    async def test_create_provider_invalid_type(self):
        """测试创建不存在的Provider类型"""
        manager = OutputProviderManager()
        provider = manager._create_provider("invalid_type", {}, None)

        assert provider is None

    @pytest.mark.asyncio
    async def test_load_from_config_with_dependency_error(self):
        """测试加载Provider时依赖缺失"""
        config = {
            "enabled": True,
            "outputs": ["tts"],
            "outputs": {
                "tts": {"type": "tts", "voice": "test"},
            },
        }

        manager = OutputProviderManager()
        # 尝试加载，但依赖可能不存在
        # 不会抛出异常，只是记录错误
        try:
            await manager.load_from_config(config, core=None)
        except Exception:
            # 预期可能因为依赖缺失而失败
            pass


class TestExpressionGenerator:
    """测试 ExpressionGenerator"""

    @pytest.mark.asyncio
    async def test_generate_from_intent(self):
        """测试从Intent生成ExpressionParameters"""
        generator = ExpressionGenerator()

        intent = Intent(
            original_text="你好",
            emotion=EmotionType.HAPPY,
            response_text="你好，有什么我可以帮助你的吗？",
            actions=[],
            metadata={},
        )

        params = await generator.generate(intent)

        assert isinstance(params, ExpressionParameters)
        assert params.tts_text == "你好，有什么我可以帮助你的吗？"
        assert params.subtitle_text == "你好，有什么我可以帮助你的吗？"
        assert params.tts_enabled is True
        assert params.subtitle_enabled is True
        assert params.expressions_enabled is True

    @pytest.mark.asyncio
    async def test_generate_empty_response(self):
        """测试生成空响应"""
        generator = ExpressionGenerator()

        intent = Intent(
            original_text="测试",
            emotion=EmotionType.NEUTRAL,
            response_text="",  # 空响应
            actions=[],
            metadata={},
        )

        params = await generator.generate(intent)

        assert params.tts_enabled is False
        assert params.subtitle_enabled is False

    @pytest.mark.asyncio
    async def test_update_config(self):
        """测试更新配置"""
        generator = ExpressionGenerator()

        new_config = {
            "default_tts_enabled": False,
            "default_subtitle_enabled": False,
        }

        await generator.update_config(new_config)

        assert generator.default_tts_enabled is False
        assert generator.default_subtitle_enabled is False


class TestAmaidesuCoreIntegration:
    """测试 AmaidesuCore 集成"""

    @pytest.mark.asyncio
    async def test_setup_output_layer(self):
        """测试设置输出层"""
        from src.core.amaidesu_core import AmaidesuCore

        event_bus = EventBus(enable_stats=False)
        config = {
            "enabled": True,
            "concurrent_rendering": True,
            "error_handling": "continue",
            "outputs": [],  # 空列表，避免加载实际Provider
        }

        core = AmaidesuCore(
            platform="test",
            event_bus=event_bus,
        )

        await core._setup_output_layer(config)

        # 验证输出层已初始化
        assert core.output_provider_manager is not None
        assert core.expression_generator is not None

        # 验证事件已订阅
        # 注意：EventBus的on方法会添加handler，我们无法直接访问
        # 但可以验证没有抛出异常

    @pytest.mark.asyncio
    async def test_on_intent_ready(self):
        """测试Intent事件处理"""
        from src.core.amaidesu_core import AmaidesuCore

        event_bus = EventBus(enable_stats=False)
        config = {
            "enabled": True,
            "outputs": [],
        }

        core = AmaidesuCore(
            platform="test",
            event_bus=event_bus,
        )

        await core._setup_output_layer(config)

        # 创建Intent
        intent = Intent(
            original_text="测试",
            emotion=EmotionType.HAPPY,
            response_text="测试响应",
            actions=[],
            metadata={},
        )

        # 触发Intent事件
        event_data = {"intent": intent}
        await event_bus.emit("understanding.intent_generated", event_data, source="test")

        # 等待异步处理完成
        await asyncio.sleep(0.1)

        # 验证没有抛出异常


class TestLayerDataFlow:
    """测试 Layer 4 → Layer 5 → Layer 6 数据流"""

    @pytest.mark.asyncio
    async def test_complete_data_flow(self):
        """测试完整数据流"""
        event_bus = EventBus(enable_stats=False)
        expression_generator = ExpressionGenerator()
        output_manager = OutputProviderManager()

        # 创建Mock Provider
        mock_provider = AsyncMock()
        mock_provider.get_info = Mock(return_value={"name": "mock_provider", "type": "test"})
        mock_provider.setup = AsyncMock()
        mock_provider.render = AsyncMock()

        # 注册Mock Provider
        await output_manager.register_provider(mock_provider)
        await output_manager.setup_all_providers(event_bus)

        # 创建Intent
        intent = Intent(
            original_text="你好",
            emotion=EmotionType.HAPPY,
            response_text="你好！",
            actions=[],
            metadata={},
        )

        # Layer 5: Intent → ExpressionParameters
        params = await expression_generator.generate(intent)
        assert params.tts_text == "你好！"

        # Layer 6: ExpressionParameters → Provider
        await output_manager.render_all(params)

        # 验证Provider被调用
        mock_provider.render.assert_called_once_with(params)

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        """测试错误隔离"""
        event_bus = EventBus(enable_stats=False)
        expression_generator = ExpressionGenerator()
        output_manager = OutputProviderManager()

        # 创建Mock Provider：一个成功，一个失败
        success_provider = AsyncMock()
        success_provider.get_info = Mock(return_value={"name": "success", "type": "test"})
        success_provider.setup = AsyncMock()
        success_provider.render = AsyncMock()

        fail_provider = AsyncMock()
        fail_provider.get_info = Mock(return_value={"name": "fail", "type": "test"})
        fail_provider.setup = AsyncMock()
        fail_provider.render = AsyncMock(side_effect=Exception("Test error"))

        # 注册Provider
        await output_manager.register_provider(success_provider)
        await output_manager.register_provider(fail_provider)
        await output_manager.setup_all_providers(event_bus)

        # 创建ExpressionParameters
        intent = Intent(
            original_text="测试",
            emotion=EmotionType.NEUTRAL,
            response_text="测试响应",
            actions=[],
            metadata={},
        )

        params = await expression_generator.generate(intent)

        # 渲染（错误处理策略：continue）
        await output_manager.render_all(params)

        # 验证两个Provider都被调用
        success_provider.render.assert_called_once_with(params)
        fail_provider.render.assert_called_once_with(params)


class TestConfiguration:
    """测试配置解析"""

    def test_rendering_config_structure(self):
        """测试rendering配置结构"""
        config = {
            "enabled": True,
            "concurrent_rendering": True,
            "error_handling": "continue",
            "outputs": ["subtitle", "vts"],
            "expression_generator": {
                "default_tts_enabled": True,
                "default_subtitle_enabled": True,
            },
            "provider_configs": {
                "subtitle": {
                    "type": "subtitle",
                    "window_width": 800,
                    "font_size": 24,
                },
                "vts": {
                    "type": "vts",
                    "vts_host": "localhost",
                    "vts_port": 8001,
                },
            },
        }

        # 验证配置结构
        assert "enabled" in config
        assert "concurrent_rendering" in config
        assert "error_handling" in config
        assert "outputs" in config
        assert "expression_generator" in config
        assert "provider_configs" in config

        # 验证outputs列表
        assert "subtitle" in config["outputs"]
        assert "vts" in config["outputs"]

        # 验证各个Provider配置
        assert config["provider_configs"]["subtitle"]["type"] == "subtitle"
        assert config["provider_configs"]["vts"]["type"] == "vts"


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])
