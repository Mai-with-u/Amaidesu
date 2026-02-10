import asyncio
import base64
import logging
import time
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, Optional

# 依赖检查
try:
    import mss
    import mss.tools
except ImportError:
    mss = None

try:
    import numpy as np
    from PIL import Image
except ImportError:
    Image = None
    np = None

try:
    import cv2
except ImportError:
    cv2 = None


@dataclass
class FrameData:
    """帧数据结构"""

    timestamp: float
    image_base64: str
    image_array: np.ndarray
    difference_score: float = 0.0


class ScreenAnalyzer:
    """
    屏幕变化分析器

    功能：
    - 每0.2秒截图一次
    - 比对当前图和上一张图的差异
    - 缓存帧数据，最多5份
    - 检测过去3次比对的累计差异是否突破阈值
    - 突破阈值时返回当前图像并清空缓存
    """

    def __init__(
        self,
        interval: float = 0.2,
        max_cache_size: int = 5,
        diff_threshold: float = 30.0,
        check_window: int = 3,
        resize_width: int = 320,
        resize_height: int = 240,
    ):
        """
        初始化分析器

        Args:
            interval: 截图间隔（秒）
            max_cache_size: 最大缓存帧数
            diff_threshold: 差异阈值（0-100）
            check_window: 检查窗口大小（检查最近几次的差异）
            resize_width: 缩放宽度（用于提高计算效率）
            resize_height: 缩放高度（用于提高计算效率）
        """
        self.interval = interval
        self.max_cache_size = max_cache_size
        self.diff_threshold = diff_threshold
        self.check_window = check_window
        self.resize_width = resize_width
        self.resize_height = resize_height

        # 帧缓存
        self.frame_cache: deque[FrameData] = deque(maxlen=max_cache_size)

        # 控制变量
        self.is_running = False
        self._task: Optional[asyncio.Task] = None

        # 回调函数
        self.on_change_detected = None  # 检测到变化时的回调

        # 日志
        self.logger = logging.getLogger(self.__class__.__name__)

        # 检查依赖
        if not self._check_dependencies():
            raise ImportError("缺少必要依赖：mss, Pillow, numpy, opencv-python")

    def _check_dependencies(self) -> bool:
        """检查必要依赖"""
        return all([mss is not None, Image is not None, np is not None, cv2 is not None])

    def set_change_callback(self, callback):
        """设置变化检测回调函数"""
        self.on_change_detected = callback

    async def start(self):
        """启动分析器"""
        if self.is_running:
            self.logger.warning("ScreenAnalyzer 已在运行")
            return

        self.is_running = True
        self._task = asyncio.create_task(self._analysis_loop())
        self.logger.info(f"ScreenAnalyzer 已启动，间隔: {self.interval}s, 阈值: {self.diff_threshold}")

    async def stop(self):
        """停止分析器"""
        if not self.is_running:
            return

        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self.logger.warning("停止 ScreenAnalyzer 超时")

        self.clear_cache()
        self.logger.info("ScreenAnalyzer 已停止")

    def clear_cache(self):
        """清空缓存"""
        self.frame_cache.clear()
        self.logger.debug("帧缓存已清空")

    async def _analysis_loop(self):
        """分析循环"""
        self.logger.info("屏幕分析循环启动")

        while self.is_running:
            start_time = time.monotonic()

            try:
                # 截图并处理
                await self._capture_and_analyze()

            except Exception as e:
                self.logger.error(f"分析循环中发生错误: {e}", exc_info=True)

            # 计算等待时间
            elapsed = time.monotonic() - start_time
            wait_time = max(0, self.interval - elapsed)

            try:
                await asyncio.sleep(wait_time)
            except asyncio.CancelledError:
                self.logger.info("分析循环被取消")
                break

        self.logger.info("屏幕分析循环结束")

    async def _capture_and_analyze(self):
        """截图并分析"""
        # 截图
        image_data = await self._capture_screenshot()
        if not image_data:
            return

        current_time = time.time()

        # 转换为numpy数组（用于差异计算）
        image_array = self._base64_to_array(image_data)
        if image_array is None:
            return

        # 创建当前帧数据
        current_frame = FrameData(timestamp=current_time, image_base64=image_data, image_array=image_array)

        # 如果有上一帧，计算差异
        if len(self.frame_cache) > 0:
            previous_frame = self.frame_cache[-1]
            difference_score = self._calculate_difference(previous_frame.image_array, image_array)
            current_frame.difference_score = difference_score

            self.logger.debug(f"帧差异得分: {difference_score:.2f}")

        # 添加到缓存
        self.frame_cache.append(current_frame)

        # 检查是否触发阈值
        if await self._check_threshold():
            # 触发阈值，返回当前图像
            await self._handle_change_detected(current_frame)

    async def _capture_screenshot(self) -> Optional[str]:
        """截图并返回Base64编码"""
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # 主显示器
                sct_img = sct.grab(monitor)

                # 转换为PIL图像
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

                # 缩放图像以提高处理效率
                img = img.resize((self.resize_width, self.resize_height), Image.Resampling.LANCZOS)

                # 转换为Base64
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                img_bytes = buffer.getvalue()
                encoded_image = base64.b64encode(img_bytes).decode("utf-8")

                return encoded_image

        except Exception as e:
            self.logger.error(f"截图失败: {e}", exc_info=True)
            return None

    def _base64_to_array(self, base64_data: str) -> Optional[np.ndarray]:
        """将Base64图像转换为numpy数组"""
        try:
            # 解码Base64
            image_bytes = base64.b64decode(base64_data)

            # 转换为PIL图像
            image = Image.open(BytesIO(image_bytes))

            # 转换为numpy数组
            image_array = np.array(image)

            # 如果是RGB，转换为灰度图以提高处理效率
            if len(image_array.shape) == 3:
                image_array = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)

            return image_array

        except Exception as e:
            self.logger.error(f"图像转换失败: {e}", exc_info=True)
            return None

    def _calculate_difference(self, img1: np.ndarray, img2: np.ndarray) -> float:
        """
        计算两张图像的差异度

        Args:
            img1: 上一帧图像
            img2: 当前帧图像

        Returns:
            差异度分数 (0-100)
        """
        try:
            # 确保两张图像尺寸相同
            if img1.shape != img2.shape:
                self.logger.warning("图像尺寸不匹配，无法计算差异")
                return 0.0

            # 方法1: 均方误差 (MSE)
            mse = np.mean((img1.astype(float) - img2.astype(float)) ** 2)

            # 方法2: 结构相似性 (SSIM) - 需要skimage
            # 这里使用简化版本

            # 方法3: 直方图比较
            hist1 = cv2.calcHist([img1], [0], None, [256], [0, 256])
            hist2 = cv2.calcHist([img2], [0], None, [256], [0, 256])
            hist_diff = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

            # 组合不同的差异指标
            # MSE归一化到0-100范围
            mse_normalized = min(100.0, (mse / 255.0) * 100)

            # 直方图相关性转换为差异度
            hist_diff_normalized = (1 - hist_diff) * 100

            # 加权平均
            final_score = mse_normalized * 0.7 + hist_diff_normalized * 0.3

            return final_score

        except Exception as e:
            self.logger.error(f"计算图像差异失败: {e}", exc_info=True)
            return 0.0

    async def _check_threshold(self) -> bool:
        """检查是否达到阈值"""
        if len(self.frame_cache) < self.check_window:
            return False

        # 获取最近几帧的差异分数
        recent_frames = list(self.frame_cache)[-self.check_window :]
        recent_diffs = [frame.difference_score for frame in recent_frames]

        # 计算累计差异
        total_diff = sum(recent_diffs)

        self.logger.debug(f"最近{self.check_window}帧累计差异: {total_diff:.2f}, 阈值: {self.diff_threshold}")

        return total_diff > self.diff_threshold

    async def _handle_change_detected(self, current_frame: FrameData):
        """处理检测到变化的情况"""
        self.logger.info(f"检测到显著变化！累计差异超过阈值 {self.diff_threshold}")

        # 准备返回数据
        change_data = {
            "timestamp": current_frame.timestamp,
            "image_base64": current_frame.image_base64,
            "difference_score": current_frame.difference_score,
            "cache_frames": len(self.frame_cache),
            "triggered_by": "threshold_exceeded",
        }

        # 调用回调函数
        if self.on_change_detected:
            try:
                if asyncio.iscoroutinefunction(self.on_change_detected):
                    await self.on_change_detected(change_data)
                else:
                    self.on_change_detected(change_data)
            except Exception as e:
                self.logger.error(f"回调函数执行失败: {e}", exc_info=True)

        # 清空缓存
        self.clear_cache()

    def get_cache_status(self) -> Dict[str, Any]:
        """获取缓存状态"""
        if len(self.frame_cache) == 0:
            return {"cache_size": 0, "recent_differences": [], "total_recent_diff": 0.0, "is_running": self.is_running}

        recent_diffs = [frame.difference_score for frame in self.frame_cache]

        return {
            "cache_size": len(self.frame_cache),
            "recent_differences": recent_diffs,
            "total_recent_diff": sum(recent_diffs[-self.check_window :]),
            "threshold": self.diff_threshold,
            "is_running": self.is_running,
            "last_timestamp": self.frame_cache[-1].timestamp if self.frame_cache else None,
        }

    def update_threshold(self, new_threshold: float):
        """动态更新阈值"""
        old_threshold = self.diff_threshold
        self.diff_threshold = new_threshold
        self.logger.info(f"阈值已更新: {old_threshold} -> {new_threshold}")


# 使用示例
async def example_usage():
    """使用示例"""

    def on_change(data):
        print(f"检测到变化！时间戳: {data['timestamp']}")
        print(f"差异分数: {data['difference_score']:.2f}")
        print(f"图像数据大小: {len(data['image_base64'])} bytes")

    # 创建分析器
    analyzer = ScreenAnalyzer(
        interval=0.2,  # 0.2秒间隔
        diff_threshold=50.0,  # 差异阈值
        check_window=3,  # 检查最近3帧
    )

    # 设置回调
    analyzer.set_change_callback(on_change)

    try:
        # 启动分析器
        await analyzer.start()

        # 运行10秒
        await asyncio.sleep(10)

    finally:
        # 停止分析器
        await analyzer.stop()


if __name__ == "__main__":
    asyncio.run(example_usage())
