"""
ScreenReader —— 屏幕变化 → VLM 文本（v2 / Wave 5 迁移 → v2.0.9 收编）

迁移自 ``src/stages/input/collectors/read_pingmu/``（占位补全）。仅在
``ScreenAnalyzer`` 检测到屏幕**变化**时才调用 VLM，避免无变化的轮询浪费 token。

v2.0.9 收编：原本用裸 aiohttp 自带 api_key/base_url/model_name 绕过
LLMManager profile 体系；现统一改为 ``LLMManager.chat_vision(client_type="vlm")``，
key/model/重试/日志走 model.toml 的 ``[vlm]`` profile 与 ``[[llm_providers]]`` 池。

verbatim 边界：缓存去重策略（最近 ``max_cached_images`` 张图像哈希，避免重复调用）、
回调式上下文更新（不破坏 Collector 主循环）。
"""

from __future__ import annotations

import hashlib
import io
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Deque, Optional

from src.modules.logging import get_logger

# 注：v2.0.9 移除 aiohttp 依赖（VLM 调用统一走 LLMManager → chat_vision → OpenAIClient.vision）。
# 图像以 bytes 形式传入 chat_vision，由 OpenAIClient._path_or_url_to_data_url 自动 data-URL 化。

# VLM 调用的 prompt 模板（v2.0.9 收编：从原 aiohttp payload 的 user message content 迁移）。
_VLM_PROMPT = "请描述当前屏幕内容（简明扼要）"

# 屏幕分析 system message（提示模型保持客观、聚焦可见内容）。
_VLM_SYSTEM_MESSAGE = (
    "你是 VTuber 主播的屏幕感知助手。请根据用户提供的截图，简明扼要地描述屏幕上的关键内容"
    "（如打开的窗口、正在进行的操作、文字内容等），便于主播理解屏幕上下文并据此回应弹幕。"
)


@dataclass(slots=True)
class ScreenAnalysisResult:
    """单次屏幕分析结果（VLM 输出）"""

    new_current_context: str  # 新识别的屏幕文本/上下文
    raw_response: dict[str, Any]  # VLM 原始响应（来自 LLMResponse 字段）


class ScreenReader:
    """
    屏幕变化 → VLM 文本分析

    - 仅在屏幕发生变化（``ScreenAnalyzer`` 触发回调）时才调用 VLM，省 token
    - 维护最近 ``max_cached_images`` 张图像哈希作为缓存；相同 hash 直接跳过
    - 通过 ``set_context_update_callback`` 注入主循环的上下文更新入口
    - v2.0.9：VLM 调用统一经由 :class:`LLMManager.chat_vision`，key/model/重试/日志由
      ``config/model.toml`` 的 ``[vlm]`` profile + ``[[llm_providers]]`` 池统一管理
    """

    def __init__(
        self,
        max_cached_images: int = 5,
        llm_manager: Optional[Any] = None,
    ):
        # v2.0.9：移除 api_key/base_url/model_name 参数（统一走 model.toml）。
        # llm_manager 为 None 时保留"跳过 VLM 调用 + 返回说明性结果"的降级语义，
        # 与旧版 api_key 为空时的行为等价，便于未配置 VLM 场景（仅做缓存去重）。
        self.max_cached_images = max_cached_images
        self._llm_manager = llm_manager

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
        """通过 LLMManager 调用 VLM（v2.0.9 收编路径）。

        降级语义：
        - 未注入 llm_manager → 返回带 "skipped" 标记的说明性 ScreenAnalysisResult
          （与旧版 api_key 为空时等价，便于未配置 VLM 的场景仅做缓存去重）
        - llm_manager 注入但 chat_vision 失败 → 返回 None（异常分支由调用方按既有
          日志路径处理）
        """
        if self._llm_manager is None:
            self.logger.debug("未注入 llm_manager，跳过 VLM 调用（仅缓存去重生效）")
            return ScreenAnalysisResult(
                new_current_context="[ScreenReader 未注入 llm_manager，仅缓存去重生效]",
                raw_response={"skipped": True},
            )

        try:
            image_bytes = self._image_to_bytes(image)
            response = await self._llm_manager.chat_vision(
                prompt=_VLM_PROMPT,
                images=[image_bytes],
                client_type="vlm",
                system_message=_VLM_SYSTEM_MESSAGE,
            )
        except Exception as e:
            self.logger.error(f"VLM 调用异常: {e}", exc_info=True)
            return None

        if not response.success:
            self.logger.warning(
                f"VLM 调用未成功 (error={response.error!r}); 降级为不返回分析结果（缓存已记录，不重复触发）"
            )
            return None

        text = (response.content or "").strip()
        raw = {
            "model": response.model,
            "usage": response.usage,
            "success": response.success,
            "reasoning_content": getattr(response, "reasoning_content", None),
        }
        return ScreenAnalysisResult(new_current_context=text, raw_response=raw)

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
    def _image_to_bytes(image: Any) -> bytes:
        """PIL 图像 → PNG 字节流（供 LLMManager.chat_vision 作为 image 参数）。

        chat_vision 的 OpenAIClient 后端接受 ``bytes``，由
        ``_infer_mime_from_bytes`` 自动推断 ``image/png`` 并 base64 编码。
        """
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
