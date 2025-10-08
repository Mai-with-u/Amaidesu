# Screen Monitor Plugin - 屏幕监控插件
# 
# 依赖:
# - pip install openai  (OpenAI 兼容 API 客户端)
# - pip install pillow  (图像处理，用于拼接功能)
# - pip install mss     (屏幕截图)
#
# 功能:
# - 自动启动屏幕分析和AI读取
# - 将AI分析结果发送到核心系统

import asyncio
import time
import logging
from typing import Dict, Any, Optional
import os

from core.plugin_manager import BasePlugin
from core.amaidesu_core import AmaidesuCore
from maim_message import MessageBase, Seg, UserInfo, GroupInfo, FormatInfo, TemplateInfo, BaseMessageInfo

# 导入屏幕分析和读取模块
try:
    from .screen_analyzer import ScreenAnalyzer
    from .screen_reader import ScreenReader
except ImportError:
    ScreenAnalyzer = None
    ScreenReader = None


class ScreenMonitorPlugin(BasePlugin):
    """
    屏幕监控插件
    
    功能：
    - 启动屏幕变化检测 (ScreenAnalyzer)
    - 启动AI内容读取 (ScreenReader) 
    - 将AI分析的屏幕内容发送到核心系统
    """
    
    _is_amaidesu_plugin: bool = True  # Plugin marker
    
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        
        # 插件配置
        self.enabled = plugin_config.get("enabled", True)
        self.api_key = plugin_config.get("api_key", "")
        self.base_url = plugin_config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model_name = plugin_config.get("model_name", "qwen2.5-vl-72b-instruct")
        
        # 分析器配置
        self.screenshot_interval = plugin_config.get("screenshot_interval", 0.3)
        self.diff_threshold = plugin_config.get("diff_threshold", 25.0)
        self.check_window = plugin_config.get("check_window", 3)
        self.max_cache_size = plugin_config.get("max_cache_size", 5)
        self.max_cached_images = plugin_config.get("max_cached_images", 5)
        
        # 消息配置
        self.message_config = plugin_config.get("message", {})
        
        # 组件
        self.screen_analyzer: Optional[ScreenAnalyzer] = None
        self.screen_reader: Optional[ScreenReader] = None
        
        # 运行状态
        self._running = False
        
        # 统计信息
        self.messages_sent = 0
        self.last_message_time = 0.0
    
    async def setup(self):
        """插件初始化"""
        await super().setup()
        
        if not self.enabled:
            self.logger.info("屏幕监控插件已禁用")
            return
        
        # 检查依赖
        if ScreenAnalyzer is None or ScreenReader is None:
            self.logger.error("无法导入屏幕分析模块，请确保相关文件存在")
            return
        
        try:
            # 创建屏幕阅读器
            self.screen_reader = ScreenReader(
                api_key=self.api_key,
                base_url=self.base_url,
                model_name=self.model_name,
                max_cached_images=self.max_cached_images
            )
            
            # 创建屏幕分析器
            self.screen_analyzer = ScreenAnalyzer(
                interval=self.screenshot_interval,
                diff_threshold=self.diff_threshold,
                check_window=self.check_window,
                max_cache_size=self.max_cache_size
            )
            
            # 设置回调函数
            self.screen_reader.set_context_update_callback(self._on_context_update)
            self.screen_analyzer.set_change_callback(self._on_screen_change)
            
            # 启动屏幕监控
            await self.screen_analyzer.start()
            self._running = True
            
            self.logger.info(f"屏幕监控插件已启动 (间隔: {self.screenshot_interval}s, 阈值: {self.diff_threshold})")
            
        except Exception as e:
            self.logger.error(f"启动屏幕监控插件失败: {e}", exc_info=True)
    
    async def cleanup(self):
        """插件清理"""
        if self._running and self.screen_analyzer:
            self.logger.info("正在停止屏幕监控...")
            await self.screen_analyzer.stop()
            self._running = False
        
        await super().cleanup()
        self.logger.info("屏幕监控插件已停止")
    
    async def _on_screen_change(self, change_data: Dict[str, Any]):
        """处理屏幕变化检测"""
        if not self._running or not self.screen_reader:
            return
        
        self.logger.debug(f"检测到屏幕变化: 差异分数 {change_data.get('difference_score', 0):.2f}")
        
        try:
            # 将变化数据传递给 screen_reader 进行分析
            result = await self.screen_reader.process_screen_change(change_data)
            if result:
                self.logger.debug(f"AI分析完成: {result.new_current_context[:50]}...")
            else:
                self.logger.debug("图像已缓存或分析失败")
        except Exception as e:
            self.logger.error(f"处理屏幕变化时出错: {e}", exc_info=True)
    
    async def _on_context_update(self, data: Dict[str, Any]):
        """处理上下文更新 - 发送消息到核心系统"""
        try:
            # 提取分析结果
            analysis_result = data.get("analysis_result")
            if not analysis_result:
                return
            
            new_context = analysis_result.new_current_context
            images_processed = data.get("images_processed", 1)
            
            # 创建屏幕描述消息
            message_text = f"[屏幕描述更新] {new_context}"
            if images_processed > 1:
                message_text = f"[屏幕变化序列({images_processed}帧)] {new_context}"
            
            # 创建并发送消息
            message = await self._create_screen_message(message_text, data)
            if message:
                await self.core.send_to_maicore(message)
                self.messages_sent += 1
                self.last_message_time = time.time()
                
                self.logger.info(f"屏幕描述消息已发送: {new_context[:50]}...")
                
                # 输出统计信息
                stats = data.get("statistics", {})
                self.logger.debug(
                    f"统计 - 消息发送: {self.messages_sent}, "
                    f"AI分析: {stats.get('total_analyses', 0)}, "
                    f"缓存图像: {stats.get('current_cache_size', 0)}, "
                    f"拼接分析: {stats.get('stitched_analyses_count', 0)}"
                )
            
        except Exception as e:
            self.logger.error(f"发送屏幕描述消息失败: {e}", exc_info=True)
    
    async def _create_screen_message(self, text: str, context_data: Dict[str, Any]) -> Optional[MessageBase]:
        """创建屏幕描述消息"""
        try:
            timestamp = time.time()
            
            # 用户信息
            user_info = UserInfo(
                platform=self.core.platform,
                user_id=self.message_config.get("user_id", 99999),
                user_nickname=self.message_config.get("user_nickname", "屏幕监控"),
                user_cardname=self.message_config.get("user_cardname", "")
            )
            
            # 群组信息（可选）
            group_info = None
            if self.message_config.get("enable_group_info", False):
                group_info = GroupInfo(
                    platform=self.core.platform,
                    group_id=self.message_config.get("group_id", 0),
                    group_name=self.message_config.get("group_name", "屏幕监控")
                )
            
            # 格式信息
            format_info = FormatInfo(
                content_format=self.message_config.get("content_format", ["text"]),
                accept_format=self.message_config.get("accept_format", ["text"])
            )
            
            # 附加配置
            additional_config = self.message_config.get("additional_config", {}).copy()
            additional_config.update({
                "source": "screen_monitor_plugin",
                "screen_context": context_data.get("current_context", ""),
                "main_context": context_data.get("main_context", ""),
                "statistics": context_data.get("statistics", {})
            })
            
            # 模板信息（可选）
            template_info = None
            if self.message_config.get("enable_template", False):
                template_info = TemplateInfo(
                    template_name=self.message_config.get("template_name", "ScreenMonitor"),
                    template_items={
                        "screen_context": context_data.get("current_context", ""),
                        "main_context": context_data.get("main_context", ""),
                        "statistics": context_data.get("statistics", {}),
                        "timestamp": timestamp
                    }
                )
            
            # 创建消息基础信息
            message_info = BaseMessageInfo(
                platform=self.core.platform,
                message_id=f"screen_monitor_{int(timestamp * 1000)}_{hash(text) % 10000}",
                time=timestamp,
                user_info=user_info,
                group_info=group_info,
                template_info=template_info,
                format_info=format_info,
                additional_config=additional_config,
            )
            
            # 消息段
            message_segment = Seg(type="screen", data=text)
            
            # 创建消息
            message = MessageBase(
                message_info=message_info,
                message_segment=message_segment,
                raw_message=text
            )
            
            return message
            
        except Exception as e:
            self.logger.error(f"创建屏幕消息失败: {e}", exc_info=True)
            return None
    
    def get_plugin_status(self) -> Dict[str, Any]:
        """获取插件状态"""
        status = {
            "enabled": self.enabled,
            "running": self._running,
            "messages_sent": self.messages_sent,
            "last_message_time": self.last_message_time,
        }
        
        if self.screen_reader:
            status["reader_stats"] = self.screen_reader.get_statistics()
        
        if self.screen_analyzer:
            status["analyzer_stats"] = self.screen_analyzer.get_cache_status()
        
        return status


# 插件入口点
plugin_entrypoint = ScreenMonitorPlugin 
