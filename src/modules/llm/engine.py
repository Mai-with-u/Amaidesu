"""LLM 调用引擎 - 编排层

一次 LLM 调用的编排：按用途 profile 从 ``model_list`` 选型、单模型重试、
慢调用告警与故障切换。

设计要点：

- **选型策略**（profile 级 ``selection_strategy.name``）：
    - ``sequential``：按 model_list 顺序，第一个起跑
    - ``balance``：按累计调用计数挑最闲的（最少调用的优先）
    - ``random``：随机选一个
- **故障切换**：当前模型失败切下一个；超 ``slow_threshold_ms`` 仅告警（不切）。
  成功后立即返回，不再尝试。
- **硬超时墙**：profile 级 ``hard_timeout_ms`` 包住整个单模型尝试（含墙内
  重试），到点取消 in-flight 请求；超时切下一个模型。流式首 token 已产出
  后到点则只中止，已外发的增量不追溯。
- **厂商无关**：本模块不 import 任何厂商适配端（``clients/<vendor>/``），
  provider 客户端构造与能力解析一律经 ``clients`` 包的调度表完成。

profile 解析与 provider 池/模型索引的装配在 :mod:`src.modules.llm.bootstrap`。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel

from src.modules.llm.bootstrap import (
    ProfileNames,
    _ResolvedProfile,
    build_resolved_profile,
    index_models,
    register_providers,
    resolve_profile_name,
    validate_profile_binding,
    warn_hard_timeout_conflicts,
)
from src.modules.llm.client import LLMResponse
from src.modules.llm.clients import resolve_client_method
from src.modules.llm.errors import FatalError, LLMError, LLMInterruptedError, LLMTimeoutError, RetryableError
from src.modules.llm.interrupt import HardTimeoutExceeded, guarded_call
from src.modules.llm.observation import calculate_cost, record_usage
from src.modules.llm.payload import GenerateRequest, ImagePart, Message, Response, TextPart, ToolCall, ToolSpec, Usage
from src.modules.logging import get_logger
from src.modules.storage.repos import LLMRepo
from src.modules.storage.repos.llm import LLMRequestInsert

__all__ = ["LLMManager", "LLMResponse", "RetryConfig"]


class RetryConfig(BaseModel):
    """LLM 调用重试配置（provider 维度全局默认；profile 不覆盖）"""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0


# 错误分类 → 单模型内重试决策（分类异常由 Client 产出，Engine 只消费分类，
# 不解析异常内容做二次判定）
_RETRY_DECISION_RETRY = "retry"
_RETRY_DECISION_FAILOVER = "failover"
_RETRY_DECISION_ABORT = "abort"

_RETRY_POLICY: Dict[type, str] = {
    RetryableError: _RETRY_DECISION_RETRY,
    FatalError: _RETRY_DECISION_FAILOVER,
    LLMTimeoutError: _RETRY_DECISION_FAILOVER,
    LLMInterruptedError: _RETRY_DECISION_ABORT,
}


# === 契约归一化与新旧响应适配（Engine 的固定职责：消费方输入 → payload → Client）===

_PAYLOAD_METHODS = frozenset({"generate", "generate_vision"})
"""走中立 payload 契约的客户端能力名（Client 返回 payload.Response）。"""


class _StreamGate:
    """流式增量直通闸门：收到增量立即转调消费方回调（打字机效果）。

    增量外发即不可撤回，直通意味着首 token 之后的失败分支不能 failover
    （换模型会拼出前后矛盾的输出）、也不能追溯已外发的增量（不是错误）。
    ``started`` 作为首 token 判据：硬超时发生在首 token 前仍可 failover，
    发生在首 token 后只能整体中止。
    """

    def __init__(self, on_delta: Optional[Callable[[str, str], None]]) -> None:
        self.has_consumer = on_delta is not None
        self._on_delta = on_delta
        self.started = False

    @property
    def callback(self) -> Callable[[str, str], None]:
        def _gate(kind: str, text_delta: str) -> None:
            self.started = True
            if self._on_delta is not None:
                self._on_delta(kind, text_delta)

        return _gate


def _content_to_parts(content: Any) -> List[Any]:
    """OpenAI 风格 content（str / 分段列表）→ 中立 parts 列表"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        raise TypeError(f"不支持的消息 content 类型: {type(content).__name__}")
    parts: List[Any] = []
    for piece in content:
        if isinstance(piece, str):
            parts.append(piece)
        elif isinstance(piece, dict) and piece.get("type") == "text":
            parts.append(TextPart(text=str(piece.get("text", ""))))
        elif isinstance(piece, dict) and piece.get("type") == "image_url":
            image_url = piece.get("image_url")
            url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url or "")
            parts.append(ImagePart(image=url))
        else:
            raise TypeError(f"不支持的消息 content 片段: {type(piece).__name__}")
    return parts


def _tool_calls_from_protocol(raw: Any) -> List[ToolCall]:
    """厂商协议形态的 assistant ``tool_calls`` → 中立 ToolCall 列表。

    消费方（Planner / MinecraftAgent）喂回多轮 ReAct 上下文时按 OpenAI
    嵌套形态给值（``{"id", "type", "function": {"name", "arguments"}}``，
    arguments 为 JSON 字符串）；中立契约要求 arguments 已是对象，转换在此
    完成，非列表输入与解析失败的 arguments 一律退化为空结果。
    """
    calls: List[ToolCall] = []
    if not isinstance(raw, list):
        return calls
    for item in raw:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except (json.JSONDecodeError, TypeError):
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCall(
                id=str(item.get("id", "") or ""),
                name=str(function.get("name", "") or ""),
                arguments=arguments,
            )
        )
    return calls


def _normalize_generate_input(
    input: Any,
    *,
    system: Optional[str],
    tools: Optional[List[Any]],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> GenerateRequest:
    """消费方输入（str 或 OpenAI 风格 dict 列表或 Message 列表）→ 中立请求。

    dict 列表里的 system 消息折叠进请求的独立 system 参数（各厂商对
    system 的承载位置不同，由适配端翻译）。
    """
    messages: List[Message] = []
    system_texts: List[str] = []
    if isinstance(input, str):
        messages.append(Message(role="user", parts=[input]))
    elif isinstance(input, list):
        for item in input:
            if isinstance(item, Message):
                messages.append(item)
            elif isinstance(item, dict):
                role = str(item.get("role", "user"))
                content = item.get("content", "")
                if role == "system":
                    parts = _content_to_parts(content)
                    system_texts.append("".join(p if isinstance(p, str) else (p.text or "") for p in parts))
                    continue
                if role not in ("user", "assistant", "tool"):
                    raise ValueError(f"不支持的消息 role: {role!r}")
                tool_call_id = item.get("tool_call_id")
                messages.append(
                    Message(
                        role=role,
                        parts=_content_to_parts(content),
                        tool_calls=_tool_calls_from_protocol(item.get("tool_calls")),
                        tool_call_id=str(tool_call_id) if tool_call_id else None,
                    )
                )
            else:
                raise TypeError(f"不支持的消息类型: {type(item).__name__}")
    else:
        raise TypeError(f"不支持的输入类型: {type(input).__name__}")

    merged_system = "\n\n".join(([system] if system else []) + system_texts) or None
    tool_specs: List[ToolSpec] = []
    for tool in tools or []:
        if isinstance(tool, ToolSpec):
            tool_specs.append(tool)
        elif isinstance(tool, dict):
            tool_specs.append(
                ToolSpec(
                    name=str(tool.get("name", "")),
                    description=str(tool.get("description", "")),
                    parameters=tool.get("parameters") or {},
                )
            )
        else:
            raise TypeError(f"不支持的 tools 元素类型: {type(tool).__name__}")

    return GenerateRequest(
        messages=messages,
        system=merged_system,
        tools=tool_specs,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _payload_response_to_legacy(resp: Response) -> LLMResponse:
    """payload.Response → 遗留 LLMResponse（记账/请求历史链路仍消费遗留形状）"""
    return LLMResponse(
        success=resp.success,
        content=resp.content,
        model=resp.model,
        usage=(
            {
                k: v
                for k, v in {
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.total_tokens,
                    "cache_hit_tokens": resp.usage.cache_hit_tokens,
                    "cache_miss_tokens": resp.usage.cache_miss_tokens,
                }.items()
                if v is not None
            }
            if resp.usage is not None
            else None
        ),
        tool_calls=[
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in resp.tool_calls
        ],
        reasoning_content=resp.reasoning_content,
        error=resp.error,
        request_id=resp.request_id,
    )


def _legacy_response_to_payload(result: LLMResponse) -> Response:
    """遗留 LLMResponse → payload.Response（Engine 对外统一返回中立形状）"""
    tool_calls = [
        ToolCall(
            id=tc.get("id", ""),
            name=tc.get("function", {}).get("name", ""),
            arguments=tc.get("function", {}).get("arguments", {}),
        )
        for tc in (result.tool_calls or [])
        if isinstance(tc, dict)
    ]
    usage = None
    if result.usage is not None:
        usage = Usage(
            prompt_tokens=result.usage.get("prompt_tokens", 0),
            completion_tokens=result.usage.get("completion_tokens", 0),
            total_tokens=result.usage.get("total_tokens", 0),
            cache_hit_tokens=result.usage.get("cache_hit_tokens"),
            cache_miss_tokens=result.usage.get("cache_miss_tokens"),
        )
    return Response(
        success=result.success,
        content=result.content,
        tool_calls=tool_calls,
        usage=usage,
        model=result.model,
        reasoning_content=result.reasoning_content,
        error=result.error,
        request_id=result.request_id,
    )


class LLMManager:
    """LLM 管理器 - profile 驱动的多模型故障切换

    核心基础设施服务，与 EventBus 同级。

    职责：
    - 按 profile.model_list 调度模型选择（sequential / balance / random）
    - 实现故障切换（当前模型失败切下一个）与慢调用告警
    - 内置重试（仅在同一模型上重试，不跨模型）
    - 费用计算（统一口径 observation.calculate_cost）+ llm_usage 落库

    provider 池/模型索引/profile 解析快照的构建委托给 bootstrap 模块。

    使用示例：
        ```python
        # 在 main.py 中初始化
        llm_manager = LLMManager()
        await llm_manager.setup(config["model"])

        # 调用（按用途 profile 名）
        response = await llm_manager.generate(
            "你好",
            profile="planner",
        )
        ```
    """

    def __init__(self, llm_repo: Optional[LLMRepo] = None):
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
        # 价格表（{model_identifier: {price_in, price_out, ...}}），费用计算唯一口径
        self._model_prices: Dict[str, Dict[str, Any]] = {}
        self._retry_config = RetryConfig()
        # 注入后每次成功调用旁路写一条 llm_usage（失败降级不阻断调用）；None 时不落库
        self._llm_repo = llm_repo
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
            client_type = "<clients 调度表中注册的客户端类型>"
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
        self._model_prices = {}

        provider_configs = config.get("llm_providers") or []
        if not provider_configs:
            raise ValueError("LLMManager.setup 收到空 llm_providers（至少需要 1 个 provider）")

        # provider 客户端注册（每个 provider 一个连接）与模型索引由 bootstrap 承担
        self._providers, self._provider_clients = register_providers(provider_configs, self.logger)
        self._models = index_models(config.get("llm_models") or [], self._providers)

        # 解析 llm_profiles 快照（封闭集合校验：未知用途在装配期硬错）
        profile_configs = config.get("llm_profiles") or {}
        for pname, pcfg in profile_configs.items():
            validate_profile_binding(pname)
            self._profiles[pname] = build_resolved_profile(pname, pcfg, self._models)
            self._profile_call_counts[pname] = 0
            self._model_call_counts[pname] = {}

        # 启动期弱校验：profile 硬超时小于 provider 请求超时时告警（防请求级超时变死配置）
        warn_hard_timeout_conflicts(self._profiles, self._providers, self.logger)

        # 价格唯一来源 = model.toml [[llm_models]] 的定价字段
        # 价格表按 model_identifier 键入（费用计算用的是请求实际的 API 模型标识）
        self._model_prices = {
            mcfg.get("model_identifier") or mname: {
                "price_in": mcfg.get("price_in", 0.0),
                "price_out": mcfg.get("price_out", 0.0),
                "cache_price_in": mcfg.get("cache_price_in", 0.0),
                "cache": mcfg.get("cache", ""),
            }
            for mname, (mcfg, _prov) in self._models.items()
            if mcfg.get("price_in", 0.0) > 0 or mcfg.get("price_out", 0.0) > 0
        }

        self.logger.info(
            f"LLMManager 初始化完成，providers: {list(self._providers.keys())}, profiles: {list(self._profiles.keys())}"
        )

    # === 公共 API：generate / generate_vision（冻结的两个对外入口）

    async def generate(
        self,
        input: Union[str, List[Union[Message, Dict[str, Any]]]],
        *,
        profile: Optional[str] = None,
        system: Optional[str] = None,
        tools: Optional[List[Union[ToolSpec, Dict[str, Any]]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        on_delta: Optional[Callable[[str, str], None]] = None,
        interrupt: Optional[asyncio.Event] = None,
    ) -> Response:
        """执行一次 LLM 调用（对外唯一文本入口，返回中立 payload.Response）。

        签名宽是刻意的容错设计：``input`` 接受裸字符串（单轮用户消息）、
        中立 ``Message`` 列表或 OpenAI 风格 dict 列表——Engine 统一归一化
        到 payload 再调度，消费方不必为迁就契约改自己的输入形状。
        ``temperature`` / ``max_tokens`` 缺省时回退 profile 档位。
        """
        profile_name = self._resolve_profile_name(profile)
        request = _normalize_generate_input(
            input, system=system, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        result = await self._call_with_failover(
            profile_name,
            method="generate",
            request=request,
            on_delta=on_delta,
            interrupt_flag=interrupt,
        )
        return _legacy_response_to_payload(result)

    async def generate_vision(
        self,
        prompt: str,
        images: List[Any],
        *,
        profile: Optional[str] = None,
        system: Optional[str] = None,
        interrupt: Optional[asyncio.Event] = None,
    ) -> Response:
        """执行一次视觉调用（对外唯一视觉入口，返回中立 payload.Response）。

        签名宽是刻意的容错设计：``images`` 接受路径 / URL / 原始字节的
        混合列表，具体编码方式由适配端按厂商协议翻译。
        ``profile`` 缺省走 vision 档位。
        """
        if profile is None:
            profile_name = ProfileNames.VISION
        else:
            profile_name = self._resolve_profile_name(profile)
        request = _normalize_generate_input(prompt, system=system, tools=None, temperature=None, max_tokens=None)
        result = await self._call_with_failover(
            profile_name,
            method="generate_vision",
            request=request,
            images=images,
            interrupt_flag=interrupt,
        )
        return _legacy_response_to_payload(result)

    # === 内部：profile 解析 ===

    def _resolve_profile_name(self, profile: Optional[str]) -> str:
        """把调用方传入的 profile 参数解析为已配置 profile 名（解析规则见 bootstrap 模块）。"""
        return resolve_profile_name(profile, self._profiles, self.logger)

    def _get_profile(self, profile_name: str) -> _ResolvedProfile:
        if profile_name not in self._profiles:
            raise ValueError(f"profile {profile_name!r} 未配置。已配置的 profile: {list(self._profiles.keys())}")
        return self._profiles[profile_name]

    # === 内部：模型选择（按 selection_strategy 排序）===

    def _select_models_for_call(self, profile: _ResolvedProfile) -> List[Any]:
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
        # 防御：理论上 bootstrap 构建快照时已校验
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
        """单模型尝试整体（含内部重试）置于 profile 硬超时墙内。

        语义口径：hard_timeout 是墙，重试在墙内尽力、可能被截断——墙到点
        取消 in-flight 请求（含退避等待），整个尝试标记失败交还故障切换。
        两个分支例外：

        - 流式首 token 已产出：已外发的增量不追溯，也不得切换模型，
          只能整体中止（以 ``LLMInterruptedError`` 表达，向上传播）
        - 调用方中断 / 父任务取消：同一取消路径收割子任务后原样传播

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

        # 流式增量直通：收到即转调消费方（首 token 前后分支判据在 gate.started）
        gate = _StreamGate(call_kwargs.get("on_delta"))
        if gate.has_consumer:
            call_kwargs["on_delta"] = gate.callback

        hard_timeout_ms = profile.hard_timeout_ms
        try:
            response, error = await guarded_call(
                lambda: self._retry_loop(
                    method=method,
                    client=client,
                    provider_cfg=provider_cfg,
                    model_identifier=model_identifier,
                    model_name=model_name,
                    profile_name=profile_name,
                    request_id=request_id,
                    start_time=start_time,
                    slow_threshold_ms=slow_threshold_ms,
                    call_kwargs=call_kwargs,
                ),
                timeout_ms=hard_timeout_ms,
                interrupt_flag=call_kwargs.get("interrupt_flag"),
            )
        except HardTimeoutExceeded:
            if gate.started:
                raise LLMInterruptedError(
                    f"流式输出已开始后触达硬超时（{hard_timeout_ms}ms），已输出内容不追溯，整体中止"
                ) from None
            error = f"硬超时（{hard_timeout_ms}ms）：重试在墙内被截断"
            self.logger.warning(f"[LLM 硬超时] profile={profile_name} model={model_name} {error}，切下一个模型")
            return None, error
        return response, error

    async def _retry_loop(
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
        call_kwargs: Dict[str, Any],
    ) -> Tuple[Optional[LLMResponse], Optional[str]]:
        """墙内的单模型重试循环：按 ``_RETRY_POLICY`` 分类表驱动重试/切换/中止。"""
        max_retries = int(provider_cfg.get("max_retries", self._retry_config.max_retries) or 0)
        base_delay = float(provider_cfg.get("retry_delay", self._retry_config.base_delay) or 0.0)
        max_delay = self._retry_config.max_delay
        # max_retries=0 仍要给 1 次首次尝试；N>0 给 N 次（含首次）
        total_attempts = max(max_retries, 1)

        last_error: Optional[str] = None
        for attempt in range(total_attempts):
            attempt_start = time.time()
            try:
                # 能力解析走 clients 调度表，不在引擎层对客户端做 getattr 动态分派
                method_func = resolve_client_method(client, method)
                response = await method_func(**call_kwargs)
                if method in _PAYLOAD_METHODS:
                    # 中立 payload 契约路径：Client 返回 payload.Response，
                    # 记账/请求历史链路仍消费遗留形状，此处统一适配
                    response = _payload_response_to_legacy(response)
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
            except LLMError as exc:
                # 分类表驱动：按 Client 产出的分类查决策表，不解析异常内容
                decision = _RETRY_POLICY.get(type(exc), _RETRY_DECISION_FAILOVER)
                last_error = f"{type(exc).__name__}: {exc}"
                if decision == _RETRY_DECISION_ABORT:
                    # 中断整体中止，向调用方传播
                    raise
                if decision == _RETRY_DECISION_FAILOVER:
                    # Fatal / Timeout 不在同一模型上重试，直接切下一个模型
                    return None, last_error
                # retry：落到底部退避后继续下一轮
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                # 未分类异常兜底（legacy 客户端/测试桩直接抛裸异常）：维持既有重试行为
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
        """成功路径后置动作：llm_usage 落库 + 请求历史"""
        if result.usage and self._llm_repo:
            duration_ms = int((time.time() - start_time) * 1000)
            try:
                await self._persist_llm_call(
                    request_id=request_id,
                    profile_name=profile_name,
                    model_name=model_name,
                    method=method,
                    result=result,
                    kwargs=kwargs,
                    duration_ms=duration_ms,
                )
            except Exception as exc:  # noqa: BLE001
                # 兜底（_persist_llm_call 内部已 try/except；此处防止传播异常）
                self.logger.warning(f"两账落库包装失败: {exc}")
        self._record_request_history(
            request_id=request_id,
            client_type=profile_name,
            result=result,
            kwargs=kwargs,
            start_time=start_time,
        )

    @staticmethod
    def _build_request_params(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """从调用 kwargs 提取请求参数快照（两账落库与请求历史共用）。"""
        request_params = {
            "messages": kwargs.get("messages"),
            "temperature": kwargs.get("temperature"),
            "max_tokens": kwargs.get("max_tokens"),
            "tools": kwargs.get("tools"),
        }
        if request_params["messages"] is None and kwargs.get("request") is not None:
            # 中立 payload 契约路径：消息以 GenerateRequest 承载
            # system 不在 messages 里，单独快照，否则提示词预览缺主提示词
            request_params["system"] = kwargs["request"].system
            request_params["messages"] = [m.model_dump() for m in kwargs["request"].messages]
            request_params["tools"] = [t.model_dump() for t in kwargs["request"].tools] or None
        return {k: v for k, v in request_params.items() if v is not None}

    async def _persist_llm_call(
        self,
        *,
        request_id: str,
        profile_name: str,
        model_name: str,
        method: str,
        result: LLMResponse,
        kwargs: Dict[str, Any],
        duration_ms: int,
    ) -> None:
        """一次成功调用的两账同事务落库（``llm_usage`` + ``llm_requests`` 原子写入）。

        ``LLMRepo`` 注入且结果带 usage 时生效；聚合账与请求明细共享同一
        ``request_id``，经 observation（``record_usage`` 携带明细载荷）在单个
        SQLite 事务内写入，第二步失败整体回滚。费用口径统一走
        ``observation.calculate_cost``（请求历史同口径）；任何写入失败只记
        warning，绝不阻断 LLM 调用链。
        """
        try:
            usage = result.usage or {}
            cost = 0.0
            if usage:
                cost_info = calculate_cost(
                    self._model_prices,
                    result.model or model_name,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )
                cost = float(cost_info.get("cost", 0.0))
            provider_name = "unknown"
            if model_name in self._models:
                _, provider_name = self._models[model_name]
            # 请求明细行与 _record_request_history 的 RequestRecord 同源同构
            # （request_params 构造共用）；先于历史链路落库，其 INSERT OR
            # IGNORE 对同一 request_id 幂等
            request_row = LLMRequestInsert(
                request_id=request_id,
                timestamp_ms=int(time.time() * 1000),
                client_type=profile_name,
                model_name=result.model or model_name,
                request_params_json=json.dumps(self._build_request_params(kwargs), ensure_ascii=False, default=str),
                response_content=result.content,
                reasoning_content=result.reasoning_content,
                tool_calls_json=json.dumps(result.tool_calls or [], ensure_ascii=False, default=str),
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                total_tokens=int(usage.get("total_tokens", 0)),
                cache_hit_tokens=int(usage.get("cache_hit_tokens", 0)),
                cache_miss_tokens=int(usage.get("cache_miss_tokens", 0)),
                cost=cost,
                success=result.success,
                error=result.error,
                latency_ms=duration_ms,
            )
            # 落库统一走 observation.record_usage（两表唯一写入点）；携带明细
            # 载荷即两账同事务，缓存列与成本入参的处理收敛在 observation 侧
            await record_usage(
                self._llm_repo,
                model_name=result.model or model_name,
                provider_name=str(provider_name),
                request_type=method,
                usage=usage,
                cost=cost,
                duration_ms=duration_ms,
                profile_name=profile_name,
                request=request_row,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"两账落库失败: {exc}")

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

            request_params = self._build_request_params(kwargs)

            usage = None
            if result.usage:
                usage = TokenUsage(
                    prompt_tokens=result.usage.get("prompt_tokens", 0),
                    completion_tokens=result.usage.get("completion_tokens", 0),
                    total_tokens=result.usage.get("total_tokens", 0),
                )

            cost = 0.0
            if usage:
                cost_info = calculate_cost(self._model_prices, model_name, usage.prompt_tokens, usage.completion_tokens)
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
        self._model_prices.clear()
