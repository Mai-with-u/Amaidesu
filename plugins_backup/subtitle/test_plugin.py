#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
字幕插件测试文件
用于验证插件的基本功能
"""

import asyncio
from unittest.mock import MagicMock, Mock


def test_subtitle_plugin():
    """测试字幕插件的基本功能"""
    print("开始测试字幕插件...")

    # Mock 核心和配置
    mock_core = Mock()
    mock_core.register_service = MagicMock()
    mock_core.get_service = MagicMock()

    # 测试配置
    test_config = {
        "enabled": True,
        "window_width": 600,
        "window_height": 80,
        "window_offset_y": 50,
        "font_family": "Microsoft YaHei UI",
        "font_size": 24,
        "font_weight": "bold",
        "text_color": "white",
        "outline_enabled": True,
        "outline_color": "black",
        "outline_width": 2,
        "background_enabled": True,
        "background_color": "#000000",
        "background_opacity": 0.8,
        "corner_radius": 10,
        "fade_delay_seconds": 2,
        "auto_hide": True,
        "window_alpha": 0.9,
    }

    try:
        # 导入插件
        from src.plugins.subtitle.plugin import SubtitlePlugin

        # 创建插件实例
        plugin = SubtitlePlugin(mock_core, test_config)
        print("✓ 插件创建成功")

        # 异步测试函数
        async def run_tests():
            try:
                # 设置插件
                await plugin.setup()
                print("✓ 插件设置成功")

                # 等待 GUI 线程启动
                await asyncio.sleep(1)

                if plugin.is_running:
                    print("✓ GUI 线程运行正常")

                    # 测试显示字幕
                    test_texts = ["这是一个测试字幕", "支持中文显示", "CustomTkinter + 描边效果", "自动隐藏功能测试"]

                    for i, text in enumerate(test_texts):
                        print(f"显示测试文本 {i + 1}: {text}")
                        await plugin.record_speech(text, 2.0)
                        await asyncio.sleep(3)  # 等待显示

                    print("✓ 字幕显示测试完成")

                    # 测试空文本隐藏
                    print("测试自动隐藏...")
                    await plugin.record_speech("", 0.0)
                    await asyncio.sleep(1)
                    print("✓ 自动隐藏测试完成")

                else:
                    print("✗ GUI 线程未启动")

                # 清理插件
                await plugin.cleanup()
                print("✓ 插件清理完成")

            except Exception as e:
                print(f"✗ 测试过程中出错: {e}")
                import traceback

                traceback.print_exc()

        # 运行异步测试
        asyncio.run(run_tests())
        print("字幕插件测试完成")

    except ImportError as e:
        print(f"✗ 无法导入插件: {e}")
        print("请确认 CustomTkinter 已正确安装")
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_subtitle_plugin()
