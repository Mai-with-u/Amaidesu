"""模型配置 Schema 定义

定义所有 LLM/VLM 模型配置的 Pydantic Schema。
这些配置拆分到 config/model.toml 文件中。
"""

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class LLMConfig(BaseConfig):
    """标准 LLM 配置（用于高质量任务）"""

    type: str = Field(default="llm", description="配置类型标识")
    client: str = Field(default="openai", description="客户端类型（openai 兼容所有 OpenAI API 服务）")
    model: str = Field(default="gpt-4", description="模型名称")
    temperature: float = Field(default=0.2, description="生成温度 (0.0-2.0)")
    api_key: str = Field(default="", description="API 密钥（留空则使用环境变量）")
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="API 端点（可自定义）",
    )
    max_tokens: int = Field(default=1024, description="生成的最大 Token 数")
    max_retries: int = Field(default=3, description="请求失败时的最大重试次数")
    retry_delay: float = Field(default=1.0, description="重试间隔时间（秒）")


class FastLLMConfig(BaseConfig):
    """快速 LLM 配置（用于低延迟任务，如 Avatar 表情分析）"""

    type: str = Field(default="llm_fast", description="配置类型标识")
    client: str = Field(default="openai", description="客户端类型")
    model: str = Field(default="gpt-3.5-turbo", description="模型名称")
    temperature: float = Field(default=0.2, description="生成温度")
    api_key: str = Field(default="", description="API 密钥（留空则使用环境变量）")
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="API 端点",
    )
    max_tokens: int = Field(default=1024, description="最大 Token 数")


class VLMConfig(BaseConfig):
    """视觉语言模型配置（用于图像理解任务）"""

    type: str = Field(default="vlm", description="配置类型标识")
    client: str = Field(default="openai", description="客户端类型")
    model: str = Field(default="gpt-4-vision-preview", description="视觉模型名称")
    temperature: float = Field(default=0.3, description="生成温度")
    api_key: str = Field(default="", description="API 密钥（留空则使用环境变量）")
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="API 端点",
    )
    max_tokens: int = Field(default=1024, description="最大 Token 数")


class LocalLLMConfig(BaseConfig):
    """本地模型配置（Ollama / LM Studio / vLLM 等）

    这些服务提供 OpenAI 兼容 API，
    使用 client = "openai" 并配置 base_url 即可。
    """

    type: str = Field(default="llm_local", description="配置类型标识")
    client: str = Field(default="openai", description="客户端类型")
    model: str = Field(default="llama3", description="本地模型名称")
    base_url: str = Field(
        default="http://localhost:11434/v1",
        description="本地 API 端点（Ollama 默认地址）",
    )
    api_key: str = Field(default="sk-dummy", description="API 密钥（本地服务通常不需要真实密钥）")


class ModelConfig(BaseConfig):
    """模型配置根类

    聚合所有 LLM/VLM 模型配置。
    对应 config/model.toml 文件。
    """

    type: str = Field(default="model", description="配置类型标识")
    llm: LLMConfig = Field(default_factory=LLMConfig, description="标准 LLM 配置")
    llm_fast: FastLLMConfig = Field(default_factory=FastLLMConfig, description="快速 LLM 配置")
    vlm: VLMConfig = Field(default_factory=VLMConfig, description="视觉语言模型配置")
    llm_local: LocalLLMConfig = Field(default_factory=LocalLLMConfig, description="本地模型配置")
