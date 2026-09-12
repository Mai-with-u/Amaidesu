"""
ScreenReader —— 屏幕变化 → VLM 文本

仅在 ``ScreenAnalyzer`` 检测到屏幕**变化**时才调用 VLM，避免无变化的轮询浪费 token。

VLM 调用统一走 ``LLMManager.chat_vision(client_type="vision")``，key/model/重试/日志
走 model.toml 的 ``[vlm]`` profile 与 ``[[llm_providers]]`` 池。

缓存去重策略：最近 ``max_cached_images`` 张图像哈希，避免重复调用；
回调式上下文更新（不破坏 Collector 主循环）。
"""

from __future__ import annotations

import hashlib
import io
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Deque, Optional

from src.modules.logging import get_logger
from src.modules.prompts import PromptManager, get_prompt_manager

# 注：不依赖 aiohttp（VLM 调用统一走 LLMManager → chat_vision → OpenAIClient.vision）。
# 图像以 bytes 形式传入 chat_vision，由 OpenAIClient._path_or_url_to_data_url 自动 data-URL 化。

# VLM 提示词模板键（正文见包内 prompts/screen_vlm_prompt.md 与 screen_vlm_system.md，
# 由 src/**/prompts/ 约定目录内聚承载）。system message 与 user prompt 是 chat_vision
# 的两个独立参数，故拆为两个零变量模板。
_SCREEN_PROMPT_KEY = "screen_vlm_prompt"
_SCREEN_SYSTEM_KEY = "screen_vlm_system"


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
    - VLM 调用统一经由 :class:`LLMManager.chat_vision`，key/model/重试/日志由
      ``config/model.toml`` 的 ``[vlm]`` profile + ``[[llm_providers]]`` 池统一管理
    """

    def __init__(
        self,
        max_cached_images: int = 5,
        llm_manager: Optional[Any] = None,
        prompt_manager: Optional[PromptManager] = None,
    ):
        # VLM 连接配置统一走 model.toml。
        # llm_manager 为 None 时保留"跳过 VLM 调用 + 返回说明性结果"的降级语义，
        # 便于未配置 VLM 场景（仅做缓存去重）。
        self.max_cached_images = max_cached_images
        self._llm_manager = llm_manager
        # PromptManager 取用方式：优先构造注入；未注入时回退全局单例
        # get_prompt_manager()（惰性、仅首次 VLM 调用时触发）。不选构造链透传的
        # 理由：ScreenReader 在 ScreenChangeCollector.collect() 内部创建，透传需
        # 横穿 main → factory → collector 三层只为这一个消费者，与项目文档的
        # 单例访问路径（模拟器 llm_wrapper 同款模式）相比改面大且无额外收益。
        self._prompt_manager = prompt_manager
        # 零变量模板渲染结果恒定，首用后缓存
        self._vlm_prompt: Optional[str] = None
        self._vlm_system_message: Optional[str] = None

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

    def _get_vlm_prompt(self) -> str:
        """渲染 VLM 用户 prompt（零变量模板，结果缓存复用）"""
        if self._vlm_prompt is None:
            manager = self._prompt_manager or get_prompt_manager()
            self._vlm_prompt = manager.render(_SCREEN_PROMPT_KEY)
        return self._vlm_prompt

    def _get_vlm_system_message(self) -> str:
        """渲染 VLM system message（零变量模板，结果缓存复用）"""
        if self._vlm_system_message is None:
            manager = self._prompt_manager or get_prompt_manager()
            self._vlm_system_message = manager.render(_SCREEN_SYSTEM_KEY)
        return self._vlm_system_message

    async def _call_vlm(self, image: Any, change_data: dict[str, Any]) -> Optional[ScreenAnalysisResult]:
        """通过 LLMManager 调用 VLM。

        降级语义：
        - 未注入 llm_manager → 返回带 "skipped" 标记的说明性 ScreenAnalysisResult
          （便于未配置 VLM 的场景仅做缓存去重）
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
                prompt=self._get_vlm_prompt(),
                images=[image_bytes],
                client_type="vision",
                system_message=self._get_vlm_system_message(),
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
