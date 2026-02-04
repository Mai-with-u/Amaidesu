#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Screen Monitor Plugin 测试脚本

用于测试屏幕监控插件的功能，包括：
- 组件初始化
- 屏幕变化检测
- AI内容分析
- 消息发送模拟
"""

import asyncio
import logging
import time
from typing import Dict, Any

# 模拟核心系统
class MockAmaidesuCore:
    def __init__(self):
        self.platform = "test_platform"
        self.messages_received = []
        
    async def send_to_maicore(self, message):
        """模拟发送消息到核心系统"""
        self.messages_received.append({
            "timestamp": time.time(),
            "message": message,
            "text": message.raw_message if hasattr(message, 'raw_message') else str(message)
        })
        print(f"📤 模拟发送消息: {message.raw_message[:50]}...")
        
    def get_service(self, service_name):
        """模拟获取服务"""
        return None


async def test_screen_monitor_plugin():
    """测试屏幕监控插件"""
    
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("🚀 开始测试屏幕监控插件...")
    
    try:
        # 导入插件
        from .screen_monitor_plugin import ScreenMonitorPlugin
        
        # 创建模拟核心
        mock_core = MockAmaidesuCore()
        
        # 插件配置
        plugin_config = {
            "enabled": True,
            "api_key": "sk-587745e2aa7843d8b9217655a7c4d17c",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model_name": "qwen2.5-vl-72b-instruct",
            "screenshot_interval": 1.0,  # 测试用较长间隔
            "diff_threshold": 20.0,
            "check_window": 2,
            "max_cache_size": 3,
            "max_cached_images": 3,
            "message": {
                "user_id": 99999,
                "user_nickname": "屏幕监控测试",
                "content_format": ["text"],
                "accept_format": ["text"]
            }
        }
        
        print("🔧 创建插件实例...")
        plugin = ScreenMonitorPlugin(mock_core, plugin_config)
        
        print("⚙️ 初始化插件...")
        await plugin.setup()
        
        if not plugin._running:
            print("❌ 插件未能正常启动，可能缺少依赖或配置错误")
            return
        
        print("✅ 插件启动成功!")
        print("📱 开始监控屏幕变化...")
        print("💡 提示: 在屏幕上移动鼠标或切换窗口来触发变化检测")
        print("🖼️ 拼接功能: 连续变化会被拼接分析")
        print("=" * 60)
        
        # 运行测试时间
        test_duration = 30  # 30秒测试
        start_time = time.time()
        
        while time.time() - start_time < test_duration:
            # 显示实时状态
            status = plugin.get_plugin_status()
            
            print(f"\r⏱️ 运行时间: {int(time.time() - start_time)}s | "
                  f"消息发送: {status['messages_sent']} | "
                  f"AI分析: {status.get('reader_stats', {}).get('total_analyses', 0)} | "
                  f"缓存图像: {status.get('reader_stats', {}).get('current_cache_size', 0)}", 
                  end='', flush=True)
            
            await asyncio.sleep(1)
        
        print(f"\n\n🛑 测试完成!")
        
        # 显示最终统计
        final_status = plugin.get_plugin_status()
        reader_stats = final_status.get('reader_stats', {})
        analyzer_stats = final_status.get('analyzer_stats', {})
        
        print(f"\n📊 最终统计:")
        print(f"  📤 发送消息数: {final_status['messages_sent']}")
        print(f"  🔬 AI分析次数: {reader_stats.get('total_analyses', 0)}")
        print(f"  🎬 拼接分析次数: {reader_stats.get('stitched_analyses_count', 0)}")
        print(f"  📦 缓存图像总数: {reader_stats.get('cached_images_count', 0)}")
        print(f"  🗑️ 丢弃请求数: {reader_stats.get('dropped_requests', 0)}")
        print(f"  📱 分析器缓存: {analyzer_stats.get('cache_size', 0)}")
        print(f"  🎨 PIL状态: {'可用' if reader_stats.get('pil_available', False) else '不可用'}")
        
        print(f"\n📨 接收到的消息 ({len(mock_core.messages_received)}):")
        for i, msg_data in enumerate(mock_core.messages_received[-5:], 1):  # 显示最后5条
            print(f"  {i}. {msg_data['text'][:80]}...")
        
        print(f"\n🎯 当前上下文状态:")
        if reader_stats:
            print(f"  主上下文: {reader_stats.get('current_main_context', 'N/A')[:50]}...")
            print(f"  当前上下文: {reader_stats.get('current_context', 'N/A')[:50]}...")
        
    except ImportError as e:
        print(f"❌ 导入错误: {e}")
        print("请确保相关模块存在并且依赖已安装")
        
    except Exception as e:
        print(f"❌ 测试过程中出错: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # 清理
        try:
            await plugin.cleanup()
            print("🧹 插件已清理")
        except:
            pass


if __name__ == "__main__":
    print("=" * 60)
    print("          🖥️ Screen Monitor Plugin 测试工具")
    print("=" * 60)
    
    try:
        asyncio.run(test_screen_monitor_plugin())
    except KeyboardInterrupt:
        print("\n\n⏹️ 测试被用户中断")
    except Exception as e:
        print(f"\n❌ 运行测试时出错: {e}")
        
    print("\n✅ 测试程序结束") 