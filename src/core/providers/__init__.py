"""
Provider接口模块

导出了新架构中的所有Provider接口和数据类。
"""

from .base import RenderParameters
from .input_provider import InputProvider
from .output_provider import OutputProvider
from .decision_provider import DecisionProvider
from .maicore_decision_provider import MaiCoreDecisionProvider
from .local_llm_decision_provider import LocalLLMDecisionProvider
from .rule_engine_decision_provider import RuleEngineDecisionProvider

# RawData 和 NormalizedText 从 data_types 导入，避免重复定义
from src.data_types.raw_data import RawData
from src.data_types.normalized_text import NormalizedText

# CanonicalMessage 从 canonical 模块导入
from src.layers.canonical.canonical_message import CanonicalMessage

__all__ = [
    # 数据类（从data_types重新导出）
    "RawData",
    "NormalizedText",
    "RenderParameters",
    "CanonicalMessage",
    # Provider接口
    "InputProvider",
    "OutputProvider",
    "DecisionProvider",
    # Provider实现
    "MaiCoreDecisionProvider",
    "LocalLLMDecisionProvider",
    "RuleEngineDecisionProvider",
]
