"""
LLM 服务 - 核心基础设施

提供统一的 LLM 调用接口，管理多个 LLM 后端配置（llm, llm_fast, vlm 等）
"""

import asyncio
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, AsyncIterator
from src.utils.logger import get_logger


class LLMService:
    """
    LLM 服务管理器

    核心基础设施服务，与 EventBus 同级。

    职责：
    - 管理多个 LLM 后端配置（llm, llm_fast, vlm 等）
    - 提供统一的调用接口
    - 内置重试、超时、降级机制
    - Token 使用量统计

    使用示例：
        ```python
        # 在 AmaidesuCore 中初始化
        self.llm_service = LLMService()
        await self.llm_service.setup(config)

        # 在 Provider/模块中使用
        response = await llm_service.chat(
            prompt="你好",
            backend="llm_fast",
        )
        ```
    """

    def __init__(self):
        self.logger = get_logger("LLMService")
        self._backends: Dict[str, Any] = {}
        self._config: Dict[str, Any] = {}
        self._token_manager = None
        self._retry_config = RetryConfig()

    async def setup(self, config: Dict[str, Any]) -> None:
        """
        从配置初始化所有 LLM 后端

        Args:
            config: 完整配置字典，包含 [llm], [llm_fast], [vlm] 等部分
        """
        self._config = config

        # 延迟导入，避免循环依赖

        # 支持的后端类型映射
        backend_types = {
            "openai": None,  # 将在 setup 时动态导入
            "ollama": None,
        }

        # 初始化配置中定义的后端
        for name in ["llm", "llm_fast", "vlm"]:
            if name in config:
                backend_config = config[name]
                backend_type = backend_config.get("backend", "openai")

                if backend_type not in backend_types:
                    self.logger.warning(f"未知的后端类型: {backend_type}，使用 openai")
                    backend_type = "openai"

                # 动态导入后端实现
                if backend_type == "openai":
                    from src.core.llm_backends.openai_backend import OpenAIBackend

                    backend_class = OpenAIBackend
                elif backend_type == "ollama":
                    from src.core.llm_backends.ollama_backend import OllamaBackend

                    backend_class = OllamaBackend
                else:
                    # 降级到 OpenAI
                    from src.core.llm_backends.openai_backend import OpenAIBackend

                    backend_class = OpenAIBackend

                self._backends[name] = backend_class(backend_config)
                self.logger.info(f"已初始化 {name} 后端 ({backend_type})")

        # 初始化 token 管理器
        from src.openai_client.token_usage_manager import TokenUsageManager

        self._token_manager = TokenUsageManager(use_global=True)

    # === 主要调用接口 ===

    async def chat(
        self,
        prompt: str,
        *,
        backend: str = "llm",
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        聊天调用

        Args:
            prompt: 用户输入
            backend: 使用的后端名称（llm, llm_fast, vlm）
            system_message: 系统消息
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            LLMResponse: 响应结果
        """
        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            backend,
            "chat",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def stream_chat(
        self,
        prompt: str,
        *,
        backend: str = "llm",
        system_message: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """
        流式聊天调用

        Args:
            prompt: 用户输入
            backend: 使用的后端名称
            system_message: 系统消息
            stop_event: 停止事件（用于中断流式输出）

        Yields:
            str: 增量文本内容
        """
        llm_backend = self._get_backend(backend)
        messages = self._build_messages(prompt, system_message)

        async for chunk in llm_backend.stream_chat(
            messages=messages,
            stop_event=stop_event,
        ):
            yield chunk

    async def call_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        *,
        backend: str = "llm",
        system_message: Optional[str] = None,
    ) -> LLMResponse:
        """
        工具调用

        Args:
            prompt: 用户输入
            tools: 工具定义列表（OpenAI 格式）
            backend: 使用的后端名称
            system_message: 系统消息

        Returns:
            LLMResponse: 包含 tool_calls 的响应结果
        """
        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            backend,
            "chat",
            messages=messages,
            tools=tools,
        )

    async def vision(
        self,
        prompt: str,
        images: List[Any],
        *,
        backend: str = "vlm",
        system_message: Optional[str] = None,
    ) -> LLMResponse:
        """
        视觉理解调用

        Args:
            prompt: 用户输入
            images: 图片列表（URL、路径或字节）
            backend: 使用的后端名称（默认 vlm）
            system_message: 系统消息

        Returns:
            LLMResponse: 响应结果
        """
        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            backend,
            "vision",
            messages=messages,
            images=images,
        )

    # === 便捷方法 ===

    async def simple_chat(
        self,
        prompt: str,
        backend: str = "llm",
        system_message: Optional[str] = None,
    ) -> str:
        """
        简化聊天，直接返回文本

        Args:
            prompt: 用户输入
            backend: 使用的后端名称
            system_message: 系统消息

        Returns:
            str: 响应文本，失败时返回错误信息
        """
        result = await self.chat(prompt, backend=backend, system_message=system_message)
        return result.content if result.success else f"错误: {result.error}"

    async def simple_vision(
        self,
        prompt: str,
        images: List[Any],
        backend: str = "vlm",
    ) -> str:
        """简化视觉理解，直接返回文本"""
        result = await self.vision(prompt, images, backend=backend)
        return result.content if result.success else f"错误: {result.error}"

    # === 内部方法 ===

    def _get_backend(self, name: str) -> Any:
        """获取指定后端"""
        if name not in self._backends:
            raise ValueError(f"LLM 后端 '{name}' 未配置")
        return self._backends[name]

    def _build_messages(
        self,
        prompt: str,
        system_message: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """构建消息列表"""
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _call_with_retry(
        self,
        backend_name: str,
        method: str,
        **kwargs,
    ) -> LLMResponse:
        """带重试的调用"""
        llm_backend = self._get_backend(backend_name)
        last_error = None

        for attempt in range(self._retry_config.max_retries):
            try:
                method_func = getattr(llm_backend, method)
                result = await method_func(**kwargs)

                # 记录 token 使用量
                if result.success and result.usage and self._token_manager:
                    self._token_manager.record_usage(
                        model_name=result.model or "unknown",
                        prompt_tokens=result.usage.get("prompt_tokens", 0),
                        completion_tokens=result.usage.get("completion_tokens", 0),
                        total_tokens=result.usage.get("total_tokens", 0),
                    )

                return result

            except Exception as e:
                last_error = e
                self.logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{self._retry_config.max_retries}): {e}")
                if attempt < self._retry_config.max_retries - 1:
                    delay = min(
                        self._retry_config.base_delay * (2**attempt),
                        self._retry_config.max_delay,
                    )
                    await asyncio.sleep(delay)

        # 所有重试失败
        self.logger.error(f"所有 LLM 调用重试失败: {last_error}")
        return LLMResponse(success=False, content=None, error=str(last_error))

    # === 生命周期 ===

    async def cleanup(self) -> None:
        """清理所有后端资源"""
        for name, backend in self._backends.items():
            try:
                await backend.cleanup()
                self.logger.debug(f"已清理 {name} 后端")
            except Exception as e:
                self.logger.warning(f"清理 {name} 后端失败: {e}")
        self._backends.clear()

    # === 统计信息 ===

    def get_token_usage_summary(self) -> str:
        """获取 token 使用量摘要"""
        if self._token_manager:
            return self._token_manager.format_total_cost_summary()
        return "Token 管理器未初始化"

    def get_backend_info(self) -> Dict[str, Any]:
        """获取所有后端信息"""
        return {name: backend.get_info() for name, backend in self._backends.items()}
