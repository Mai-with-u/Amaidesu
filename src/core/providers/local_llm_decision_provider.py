"""
LocalLLMDecisionProvider - 本地LLM决策提供者

职责:
- 使用 LLM Service 进行决策
- 支持自定义 prompt 模板
- 错误处理和降级机制
"""

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from src.core.providers.decision_provider import DecisionProvider
from src.utils.logger import get_logger
from maim_message import MessageBase

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.llm_service import LLMService
    from src.canonical.canonical_message import CanonicalMessage


class LocalLLMDecisionProvider(DecisionProvider):
    """
    本地LLM决策提供者

    使用 LLM Service 统一接口进行决策。

    配置示例:
        ```toml
        [decision.local_llm]
        backend = "llm_fast"  # 使用的 LLM 后端（llm, llm_fast, vlm）
        prompt_template = "You are a helpful assistant. User: {text}"
        fallback_mode = "simple"
        ```

    属性:
        backend: 使用的 LLM 后端名称
        prompt_template: Prompt模板，使用{text}占位符
        fallback_mode: 降级模式（"simple"返回简单响应，"error"抛出异常）
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 LocalLLMDecisionProvider

        Args:
            config: 配置字典
        """
        self.config = config
        self.logger = get_logger("LocalLLMDecisionProvider")

        # LLM Service 引用（通过 setup 注入）
        self._llm_service: Optional["LLMService"] = None

        # LLM 配置
        self.backend = config.get("backend", "llm")  # 使用的后端名称

        # Prompt 配置
        self.prompt_template = config.get(
            "prompt_template",
            "You are a helpful AI assistant. Please respond to the user's message.\n\nUser: {text}\n\nAssistant:",
        )

        # 降级模式配置
        self.fallback_mode = config.get("fallback_mode", "simple")

        # 统计信息
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0

        # EventBus 引用（用于事件通知）
        self._event_bus: Optional["EventBus"] = None

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> None:
        """
        设置 LocalLLMDecisionProvider

        Args:
            event_bus: EventBus 实例
            config: Provider 配置（使用 __init__ 传入的 config）
        """
        self._event_bus = event_bus
        self.logger.info("初始化 LocalLLMDecisionProvider...")

        # 验证 prompt 模板包含 {text} 占位符
        if "{text}" not in self.prompt_template:
            self.logger.warning("prompt_template 中未包含 {text} 占位符，将直接替换")
            self.prompt_template = self.prompt_template.replace("{}", "{text}")

        self.logger.info(f"LocalLLMDecisionProvider 初始化完成 (Backend: {self.backend})")

    async def decide(self, canonical_message: "CanonicalMessage") -> MessageBase:
        """
        进行决策（通过 LLM 生成响应）

        Args:
            canonical_message: 标准化消息

        Returns:
            MessageBase: 决策结果（LLM 生成的响应）

        Raises:
            RuntimeError: 如果所有重试失败且 fallback_mode 为 "error"
        """
        if self._llm_service is None:
            raise RuntimeError("LLM Service 未注入！请确保在 setup 中正确配置。")

        self._total_requests += 1

        # 构建提示词
        prompt = self.prompt_template.format(text=canonical_message.text)

        try:
            # 使用 LLM Service 进行调用
            response = await self._llm_service.chat(
                prompt=prompt,
                backend=self.backend,
            )

            if not response.success:
                self._failed_requests += 1
                self.logger.error(f"LLM 调用失败: {response.error}")
                # 使用降级策略
                return self._handle_fallback(canonical_message)

            self._successful_requests += 1
            return self._create_message_base(response.content, canonical_message)

        except Exception as e:
            self._failed_requests += 1
            self.logger.error(f"LLM 调用异常: {e}", exc_info=True)
            # 使用降级策略
            return self._handle_fallback(canonical_message)

    def _handle_fallback(self, canonical_message: "CanonicalMessage") -> MessageBase:
        """处理降级逻辑"""
        if self.fallback_mode == "simple":
            # 简单降级：返回原始文本
            return self._create_message_base(canonical_message.text, canonical_message)
        elif self.fallback_mode == "echo":
            # 回声降级：重复用户输入
            return self._create_message_base(f"你说：{canonical_message.text}", canonical_message)
        else:
            # 错误降级：抛出异常
            raise RuntimeError("LLM 请求失败，且未配置降级模式")

    def _create_message_base(self, text: str, canonical_message: "CanonicalMessage") -> "MessageBase":
        """
        创建MessageBase对象

        Args:
            text: 响应文本
            canonical_message: 原始CanonicalMessage

        Returns:
            MessageBase实例
        """
        try:
            from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo

            # 构建UserInfo
            user_info = UserInfo(
                user_id=canonical_message.metadata.get("user_id", "llm"),
                nickname=canonical_message.metadata.get("user_nickname", "Local LLM"),
            )

            # 构建FormatInfo
            format_info = FormatInfo(font=None, color=None, size=None)

            # 构建Seg（文本片段）
            seg = Seg(type="text", data=text, format=format_info)

            # 构建MessageBase
            message = MessageBase(
                message_info=BaseMessageInfo(
                    message_id=f"llm_{int(canonical_message.timestamp)}",
                    platform="llm",
                    sender=user_info,
                    timestamp=canonical_message.timestamp,
                ),
                message_segment=seg,
            )

            return message
        except Exception as e:
            self.logger.error(f"创建MessageBase失败: {e}", exc_info=True)
            raise

    async def cleanup(self) -> None:
        """
        清理资源

        输出统计信息。
        """
        self.logger.info("清理LocalLLMDecisionProvider...")

        # 输出统计信息
        success_rate = self._successful_requests / self._total_requests * 100 if self._total_requests > 0 else 0
        self.logger.info(
            f"统计: 总请求={self._total_requests}, 成功={self._successful_requests}, "
            f"失败={self._failed_requests}, 成功率={success_rate:.1f}%"
        )

        self.logger.info("LocalLLMDecisionProvider已清理")

    def get_info(self) -> Dict[str, Any]:
        """
        获取Provider信息

        Returns:
            Provider信息字典
        """
        return {
            "name": "LocalLLMDecisionProvider",
            "version": "1.0.0",
            "api_base": self.api_base,
            "model": self.model,
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "failed_requests": self._failed_requests,
        }
