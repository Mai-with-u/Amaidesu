# ScreenReader - 屏幕内容阅读器
# 
# 依赖:
# - pip install openai  (OpenAI 兼容 API 客户端)
# - pip install pillow  (图像处理，用于拼接功能)
#
# 可选依赖:
# - PIL/Pillow: 如果不安装，将禁用图像拼接功能，只使用最新图像

import asyncio
import time
import logging
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
from collections import deque
import base64
import io

# OpenAI 兼容客户端
try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

# PIL 用于图像处理
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


@dataclass
class ContextHistoryItem:
    """上下文历史项"""
    timestamp: float
    context: str
    trigger_reason: str = "screen_change"


@dataclass 
class AnalysisResult:
    """分析结果"""
    new_current_context: str


@dataclass
class CachedImage:
    """缓存的图像数据"""
    timestamp: float
    image_base64: str
    difference_score: float
    metadata: Dict[str, Any]


class ScreenReader:
    """
    屏幕内容阅读器
    
    功能：
    - 接收 screen_analyzer 的图像变化消息
    - 维护 main_context (总体概括) 和 current_context (近期内容)
    - 保存 current_context 的历史记录
    - 使用 LLM 视觉模型结合上下文分析新图像
    - 缓存被跳过的图像变化，拼接后一起处理
    """
    
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_name: str = "qwen-vl-plus",
        max_history_size: int = 20,
        timeout_seconds: int = 30,
        max_cached_images: int = 5
    ):
        """
        初始化屏幕阅读器
        
        Args:
            api_key: OpenAI 兼容 API Key
            base_url: OpenAI 兼容 Base URL
            model_name: 视觉模型名称
            max_history_size: 最大历史记录数量
            timeout_seconds: 请求超时时间
            max_cached_images: 最大缓存图像数量
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.max_history_size = max_history_size
        self.timeout_seconds = timeout_seconds
        self.max_cached_images = max_cached_images
        
        # 上下文状态
        self.main_context = "屏幕内容尚未初始化"
        self.current_context = "当前屏幕内容尚未获取"
        
        # 历史记录
        self.context_history: deque[ContextHistoryItem] = deque(maxlen=max_history_size)
        
        # 图像缓存
        self._cached_images: deque[CachedImage] = deque(maxlen=max_cached_images)
        
        # 统计信息
        self.total_analyses = 0
        self.main_context_updates = 0
        self.current_context_updates = 0
        self.last_analysis_time = 0.0
        self.cached_images_count = 0
        self.stitched_analyses_count = 0
        
        # 控制变量
        self.is_initialized = False
        self._analysis_lock = asyncio.Lock()
        self._is_processing = False  # 标记是否正在处理
        self._drop_count = 0  # 丢弃的请求计数
        
        # 回调函数
        self.on_context_updated: Optional[Callable] = None
        
        # 日志
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 检查依赖
        if not PIL_AVAILABLE:
            self.logger.warning("PIL 库不可用，图像拼接功能将被禁用")
        
        # 初始化 OpenAI 客户端
        if AsyncOpenAI is None:
            raise ImportError("缺少 openai 库，请运行: pip install openai")
        
        try:
            self.openai_client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds
            )
            self.is_initialized = True
            self.logger.info(f"ScreenReader 初始化成功，模型: {model_name}")
        except Exception as e:
            self.logger.error(f"初始化 OpenAI 客户端失败: {e}")
            raise
    
    def set_context_update_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """设置上下文更新回调函数"""
        self.on_context_updated = callback
    
    async def process_screen_change(self, change_data: Dict[str, Any]) -> Optional[AnalysisResult]:
        """
        处理来自 screen_analyzer 的屏幕变化消息
        智能并发控制：如果正在处理，缓存图像而不是丢弃
        
        Args:
            change_data: 包含图像数据和元信息的字典
            
        Returns:
            分析结果
        """
        if not self.is_initialized:
            self.logger.warning("ScreenReader 未正确初始化")
            return None
        
        # 检查是否正在处理
        if self._is_processing:
            # 缓存当前图像而不是丢弃
            cached_image = CachedImage(
                timestamp=change_data.get("timestamp", time.time()),
                image_base64=change_data.get("image_base64", ""),
                difference_score=change_data.get("difference_score", 0.0),
                metadata=change_data
            )
            
            self._cached_images.append(cached_image)
            self.cached_images_count += 1
            
            self.logger.debug(f"LLM正在处理中，缓存图像 (缓存数量: {len(self._cached_images)})")
            return None
        
        # 开始处理
        return await self._process_image_request(change_data)
    
    async def _process_image_request(self, change_data: Dict[str, Any]) -> Optional[AnalysisResult]:
        """处理图像请求，包括缓存的图像"""
        try:
            self._is_processing = True
            
            # 准备要处理的图像列表
            images_to_process = []
            
            # 添加缓存的图像
            while self._cached_images:
                cached_image = self._cached_images.popleft()
                images_to_process.append(cached_image)
            
            # 添加当前图像
            current_image = CachedImage(
                timestamp=change_data.get("timestamp", time.time()),
                image_base64=change_data.get("image_base64", ""),
                difference_score=change_data.get("difference_score", 0.0),
                metadata=change_data
            )
            images_to_process.append(current_image)
            
            # 处理图像
            if len(images_to_process) > 1:
                self.logger.info(f"处理 {len(images_to_process)} 张连续图像变化")
                self.stitched_analyses_count += 1
            
            result = await self._analyze_images(images_to_process)
            
            if result:
                # 更新上下文
                await self._update_contexts(result)
                
                # 触发回调
                if self.on_context_updated:
                    await self._trigger_callback(result, change_data, len(images_to_process))
            
            return result
            
        finally:
            self._is_processing = False
    
    def _stitch_images(self, images: List[CachedImage]) -> Optional[str]:
        """横向拼接多张图像"""
        if not PIL_AVAILABLE:
            self.logger.warning("PIL 不可用，无法拼接图像，使用最后一张")
            return images[-1].image_base64 if images else None
        
        if len(images) == 1:
            return images[0].image_base64
        
        try:
            pil_images = []
            
            # 解码所有图像
            for cached_img in images:
                img_data = base64.b64decode(cached_img.image_base64)
                pil_img = Image.open(io.BytesIO(img_data))
                pil_images.append(pil_img)
            
            # 计算拼接后的尺寸
            max_height = max(img.height for img in pil_images)
            total_width = sum(img.width for img in pil_images)
            
            # 创建新的图像
            stitched_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
            
            # 逐个粘贴图像
            x_offset = 0
            for img in pil_images:
                # 如果高度不同，居中对齐
                y_offset = (max_height - img.height) // 2
                stitched_image.paste(img, (x_offset, y_offset))
                x_offset += img.width
            
            # 转换回 base64
            buffer = io.BytesIO()
            stitched_image.save(buffer, format='PNG')
            stitched_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            self.logger.debug(f"成功拼接 {len(images)} 张图像，尺寸: {total_width}x{max_height}")
            return stitched_base64
            
        except Exception as e:
            self.logger.error(f"拼接图像失败: {e}")
            # 拼接失败，返回最后一张图像
            return images[-1].image_base64 if images else None
    
    async def _analyze_images(self, images: List[CachedImage]) -> Optional[AnalysisResult]:
        """分析单张或多张拼接的图像"""
        try:
            self.logger.info("开始处理屏幕变化...")
            
            if not images:
                self.logger.error("没有图像数据")
                return None
            
            # 拼接图像
            stitched_image_base64 = self._stitch_images(images)
            if not stitched_image_base64:
                self.logger.error("图像拼接失败")
                return None
            
            # 分析图像
            result = await self._analyze_screen_image(
                image_base64=stitched_image_base64,
                images_count=len(images),
                metadata=images[-1].metadata  # 使用最新图像的元数据
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"处理屏幕变化失败: {e}", exc_info=True)
            return None
    
    async def _analyze_screen_image(
        self, 
        image_base64: str, 
        images_count: int,
        metadata: Dict[str, Any]
    ) -> Optional[AnalysisResult]:
        """使用 LLM 视觉模型分析屏幕图像"""
        
        # 构建分析提示词
        analysis_prompt = self._build_analysis_prompt(images_count)
        
        # 构建 OpenAI Vision API 消息
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": analysis_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}"
                        }
                    }
                ]
            }
        ]
        
        try:
            self.logger.debug(f"发送图像分析请求到 {self.base_url} (图像数量: {images_count})")
            
            completion = await self.openai_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=1000,
                temperature=0.3  # 使用较低温度保证一致性
            )
            
            if completion.choices and completion.choices[0].message:
                response_content = completion.choices[0].message.content
                if response_content:
                    # 解析 LLM 响应
                    result = self._parse_llm_response(response_content, metadata)
                    self.total_analyses += 1
                    self.last_analysis_time = time.time()
                    return result
            
            self.logger.warning("LLM 响应内容为空")
            return None
            
        except Exception as e:
            self.logger.error(f"调用 LLM 分析图像失败: {e}", exc_info=True)
            return None
    
    def _build_analysis_prompt(self, images_count: int = 1) -> str:
        """构建图像分析提示词"""
        
        if images_count == 1:
            prompt = f"""
请分析这张屏幕截图，并根据上一时刻屏幕的内容，总结变化，生成新的屏幕内容描述。

上一时刻屏幕的内容: {self.current_context}

请根据图像内容和上述上下文，生成新的屏幕内容描述。

请直接回复屏幕内容描述，不需要JSON格式。描述应该简洁明了，1-2句话即可。
"""
        else:
            prompt = f"""
请分析这张横向拼接的屏幕截图。这张图像包含了 {images_count} 个连续的屏幕变化时刻，从左到右按时间顺序排列。

上一时刻屏幕的内容: {self.current_context}

请根据这些连续的屏幕变化和上述上下文，总结整个变化过程，生成新的屏幕内容描述。

注意：
- 图像从左到右显示了连续的 {images_count} 个时刻
- 重点关注整个变化过程和最终状态
- 描述应该体现变化的连续性

请直接回复屏幕内容描述，不需要JSON格式。描述应该简洁明了，1-2句话即可。
"""
        
        return prompt
    
    def _parse_llm_response(self, response: str, metadata: Dict[str, Any]) -> Optional[AnalysisResult]:
        """解析 LLM 的文本响应"""
        try:
            # 清理响应文本
            response = response.strip()
            
            # 移除可能的代码块标记
            if response.startswith("```"):
                lines = response.split('\n')
                if len(lines) > 1:
                    response = '\n'.join(lines[1:])
                if response.endswith("```"):
                    response = response[:-3]
            
            # 移除多余的空白字符
            response = response.strip()
            
            # 如果响应为空，返回默认描述
            if not response:
                response = "屏幕内容已更新"
            
            # 创建分析结果
            result = AnalysisResult(
                new_current_context=response
            )
            
            self.logger.debug(f"成功解析 LLM 响应: {response[:50]}...")
            return result
            
        except Exception as e:
            self.logger.error(f"解析 LLM 响应时发生错误: {e}")
            self.logger.debug(f"原始响应: {response[:200]}...")
            
            # 返回备用结果
            return AnalysisResult(
                new_current_context="屏幕内容已更新 (AI分析异常)"
            )
    
    async def _update_contexts(self, result: AnalysisResult):
        """更新上下文状态"""
        # 保存旧的 current_context 到历史记录
        if self.current_context != "当前屏幕内容尚未获取":
            history_item = ContextHistoryItem(
                timestamp=time.time(),
                context=self.current_context,
                trigger_reason="screen_change"
            )
            self.context_history.append(history_item)
        
        # 更新 current_context
        old_current = self.current_context
        self.current_context = result.new_current_context
        self.current_context_updates += 1
        
        self.logger.info(f"更新 current_context: {old_current[:30]}... -> {self.current_context[:30]}...")
    
    async def _trigger_callback(self, result: AnalysisResult, change_data: Dict[str, Any], images_processed: int = 1):
        """触发上下文更新回调"""
        if not self.on_context_updated:
            return
        
        callback_data = {
            "main_context": self.main_context,
            "current_context": self.current_context,
            "analysis_result": result,
            "change_data": change_data,
            "images_processed": images_processed,
            "statistics": self.get_statistics()
        }
        
        try:
            if asyncio.iscoroutinefunction(self.on_context_updated):
                await self.on_context_updated(callback_data)
            else:
                self.on_context_updated(callback_data)
        except Exception as e:
            self.logger.error(f"执行上下文更新回调失败: {e}", exc_info=True)
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_analyses": self.total_analyses,
            "main_context_updates": self.main_context_updates,
            "current_context_updates": self.current_context_updates,
            "history_size": len(self.context_history),
            "last_analysis_time": self.last_analysis_time,
            "current_main_context": self.main_context,
            "current_context": self.current_context,
            "dropped_requests": self._drop_count,
            "cached_images_count": self.cached_images_count,
            "stitched_analyses_count": self.stitched_analyses_count,
            "current_cache_size": len(self._cached_images),
            "is_processing": self._is_processing,
            "pil_available": PIL_AVAILABLE
        }
    
    def get_context_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取上下文历史记录"""
        history = list(self.context_history)
        if limit:
            history = history[-limit:]
        
        return [
            {
                "timestamp": item.timestamp,
                "context": item.context,
                "trigger_reason": item.trigger_reason
            }
            for item in history
        ]
    
    def update_main_context_manually(self, new_main_context: str):
        """手动更新主上下文"""
        old_main = self.main_context
        self.main_context = new_main_context
        self.main_context_updates += 1
        
        # 添加到历史记录
        history_item = ContextHistoryItem(
            timestamp=time.time(),
            context=f"主上下文手动更新: {new_main_context}",
            trigger_reason="manual_update"
        )
        self.context_history.append(history_item)
        
        self.logger.info(f"手动更新 main_context: {old_main[:30]}... -> {new_main_context[:30]}...")
    
    def clear_history(self):
        """清空历史记录"""
        self.context_history.clear()
        self.logger.info("上下文历史记录已清空")
    
    def clear_cached_images(self):
        """清空图像缓存"""
        cleared_count = len(self._cached_images)
        self._cached_images.clear()
        self.logger.info(f"已清空 {cleared_count} 张缓存图像")
    
    def reset_contexts(self):
        """重置所有上下文"""
        self.main_context = "屏幕内容尚未初始化"
        self.current_context = "当前屏幕内容尚未获取"
        self.clear_history()
        self.clear_cached_images()
        self.total_analyses = 0
        self.main_context_updates = 0
        self.current_context_updates = 0
        self.cached_images_count = 0
        self.stitched_analyses_count = 0
        self.logger.info("所有上下文已重置")


# 使用示例 - 整体协同运作
async def example_usage():
    """完整的屏幕监控示例：screen_analyzer + screen_reader 协同工作"""
    
    # 导入 ScreenAnalyzer
    try:
        from .screen_analyzer import ScreenAnalyzer
    except ImportError:
        # 如果在当前目录运行，尝试直接导入
        try:
            from screen_analyzer import ScreenAnalyzer
        except ImportError:
            print("错误: 无法导入 ScreenAnalyzer，请确保 screen_analyzer.py 在同一目录")
            return
    
    print("🚀 启动完整的屏幕监控系统...")
    
    # 1. 创建屏幕阅读器
    reader = ScreenReader(
        api_key="sk-587745e2aa7843d8b9217655a7c4d17c",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen2.5-vl-72b-instruct"
    )
    
    # 2. 创建屏幕分析器
    analyzer = ScreenAnalyzer(
        interval=0.3,           # 1秒截图间隔（测试用）
        diff_threshold=25.0,    # 较低的阈值，更容易触发
        check_window=3,         # 检查最近2帧
        max_cache_size=5
    )
    
    # 3. 设置上下文更新回调
    async def on_context_update(data):
        print("\n📄 上下文已更新:")
        print(f"  🎯 主上下文: {data['main_context']}")
        print(f"  📝 当前上下文: {data['current_context']}")
        print(f"  🆕 新内容: {data['analysis_result'].new_current_context}")
        print(f"  🖼️ 处理图像数: {data['images_processed']}")
        stats = data['statistics']
        print(f"  📊 更新次数: {stats['current_context_updates']}")
        print(f"  🗑️ 丢弃请求: {stats['dropped_requests']}, 正在处理: {'🟢' if stats['is_processing'] else '🔴'}")
        print(f"  📦 缓存图像: {stats['current_cache_size']}/{stats['cached_images_count']}, 拼接分析: {stats['stitched_analyses_count']}")
        print(f"  🎨 PIL可用: {'✅' if stats['pil_available'] else '❌'}")
        print("-" * 60)
    
    reader.set_context_update_callback(on_context_update)
    
    # 4. 设置变化检测回调 - 连接 analyzer 到 reader
    async def on_screen_change(change_data):
        print("\n🔍 检测到屏幕变化!")
        print(f"  ⏰ 时间戳: {change_data['timestamp']}")
        print(f"  📊 差异分数: {change_data['difference_score']:.2f}")
        print(f"  🖼️ 图像大小: {len(change_data['image_base64'])} bytes")
        
        # 将变化数据传递给 screen_reader 进行分析
        try:
            result = await reader.process_screen_change(change_data)
            if result:
                print(f"  ✅ AI分析完成: {result.new_current_context}")
            else:
                print("  📦 图像已缓存或分析失败")
        except Exception as e:
            print(f"  ⚠️ 处理变化时出错: {e}")
    
    analyzer.set_change_callback(on_screen_change)
    
    # 5. 启动系统
    print(f"📱 启动屏幕分析器 (间隔: {analyzer.interval}s, 阈值: {analyzer.diff_threshold})")
    
    try:
        # 启动分析器
        await analyzer.start()
        
        print("🎮 系统运行中... (按 Ctrl+C 停止)")
        print("💡 提示: 在屏幕上移动鼠标或切换窗口来触发变化检测")
        print("🖼️ 新功能: 被跳过的图像会被缓存并拼接处理，避免丢失变化信息")
        print("=" * 60)
        
        # 运行指定时间
        runtime = 60  # 运行30秒
        print(f"⏳ 将运行 {runtime} 秒进行测试...")
        
        await asyncio.sleep(runtime)
        
    except KeyboardInterrupt:
        print("\n\n⏹️ 用户中断...")
    
    finally:
        # 停止分析器
        print("🛑 正在停止屏幕分析器...")
        await analyzer.stop()
        
        # 显示最终统计
        print("\n📈 最终统计信息:")
        stats = reader.get_statistics()
        analyzer_stats = analyzer.get_cache_status()
        
        print(f"  🔬 总分析次数: {stats['total_analyses']}")
        print(f"  📝 current_context更新: {stats['current_context_updates']}")
        print(f"  🎯 main_context更新: {stats['main_context_updates']}")
        print(f"  🗑️ 丢弃的请求: {stats['dropped_requests']}")
        print(f"  📦 缓存的图像总数: {stats['cached_images_count']}")
        print(f"  🎬 拼接分析次数: {stats['stitched_analyses_count']}")
        print(f"  🎨 PIL库状态: {'可用' if stats['pil_available'] else '不可用'}")
        print(f"  📱 分析器缓存: {analyzer_stats['cache_size']}")
        print(f"  🎛️ 当前main_context: {stats['current_main_context']}")
        print(f"  📄 当前context: {stats['current_context']}")
        
        print("\n✅ 测试完成!")


if __name__ == "__main__":
    asyncio.run(example_usage())
