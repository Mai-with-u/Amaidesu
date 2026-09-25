"""LLM 装配层 - provider 池 / 模型索引 / profile 解析

负责把 ``config["model"]`` 三层配置（llm_providers / llm_models /
llm_profiles）解析为运行期数据结构：

- **provider 池**：每个 ``[[llm_providers]]`` 项经 clients 调度表构造一个
  共享客户端实例，多个 profile / model 复用同一连接。
- **模型索引**：``[[llm_models]]`` 按 name 建索引并校验 provider 引用。
- **profile 解析快照**：``[llm_profiles.<name>]`` 的 ``model_list`` 锁定为
  有序的 (model, provider) 序列，供引擎按选型策略调度。

引擎（engine.py）在 setup 期调用本模块，自身不接触装配细节。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.modules.config.model_schemas import LLMProfilesConfig
from src.modules.llm.clients import get_client_impl
from src.modules.logging import get_logger

__all__ = [
    "ProfileNames",
    "KNOWN_PROFILE_NAMES",
    "_ResolvedModel",
    "_ResolvedProfile",
    "build_resolved_profile",
    "index_models",
    "register_providers",
    "resolve_profile_name",
    "validate_profile_binding",
]


class ProfileNames:
    """用途 profile 命名常量（与 ``model.toml`` 中 ``[llm_profiles.<name>]`` 一一对应）。

    profile 集合是封闭集合：权威成员清单由配置 schema 的
    ``LLMProfilesConfig`` 显式字段定义（见 :data:`KNOWN_PROFILE_NAMES`），
    未知用途在配置加载与装配期一律硬错。本常量仅作字面量共享。
    """

    PLANNER = "planner"
    REPLYER = "replyer"
    SUMMARY = "summary"
    MINECRAFT = "minecraft"
    MINECRAFT_BUILDER = "minecraft_builder"
    VISION = "vision"
    SIMULATOR = "simulator"

    # 默认 profile（向后兼容调用方未显式指定 client_type 场景）
    DEFAULT = PLANNER


# 封闭 profile 集合的运行期权威源 = 配置 schema 容器的显式字段名集合。
KNOWN_PROFILE_NAMES: frozenset[str] = frozenset(LLMProfilesConfig.model_fields)


def validate_profile_binding(profile_name: str) -> None:
    """校验组件声明的 profile 存在于封闭集合；未知 profile 硬错。

    消费方（planner / replyer 等）声明自己绑定的 profile 时调用本函数，
    声明错误在装配期暴露，不允许静默兜底。
    """
    if profile_name not in KNOWN_PROFILE_NAMES:
        raise ValueError(f"未知的 LLM 用途 profile: {profile_name!r}（封闭集合：{sorted(KNOWN_PROFILE_NAMES)}）")


class _ResolvedModel(BaseModel):
    """单个 model 在引擎视角下的解析结果

    Attributes:
        model_name: model 注册名（对应 ``llm_models[].name``）
        model_identifier: 实际 API 模型标识
        provider_name: 关联 provider 名
    """

    model_name: str
    model_identifier: str
    provider_name: str

    model_config = {"frozen": True}


class _ResolvedProfile(BaseModel):
    """profile 解析快照（按 model_list 顺序锁定 model 实例）"""

    profile_name: str
    slow_threshold_ms: int
    selection_strategy: str  # sequential / balance / random
    seed: int  # random 策略用；0 表示不固定
    temperature: float = 0.3
    reasoning_effort: str = ""
    models: List[_ResolvedModel] = Field(default_factory=list)

    model_config = {"frozen": True}


def register_providers(
    provider_configs: List[Dict[str, Any]],
    logger: Any = None,
) -> Tuple[Dict[str, Tuple[Dict[str, Any], Any]], Dict[str, Any]]:
    """构造 provider 连接池：每个 provider 配置经调度表实例化一个共享客户端。

    Args:
        provider_configs: ``[[llm_providers]]`` 配置列表
        logger: 装配日志载体（引擎把自己的 logger 传入，保持日志名一致）

    Returns:
        (providers, provider_clients)：
        providers 为 ``{name: (配置, 客户端实例)}``；
        provider_clients 为 ``{name: 客户端实例}``（快速查找用）。
    """
    log = logger if logger is not None else get_logger("LLMBootstrap")
    providers: Dict[str, Tuple[Dict[str, Any], Any]] = {}
    provider_clients: Dict[str, Any] = {}
    for pcfg in provider_configs:
        name = pcfg.get("name", "default")
        if name in providers:
            raise ValueError(f"llm_providers 中存在重复的 provider name: {name!r}")
        client_type = pcfg.get("client_type", "openai")
        impl = get_client_impl(client_type)
        client_instance = impl(pcfg)
        providers[name] = (pcfg, client_instance)
        provider_clients[name] = client_instance
        log.info(f"已注册 provider '{name}' (客户端: {client_type}, 端点: {pcfg.get('base_url', 'N/A')})")
    return providers, provider_clients


def index_models(
    model_configs: List[Dict[str, Any]],
    providers: Dict[str, Tuple[Dict[str, Any], Any]],
) -> Dict[str, Tuple[Dict[str, Any], str]]:
    """按 name 索引 ``[[llm_models]]``，并校验 api_provider 引用存在。"""
    models: Dict[str, Tuple[Dict[str, Any], str]] = {}
    for mcfg in model_configs:
        mname = mcfg.get("name", "default")
        if mname in models:
            raise ValueError(f"llm_models 中存在重复的 model name: {mname!r}")
        api_provider = mcfg.get("api_provider")
        if api_provider not in providers:
            raise ValueError(
                f"llm_models[{mname!r}].api_provider={api_provider!r} "
                f"未在 llm_providers 中找到（可用：{sorted(providers.keys())}）"
            )
        models[mname] = (mcfg, api_provider)
    return models


def build_resolved_profile(
    pname: str,
    pcfg: Dict[str, Any],
    models: Dict[str, Tuple[Dict[str, Any], str]],
) -> _ResolvedProfile:
    """构造 profile 解析快照：model_list → [(model_name, identifier, provider)]"""
    model_list = pcfg.get("model_list") or []
    if not model_list:
        raise ValueError(f"profile {pname!r} 的 model_list 为空（至少 1 个模型）")

    resolved_models: List[_ResolvedModel] = []
    for mname in model_list:
        if mname not in models:
            raise ValueError(f"profile {pname!r}.model_list 引用未知 model {mname!r} （可用：{sorted(models.keys())}）")
        mcfg, provider_name = models[mname]
        resolved_models.append(
            _ResolvedModel(
                model_name=mname,
                model_identifier=mcfg.get("model_identifier", mname),
                provider_name=provider_name,
            )
        )

    strategy_cfg = pcfg.get("selection_strategy") or {}
    if isinstance(strategy_cfg, dict):
        strategy_name = strategy_cfg.get("name", "sequential")
        seed = int(strategy_cfg.get("seed", 0) or 0)
    else:
        # 兼容 Pydantic model 转 dict 后残留 BaseModel 形态
        strategy_name = getattr(strategy_cfg, "name", "sequential") or "sequential"
        seed = int(getattr(strategy_cfg, "seed", 0) or 0)

    if strategy_name not in ("sequential", "balance", "random"):
        raise ValueError(
            f"profile {pname!r}.selection_strategy.name={strategy_name!r} "
            f"不在 ['sequential', 'balance', 'random'] 范围内"
        )

    return _ResolvedProfile(
        profile_name=pname,
        slow_threshold_ms=int(pcfg.get("slow_threshold_ms", 15_000) or 15_000),
        selection_strategy=strategy_name,
        seed=seed,
        temperature=pcfg.get("temperature", 0.3),
        reasoning_effort=pcfg.get("reasoning_effort", "") or "",
        models=resolved_models,
    )


def resolve_profile_name(
    client_type: Optional[str],
    profiles: Dict[str, Any],
    logger: Any = None,
) -> str:
    """把 ``client_type`` 参数解析为 profile 名。

    - None / 缺省 → ProfileNames.DEFAULT
    - 已是 profile 名（存在于 profiles）→ 原样返回
    - 其余值 fail-fast（旧名 llm / llm_fast / vlm 等兼容映射已退役，
      消费方一律使用封闭集合内的 profile 名）
    """
    if client_type is None:
        return ProfileNames.DEFAULT
    if client_type in profiles:
        return client_type
    # 真正未注册：fail-fast
    raise ValueError(f"LLM 客户端/用途 '{client_type}' 未配置。已配置的 profile: {list(profiles.keys())}")
