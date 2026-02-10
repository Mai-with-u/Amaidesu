"""
LLM 管理器 - 核心基础设施

提供统一的 LLM 调用接口，管理多个 LLM 客户端类型（llm, llm_fast, vlm 等）

设计文档: refactor/design/llm_manager.md
"""

import asyncio
import os
from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.logging import get_logger

# === 数据类定义 ===


class LLMResponse(BaseModel):
    """LLM 响应结果"""

    success: bool
    content: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    reasoning_content: Optional[str] = None
    error: Optional[str] = None


class RetryConfig(BaseModel):
    """LLM 调用重试配置"""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0


# 支持的客户端类型
class ClientType:
    """LLM 客户端类型常量"""

    LLM = "llm"  # 标准LLM客户端（高质量任务）
    LLM_FAST = "llm_fast"  # 快速LLM客户端（低延迟任务）
    VLM = "vlm"  # 视觉语言模型客户端（图像理解）
    LLM_LOCAL = "llm_local"  # 本地LLM客户端（Ollama等）

    # 所有支持的客户端类型
    ALL = [LLM, LLM_FAST, VLM, LLM_LOCAL]

    # 默认客户端
    DEFAULT = LLM

    @classmethod
    def is_valid(cls, client_type: str) -> bool:
        """检查客户端类型是否有效"""
        return client_type in cls.ALL

    @classmethod
    def get_default_for_purpose(cls, purpose: str) -> str:
        """根据用途获取推荐的客户端类型"""
        purpose_map = {
            "chat": cls.LLM,
            "fast": cls.LLM_FAST,
            "vision": cls.VLM,
            "local": cls.LLM_LOCAL,
            "default": cls.LLM,
        }
        return purpose_map.get(purpose.lower(), cls.LLM)


class LLMManager:
    """
    LLM 管理器 - 多客户端架构

    核心基础设施服务，与 EventBus 同级。

    职责：
    - 管理多个 LLM 客户端类型（llm, llm_fast, vlm 等）
    - 每个客户端类型可独立配置不同的后端（OpenAI、Ollama 等）
    - 提供统一的调用接口
    - 内置重试、超时、降级机制
    - Token 使用量统计

    使用示例：
        ```python
        # 在 main.py 中初始化
        llm_manager = LLMManager()
        await llm_manager.setup(config)

        # 使用标准 LLM 客户端
        response = await llm_manager.chat("你好")

        # 使用快速 LLM 客户端
        response = await llm_manager.chat_fast("解析意图")

        # 使用视觉语言模型
        response = await llm_manager.chat_vision("描述图片", images=["path/to/image.jpg"])

        # 使用 messages 格式
        response = await llm_manager.chat_messages(
            [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": "Hello!"}],
            client_type="llm_fast",
        )
        ```
    """

    # 客户端类型到默认后端类型的映射
    DEFAULT_BACKENDS = {
        ClientType.LLM: "openai",
        ClientType.LLM_FAST: "openai",
        ClientType.VLM: "openai",
        ClientType.LLM_LOCAL: "ollama",
    }

    def __init__(self):
        self.logger = get_logger("LLMManager")
        self._clients: Dict[str, Any] = {}  # client_type -> client_instance
        self._client_configs: Dict[str, Dict[str, Any]] = {}  # client_type -> config
        self._config: Dict[str, Any] = {}
        self._token_manager = None
        self._retry_config = RetryConfig()

    async def setup(self, config: Dict[str, Any]) -> None:
        """
        从配置初始化所有 LLM 客户端

        Args:
            config: 完整配置字典，包含 [llm], [llm_fast], [vlm], [llm_local] 等部分

        配置示例：
            ```toml
            [llm]
            client = "openai"
            model = "gpt-4"
            api_key = "your-api-key"

            [llm_fast]
            client = "openai"
            model = "gpt-3.5-turbo"
            api_key = "your-api-key"

            [vlm]
            client = "openai"
            model = "gpt-4-vision-preview"
            api_key = "your-api-key"
            ```
        """
        self._config = config

        # 初始化配置中定义的每个客户端
        for client_type in ClientType.ALL:
            if client_type in config:
                await self._init_client(client_type, config[client_type])

        # 确保至少有一个默认客户端
        if ClientType.DEFAULT not in self._clients:
            self.logger.warning(f"未配置默认客户端 '{ClientType.DEFAULT}'，将使用默认配置初始化")
            await self._init_client(ClientType.DEFAULT, self._get_default_config(ClientType.DEFAULT))

        # 初始化 token 管理器
        from src.modules.llm.clients.token_usage_manager import TokenUsageManager

        self._token_manager = TokenUsageManager(use_global=True)

        self.logger.info(f"LLMManager 初始化完成，已配置客户端: {list(self._clients.keys())}")

    async def _init_client(self, client_type: str, client_config: Dict[str, Any]) -> None:
        """
        初始化单个客户端

        Args:
            client_type: 客户端类型 (llm, llm_fast, vlm, llm_local)
            client_config: 客户端配置
        """
        # 获取客户端类型，默认从配置或使用默认值
        client_impl = client_config.get("client", self.DEFAULT_BACKENDS.get(client_type, "openai"))

        # 动态导入客户端实现
        client_class = self._get_client_class(client_impl)

        # 应用环境变量覆盖（API Key 等）
        enriched_config = self._enrich_config_with_env(client_config, client_impl)

        # 创建客户端实例
        self._clients[client_type] = client_class(enriched_config)
        self._client_configs[client_type] = enriched_config

        self.logger.info(
            f"已初始化客户端 '{client_type}' (客户端: {client_impl}, 模型: {enriched_config.get('model', 'N/A')})"
        )

    def _get_client_class(self, client_impl: str) -> type:
        """
        动态导入客户端类

        Args:
            client_impl: 客户端实现类型 (openai, ollama, anthropic)

        Returns:
            客户端类

        Raises:
            ValueError: 如果客户端类型不支持
        """
        if client_impl == "openai":
            from src.modules.llm.clients.openai_client import OpenAIClient

            return OpenAIClient
        elif client_impl == "ollama":
            from src.modules.llm.clients.ollama_client import OllamaClient

            return OllamaClient
        elif client_impl == "anthropic":
            # 未来可以添加 Anthropic 客户端
            from src.modules.llm.clients.openai_client import OpenAIClient

            self.logger.warning("Anthropic 客户端暂未实现，降级到 OpenAI 客户端")
            return OpenAIClient
        else:
            self.logger.warning(f"未知的客户端类型 '{client_impl}'，降级到 OpenAI 客户端")
            from src.modules.llm.clients.openai_client import OpenAIClient

            return OpenAIClient

    def _enrich_config_with_env(self, config: Dict[str, Any], client_impl: str) -> Dict[str, Any]:
        """
        使用环境变量丰富配置

        优先级：环境变量 > 配置文件 > 默认值

        Args:
            config: 原始配置
            client_impl: 客户端实现类型

        Returns:
            丰富后的配置
        """
        enriched = config.copy()

        # API Key 环境变量映射
        env_key_map = {
            "openai": "OPENAI_API_KEY",
            "ollama": "OLLAMA_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }

        env_key = env_key_map.get(client_impl)
        if env_key and not enriched.get("api_key"):
            enriched["api_key"] = os.environ.get(env_key, "sk-dummy")

        # Base URL 环境变量
        if not enriched.get("base_url") and client_impl == "openai":
            enriched["base_url"] = os.environ.get("OPENAI_BASE_URL")

        # Ollama 特殊处理
        if client_impl == "ollama" and not enriched.get("base_url"):
            enriched["base_url"] = os.environ.get(
                "OLLAMA_BASE_URL", enriched.get("api_base", "http://localhost:11434/v1")
            )

        return enriched

    def _get_default_config(self, client_type: str) -> Dict[str, Any]:
        """
        获取客户端类型的默认配置

        Args:
            client_type: 客户端类型

        Returns:
            默认配置字典
        """
        defaults = {
            ClientType.LLM: {
                "client": "openai",
                "model": "gpt-4o-mini",
                "temperature": 0.2,
                "max_tokens": 1024,
            },
            ClientType.LLM_FAST: {
                "client": "openai",
                "model": "gpt-3.5-turbo",
                "temperature": 0.2,
                "max_tokens": 512,
            },
            ClientType.VLM: {
                "client": "openai",
                "model": "gpt-4-vision-preview",
                "temperature": 0.3,
                "max_tokens": 1024,
            },
            ClientType.LLM_LOCAL: {
                "client": "ollama",
                "model": "llama3",
                "base_url": "http://localhost:11434/v1",
            },
        }
        return defaults.get(client_type, {})

    # === 主要调用接口 ===

    async def chat(
        self,
        prompt: str,
        *,
        client_type: str = None,
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        聊天调用（使用 prompt 字符串）

        Args:
            prompt: 用户输入
            client_type: 使用的客户端类型（llm, llm_fast, vlm），默认为 llm
            system_message: 系统消息
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            LLMResponse: 响应结果
        """
        if client_type is None:
            client_type = ClientType.DEFAULT

        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            client_type,
            "chat",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def chat_fast(
        self,
        prompt: str,
        *,
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        快速聊天调用（使用 llm_fast 客户端）

        Args:
            prompt: 用户输入
            system_message: 系统消息
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            LLMResponse: 响应结果
        """
        return await self.chat(
            prompt,
            client_type=ClientType.LLM_FAST,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def chat_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        client_type: str = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """
        聊天调用（使用 messages 列表）

        Args:
            messages: 消息列表（OpenAI 格式）
            client_type: 使用的客户端类型（llm, llm_fast, vlm），默认为 llm
            temperature: 温度参数
            max_tokens: 最大 token 数
            tools: 工具定义（OpenAI 格式）

        Returns:
            LLMResponse: 响应结果
        """
        if client_type is None:
            client_type = ClientType.DEFAULT

        return await self._call_with_retry(
            client_type,
            "chat",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )

    async def stream_chat(
        self,
        prompt: str,
        *,
        client_type: str = None,
        system_message: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """
        流式聊天调用

        Args:
            prompt: 用户输入
            client_type: 使用的客户端类型（llm, llm_fast, vlm），默认为 llm
            system_message: 系统消息
            stop_event: 停止事件（用于中断流式输出）

        Yields:
            str: 增量文本内容
        """
        if client_type is None:
            client_type = ClientType.DEFAULT

        llm_client = self._get_client(client_type)
        messages = self._build_messages(prompt, system_message)

        async for chunk in llm_client.stream_chat(
            messages=messages,
            stop_event=stop_event,
        ):
            yield chunk

    async def chat_vision(
        self,
        prompt: str,
        images: List[Any],
        *,
        client_type: str = None,
        system_message: Optional[str] = None,
    ) -> LLMResponse:
        """
        视觉理解调用

        Args:
            prompt: 用户输入
            images: 图片列表（URL、路径或字节）
            client_type: 使用的客户端类型，默认为 vlm
            system_message: 系统消息

        Returns:
            LLMResponse: 响应结果
        """
        if client_type is None:
            client_type = ClientType.VLM

        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            client_type,
            "vision",
            messages=messages,
            images=images,
        )

    async def call_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        *,
        client_type: str = None,
        system_message: Optional[str] = None,
    ) -> LLMResponse:
        """
        工具调用

        Args:
            prompt: 用户输入
            tools: 工具定义列表（OpenAI 格式）
            client_type: 使用的客户端类型，默认为 llm
            system_message: 系统消息

        Returns:
            LLMResponse: 包含 tool_calls 的响应结果
        """
        if client_type is None:
            client_type = ClientType.DEFAULT

        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            client_type,
            "chat",
            messages=messages,
            tools=tools,
        )

    # === 便捷方法 ===

    async def simple_chat(
        self,
        prompt: str,
        *,
        client_type: str = None,
        system_message: Optional[str] = None,
    ) -> str:
        """
        简化聊天，直接返回文本

        Args:
            prompt: 用户输入
            client_type: 使用的客户端类型
            system_message: 系统消息

        Returns:
            str: 响应文本，失败时返回错误信息
        """
        result = await self.chat(prompt, client_type=client_type, system_message=system_message)
        return result.content if result.success and result.content else f"错误: {result.error}"

    async def simple_vision(
        self,
        prompt: str,
        images: List[Any],
        *,
        client_type: str = None,
    ) -> str:
        """
        简化视觉理解，直接返回文本

        Args:
            prompt: 用户输入
            images: 图片列表
            client_type: 使用的客户端类型

        Returns:
            str: 响应文本
        """
        result = await self.chat_vision(prompt, images, client_type=client_type)
        return result.content if result.success and result.content else f"错误: {result.error}"

    # === 客户端获取 ===

    def get_client(self, client_type: str = None):
        """
        获取指定类型的客户端实例

        Args:
            client_type: 客户端类型，默认为 DEFAULT

        Returns:
            后端客户端实例

        Raises:
            ValueError: 如果客户端未配置
        """
        if client_type is None:
            client_type = ClientType.DEFAULT
        return self._get_client(client_type)

    def has_client(self, client_type: str) -> bool:
        """
        检查指定客户端类型是否已配置

        Args:
            client_type: 客户端类型

        Returns:
            bool: 是否已配置
        """
        return client_type in self._clients

    def list_clients(self) -> List[str]:
        """
        列出所有已配置的客户端类型

        Returns:
            客户端类型列表
        """
        return list(self._clients.keys())

    def get_client_config(self, client_type: str) -> Optional[Dict[str, Any]]:
        """
        获取指定客户端类型的配置

        Args:
            client_type: 客户端类型

        Returns:
            配置字典，如果未配置则返回 None
        """
        return self._client_configs.get(client_type)

    # === 内部方法 ===

    def _get_client(self, client_type: str) -> Any:
        """
        获取指定客户端

        Args:
            client_type: 客户端类型

        Returns:
            后端实例

        Raises:
            ValueError: 如果客户端未配置
        """
        if client_type not in self._clients:
            raise ValueError(f"LLM 客户端 '{client_type}' 未配置。已配置的客户端: {list(self._clients.keys())}")
        return self._clients[client_type]

    def _build_messages(
        self,
        prompt: str,
        system_message: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        构建消息列表

        Args:
            prompt: 用户输入
            system_message: 系统消息

        Returns:
            消息列表
        """
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _call_with_retry(
        self,
        client_type: str,
        method: str,
        **kwargs,
    ) -> LLMResponse:
        """
        带重试的调用

        Args:
            client_type: 客户端类型
            method: 调用方法名
            **kwargs: 方法参数

        Returns:
            LLMResponse: 响应结果
        """
        llm_client = self._get_client(client_type)
        client_config = self._client_configs.get(client_type, {})

        # 获取重试配置
        max_retries = client_config.get("max_retries", self._retry_config.max_retries)
        base_delay = client_config.get("retry_delay", self._retry_config.base_delay)
        max_delay = self._retry_config.max_delay

        last_error = None

        for attempt in range(max_retries):
            try:
                method_func = getattr(llm_client, method)
                result = await method_func(**kwargs)

                # 记录 token 使用量
                if result.success and result.usage and self._token_manager:
                    self._token_manager.record_usage(
                        model_name=result.model or client_type,
                        prompt_tokens=result.usage.get("prompt_tokens", 0),
                        completion_tokens=result.usage.get("completion_tokens", 0),
                        total_tokens=result.usage.get("total_tokens", 0),
                    )

                return result

            except Exception as e:
                last_error = e
                self.logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{max_retries}, 客户端: {client_type}): {e}")
                if attempt < max_retries - 1:
                    delay = min(base_delay * (2**attempt), max_delay)
                    await asyncio.sleep(delay)

        # 所有重试失败
        self.logger.error(f"所有 LLM 调用重试失败 (客户端: {client_type}): {last_error}")
        return LLMResponse(success=False, content=None, error=str(last_error))

    # === 生命周期 ===

    async def cleanup(self) -> None:
        """清理所有客户端资源"""
        for name, client in self._clients.items():
            try:
                await client.cleanup()
                self.logger.debug(f"已清理 {name} 客户端")
            except Exception as e:
                self.logger.warning(f"清理 {name} 客户端失败: {e}")
        self._clients.clear()
        self._client_configs.clear()

    # === 统计信息 ===

    def get_token_usage_summary(self) -> str:
        """
        获取 token 使用量摘要

        Returns:
            token 使用量摘要字符串
        """
        if self._token_manager:
            return self._token_manager.format_total_cost_summary()
        return "Token 管理器未初始化"

    def get_client_info(self) -> Dict[str, Any]:
        """
        获取所有客户端信息

        Returns:
            客户端信息字典
        """
        return {
            name: {
                "client": client.__class__.__name__,
                "config": self._client_configs.get(name, {}),
            }
            for name, client in self._clients.items()
        }
