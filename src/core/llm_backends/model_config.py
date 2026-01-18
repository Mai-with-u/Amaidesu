"""
LLM 模型配置类

用于配置单个 LLM 后端的参数
"""

from dataclasses import dataclass


@dataclass
class ModelConfig:
    """LLM 模型配置"""

    model_name: str
    api_key: str
    base_url: str
    max_tokens: int = 1024
    temperature: float = 0.2
