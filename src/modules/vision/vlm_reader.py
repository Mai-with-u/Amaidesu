"""视觉语言模型文本识别的 `TextReader` 生产实现。

读屏图像交由 VLM 提取文本：完整等待识别结果，模板渲染或生成失败时
记录原因并返回空文本（降级语义由 `look_at_screen` 工具统一组装）。
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.llm.bootstrap import ProfileNames
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager

logger = get_logger("vlm_reader")

# VLM 调用模板键（模板文件位于 vision/prompts/）
SCREEN_VLM_PROMPT_KEY = "screen_vlm_prompt"
SCREEN_VLM_SYSTEM_KEY = "screen_vlm_system"


class LlmVisionTextReader:
    """完整等待视觉模型识别画面，生成失败时记录原因并返回空文本。"""

    def __init__(
        self,
        *,
        llm_manager: Any,
        prompt_manager: Optional[PromptManager] = None,
    ) -> None:
        self._llm_manager = llm_manager
        self._prompt_manager = prompt_manager

    def _render_user_prompt(self, question: Optional[str]) -> str:
        """user prompt：优先用调用方传入的 question，否则渲染默认模板。"""
        if question:
            return question
        if self._prompt_manager is None:
            return "描述屏幕上正在显示的内容。"
        try:
            rendered = self._prompt_manager.render(SCREEN_VLM_PROMPT_KEY)
        except Exception as exc:  # noqa: BLE001 - 模板缺失/变量不匹配时降级到内置默认
            logger.warning(f"LlmVisionTextReader 渲染 {SCREEN_VLM_PROMPT_KEY} 失败: {exc}; 改用内置默认")
            return "描述屏幕上正在显示的内容。"
        return rendered or "描述屏幕上正在显示的内容。"

    def _render_system_prompt(self) -> Optional[str]:
        """system 模板（如注入 prompt_manager）：渲染 screen_vlm_system。"""
        if self._prompt_manager is None:
            return None
        try:
            return self._prompt_manager.render(SCREEN_VLM_SYSTEM_KEY)
        except Exception as exc:  # noqa: BLE001 - 模板缺失时降级为 None（系统无 system 也可调）
            logger.warning(f"LlmVisionTextReader 渲染 {SCREEN_VLM_SYSTEM_KEY} 失败: {exc}; system 留空")
            return None

    async def read(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        question: Optional[str] = None,
    ) -> str:
        """等待完整识别结果；生成失败时返回空文本。"""
        prompt = self._render_user_prompt(question)
        system = self._render_system_prompt()
        try:
            # 读屏可能包含较长文字；等待模型完整返回，并继承父任务的主动取消。
            response = await self._llm_manager.generate_vision(
                prompt,
                [image_bytes],
                profile=ProfileNames.VISION,
                system=system,
            )
        except Exception as exc:  # noqa: BLE001 - 边界处兜底（不抛）
            logger.warning(f"LlmVisionTextReader VLM 调用异常: {exc}", exc=True)
            return ""

        if not response.ok:
            logger.warning(f"LlmVisionTextReader VLM 返回失败 (error={response.error!r}); 降级为空文本")
            return ""

        content = response.content or ""
        return content.strip()


__all__ = [
    "LlmVisionTextReader",
    "SCREEN_VLM_PROMPT_KEY",
    "SCREEN_VLM_SYSTEM_KEY",
]
