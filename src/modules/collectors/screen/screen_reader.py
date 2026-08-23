"""
ScreenReader —— 屏幕变化 → VLM 文本（v2 / Wave 5 迁移）

迁移自 ``src/stages/input/collectors/read_pingmu/``（占位补全）。仅在
``ScreenAnalyzer`` 检测到屏幕**变化**时才调用 VLM，避免无变化的轮询浪费 token。

verbatim 边界：缓存去重策略（最近 ``max_cached_images`` 张图像哈希，避免重复调用）、
回调式上下文更新（不破坏 Collector 主循环）。
"""

from __future__ import annotations

import base64
import hashlib
import io
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Deque, Optional

from src.modules.logging import get_logger

try:
    import aiohttp

    _AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None
    _AIOHTTP_AVAILABLE = False


@dataclass(slots=True)
class ScreenAnalysisResult:
    """单次屏幕分析结果（VLM 输出）"""

    new_current_context: str  # 新识别的屏幕文本/上下文
    raw_response: dict[str, Any]  # VLM 原始响应


class ScreenReader:
    """
    屏幕变化 → VLM 文本分析

    - 仅在屏幕发生变化（``ScreenAnalyzer`` 触发回调）时才调用 VLM，省 token
    - 维护最近 ``max_cached_images`` 张图像哈希作为缓存；相同 hash 直接跳过
    - 通过 ``set_context_update_callback`` 注入主循环的上下文更新入口
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name: str = "qwen2.5-vl-72b-instruct",
        max_cached_images: int = 5,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.max_cached_images = max_cached_images

        self.logger = get_logger("ScreenReader")
        self._image_hash_cache: Deque[str] = deque(maxlen=max_cached_images)
        self._on_context_update: Optional[Callable[[dict], Awaitable[None]]] = None

    def set_context_update_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """设置上下文更新回调（注入 ScreenChangeCollector）"""
        self._on_context_update = callback

    async def process_screen_change(self, change_data: dict[str, Any]) -> Optional[ScreenAnalysisResult]:
        """处理屏幕变化：缓存去重 + VLM 分析 + 回调更新"""
        image = change_data.get("image")
        if image is None:
            return None

        # 缓存去重（基于缩略图哈希）
        image_hash = self._hash_image(image)
        if image_hash in self._image_hash_cache:
            self.logger.debug("图像已缓存，跳过 VLM 调用")
            return None
        self._image_hash_cache.append(image_hash)

        # 调用 VLM
        result = await self._call_vlm(image, change_data)
        if result is None:
            return None

        # 回调更新主循环
        if self._on_context_update is not None:
            try:
                await self._on_context_update({"analysis_result": result, "change_data": change_data})
            except Exception as e:
                self.logger.error(f"context_update 回调异常: {e}", exc_info=True)

        return result

    async def _call_vlm(self, image: Any, change_data: dict[str, Any]) -> Optional[ScreenAnalysisResult]:
        """调用 VLM API"""
        if not self.api_key:
            self.logger.debug("未配置 api_key，跳过 VLM 调用")
            return ScreenAnalysisResult(
                new_current_context="[ScreenReader 未配置 api_key，仅缓存去重生效]",
                raw_response={"skipped": True},
            )
        if not _AIOHTTP_AVAILABLE:
            self.logger.warning("缺少 aiohttp，无法调用 VLM")
            return None

        try:
            image_b64 = self._image_to_base64(image)
            payload = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                            },
                            {
                                "type": "text",
                                "text": "请描述当前屏幕内容（简明扼要）",
                            },
                        ],
                    }
                ],
            }
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    data = await response.json()
            text = self._extract_text(data)
            return ScreenAnalysisResult(new_current_context=text, raw_response=data)
        except Exception as e:
            self.logger.error(f"VLM 调用失败: {e}", exc_info=True)
            return None

    @staticmethod
    def _hash_image(image: Any) -> str:
        """基于 PIL 图像缩略图的 SHA-256 哈希"""
        try:
            thumb = image.copy()
            thumb.thumbnail((32, 32))
            return hashlib.sha256(thumb.tobytes()).hexdigest()
        except Exception:
            return hashlib.sha256(str(id(image)).encode()).hexdigest()

    @staticmethod
    def _image_to_base64(image: Any) -> str:
        """PIL 图像 → base64 字符串（PNG）"""
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    @staticmethod
    def _extract_text(vlm_response: dict[str, Any]) -> str:
        """从 VLM 响应中提取文本"""
        try:
            choices = vlm_response.get("choices", [])
            if choices:
                message = choices[0].get("message", {})
                content = message.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = [item.get("text", "") for item in content if isinstance(item, dict)]
                    return "".join(parts)
            return ""
        except Exception:
            return ""
