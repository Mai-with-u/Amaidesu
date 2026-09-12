"""LLM 管理器 - 核心基础设施

统一 LLM 调用入口，按用途 profile 解析 ``llm_profiles.<name>``，
从该 profile 的 ``model_list`` 选取实际模型并完成故障切换。

设计要点：

- **provider 维度连接池**：每个 ``[[llm_providers]]`` 项构造一个共享客户端，
  多个 profile / model 可复用同一连接。
- **用途驱动**：调用方传 profile 名（planner / replyer / summary /
  minecraft / vision / simulator），LLMManager 按 ``model_list`` 选择
  具体模型。
- **故障切换**：当前模型超 ``hard_timeout_ms`` 取消请求并切下一个；
  超 ``slow_threshold_ms`` 仅告警（不切）。成功后立即返回，不再尝试。
- **选择策略**（profile 级 ``selection_strategy.name``）：
    - ``sequential``：按 model_list 顺序，第一个起跑
    - ``balance``：按累计调用计数挑最闲的（最少调用的优先）
    - ``random``：随机选一个
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.modules.llm.clients.base import get_client_impl
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.storage.sqlite_store import SQLiteStore

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
    # 本次调用的请求历史 ID（request_history_manager 落库键）。调用方（如
    # Planner 决策事件）用它作为"查看完整请求"的指针；失败路径同样回填。
    request_id: str = ""


def normalize_tool_calls_for_protocol(tool_calls: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """把 LLMResponse.tool_calls 规整为可安全喂回的 OpenAI 协议形态。

    client 解析层返回的 ``function.arguments`` 是 dict（消费端友好）；
    而 assistant 消息喂回时协议要求 ``function.arguments`` 为 JSON 字符串，
    dict 直接透传会被严格端点拒绝（400 invalid type: map）。本函数是
    喂回前的最后防线，输出只含协议字段。
    """
    normalized: List[Dict[str, Any]] = []
    for tc in tool_calls or []:
        func = tc.get("function") or {}
        args = func.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args or {}, ensure_ascii=False, default=str)
        normalized.append(
            {
                "id": tc.get("id", ""),
                "type": "function",
                "function": {"name": func.get("name", ""), "arguments": args},
            }
        )
    return normalized


class RetryConfig(BaseModel):
    """LLM 调用重试配置（provider 维度全局默认；profile 不覆盖）"""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0


# === 用途 profile 名常量（与 config/model.toml [llm_profiles.<name>] 对齐）===


class ProfileNames:
    """用途 profile 命名常量（与 ``model.toml`` 中 ``[llm_profiles.<name>]`` 一一对应）。

    旧版 client_type 命名（llm / llm_fast / vlm / llm_local / llm_summary /
    llm_agenda）已废弃——新结构按"用途"分（planner / replyer / summary /
    minecraft / vision / simulator），由 LLMManager 在 setup 期从
    ``config["llm_profiles"]`` 读取实例化清单，本常量仅作字面量共享。
    """

    PLANNER = "planner"
    REPLYER = "replyer"
    SUMMARY = "summary"
    MINECRAFT = "minecraft"
    VISION = "vision"
    SIMULATOR = "simulator"

    ALL: Tuple[str, ...] = (PLANNER, REPLYER, SUMMARY, MINECRAFT, VISION, SIMULATOR)

    # 默认 profile（向后兼容调用方未显式指定 client_type 场景）
    DEFAULT = PLANNER

    @classmethod
    def is_valid(cls, name: str) -> bool:
        return name in cls.ALL


# 向后兼容别名：旧测试 / 旧代码可能引用 ``ClientType``；语义已收敛到 ProfileNames
ClientType = ProfileNames


class _ResolvedModel(BaseModel):
    """单个 model 在 LLMManager 视角下的解析结果

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
    hard_timeout_ms: int
    slow_threshold_ms: int
    selection_strategy: str  # sequential / balance / random
    seed: int  # random 策略用；0 表示不固定
    temperature: float = 0.3
    max_tokens: int = 4096
    models: List[_ResolvedModel] = Field(default_factory=list)

    model_config = {"frozen": True}


class LLMManager:
    """LLM 管理器 - profile 驱动的多模型故障切换

    核心基础设施服务，与 EventBus 同级。

    职责：
    - 按 ``llm_providers`` 注册 provider 客户端（每个 provider 一个共享连接）
    - 按 ``llm_models`` / ``llm_profiles`` 构建 profile 解析快照
    - 按 profile.model_list 调度模型选择（sequential / balance / random）
    - 实现硬超时切换（取消 + 下一个模型）与慢调用告警
    - 内置重试（仅在同一模型上重试，不跨模型）
    - Token 使用量统计 + llm_usage 落库

    使用示例：
        ```python
        # 在 main.py 中初始化
        llm_manager = LLMManager()
        await llm_manager.setup(config["model"])

        # 调用（按用途 profile 名）
        response = await llm_manager.chat_messages(
            [{"role": "user", "content": "你好"}],
            client_type="planner",
        )
        ```
    """

    def __init__(self, sqlite_store: Optional["SQLiteStore"] = None):
        self.logger = get_logger("LLMManager")
        # provider_name -> provider 配置 + 客户端实例（共享连接）
        self._providers: Dict[str, Tuple[Dict[str, Any], Any]] = {}
        # provider_name -> 客户端实例（仅客户端引用，便于快速查找）
        self._provider_clients: Dict[str, Any] = {}
        # model_name -> model 配置 + 关联 provider_name
        self._models: Dict[str, Tuple[Dict[str, Any], str]] = {}
        # profile_name -> _ResolvedProfile 快照
        self._profiles: Dict[str, _ResolvedProfile] = {}
        # profile_name -> 累计调用次数（balance 策略使用）
        self._profile_call_counts: Dict[str, int] = {}
        # profile_name -> 各 model 已调用次数（balance 策略使用）
        self._model_call_counts: Dict[str, Dict[str, int]] = {}
        self._config: Dict[str, Any] = {}
        self._token_manager = None
        self._retry_config = RetryConfig()
        # 注入后每次成功调用旁路写一条 llm_usage（失败降级不阻断调用）；None 时不落库
        self._sqlite_store = sqlite_store
        # 随机策略 RNG（lazy 创建，按 seed 决定是否固定）
        self._rng: Optional[random.Random] = None

    # === setup / 生命周期 ===

    async def setup(self, config: Dict[str, Any]) -> None:
        """从三层配置（providers / models / profiles）初始化 LLM 运行时。

        Args:
            config: ``config["model"]`` 子树，含：
                - ``llm_providers``: list[dict]，每个 provider 含 name/client_type/base_url/api_key 等
                - ``llm_models``: list[dict]，模型清单（name / model_identifier / api_provider / 价格）
                - ``llm_profiles``: dict[str, dict]，用途 profile；model_list 必填

        配置示例：
            ```toml
            [[llm_providers]]
            name = "deepseek"
            client_type = "openai"
            base_url = "https://api.deepseek.com/v1"
            api_key = "sk-xxx"

            [[llm_models]]
            name = "ds-chat"
            model_identifier = "deepseek-chat"
            api_provider = "deepseek"

            [llm_profiles.planner]
            model_list = ["ds-chat"]
            selection_strategy = { name = "sequential" }
            hard_timeout_ms = 90000
            ```
        """
        self._config = config

        # 重置状态：setup 可重复调用（热重载 / 测试 re-setup 共用实例场景）
        self._providers.clear()
        self._provider_clients.clear()
        self._models.clear()
        self._profiles.clear()
        self._profile_call_counts.clear()
        self._model_call_counts.clear()
        self._rng = None

        provider_configs = config.get("llm_providers") or []
        if not provider_configs:
            raise ValueError("LLMManager.setup 收到空 llm_providers（至少需要 1 个 provider）")

        # === 1. 注册 provider 客户端（每个 provider 一个连接）===
        for pcfg in provider_configs:
            name = pcfg.get("name", "default")
            if name in self._providers:
                raise ValueError(f"llm_providers 中存在重复的 provider name: {name!r}")
            client_type = pcfg.get("client_type", "openai")
            impl = get_client_impl(client_type)
            client_instance = impl(pcfg)
            self._providers[name] = (pcfg, client_instance)
            self._provider_clients[name] = client_instance
            self.logger.info(f"已注册 provider '{name}' (客户端: {client_type}, 端点: {pcfg.get('base_url', 'N/A')})")

        # === 2. 索引 llm_models（按 name）===
        model_configs = config.get("llm_models") or []
        for mcfg in model_configs:
            mname = mcfg.get("name", "default")
            if mname in self._models:
                raise ValueError(f"llm_models 中存在重复的 model name: {mname!r}")
            api_provider = mcfg.get("api_provider")
            if api_provider not in self._providers:
                raise ValueError(
                    f"llm_models[{mname!r}].api_provider={api_provider!r} "
                    f"未在 llm_providers 中找到（可用：{sorted(self._providers.keys())}）"
                )
            self._models[mname] = (mcfg, api_provider)

        # === 3. 解析 llm_profiles 快照 ===
        profile_configs = config.get("llm_profiles") or {}
        for pname, pcfg in profile_configs.items():
            self._profiles[pname] = self._build_resolved_profile(pname, pcfg)
            self._profile_call_counts[pname] = 0
            self._model_call_counts[pname] = {}

        # === 4. 初始化 token manager ===
        # 必须函数体内 import：测试用 patch 拦截（src.modules.llm.clients.token_usage_manager.TokenUsageManager），
        # 顶部 import 会使 patch 失效（参见 tests/modules/llm/test_llm_manager.py）
        from src.modules.llm.clients.token_usage_manager import TokenUsageManager

        self._token_manager = TokenUsageManager(use_global=True)

        self.logger.info(
            f"LLMManager 初始化完成，providers: {list(self._providers.keys())}, profiles: {list(self._profiles.keys())}"
        )

    def _build_resolved_profile(self, pname: str, pcfg: Dict[str, Any]) -> _ResolvedProfile:
        """构造 profile 解析快照：model_list → [(model_name, identifier, provider)]"""
        model_list = pcfg.get("model_list") or []
        if not model_list:
            raise ValueError(f"profile {pname!r} 的 model_list 为空（至少 1 个模型）")

        resolved_models: List[_ResolvedModel] = []
        for mname in model_list:
            if mname not in self._models:
                raise ValueError(
                    f"profile {pname!r}.model_list 引用未知 model {mname!r} （可用：{sorted(self._models.keys())}）"
                )
            mcfg, provider_name = self._models[mname]
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
            hard_timeout_ms=int(pcfg.get("hard_timeout_ms", 90_000) or 90_000),
            slow_threshold_ms=int(pcfg.get("slow_threshold_ms", 15_000) or 15_000),
            selection_strategy=strategy_name,
            seed=seed,
            temperature=pcfg.get("temperature", 0.3),
            max_tokens=pcfg.get("max_tokens", 4096),
            models=resolved_models,
        )

    # === 公共 API：chat / chat_messages / chat_vision / stream_chat / call_tools / simple_*

    async def chat(
        self,
        prompt: str,
        *,
        client_type: Optional[str] = None,
        system_message: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """聊天调用（按用途 profile 名走 model_list 选择 + 故障切换）"""
        profile_name = self._resolve_profile_name(client_type)
        messages = self._build_messages(prompt, system_message)
        return await self._call_with_failover(
            profile_name,
            method="chat",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def chat_fast(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """快速聊天（语义别名：等价 ``chat(client_type='replyer')``，保留向后兼容）"""
        return await self.chat(prompt, client_type=ProfileNames.REPLYER, **kwargs)

    async def chat_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        client_type: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        tools: Optional[List[Dict[str, Any]]] = None,
        on_delta: Optional[Callable[[str, str], None]] = None,
    ) -> LLMResponse:
        """聊天调用（messages 列表 + 可选 tools + 可选流式回调）"""
        profile_name = self._resolve_profile_name(client_type)
        return await self._call_with_failover(
            profile_name,
            method="chat",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            on_delta=on_delta,
        )

    async def stream_chat(
        self,
        prompt: str,
        *,
        client_type: Optional[str] = None,
        system_message: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """流式聊天调用（不参与故障切换——流式语义不允许多模型切换；单模型失败即停）"""
        profile_name = self._resolve_profile_name(client_type)
        resolved = self._get_profile(profile_name)
        if not resolved.models:
            raise ValueError(f"profile {profile_name!r} 无可用模型")
        first = resolved.models[0]
        client = self._provider_clients[first.provider_name]
        messages = self._build_messages(prompt, system_message)
        async for chunk in client.stream_chat(
            messages=messages,
            model=first.model_identifier,
            stop_event=stop_event,
        ):
            yield chunk

    async def chat_vision(
        self,
        prompt: str,
        images: List[Any],
        *,
        client_type: Optional[str] = None,
        system_message: Optional[str] = None,
    ) -> LLMResponse:
        """视觉理解调用（默认走 vision profile，可通过 client_type 覆盖）"""
        # chat_vision 与 chat 的默认 profile 不同：默认走 vision，
        # 调用方可通过 client_type 显式指向其他 profile。
        if client_type is None:
            profile_name = ProfileNames.VISION
        else:
            profile_name = self._resolve_profile_name(client_type)
        messages = self._build_messages(prompt, system_message)
        return await self._call_with_failover(
            profile_name,
            method="vision",
            messages=messages,
            images=images,
        )

    async def call_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        *,
        client_type: Optional[str] = None,
        system_message: Optional[str] = None,
        on_delta: Optional[Callable[[str, str], None]] = None,
    ) -> LLMResponse:
        """工具调用（按用途 profile 走 model_list + 故障切换）"""
        profile_name = self._resolve_profile_name(client_type)
        messages = self._build_messages(prompt, system_message)
        response = await self._call_with_failover(
            profile_name,
            method="chat",
            messages=messages,
            tools=tools,
            on_delta=on_delta,
        )
        self.logger.warning(
            f"[诊断] call_tools 完成: profile={profile_name}, success={getattr(response, 'success', None)}, "
            f"error={getattr(response, 'error', None)!r}, "
            f"tool_calls={[tc.get('function', {}).get('name') for tc in (getattr(response, 'tool_calls', None) or []) if isinstance(tc, dict)]}, "
            f"content[:200]={(getattr(response, 'content', None) or '')[:200]!r}"
        )
        return response

    async def simple_chat(
        self,
        prompt: str,
        *,
        client_type: Optional[str] = None,
        system_message: Optional[str] = None,
    ) -> str:
        result = await self.chat(prompt, client_type=client_type, system_message=system_message)
        return result.content if result.success and result.content else f"错误: {result.error}"

    async def simple_vision(
        self,
        prompt: str,
        images: List[Any],
        *,
        client_type: Optional[str] = None,
    ) -> str:
        result = await self.chat_vision(prompt, images, client_type=client_type)
        return result.content if result.success and result.content else f"错误: {result.error}"

    # === 客户端 / profile 信息查询（兼容旧 API 形式）===

    def get_client(self, client_type: Optional[str] = None):
        """获取指定用途 profile 的首个模型对应 provider 客户端

        旧 API 形式保留（部分测试与外部探针依赖）；新代码应使用
        :func:`get_provider_client` 与 :func:`has_profile`。
        """
        profile_name = self._resolve_profile_name(client_type)
        resolved = self._get_profile(profile_name)
        if not resolved.models:
            raise ValueError(f"profile {profile_name!r} 无可用模型")
        return self._provider_clients[resolved.models[0].provider_name]

    def get_provider_client(self, provider_name: str):
        """按 provider name 获取共享客户端（不存在则抛 ValueError）"""
        if provider_name not in self._provider_clients:
            raise ValueError(f"provider {provider_name!r} 不存在（已注册: {sorted(self._provider_clients.keys())}）")
        return self._provider_clients[provider_name]

    def has_client(self, client_type: str) -> bool:
        """是否已配置指定用途 profile（兼容旧 API）"""
        return self.has_profile(client_type)

    def has_profile(self, profile_name: str) -> bool:
        """是否已配置指定用途 profile"""
        return profile_name in self._profiles

    def list_clients(self) -> List[str]:
        """列出所有已配置的用途 profile（兼容旧 API：返回 profile 名）"""
        return list(self._profiles.keys())

    def list_providers(self) -> List[str]:
        """列出所有已注册的 provider 名"""
        return list(self._providers.keys())

    def has_provider(self, provider_name: str) -> bool:
        """是否已注册指定 provider"""
        return provider_name in self._providers

    def list_models(self) -> List[str]:
        """列出所有已注册的 model 名"""
        return list(self._models.keys())

    def get_client_config(self, profile_name: str) -> Optional[Dict[str, Any]]:
        """获取指定用途 profile 的运行时配置（model_list / 阈值 / 温度等）"""
        resolved = self._profiles.get(profile_name)
        if resolved is None:
            return None
        return {
            "profile_name": resolved.profile_name,
            "hard_timeout_ms": resolved.hard_timeout_ms,
            "slow_threshold_ms": resolved.slow_threshold_ms,
            "selection_strategy": resolved.selection_strategy,
            "temperature": resolved.temperature,
            "max_tokens": resolved.max_tokens,
            "models": [
                {
                    "model_name": m.model_name,
                    "model_identifier": m.model_identifier,
                    "provider_name": m.provider_name,
                }
                for m in resolved.models
            ],
        }

    def get_client_info(self) -> Dict[str, Any]:
        """获取所有已注册 provider 的客户端信息（兼容旧 API：返回 profile 视角）"""
        info: Dict[str, Any] = {}
        for provider_name, client in self._provider_clients.items():
            info[provider_name] = {
                "client": client.__class__.__name__,
                "profiles": [
                    pname
                    for pname, resolved in self._profiles.items()
                    if any(m.provider_name == provider_name for m in resolved.models)
                ],
            }
        return info

    # === 内部：profile 解析 ===

    def _resolve_profile_name(self, client_type: Optional[str]) -> str:
        """把 ``client_type`` 参数解析为 profile 名。

        - None / 非法值 → ProfileNames.DEFAULT
        - 已是 profile 名（存在于 self._profiles）→ 原样返回
        - 旧名（llm / llm_fast / vlm 等）→ 映射到新名（兼容过渡期）
        """
        if client_type is None:
            return ProfileNames.DEFAULT
        if client_type in self._profiles:
            return client_type
        # 旧名映射（过渡期兼容，避免下游误传 hardcode 触发 ValueError）
        legacy_map = {
            "llm": ProfileNames.PLANNER,
            "llm_fast": ProfileNames.REPLYER,
            "vlm": ProfileNames.VISION,
            "llm_local": ProfileNames.MINECRAFT,
            "llm_summary": ProfileNames.SUMMARY,
            "llm_agenda": ProfileNames.SUMMARY,
            "llm_outline": ProfileNames.SUMMARY,
        }
        if client_type in legacy_map:
            mapped = legacy_map[client_type]
            self.logger.debug(f"client_type {client_type!r} 已映射到新 profile 名 {mapped!r}")
            return mapped
        # 真正未注册：fail-fast
        raise ValueError(f"LLM 客户端/用途 '{client_type}' 未配置。已配置的 profile: {list(self._profiles.keys())}")

    def _get_profile(self, profile_name: str) -> _ResolvedProfile:
        if profile_name not in self._profiles:
            raise ValueError(f"profile {profile_name!r} 未配置。已配置的 profile: {list(self._profiles.keys())}")
        return self._profiles[profile_name]

    def _build_messages(
        self,
        prompt: str,
        system_message: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """构建消息列表"""
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        return messages

    # === 内部：模型选择（按 selection_strategy 排序）===

    def _select_models_for_call(self, profile: _ResolvedProfile) -> List[_ResolvedModel]:
        """按 profile.selection_strategy 返回本次尝试的模型顺序

        - sequential：原序（首个起跑）
        - balance：调用次数最少的优先，并列时按 model_list 原序稳定
        - random：随机打乱
        """
        models = list(profile.models)
        strategy = profile.selection_strategy
        if strategy == "sequential":
            return models
        if strategy == "balance":
            counts = self._model_call_counts.get(profile.profile_name, {})
            indexed = list(enumerate(models))
            indexed.sort(key=lambda pair: (counts.get(pair[1].model_name, 0), pair[0]))
            return [m for _, m in indexed]
        if strategy == "random":
            rng = self._get_rng(profile.seed)
            shuffled = list(models)
            rng.shuffle(shuffled)
            return shuffled
        # 防御：理论上 _build_resolved_profile 已校验
        return models

    def _get_rng(self, seed: int) -> random.Random:
        """返回 random RNG。seed=0 表示不固定（每次新建）；>0 每次新建固定 RNG。

        固定 seed 时返回新实例，避免上次 shuffle 残留状态污染本次选择——
        这是 ``test_random_strategy_with_seed`` 等可重现测试的前提。
        """
        if seed == 0:
            return random.Random()
        return random.Random(seed)

    # === 内部：故障切换调用 ===

    async def _call_with_failover(
        self,
        profile_name: str,
        method: str,
        **kwargs: Any,
    ) -> LLMResponse:
        """按 model_list 顺序逐个尝试；当前模型失败（超时或异常）切下一个。

        Returns:
            LLMResponse：首个成功的模型响应；全部失败则返回 success=False 且
            ``error`` 字段列出尝试过的所有模型名。
        """
        profile = self._get_profile(profile_name)
        models_to_try = self._select_models_for_call(profile)

        request_id = f"req_{uuid.uuid4().hex[:12]}"
        start_time = time.time()
        self._profile_call_counts[profile_name] = self._profile_call_counts.get(profile_name, 0) + 1

        attempted_models: List[str] = []
        last_error: Optional[str] = None

        for idx, resolved_model in enumerate(models_to_try):
            attempted_models.append(resolved_model.model_name)
            client = self._provider_clients[resolved_model.provider_name]
            model_identifier = resolved_model.model_identifier
            provider_cfg = self._providers[resolved_model.provider_name][0]

            # balance 策略计数器：本次尝试（无论成败）计入该 model
            self._model_call_counts.setdefault(profile_name, {})[resolved_model.model_name] = (
                self._model_call_counts[profile_name].get(resolved_model.model_name, 0) + 1
            )

            response, error = await self._call_one_with_retry(
                method=method,
                client=client,
                provider_cfg=provider_cfg,
                model_identifier=model_identifier,
                model_name=resolved_model.model_name,
                profile_name=profile_name,
                request_id=request_id,
                start_time=start_time,
                slow_threshold_ms=profile.slow_threshold_ms,
                **kwargs,
            )

            if response is not None:
                response.request_id = request_id
                if idx > 0:
                    self.logger.warning(
                        f"[LLM 故障切换] profile={profile_name} 切到 {resolved_model.model_name}（之前尝试 {attempted_models[:-1]} 失败）"
                    )
                return response

            last_error = error
            self.logger.warning(
                f"[LLM 故障切换] profile={profile_name} 模型 {resolved_model.model_name} 调用失败，"
                f"尝试下一个：错误={error}"
            )

        # 全部失败
        self.logger.error(
            f"[LLM 全部失败] profile={profile_name} 尝试模型 {attempted_models} 均失败，最后错误={last_error}"
        )
        result = LLMResponse(
            success=False,
            content=None,
            error=f"全部模型失败 {attempted_models}: {last_error}",
            request_id=request_id,
        )
        self._record_request_history(
            request_id=request_id,
            client_type=profile_name,
            result=result,
            kwargs=kwargs,
            start_time=start_time,
        )
        return result

    async def _call_one_with_retry(
        self,
        *,
        method: str,
        client: Any,
        provider_cfg: Dict[str, Any],
        model_identifier: str,
        model_name: str,
        profile_name: str,
        request_id: str,
        start_time: float,
        slow_threshold_ms: int,
        **kwargs: Any,
    ) -> Tuple[Optional[LLMResponse], Optional[str]]:
        """单模型上的重试 + 慢调用告警 + 硬超时。

        Returns:
            (response, error)：成功 → (LLMResponse, None)；失败 → (None, 错误描述)
        """
        call_kwargs = dict(kwargs)
        call_kwargs["model"] = model_identifier
        # profile 生成参数为具体值（禁 None）：调用方未显式给值时用 profile 档位
        profile = self._get_profile(profile_name)
        if call_kwargs.get("temperature") is None:
            call_kwargs["temperature"] = profile.temperature
        if call_kwargs.get("max_tokens") is None:
            call_kwargs["max_tokens"] = profile.max_tokens

        max_retries = int(provider_cfg.get("max_retries", self._retry_config.max_retries) or 0)
        base_delay = float(provider_cfg.get("retry_delay", self._retry_config.base_delay) or 0.0)
        max_delay = self._retry_config.max_delay
        # max_retries=0 仍要给 1 次首次尝试；N>0 给 N 次（含首次）
        total_attempts = max(max_retries, 1)

        last_error: Optional[str] = None
        for attempt in range(total_attempts):
            attempt_start = time.time()
            try:
                method_func = getattr(client, method)
                response = await method_func(**call_kwargs)
                attempt_elapsed_ms = int((time.time() - attempt_start) * 1000)
                if attempt_elapsed_ms >= slow_threshold_ms:
                    self.logger.warning(
                        f"[LLM 慢调用告警] profile={profile_name} model={model_name} "
                        f"耗时 {attempt_elapsed_ms}ms（阈值 {slow_threshold_ms}ms，不切换）"
                    )
                if response.success:
                    await self._post_success(
                        request_id=request_id,
                        profile_name=profile_name,
                        model_name=model_name,
                        method=method,
                        result=response,
                        kwargs=call_kwargs,
                        start_time=start_time,
                    )
                    return response, None
                last_error = response.error or "未知客户端错误"
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < total_attempts - 1:
                delay = min(base_delay * (2**attempt), max_delay)
                if delay > 0:
                    await asyncio.sleep(delay)

        return None, last_error or "未知错误"

    async def _post_success(
        self,
        *,
        request_id: str,
        profile_name: str,
        model_name: str,
        method: str,
        result: LLMResponse,
        kwargs: Dict[str, Any],
        start_time: float,
    ) -> None:
        """成功路径后置动作：token 记录 + llm_usage 落库 + 请求历史"""
        if result.usage and self._token_manager:
            self._token_manager.record_usage(
                model_name=result.model or model_name,
                prompt_tokens=result.usage.get("prompt_tokens", 0),
                completion_tokens=result.usage.get("completion_tokens", 0),
                total_tokens=result.usage.get("total_tokens", 0),
            )
        if result.usage and self._sqlite_store:
            duration_ms = int((time.time() - start_time) * 1000)
            try:
                await self._persist_llm_usage(
                    profile_name=profile_name,
                    model_name=model_name,
                    method=method,
                    result=result,
                    duration_ms=duration_ms,
                )
            except Exception as exc:  # noqa: BLE001
                # 兜底（_persist_llm_usage 内部已 try/except；此处防止传播异常）
                self.logger.warning(f"llm_usage 落库包装失败: {exc}")
        self._record_request_history(
            request_id=request_id,
            client_type=profile_name,
            result=result,
            kwargs=kwargs,
            start_time=start_time,
        )

    async def _persist_llm_usage(
        self,
        *,
        profile_name: str,
        model_name: str,
        method: str,
        result: LLMResponse,
        duration_ms: int,
    ) -> None:
        """把一次成功调用的 token 消耗写入 ``llm_usage`` 表（``SQLiteStore`` 注入时生效）。

        费用口径与请求历史一致（同走 ``TokenUsageManager._calculate_cost``）；
        任何写入失败只记 warning，绝不阻断 LLM 调用链。
        """
        try:
            usage = result.usage or {}
            cost = 0.0
            if self._token_manager is not None:
                cost_info = self._token_manager._calculate_cost(
                    result.model or model_name,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )
                cost = float(cost_info.get("cost", 0.0))
            provider_name = "unknown"
            if model_name in self._models:
                _, provider_name = self._models[model_name]
            await self._sqlite_store.insert_llm_usage(
                model_name=result.model or model_name,
                provider_name=str(provider_name),
                request_type=method,
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                total_tokens=int(usage.get("total_tokens", 0)),
                cache_hit_tokens=int(usage.get("cache_hit_tokens", 0)),
                cache_miss_tokens=int(usage.get("cache_miss_tokens", 0)),
                cost=cost,
                duration_ms=duration_ms,
                profile_name=profile_name,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"llm_usage 落库失败: {exc}")

    def _record_request_history(
        self,
        request_id: str,
        client_type: str,
        result: LLMResponse,
        kwargs: Dict[str, Any],
        start_time: float,
    ) -> None:
        """记录请求历史（成功 / 失败路径均调用）"""
        try:
            # 必须函数体内 import：测试用 patch 拦截该路径，顶部 import 会使 patch 失效
            from src.modules.llm.request_history_manager import (
                RequestRecord,
                TokenUsage,
                get_global_request_history_manager,
            )

            latency_ms = int((time.time() - start_time) * 1000)
            model_name = result.model or "unknown"

            request_params = {
                "messages": kwargs.get("messages", []),
                "temperature": kwargs.get("temperature"),
                "max_tokens": kwargs.get("max_tokens"),
                "tools": kwargs.get("tools"),
            }
            request_params = {k: v for k, v in request_params.items() if v is not None}

            usage = None
            if result.usage:
                usage = TokenUsage(
                    prompt_tokens=result.usage.get("prompt_tokens", 0),
                    completion_tokens=result.usage.get("completion_tokens", 0),
                    total_tokens=result.usage.get("total_tokens", 0),
                )

            cost = 0.0
            if usage and self._token_manager:
                cost_info = self._token_manager._calculate_cost(
                    model_name, usage.prompt_tokens, usage.completion_tokens
                )
                cost = cost_info.get("cost", 0.0)

            record = RequestRecord(
                request_id=request_id,
                client_type=client_type,
                model_name=model_name,
                request_params=request_params,
                response_content=result.content,
                reasoning_content=result.reasoning_content,
                tool_calls=result.tool_calls or [],
                usage=usage,
                cost=cost,
                success=result.success,
                error=result.error,
                latency_ms=latency_ms,
            )

            history_manager = get_global_request_history_manager()
            history_manager.record_request(record)
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"记录请求历史失败: {e}")

    # === 清理 ===

    async def cleanup(self) -> None:
        """清理所有 provider 客户端资源（按 id 去重）"""
        cleaned: set[int] = set()
        for name, client in self._provider_clients.items():
            client_id = id(client)
            if client_id in cleaned:
                continue
            cleaned.add(client_id)
            try:
                await client.cleanup()
                self.logger.debug(f"已清理 provider '{name}' 客户端")
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"清理 provider '{name}' 客户端失败: {e}")
        self._provider_clients.clear()
        self._providers.clear()
        self._models.clear()
        self._profiles.clear()
        self._profile_call_counts.clear()
        self._model_call_counts.clear()

    # === 统计 ===

    def get_token_usage_summary(self) -> str:
        if self._token_manager:
            return self._token_manager.format_total_cost_summary()
        return "Token 管理器未初始化"
