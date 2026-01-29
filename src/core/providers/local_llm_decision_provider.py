"""
LocalLLMDecisionProvider - 本地LLM决策提供者

职责:
- 使用OpenAI兼容API进行决策
- 支持自定义prompt模板
- 错误处理和降级机制
"""

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from src.core.providers.decision_provider import DecisionProvider
from src.utils.logger import get_logger
from maim_message import MessageBase  # 移出TYPE_CHECKING块，因为运行时需要

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.canonical.canonical_message import CanonicalMessage


class LocalLLMDecisionProvider(DecisionProvider):
    """
    本地LLM决策提供者

    使用OpenAI兼容API（如本地Ollama）进行决策。

    配置示例:
        ```toml
        [decision.local_llm]
        api_base = "http://localhost:11434/v1"
        model = "llama2"
        api_key = "sk-dummy"  # Ollama不需要真实API key
        prompt_template = "You are a helpful assistant. User: {text}"
        timeout = 30
        max_retries = 3
        fallback_mode = "simple"
        ```

    属性:
        api_base: OpenAI API基础URL
        model: 使用的模型名称
        api_key: API密钥
        prompt_template: Prompt模板，使用{text}占位符
        timeout: 请求超时时间（秒）
        max_retries: 最大重试次数
        fallback_mode: 降级模式（"simple"返回简单响应，"error"抛出异常）
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化LocalLLMDecisionProvider

        Args:
            config: 配置字典
        """
        self.config = config
        self.logger = get_logger("LocalLLMDecisionProvider")

        # API配置
        self.api_base = config.get("api_base", "http://localhost:11434/v1")
        self.model = config.get("model", "llama2")
        self.api_key = config.get("api_key", "sk-dummy")

        # Prompt配置
        self.prompt_template = config.get(
            "prompt_template",
            "You are a helpful AI assistant. Please respond to the user's message.\n\nUser: {text}\n\nAssistant:",
        )

        # 超时和重试配置
        self.timeout = config.get("timeout", 30)
        self.max_retries = config.get("max_retries", 3)
        self.fallback_mode = config.get("fallback_mode", "simple")

        # 统计信息
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0

        # EventBus引用（用于事件通知）
        self._event_bus: Optional["EventBus"] = None

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> None:
        """
        设置LocalLLMDecisionProvider

        Args:
            event_bus: EventBus实例
            config: Provider配置（忽略，使用__init__传入的config）
        """
        self._event_bus = event_bus
        self.logger.info("初始化LocalLLMDecisionProvider...")

        # 验证API基础URL
        if not self.api_base:
            raise ValueError("api_base配置不能为空")

        # 验证模型名称
        if not self.model:
            raise ValueError("model配置不能为空")

        # 验证prompt模板包含{text}占位符
        if "{text}" not in self.prompt_template:
            self.logger.warning("prompt_template中未包含{text}占位符，将直接替换")
            self.prompt_template = self.prompt_template.replace("{}", "{text}")

        self.logger.info(f"LocalLLMDecisionProvider初始化完成 (API: {self.api_base}, Model: {self.model})")

    async def decide(self, canonical_message: "CanonicalMessage") -> MessageBase:
        """
        进行决策（通过LLM生成响应）

        Args:
            canonical_message: 标准化消息

        Returns:
            MessageBase: 决策结果（LLM生成的响应）

        Raises:
            RuntimeError: 如果所有重试失败且fallback_mode为"error"
        """
        self._total_requests += 1

        # 构建Prompt
        prompt = self.prompt_template.format(text=canonical_message.text)

        # 尝试多次请求（重试机制）
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                self.logger.debug(f"LLM请求 (尝试 {attempt + 1}/{self.max_retries})")
                response_text = await self._call_llm_api(prompt)
                self._successful_requests += 1
                return self._create_message_base(response_text, canonical_message)
            except Exception as e:
                last_exception = e
                self.logger.warning(f"LLM请求失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))  # 指数退避

        # 所有重试失败，使用降级策略
        self._failed_requests += 1
        self.logger.error(f"所有LLM请求失败，使用降级模式: {self.fallback_mode}")

        if self.fallback_mode == "simple":
            # 简单降级：返回原始文本
            return self._create_message_base(canonical_message.text, canonical_message)
        elif self.fallback_mode == "echo":
            # 回声降级：重复用户输入
            return self._create_message_base(f"你说：{canonical_message.text}", canonical_message)
        else:
            # 错误降级：抛出异常
            raise RuntimeError(f"LLM请求失败: {last_exception}") from last_exception

    async def _call_llm_api(self, prompt: str) -> str:
        """
        调用LLM API

        Args:
            prompt: 完整的prompt文本

        Returns:
            str: LLM生成的响应文本

        Raises:
            ConnectionError: 如果API连接失败
            TimeoutError: 如果请求超时
            Exception: 其他API错误
        """
        import aiohttp

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        data = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 500,
        }

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.api_base}/chat/completions"
                async with session.post(url, json=data, headers=headers, timeout=self.timeout) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"API返回错误状态 {response.status}: {error_text}")

                    result = await response.json()
                    response_text = result["choices"][0]["message"]["content"]
                    return response_text

        except asyncio.TimeoutError:
            raise TimeoutError(f"LLM API请求超时（{self.timeout}秒）") from None
        except aiohttp.ClientError as e:
            raise ConnectionError(f"LLM API连接失败: {e}") from e

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
