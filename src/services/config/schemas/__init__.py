"""
配置管理模块

此模块提供统一的配置管理功能，包括:
- 配置文件加载和合并
- 从 Pydantic Schemas 自动生成配置文件
- 配置版本检查和更新

主要子模块:
- generator: 从 Pydantic Schemas 生成 TOML 配置文件
- schemas: Pydantic Schema 定义和注册表
"""

from src.services.config.schemas.generator import (
    generate_toml,
    ensure_provider_config,
    generate_config_dict,
    merge_config_with_tomli_w,
    batch_ensure_provider_configs,
)

# 从 schemas 子模块导入 Schema 相关内容
from src.services.config.schemas.schemas import (
    BaseProviderConfig,
    PROVIDER_SCHEMA_REGISTRY,
    get_provider_schema,
    validate_provider_config,
    list_all_providers,
    verify_no_enabled_field_in_schemas,
    get_output_provider_config,
    OUTPUT_PROVIDER_CONFIG_MAP,
    # Input provider configs（已迁移的自管理 Schema 不再导出）
    # ConsoleInputProviderConfig,  # 已迁移到自管理 Schema
    BiliDanmakuProviderConfig,
    BiliDanmakuOfficialProviderConfig,
    BiliDanmakuOfficialMaiCraftProviderConfig,
    ReadPingmuProviderConfig,
    MainosabaProviderConfig,
    # Decision provider configs
    MaiCoreDecisionProviderConfig,
    LocalLLMDecisionProviderConfig,
    RuleEngineDecisionProviderConfig,
    MockDecisionProviderConfig,
    # Output provider configs（已迁移的自管理 Schema 不再导出）
    # SubtitleProviderConfig,  # 已迁移到自管理 Schema
    # VTSProviderConfig,  # 已迁移到自管理 Schema
    # TTSProviderConfig,  # 已迁移到自管理 Schema
    StickerProviderConfig,
    WarudoProviderConfig,
    ObsControlProviderConfig,
    GPTSoVITSProviderConfig,
    OmniTTSProviderConfig,
    AvatarProviderConfig,
    RemoteStreamOutputProviderConfig,
    # Non-provider configs (system-wide)
    LoggingConfig,
)

__all__ = [
    # Generator 函数
    "generate_toml",
    "ensure_provider_config",
    "generate_config_dict",
    "merge_config_with_tomli_w",
    "batch_ensure_provider_configs",
    # Schema 相关
    "BaseProviderConfig",
    "PROVIDER_SCHEMA_REGISTRY",
    "get_provider_schema",
    "validate_provider_config",
    "list_all_providers",
    "verify_no_enabled_field_in_schemas",
    "get_output_provider_config",
    "OUTPUT_PROVIDER_CONFIG_MAP",
    # Input provider configs（已迁移的自管理 Schema 不再导出）
    # "ConsoleInputProviderConfig",  # 已迁移到自管理 Schema
    "BiliDanmakuProviderConfig",
    "BiliDanmakuOfficialProviderConfig",
    "BiliDanmakuOfficialMaiCraftProviderConfig",
    "ReadPingmuProviderConfig",
    "MainosabaProviderConfig",
    # Decision provider configs
    "MaiCoreDecisionProviderConfig",
    "LocalLLMDecisionProviderConfig",
    "RuleEngineDecisionProviderConfig",
    "MockDecisionProviderConfig",
    # Output provider configs（已迁移的自管理 Schema 不再导出）
    # "SubtitleProviderConfig",  # 已迁移到自管理 Schema
    # "VTSProviderConfig",  # 已迁移到自管理 Schema
    # "TTSProviderConfig",  # 已迁移到自管理 Schema
    "StickerProviderConfig",
    "WarudoProviderConfig",
    "ObsControlProviderConfig",
    "GPTSoVITSProviderConfig",
    "OmniTTSProviderConfig",
    "AvatarProviderConfig",
    "RemoteStreamOutputProviderConfig",
    # Non-provider configs (system-wide)
    "LoggingConfig",
]
