"""模型配置 Schema 定义（三层结构）

定义 ``config/model.toml`` 的 Pydantic 聚合模型，采用三层结构：

- ``[[llm_providers]]``：API 提供商（连接信息 / 鉴权 / 重试参数）
- ``[[llm_models]]``：模型注册表（model_identifier / api_provider / 价格）
- ``[llm_profiles.<name>]``：用途 profile（planner / replyer / summary /
  minecraft / minecraft_builder / vision / simulator）；引用 model_list，由 LLMManager 做选择
  与故障切换

判据（谁改它的结构）：
- provider / model 字段 = 平台 / API 提供商侧调整 → 改 providers/models 段
- profile 字段 = 应用侧角色调整 → 改 llm_profiles 段

价格合流：价格唯一来源 = 本表 ``price_in`` / ``price_out`` / ``cache_price_in``，
费用计算（``observation.calculate_cost``）按模型标识从引擎装配的价格表取价。
"""

from __future__ import annotations

from typing import Any, List

from pydantic import Field, model_validator

from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig


class LLMProviderConfig(BaseConfig):
    """API 提供商配置（``[[llm_providers]]`` 表条目）

    一个 provider 描述一个 API 端点（OpenAI 兼容）的连接细节，
    可被多个 model 与 profile 共享。

    Attributes:
        name: provider 唯一名称，供 model.api_provider 与 profile.provider 引用
        client_type: 客户端实现标识（如 "openai"）
        base_url: API 端点
        api_key: API 密钥
        auth_type: 鉴权方式（bearer/header/query/none）
        auth_header_name: header 鉴权时的 header 名
        auth_header_prefix: header 鉴权时值的前缀
        auth_query_name: query 鉴权时的 query 参数名
        default_headers: 每次请求附加的默认 header
        timeout: 单次请求超时时间（秒）
        max_retries: 请求失败时的最大重试次数
        retry_delay: 重试间隔（秒）
        reasoning_parse_mode: 推理内容解析模式（auto/native/think_tag/none）
    """

    name: str = Field(default="default", description="provider 唯一名称（被 model.api_provider 引用）")
    client_type: str = Field(
        default="openai",
        description="客户端实现标识（如 openai）",
        json_schema_extra={"x-ui-type": "select", "x-options": ["openai"]},
    )
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="API 端点（可自定义为任何 OpenAI 兼容服务）",
    )
    api_key: str = Field(
        default="",
        description="API 密钥（留空时客户端以占位符 sk-dummy 兜底，需填真实 Key 才能调用）",
    )
    auth_type: str = Field(
        default="bearer",
        description="鉴权方式：bearer / header / query / none",
        json_schema_extra={"x-ui-type": "select", "x-options": ["bearer", "header", "query", "none"]},
    )
    # auth_* 三兄弟只在非默认鉴权方式下才有意义，标 x-ui-advanced 让 WebUI 收进折叠区
    auth_header_name: str = Field(
        default="Authorization",
        description="header 鉴权时的 header 名",
        json_schema_extra={"x-ui-advanced": True},
    )
    auth_header_prefix: str = Field(
        default="Bearer",
        description="header 鉴权时值的前缀",
        json_schema_extra={"x-ui-advanced": True},
    )
    auth_query_name: str = Field(
        default="api_key",
        description="query 鉴权时的 query 参数名",
        json_schema_extra={"x-ui-advanced": True},
    )
    default_headers: dict[str, str] = Field(default_factory=dict, description="每次请求附加的默认 header")
    timeout: int = Field(default=60, ge=1, description="单次请求超时时间（秒）")
    max_retries: int = Field(
        default=3, ge=0, description="请求失败时的最大重试次数", json_schema_extra={"x-ui-advanced": True}
    )
    retry_delay: float = Field(
        default=1.0, ge=0.0, description="重试间隔时间（秒）", json_schema_extra={"x-ui-advanced": True}
    )
    reasoning_parse_mode: str = Field(
        default="auto",
        description="推理内容解析模式：auto / native / think_tag / none",
        json_schema_extra={"x-ui-type": "select", "x-options": ["auto", "native", "think_tag", "none"]},
    )


class LLMModelConfig(BaseConfig):
    """模型注册表条目（``[[llm_models]]`` 表）

    Attributes:
        name: 模型唯一名（被 profile.model_list 引用）
        model_identifier: 实际 API 模型标识（如 "gpt-4o-mini"）
        api_provider: 关联的 provider 名（对应 llm_providers[].name）
        visual: 是否支持视觉（影响 chat_vision 是否装配）
        price_in: 输入 token 单价（per 1k，缺失时 cost 计为 0）
        price_out: 输出 token 单价（per 1k）
        cache: 缓存类型（"" 表示无缓存；"anthropic" 等按 provider 约定）
        cache_price_in: 缓存输入 token 单价（per 1k；cache 为空时忽略）
    """

    name: str = Field(default="default", description="模型唯一名（被 profile.model_list 引用）")
    model_identifier: str = Field(
        default="gpt-4o-mini",
        description="实际 API 模型标识（传给 OpenAI 兼容 endpoint 的 model 字段）",
    )
    api_provider: str = Field(
        default="default",
        description="关联的 provider 名（对应 llm_providers[].name）",
    )
    visual: bool = Field(default=False, description="是否支持视觉（影响 chat_vision 是否装配）")
    price_in: float = Field(default=0.0, ge=0.0, description="输入 token 单价（每百万 token）")
    price_out: float = Field(default=0.0, ge=0.0, description="输出 token 单价（每百万 token）")
    cache: str = Field(default="", description="缓存类型（空=无；'anthropic' 等按 provider 约定）")
    cache_price_in: float = Field(default=0.0, ge=0.0, description="缓存输入 token 单价（每百万 token）")


class LLMSelectionStrategy(BaseConfig):
    """profile 的 model_list 选择策略

    sequential：按顺序取第一个可用模型（默认）
    balance：按调用计数挑最闲的模型
    random：随机选一个
    """

    name: str = Field(default="sequential", description="选择策略名")
    seed: int = Field(default=0, description="random 策略的可选 seed（0=不固定）")


class LLMProfileConfig(BaseConfig):
    """LLM 用途 profile（``[llm_profiles.<name>]`` 段）

    Attributes:
        model_list: 引用的模型名列表（对应 llm_models[].name）
        selection_strategy: 选择策略
        hard_timeout_ms: 硬超时（毫秒）；到点取消当前请求并切下一个模型
        slow_threshold_ms: 慢调用阈值（毫秒）；超阈值仅告警，不切换
        temperature: 生成温度（0.0-2.0）
        max_tokens: 最大生成 token 数
    """

    model_list: List[str] = Field(
        default_factory=list,
        description="引用的模型名列表（对应 llm_models[].name，按顺序选择）",
    )
    selection_strategy: LLMSelectionStrategy = Field(
        default_factory=LLMSelectionStrategy,
        description="model_list 选择策略",
    )
    hard_timeout_ms: int = Field(
        default=180_000,
        ge=1000,
        description="硬超时（毫秒）；到点取消当前请求并切下一个模型",
    )
    slow_threshold_ms: int = Field(
        default=15_000,
        ge=100,
        description="慢调用阈值（毫秒）；超阈值仅告警，不切换",
    )
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="生成温度 (0.0-2.0)",
        json_schema_extra={"x-ui-type": "number"},
    )
    max_tokens: int = Field(
        default=4096,
        ge=1,
        description="最大生成 token 数",
        json_schema_extra={"x-ui-type": "integer"},
    )


_PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "planner": {"hard_timeout_ms": 90_000, "slow_threshold_ms": 15_000, "temperature": 0.7, "max_tokens": 4096},
    "replyer": {"hard_timeout_ms": 60_000, "slow_threshold_ms": 8_000, "temperature": 0.2, "max_tokens": 2048},
    "summary": {"hard_timeout_ms": 180_000, "slow_threshold_ms": 30_000, "temperature": 0.3, "max_tokens": 2048},
    "minecraft": {"hard_timeout_ms": 180_000, "slow_threshold_ms": 15_000, "temperature": 0.2, "max_tokens": 4096},
    "minecraft_builder": {
        "hard_timeout_ms": 180_000,
        "slow_threshold_ms": 15_000,
        "temperature": 0.5,
        "max_tokens": 8192,
    },
    "vision": {"hard_timeout_ms": 60_000, "slow_threshold_ms": 10_000, "temperature": 0.3, "max_tokens": 1024},
    "simulator": {"hard_timeout_ms": 60_000, "slow_threshold_ms": 15_000, "temperature": 0.9, "max_tokens": 1024},
}


def _seed_profile(name: str) -> LLMProfileConfig:
    """按用途档位构造 profile 缺省种子（缺省引用模型注册表的 default 条目）"""
    return LLMProfileConfig(model_list=["default"], **_PROFILE_PRESETS[name])


class LLMProfilesConfig(BaseConfig):
    """``[llm_profiles]`` 封闭用途集合

    用途集合是显式封闭的：成员由本模型的字段定义，``extra="forbid"``
    拒绝未知键（配置加载期硬错）；"缺"由字段缺省种子保证（全新安装
    各用途即可用），不依赖加载期的显式必填清单校验。
    """

    planner: LLMProfileConfig = Field(
        default_factory=lambda: _seed_profile("planner"),
        description="决策 profile（思考与工具编排，温度偏高）",
    )
    replyer: LLMProfileConfig = Field(
        default_factory=lambda: _seed_profile("replyer"),
        description="表达 profile（主播回复生成，低温稳定）",
    )
    summary: LLMProfileConfig = Field(
        default_factory=lambda: _seed_profile("summary"),
        description="摘要 profile（话题总结，长超时）",
    )
    minecraft: LLMProfileConfig = Field(
        default_factory=lambda: _seed_profile("minecraft"),
        description="游戏 Agent profile（Minecraft 决策循环）",
    )
    minecraft_builder: LLMProfileConfig = Field(
        default_factory=lambda: _seed_profile("minecraft_builder"),
        description="按需建筑设计 profile（独立于游戏决策的生成预算）",
    )
    vision: LLMProfileConfig = Field(
        default_factory=lambda: _seed_profile("vision"),
        description="视觉 profile（屏幕/图像理解，少 token）",
    )
    simulator: LLMProfileConfig = Field(
        default_factory=lambda: _seed_profile("simulator"),
        description="模拟 profile（虚拟用户消息生成，高温活跃）",
    )

    @model_validator(mode="before")
    @classmethod
    def seed_builder_models(cls, data: Any) -> Any:
        """已有安装新增设计用途时沿用游戏模型引用，避免引用不存在的 default 模型。"""
        if not isinstance(data, dict) or "minecraft_builder" in data:
            return data
        minecraft = data.get("minecraft", {})
        if isinstance(minecraft, LLMProfileConfig):
            minecraft = minecraft.model_dump()
        if not isinstance(minecraft, dict) or not isinstance(minecraft.get("model_list"), list):
            return data
        return {
            **data,
            "minecraft_builder": {
                **_PROFILE_PRESETS["minecraft_builder"],
                "model_list": list(minecraft["model_list"]),
            },
        }


class ModelRootConfig(BaseConfig):
    """模型配置根类

    对应 ``config/model.toml`` 文件，三层结构：
    - ``llm_providers``：provider 列表（API 连接共享）
    - ``llm_models``：模型注册表（model_identifier + 价格）
    - ``llm_profiles``：用途 profile 字典（planner / replyer / summary /
      minecraft / minecraft_builder / vision / simulator）；成员由用途 Schema 定义
    """

    __file_name__ = "model.toml"
    __section_label__ = "模型"

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, description="文件元数据")
    llm_providers: List[LLMProviderConfig] = Field(
        default_factory=lambda: [LLMProviderConfig()],
        description="API provider 列表（被 llm_models.api_provider 引用）",
    )
    llm_models: List[LLMModelConfig] = Field(
        default_factory=lambda: [LLMModelConfig()],
        description="模型注册表（被 llm_profiles.model_list 引用）",
    )
    llm_profiles: LLMProfilesConfig = Field(
        default_factory=LLMProfilesConfig,
        description=("用途 profile 封闭集合（包含游戏决策与独立的建筑设计用途）"),
        json_schema_extra={"x-ui-type": "object"},
    )

    @model_validator(mode="after")
    def _validate_references(self) -> "ModelRootConfig":
        """校验 provider.name 唯一性 + model.api_provider 引用 + profile.model_list 引用"""
        providers = self.llm_providers
        if not providers:
            raise ValueError("ModelRootConfig.llm_providers 至少需要 1 个 provider")

        # provider.name 唯一性
        names = [p.name for p in providers]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"llm_providers 中存在重复的 provider name: {sorted(duplicates)}")

        valid_provider_names = set(names)
        valid_model_names = {m.name for m in self.llm_models}

        # model.api_provider 引用校验
        for model_cfg in self.llm_models:
            if model_cfg.api_provider not in valid_provider_names:
                raise ValueError(
                    f"llm_models[{model_cfg.name!r}].api_provider={model_cfg.api_provider!r} "
                    f"未在 llm_providers 中找到（可用：{sorted(valid_provider_names)}）"
                )

        # profile.model_list 引用校验（封闭集合容器 dump 为 用途名 → profile 配置 dict）
        for profile_name, profile_cfg in self.llm_profiles.model_dump().items():
            for model_name in profile_cfg["model_list"]:
                if model_name not in valid_model_names:
                    raise ValueError(
                        f"llm_profiles[{profile_name!r}].model_list 含未知模型 "
                        f"{model_name!r}（可用：{sorted(valid_model_names)}）"
                    )

        return self


__all__ = [
    "LLMProviderConfig",
    "LLMModelConfig",
    "LLMSelectionStrategy",
    "LLMProfileConfig",
    "LLMProfilesConfig",
    "ModelRootConfig",
    # 向后兼容别名：旧测试 / 旧引用仍可访问 ModelConfig
    "ModelConfig",
]

# 向后兼容别名（测试与下游导入平滑迁移；T20 收口时全量更新）
ModelConfig = ModelRootConfig
