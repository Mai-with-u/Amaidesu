"""
测试脚本 - 验证MockProvidersPlugin是否正常工作

这个脚本会：
1. 创建最小配置
2. 加载MockProvidersPlugin
3. 运行一段时间，观察输出
4. 清理并退出
"""

import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.event_bus import EventBus
from src.plugins.mock_providers.plugin import MockProvidersPlugin
from src.utils.logger import get_logger

logger = get_logger("TestMockProviders")


async def test_plugin():
    """测试插件"""
    logger.info("=" * 60)
    logger.info("开始测试 MockProvidersPlugin")
    logger.info("=" * 60)

    # 创建最小配置
    config = {
        "enabled": True,
        "start_immediately": True,
        "enable_input": True,
        "enable_decision": True,
        "enable_output": True,
        "input": {
            "send_interval": 1.0,
            "min_interval": 0.5,
            "max_interval": 2.0,
        },
        "decision": {
            "response_delay": 0.3,
            "enable_keyword_match": True,
            "add_random_variation": True,
        },
        "output": {
            "tts": {
                "speak_delay": 0.0,
                "show_timestamp": True,
                "prefix": "🔊 TTS",
            },
            "subtitle": {
                "display_duration": 1.0,
                "show_border": True,
                "border_char": "═",
                "width": 60,
            },
        },
    }

    # 创建事件总线
    event_bus_config = {"enable_validation": False}
    event_bus = EventBus(**event_bus_config)

    # 创建插件实例
    plugin = MockProvidersPlugin(config)

    logger.info("✓ 插件实例已创建")

    try:
        # 设置插件
        providers = await plugin.setup(event_bus, config)
        logger.info(f"✓ 插件设置完成，返回了 {len(providers)} 个Provider")

        # 列出所有Provider
        for i, provider in enumerate(providers, 1):
            logger.info(f"  Provider {i}: {provider.__class__.__name__}")

        logger.info("")
        logger.info("=" * 60)
        logger.info("插件正在运行，观察输出...")
        logger.info("按 Ctrl+C 停止测试")
        logger.info("=" * 60)
        logger.info("")

        # 运行一段时间（30秒）
        await asyncio.sleep(30)

    except KeyboardInterrupt:
        logger.info("收到中断信号")
    except Exception as e:
        logger.error(f"测试过程中出错: {e}", exc_info=True)
    finally:
        logger.info("")
        logger.info("=" * 60)
        logger.info("正在清理插件...")
        logger.info("=" * 60)

        await plugin.cleanup()

        logger.info("✓ 插件清理完成")
        logger.info("")
        logger.info("=" * 60)
        logger.info("测试完成")
        logger.info("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(test_plugin())
    except KeyboardInterrupt:
        logger.info("测试被中断")
