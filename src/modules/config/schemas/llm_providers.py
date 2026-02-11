"""LLM客户端配置Schema定义

定义LLM多客户端架构的配置Schema：
- llm: 通用LLM客户端（高质量任务）
- llm_fast: 快速LLM客户端（低延迟任务）
- vlm: 视觉语言模型客户端（图像理解）
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class LLMClientConfig(BaseModel):
    """LLM客户端配置基类

    定义单个LLM客户端的通用配置参数。

    Attributes:
        backend: 后端类型 (openai - 支持 OpenAI、Ollama、LM Studio、vLLAM 等所有兼容 API)
        model: 模型名称
        api_key: API密钥（可选，优先使用环境变量）
        base_url: API基础URL（可选）
        temperature: 默认温度参数
        max_tokens: 默认最大token数
        max_retries: 最大重试次数
        retry_delay: 重试延迟（秒）
    """

    backend: Literal["openai", "anthropic"] = Field(default="openai", description="LLM后端类型")
    model: str = Field(default="gpt-4o-mini", description="模型名称")
    api_key: Optional[str] = Field(default=None, description="API密钥（可选，优先使用环境变量）")
    base_url: Optional[str] = Field(default=None, description="API基础URL（可选，用于自定义端点）")
    temperature: float = Field(default=0.2, description="默认温度参数", ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, description="默认最大token数", ge=1)
    max_retries: int = Field(default=3, description="最大重试次数", ge=0)
    retry_delay: float = Field(default=1.0, description="重试延迟（秒）", ge=0.0)

    model_config = {"extra": "ignore"}


class LLMConfig(LLMClientConfig):
    """标准LLM客户端配置

    用于高质量任务的LLM配置，如复杂对话、深度分析等。

    Attributes:
        type: 客户端类型标识，固定为"llm"
    """

    type: Literal["llm"] = "llm"

    model_config = {"extra": "ignore"}


class LLMFastConfig(LLMClientConfig):
    """快速LLM客户端配置

    用于低延迟任务的LLM配置，如意图解析、快速响应等。

    Attributes:
        type: 客户端类型标识，固定为"llm_fast"
    """

    type: Literal["llm_fast"] = "llm_fast"

    # 快速客户端默认使用更小的模型和更少的token
    model: str = Field(default="gpt-3.5-turbo", description="模型名称（快速响应）")
    max_tokens: int = Field(default=512, description="默认最大token数（快速响应）", ge=1)
    temperature: float = Field(default=0.2, description="默认温度参数（快速响应）", ge=0.0, le=2.0)

    model_config = {"extra": "ignore"}


class VLMConfig(LLMClientConfig):
    """视觉语言模型客户端配置

    用于图像理解任务的VLM配置。

    Attributes:
        type: 客户端类型标识，固定为"vlm"
    """

    type: Literal["vlm"] = "vlm"

    # VLM 默认配置
    model: str = Field(default="gpt-4-vision-preview", description="视觉模型名称")
    temperature: float = Field(default=0.3, description="默认温度参数（视觉理解）", ge=0.0, le=2.0)

    model_config = {"extra": "ignore"}


class LLMLocalConfig(LLMClientConfig):
    """本地LLM客户端配置

    用于本地 Ollama、LM Studio、vLLAM 等提供 OpenAI 兼容 API 的本地模型配置。
    只需配置 base_url 指向本地服务即可。

    Attributes:
        type: 客户端类型标识，固定为"llm_local"
    """

    type: Literal["llm_local"] = "llm_local"
    backend: Literal["openai"] = Field(default="openai", description="使用 OpenAI 兼容 API")
    model: str = Field(default="llama3", description="本地模型名称")
    base_url: Optional[str] = Field(default="http://localhost:11434/v1", description="本地API地址")

    model_config = {"extra": "ignore"}


# 导出所有Schema
__all__ = [
    "LLMClientConfig",
    "LLMConfig",
    "LLMFastConfig",
    "VLMConfig",
    "LLMLocalConfig",
]
