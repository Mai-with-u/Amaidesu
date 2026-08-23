"""模型配置 Schema 定义（v2.0.0）

定义 LLM/VLM 模型配置的 Pydantic Schema，采用"Provider + Profile"两层结构：

- LLMProviderConfig：API 提供商配置（共享的连接信息、鉴权、重试参数）
- LLMProfileConfig：使用预设（引用 provider，并指定模型/温度等参数）
- ModelConfig：聚合所有 provider 和 profile，对应 config/model.toml 文件

这种拆分允许多个 profile 共享同一个 provider（例如 llm / llm_fast 都用 deepseek），
同时让每个 profile 只描述自己关心的字段（model/temperature/max_tokens）。

v2.0.0 变化：
- ``llm_outline`` 改名为 ``llm_agenda``（Outline→Agenda 命名统一）
"""

from pydantic import Field, model_validator

from src.modules.config.schemas.base import BaseConfig


class LLMProviderConfig(BaseConfig):
    """API 提供商配置

    一个 provider 描述一个 API 端点（OpenAI 兼容）的连接细节，
    可被多个 role 共享（llm / llm_fast / vlm / llm_local / llm_summary / llm_outline）。

    Attributes:
        name: provider 唯一名称，供 role.provider 引用
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

    name: str = Field(default="default", description="provider 唯一名称（被 role.provider 引用）")
    client_type: str = Field(
        default="openai",
        description="客户端实现标识（如 openai）",
        json_schema_extra={"x-ui-type": "select", "x-options": ["openai"]},
    )
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="API 端点（可自定义为任何 OpenAI 兼容服务）",
    )
    api_key: str = Field(default="", description="API 密钥（留空则使用环境变量）")
    auth_type: str = Field(
        default="bearer",
        description="鉴权方式：bearer / header / query / none",
        json_schema_extra={"x-ui-type": "select", "x-options": ["bearer", "header", "query", "none"]},
    )
    auth_header_name: str = Field(default="Authorization", description="header 鉴权时的 header 名")
    auth_header_prefix: str = Field(default="Bearer", description="header 鉴权时值的前缀")
    auth_query_name: str = Field(default="api_key", description="query 鉴权时的 query 参数名")
    default_headers: dict[str, str] = Field(default_factory=dict, description="每次请求附加的默认 header")
    timeout: int = Field(default=60, ge=1, description="单次请求超时时间（秒）")
    max_retries: int = Field(default=3, ge=0, description="请求失败时的最大重试次数")
    retry_delay: float = Field(default=1.0, ge=0.0, description="重试间隔时间（秒）")
    reasoning_parse_mode: str = Field(
        default="auto",
        description="推理内容解析模式：auto / native / think_tag / none",
        json_schema_extra={"x-ui-type": "select", "x-options": ["auto", "native", "think_tag", "none"]},
    )


class LLMProfileConfig(BaseConfig):
    """LLM 使用预设配置

    一个 profile 引用一个 provider，并指定具体的 model/温度等专属参数。
    base_url / api_key 为空时使用 provider 的对应字段。

    Attributes:
        provider: 引用的 provider 名（必须存在于 ModelConfig.llm_providers）
        model: 模型名称
        temperature: 生成温度（None 表示使用 provider 默认）
        max_tokens: 最大生成 token 数（None 表示使用 provider 默认）
        base_url: 覆盖 provider.base_url（一般用于本地测试）
        api_key: 覆盖 provider.api_key（一般用于 profile 级密钥隔离）
    """

    provider: str = Field(default="default", description="引用的 provider 名（对应 llm_providers[].name）")
    model: str = Field(default="gpt-4o-mini", description="模型名称")
    temperature: float | None = Field(
        default=None,
        description="生成温度 (0.0-2.0)，None 表示使用 provider 默认",
        json_schema_extra={"x-ui-type": "number"},
    )
    max_tokens: int | None = Field(
        default=None,
        description="最大 Token 数，None 表示使用 provider 默认",
        json_schema_extra={"x-ui-type": "integer"},
    )
    base_url: str | None = Field(default=None, description="覆盖 provider.base_url")
    api_key: str | None = Field(default=None, description="覆盖 provider.api_key")


class ModelConfig(BaseConfig):
    """模型配置根类

    聚合所有 LLM/VLM provider 和 profile 引用。
    对应 config/model.toml 文件。

    Attributes:
        llm_providers: provider 列表（用 list 而非 dict，schema_generator 仅支持列表）
        llm: 标准 LLM 使用预设（用于高质量任务）
        llm_fast: 快速 LLM 使用预设（用于低延迟任务，如 Avatar 表情分析）
        vlm: 视觉语言模型使用预设（用于图像理解任务）
        llm_local: 本地模型使用预设（Ollama / LM Studio / vLLM 等）
        llm_summary: 房间状态摘要 LLM 使用预设（独立 client，避免与 Planner 共享连接池）
        llm_agenda: 直播大纲 LLM 使用预设（v2.0.0 由 llm_outline 改名；独立 client，Agenda AI 生成初始大纲）
    """

    llm_providers: list[LLMProviderConfig] = Field(
        default_factory=lambda: [LLMProviderConfig()],
        description="API provider 列表（被 profile.provider 引用）",
    )
    llm: LLMProfileConfig = Field(default_factory=LLMProfileConfig, description="标准 LLM 使用预设")
    llm_fast: LLMProfileConfig = Field(default_factory=LLMProfileConfig, description="快速 LLM 使用预设")
    vlm: LLMProfileConfig = Field(default_factory=LLMProfileConfig, description="视觉语言模型使用预设")
    llm_local: LLMProfileConfig = Field(default_factory=LLMProfileConfig, description="本地模型使用预设")
    llm_summary: LLMProfileConfig = Field(
        default_factory=LLMProfileConfig,
        description="房间状态摘要 LLM 使用预设（独立 client，避免与 Planner 共享连接池）",
    )
    llm_agenda: LLMProfileConfig = Field(
        default_factory=LLMProfileConfig,
        description="直播大纲 LLM 使用预设（v2.0.0 由 llm_outline 改名，独立 client；用于 Agenda AI 生成初始大纲）",
    )

    @model_validator(mode="after")
    def _validate_providers_and_profiles(self) -> "ModelConfig":
        """校验 provider 唯一性 + profile→provider 引用

        失败立即抛 ValueError（fail-fast），避免运行期才发现引用缺失。
        """
        providers = self.llm_providers
        if not providers:
            raise ValueError("ModelConfig.llm_providers 至少需要 1 个 provider")

        # provider.name 唯一性
        names = [p.name for p in providers]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"ModelConfig.llm_providers 中存在重复的 provider name: {sorted(duplicates)}")

        # profile.provider 必须能解析到某个 provider.name。
        # 仅校验在 ModelConfig(...) 构造时**显式传入**的 profile：
        # 未传入的 profile 使用默认占位（provider='default'），
        # 此时不强制要求 'default' 存在，避免"配置一半就报错"。
        valid_names = set(names)
        explicitly_set_profiles = self.model_fields_set
        for profile_field in ("llm", "llm_fast", "vlm", "llm_local", "llm_summary", "llm_agenda"):
            if profile_field not in explicitly_set_profiles:
                continue
            profile_cfg = getattr(self, profile_field)
            if profile_cfg.provider not in valid_names:
                raise ValueError(
                    f"ModelConfig.{profile_field}.provider={profile_cfg.provider!r} "
                    f"未在 llm_providers 中找到（可用：{sorted(valid_names)}）"
                )

        return self
