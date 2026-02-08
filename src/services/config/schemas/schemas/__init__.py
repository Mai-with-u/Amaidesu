"""Pydantic schemas for configuration validation."""

from typing import Dict, Type
from pydantic import BaseModel

# Base schema
from .base import BaseProviderConfig

# Input provider schemas
from .input_providers import (
    ConsoleInputProviderConfig,
    BiliDanmakuProviderConfig,
    BiliDanmakuOfficialProviderConfig,
    BiliDanmakuOfficialMaiCraftProviderConfig,
    MockDanmakuProviderConfig,
    ReadPingmuProviderConfig,
    MainosabaProviderConfig,
)

# Decision provider schemas
from .decision_providers import (
    MaiCoreDecisionProviderConfig,
    LocalLLMDecisionProviderConfig,
    RuleEngineDecisionProviderConfig,
    MockDecisionProviderConfig,
)

# Output provider schemas
from .output_providers import (
    SubtitleProviderConfig,
    VTSProviderConfig,
    TTSProviderConfig,
    StickerProviderConfig,
    WarudoProviderConfig,
    ObsControlProviderConfig,
    GPTSoVITSProviderConfig,
    OmniTTSProviderConfig,
    AvatarProviderConfig,
    RemoteStreamOutputProviderConfig,
    OUTPUT_PROVIDER_CONFIG_MAP,
    get_output_provider_config,
)

# LLM client schemas
from .llm_providers import (
    LLMClientConfig,
    LLMConfig,
    LLMFastConfig,
    VLMConfig,
    LLMLocalConfig,
)

# Non-provider schemas (system-wide configurations)
from .logging import LoggingConfig

# Provider Schema Registry
# Maps provider type to their config schema class
PROVIDER_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    # Input providers (7)
    "console_input": ConsoleInputProviderConfig,
    "bili_danmaku": BiliDanmakuProviderConfig,
    "bili_danmaku_official": BiliDanmakuOfficialProviderConfig,
    "bili_danmaku_official_maicraft": BiliDanmakuOfficialMaiCraftProviderConfig,
    "mock_danmaku": MockDanmakuProviderConfig,
    "read_pingmu": ReadPingmuProviderConfig,
    "mainosaba": MainosabaProviderConfig,
    # Decision providers (4)
    "maicore": MaiCoreDecisionProviderConfig,
    "local_llm": LocalLLMDecisionProviderConfig,
    "rule_engine": RuleEngineDecisionProviderConfig,
    "mock": MockDecisionProviderConfig,
    # Output providers (10)
    "subtitle": SubtitleProviderConfig,
    "vts": VTSProviderConfig,
    "tts": TTSProviderConfig,
    "sticker": StickerProviderConfig,
    "warudo": WarudoProviderConfig,
    "obs_control": ObsControlProviderConfig,
    "gptsovits": GPTSoVITSProviderConfig,
    "omni_tts": OmniTTSProviderConfig,
    "avatar": AvatarProviderConfig,
    "remote_stream": RemoteStreamOutputProviderConfig,
}


# Helper functions
def get_provider_schema(provider_type: str, provider_layer: str = None) -> Type[BaseModel]:
    """
    获取Provider的Schema类

    Args:
        provider_type: Provider类型标识
        provider_layer: Provider层级（可选，用于验证和日志）

    Returns:
        对应的Schema类

    Raises:
        KeyError: 如果provider_type未注册

    Note:
        provider_layer参数仅用于向后兼容和验证，实际Schema查找使用全局注册表。
    """
    if provider_type not in PROVIDER_SCHEMA_REGISTRY:
        raise KeyError(f"未注册的Provider类型: {provider_type} (layer: {provider_layer})")
    return PROVIDER_SCHEMA_REGISTRY[provider_type]


def validate_provider_config(provider_type: str, config: dict) -> BaseModel:
    """
    验证Provider配置

    Args:
        provider_type: Provider类型标识
        config: 配置字典

    Returns:
        验证后的配置对象

    Raises:
        KeyError: 如果provider_type未注册
        ValidationError: 如果配置验证失败
    """
    schema_class = get_provider_schema(provider_type)
    return schema_class(**config)


def list_all_providers() -> Dict[str, list]:
    """
    列出所有已注册的Provider

    Returns:
        字典，包含input、decision、output三个分类的Provider列表
    """
    input_providers = [
        pt
        for pt in PROVIDER_SCHEMA_REGISTRY.keys()
        if pt
        in [
            "console_input",
            "bili_danmaku",
            "bili_danmaku_official",
            "bili_danmaku_official_maicraft",
            "mock_danmaku",
            "read_pingmu",
            "mainosaba",
        ]
    ]

    decision_providers = [
        pt
        for pt in PROVIDER_SCHEMA_REGISTRY.keys()
        if pt in ["maicore", "local_llm", "rule_engine", "mock"]
    ]

    output_providers = [pt for pt in PROVIDER_SCHEMA_REGISTRY.keys() if pt not in input_providers + decision_providers]

    return {
        "input": input_providers,
        "decision": decision_providers,
        "output": output_providers,
        "total": len(PROVIDER_SCHEMA_REGISTRY),
    }


def verify_no_enabled_field_in_schemas() -> list[str]:
    """
    扫描所有Schema，检测是否包含'enabled'字段

    Returns:
        包含'enabled'字段的Schema类名列表（应为空列表）

    注意:
        这是架构要求的强制性检查。Provider的enabled状态由Manager统一管理，
        Schema中不应包含enabled字段。
    """
    schemas_with_enabled = []

    for provider_type, schema_class in PROVIDER_SCHEMA_REGISTRY.items():
        if hasattr(schema_class, "model_fields"):
            # Pydantic v2
            if "enabled" in schema_class.model_fields:
                schemas_with_enabled.append(f"{provider_type}: {schema_class.__name__}")
        elif hasattr(schema_class, "__fields__"):
            # Pydantic v1
            if "enabled" in schema_class.__fields__:
                schemas_with_enabled.append(f"{provider_type}: {schema_class.__name__}")

    return schemas_with_enabled


__all__ = [
    # Base types
    "BaseProviderConfig",
    # Input provider configs
    "ConsoleInputProviderConfig",
    "BiliDanmakuProviderConfig",
    "BiliDanmakuOfficialProviderConfig",
    "BiliDanmakuOfficialMaiCraftProviderConfig",
    "MockDanmakuProviderConfig",
    "ReadPingmuProviderConfig",
    "MainosabaProviderConfig",
    # Decision provider configs
    "MaiCoreDecisionProviderConfig",
    "LocalLLMDecisionProviderConfig",
    "RuleEngineDecisionProviderConfig",
    "MockDecisionProviderConfig",
    # Output provider configs
    "SubtitleProviderConfig",
    "VTSProviderConfig",
    "TTSProviderConfig",
    "StickerProviderConfig",
    "WarudoProviderConfig",
    "ObsControlProviderConfig",
    "GPTSoVITSProviderConfig",
    "OmniTTSProviderConfig",
    "AvatarProviderConfig",
    "RemoteStreamOutputProviderConfig",
    # LLM client configs
    "LLMClientConfig",
    "LLMConfig",
    "LLMFastConfig",
    "VLMConfig",
    "LLMLocalConfig",
    # Non-provider configs (system-wide)
    "LoggingConfig",
    # Registry
    "PROVIDER_SCHEMA_REGISTRY",
    "OUTPUT_PROVIDER_CONFIG_MAP",
    # Helper functions
    "get_provider_schema",
    "validate_provider_config",
    "list_all_providers",
    "verify_no_enabled_field_in_schemas",
    "get_output_provider_config",
]
