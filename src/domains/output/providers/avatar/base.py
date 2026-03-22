"""
AvatarProviderBase - 虚拟形象 Provider 抽象基类

定义了所有 Avatar Provider 的通用处理流程:
1. 继承 OutputProvider，自动订阅 OUTPUT_INTENT_READY 事件
2. 翻译自然语言 Intent 为平台参数 (_translate_with_llm)
3. 适配 Intent 为平台参数 (_adapt_intent)
4. 渲染到平台 (_render_to_platform)
5. 连接/断开管理
"""

import json
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from src.modules.llm.manager import LLMManager
from src.modules.llm.manager import ClientType
from src.modules.logging import get_logger
from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.di.context import ProviderContext
    from src.modules.prompts.manager import PromptManager
    from src.modules.types import Intent


class AvatarProviderBase(OutputProvider, ABC):
    """
    虚拟形象 Provider 抽象基类（重构后）

    继承 OutputProvider，自动订阅 OUTPUT_INTENT_READY 事件。
    子类只需实现平台特定的适配和渲染逻辑。

    LLM 翻译层：
    - Decision Domain 输出自然语言 emotion/action（如"开心"、"比心"）
    - Output Domain 使用 LLM 将其翻译为平台特定的英文键
    """

    # 子类必须定义这些映射表（用于 LLM 翻译层）
    EMOTION_KEYS: set = set()
    ACTION_KEYS: set = set()

    def __init__(self, config: dict, context: "ProviderContext"):
        super().__init__(config, context)
        self.logger = get_logger(self.__class__.__name__)
        self._is_connected = False

    @property
    def llm_service(self) -> Optional["LLMManager"]:
        return self.context.llm_service if self.context else None

    @property
    def prompt_service(self) -> Optional["PromptManager"]:
        return self.context.prompt_service if self.context else None

    async def execute(self, intent: "Intent"):
        """
        执行意图，翻译后适配渲染到平台

        Args:
            intent: 平台无关的 Intent
        """
        if not self._is_connected:
            self.logger.warning("未连接，跳过渲染")
            return

        try:
            # 0. 使用 LLM 翻译自然语言为平台特定键
            translated_intent = await self._translate_with_llm(intent)

            # 1. 适配 Intent 为平台参数
            params = await self._adapt_intent(translated_intent)

            # 2. 渲染到平台
            await self._render_to_platform(params)
        except Exception as e:
            self.logger.error(f"渲染失败: {e}", exc_info=True)

    async def _translate_with_llm(self, intent: "Intent") -> "Intent":
        """
        使用 LLM 将自然语言 emotion/action 翻译为平台特定的英文键

        Args:
            intent: 原始 Intent（可能包含中文自然语言）

        Returns:
            翻译后的 Intent（emotion/action 变为英文键）
        """
        from src.modules.types import Intent

        # 克隆 intent 以避免修改原对象
        emotion = intent.emotion or "neutral"
        action = intent.action or ""

        # 检查是否需要翻译（中文情感/动作）
        needs_translation = self._needs_translation(emotion, action)

        if not needs_translation:
            return intent

        # 调用 LLM 翻译
        if not self.llm_service or not self.prompt_service:
            self.logger.warning("LLM Service 或 Prompt Service 不可用，使用默认映射")
            return intent

        try:
            prompt = self._build_translation_prompt(emotion, action)
            self.logger.debug(f"LLM 翻译: emotion={emotion}, action={action}")

            response = await self.llm_service.chat(
                prompt=prompt,
                client_type=ClientType.LLM_FAST,
            )

            if not response.success or not response.content:
                self.logger.warning(f"LLM 翻译失败: {response.error}，使用默认映射")
                return intent

            translated = self._parse_translation_response(response.content)
            if translated:
                emotion = translated.get("emotion", emotion)
                action = translated.get("action", action)
                self.logger.debug(f"LLM 翻译结果: emotion={emotion}, action={action}")

            return Intent(
                emotion=emotion,
                action=action,
                speech=intent.speech,
                context=intent.context,
                metadata=intent.metadata,
            )

        except Exception as e:
            self.logger.warning(f"LLM 翻译异常: {e}，使用默认映射")
            return intent

    def _needs_translation(self, emotion: str, action: str) -> bool:
        """
        检查是否需要 LLM 翻译

        如果 emotion 或 action 是中文，返回 True

        Args:
            emotion: 情感字符串
            action: 动作字符串

        Returns:
            是否需要翻译
        """
        # 中文 Unicode 范围
        chinese_pattern = re.compile(r"[\u4e00-\u9fff]")

        # 检查是否包含中文
        if chinese_pattern.search(emotion) or chinese_pattern.search(action):
            return True

        # 检查是否已经是英文键（存在于映射表中）
        if emotion in self.EMOTION_KEYS or emotion == "neutral":
            if not action or action in self.ACTION_KEYS or not action:
                return False

        return True

    def _build_translation_prompt(self, emotion: str, action: str) -> str:
        emotion_keys = ", ".join(f'"{k}"' for k in self.EMOTION_KEYS if k != "neutral")
        action_keys = ", ".join(f'"{k}"' for k in self.ACTION_KEYS)
        return self.prompt_service.render_safe(
            "output/intent_translator",
            emotion=emotion,
            action=action,
            emotion_keys=emotion_keys,
            action_keys=action_keys,
        )

    def _parse_translation_response(self, content: str) -> Optional[dict]:
        """
        解析 LLM 翻译响应

        Args:
            content: LLM 返回的原始内容

        Returns:
            解析后的 dict 或 None
        """
        try:
            # 清理 JSON
            cleaned = content.strip()
            cleaned = re.sub(r"^```json\s*", "", cleaned)
            cleaned = re.sub(r"^```\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

            # 截取 JSON
            first_brace = cleaned.find("{")
            last_brace = cleaned.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                cleaned = cleaned[first_brace : last_brace + 1]

            # 去掉尾逗号
            cleaned = re.sub(r",\s*}", "}", cleaned)
            cleaned = re.sub(r",\s*]", "]", cleaned)

            return json.loads(cleaned)
        except Exception as e:
            self.logger.warning(f"解析翻译响应失败: {e}")
            return None

    # ==================== 子类必须实现的抽象方法 ====================

    @abstractmethod
    async def _adapt_intent(self, intent: "Intent") -> Any:
        """
        适配 Intent 为平台特定参数

        子类必须实现此方法，直接在内部完成 Intent → 平台参数的转换。

        Returns:
            平台特定的参数对象（可以是 Dict、Pydantic Model 等）
        """
        pass

    @abstractmethod
    async def _render_to_platform(self, params: Any) -> None:
        """
        平台特定的渲染逻辑

        Args:
            params: _adapt_intent() 返回的平台特定参数
        """
        pass

    # ==================== 生命周期方法 ====================

    async def init(self):
        """初始化 Provider：连接平台"""
        await self._connect()
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self):
        """清理资源：断开连接"""
        await self._disconnect()
        self.logger.info(f"{self.__class__.__name__} 已停止")

    @abstractmethod
    async def _connect(self) -> None:
        """连接到平台"""
        pass

    @abstractmethod
    async def _disconnect(self) -> None:
        """断开平台连接"""
        pass
