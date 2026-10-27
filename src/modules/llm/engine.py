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
- **厂商无关**：本模块不 import 任何厂商适配端（``clients/<vendor>/``），
  provider 客户端构造与能力解析一律经 ``clients`` 包的调度表完成。

profile 解析与 provider 池/模型索引的装配在 :mod:`src.modules.llm.bootstrap`。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel

from src.modules.diagnostics.writer import DiagnosticsWriter
from src.modules.llm.bootstrap import (
    ProfileNames,
    _ResolvedModel,
    _ResolvedProfile,
    build_resolved_profile,
    index_models,
    register_providers,
    resolve_profile_name,
    validate_profile_binding,
)
from src.modules.llm.call_observer import CallObserver
from src.modules.llm.clients import resolve_client_method
from src.modules.llm.errors import (
    FatalError,
    LLMError,
    LLMInterruptedError,
    LLMTimeoutError,
    RetryableError,
    UnsendableRequestError,
)
from src.modules.llm.interrupt import guarded_call
from src.modules.llm.payload import (
    ContextPart,
    GenerateRequest,
    ImagePart,
    ImageRefPart,
    Message,
    Reasoning,
    Response,
    TextPart,
    ToolCall,
    ToolSpec,
)
from src.modules.logging import get_logger
from src.modules.storage.repos import LLMRepo
from src.modules.vision.image_store import get_image_store

__all__ = ["LLMManager", "RetryConfig", "inline_payload_spans"]


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


# === 契约归一化（Engine 的固定职责：消费方输入 → payload → Client）===


def _content_to_parts(content: Any) -> List[Any]:
    """OpenAI 风格 content（str / 分段列表）→ 中立 parts 列表

    会话历史不允许内联图像（``image_url`` 分段）：base64 本体属
    VisionImageStore，历史中只允许 ``image_ref`` 引用分段——历史被压缩器/
    预算等多个消费者共享，任何整体序列化路径都会把内联 base64 当文本重发
    （场次22-24 实锤单笔 86 万 tokens）。图要进对话，须经观察组装层放引用。
    """
    if content is None:
        # 协议允许 assistant 发起 tool_calls 时 content 为 null（无伴随文本）
        return []
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
        elif isinstance(piece, dict) and piece.get("type") == "image_ref":
            ref = str(piece.get("ref") or "")
            if not ref:
                raise TypeError("image_ref 分段缺少引用句柄（ref 为空）")
            parts.append(ImageRefPart(ref=ref))
        elif isinstance(piece, dict) and piece.get("type") == "image_url":
            raise TypeError("对话消息不允许内联图像（image_url 分段）；图须经 VisionImageStore 以 image_ref 引用进历史")
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
        raw_arguments = None
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except (json.JSONDecodeError, TypeError) as exc:
                # 失败调用的原始参数需要原样喂回，不能用空对象掩盖模型上一次输出的问题。
                get_logger("LLMProtocol").debug(f"历史工具参数保留原文供模型自纠：{exc}", exc=True)
                raw_arguments = arguments
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCall(
                id=str(item.get("id", "") or ""),
                name=str(function.get("name", "") or ""),
                arguments=arguments,
                raw_arguments=raw_arguments,
            )
        )
    return calls


def _context_parts_from_protocol(raw: Any) -> List[ContextPart]:
    """system 消息附带的 ``context_parts``（片段归属标注）→ 中立片段；形状不对的条目跳过。

    标注只服务上下文计量，写错不能让一次真实调用失败。
    """
    parts: List[ContextPart] = []
    if not isinstance(raw, list):
        return parts
    for item in raw:
        if not isinstance(item, dict):
            continue
        section, name, text = item.get("section"), item.get("name"), item.get("text")
        if isinstance(section, str) and isinstance(name, str) and isinstance(text, str) and text:
            parts.append(ContextPart(section=section, name=name, text=text))
    return parts


def _reasoning_from_protocol(raw: Any) -> Optional[Reasoning]:
    """assistant dict 消息的 ``reasoning`` 键 → 中立 Reasoning。

    Agent 把上一步响应的 ``reasoning_echo`` 原样放回历史（对象或其 dict 形态）；
    缺失或形状不对按没有思考处理——回传只影响效果，不值得为它让整个请求失败。
    """
    if raw is None:
        return None
    if isinstance(raw, Reasoning):
        return raw
    try:
        return Reasoning.model_validate(raw)
    except ValueError as exc:
        get_logger("LLMProtocol").warning(f"历史思考形状不对，本条不回传：{exc}", exc=True)
        return None


def _request_for_model(request: GenerateRequest, model: _ResolvedModel) -> GenerateRequest:
    """按这次要调用的模型筛掉不该发的思考。

    模型关着回传开关时全部不发；开着时只发它自己产出的思考，换到备用模型时
    别的模型的思考不发——对方认不出这段思考，可能报错也可能被带偏。不改调用方
    的请求对象：故障切换时下一个模型拿到的仍是完整历史，重新按自己筛一遍。
    """
    if not any(message.reasoning is not None for message in request.messages):
        return request
    messages = [
        message
        if message.reasoning is None or (model.reasoning_echo and message.reasoning.model == model.model_name)
        else message.model_copy(update={"reasoning": None})
        for message in request.messages
    ]
    return request.model_copy(update={"messages": messages})


def _echo_reasoning(response: Response, model: _ResolvedModel) -> Optional[Reasoning]:
    """开着回传开关的模型产出思考时，打包成可回传的思考交给调用方。

    开关关闭时不附带，调用方的历史里就不会出现思考，上下文预算也不计它；
    格式未知（视觉、音频这类单次调用路径不记录格式）同样不附带。
    """
    if not model.reasoning_echo or not response.reasoning_content or response.reasoning_format is None:
        return None
    return Reasoning(text=response.reasoning_content, format=response.reasoning_format, model=model.model_name)


def _optional_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _normalize_generate_input(
    input: Any,
    *,
    system: Optional[str],
    tools: Optional[List[Any]],
    temperature: Optional[float],
) -> GenerateRequest:
    """消费方输入（str 或 OpenAI 风格 dict 列表或 Message 列表）→ 中立请求。

    dict 列表里的 system 消息折叠进请求的独立 system 参数（各厂商对
    system 的承载位置不同，由适配端翻译）。system 消息的 ``context_parts`` 与
    工具 dict 的 ``context_section`` / ``result_section`` 是上下文计量的归属
    标注，进入中立请求的同名字段，不发给模型。
    """
    messages: List[Message] = []
    system_texts: List[str] = []
    system_parts: List[ContextPart] = []
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
                    system_parts.extend(_context_parts_from_protocol(item.get("context_parts")))
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
                        reasoning=_reasoning_from_protocol(item.get("reasoning")) if role == "assistant" else None,
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
                    context_section=_optional_str(tool.get("context_section")),
                    result_section=_optional_str(tool.get("result_section")),
                )
            )
        else:
            raise TypeError(f"不支持的 tools 元素类型: {type(tool).__name__}")

    return GenerateRequest(
        messages=messages,
        system=merged_system,
        tools=tool_specs,
        temperature=temperature,
        system_parts=system_parts,
    )


# 装配守卫阈值：请求文本字段超过该长度时检查里面是否夹带二进制。注水后的 data URL
# 图像走 ImagePart 专用通道不在此列；文本字段里混进 base64 = 内联大载荷泄漏
# （观察占位符契约被绕过），宁可拒发也不静默烧 token
_MAX_INLINE_TEXT_CHARS = 200_000
# 超长字段里出现这么长一串连续的 base64 字符才算泄漏。正常的长文本（游戏回执 JSON、资料原文）
# 到处是引号、逗号、空格和中文，连不成这么长；一张截图的 base64 是几十万个连续字符。
# 只按长度判会误伤：周围 40 台机器的检视结果有 22 万字，被拒发后游戏 Agent 每步都撞同一条回执
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{10000,}")


def _hydrate_image_refs(request: GenerateRequest) -> None:
    """把消息中的图像引用按新鲜度窗口注水为内联 ImagePart。

    Agent 只把图挂在请求尾部（最后一条 assistant 之后），紧随回执的那一次
    请求看一次。窗口按位置计：引用所属消息之后的 assistant 消息数不足
    ``VisionImageStore.visible_turns`` 才注水；更早位置的引用说明有图残留在
    历史里，降级为提示文本，不让残留图每次请求重传（场次22-24 的 8.4 万
    token 轮次即残留图重发）。逐出同样降级，模型被明确告知图没了。
    替换经 ``model_copy`` 重建消息，不改写入参对象（归一化可能原样引用
    调用方手中的 Message）。
    """
    store = get_image_store()
    messages = request.messages
    for i, msg in enumerate(messages):
        if not any(isinstance(part, ImageRefPart) for part in msg.parts):
            continue
        turns_after = sum(1 for later in messages[i + 1 :] if later.role == "assistant")
        new_parts: List[Any] = []
        for part in msg.parts:
            if not isinstance(part, ImageRefPart):
                new_parts.append(part)
                continue
            data = store.get(part.ref) if turns_after < store.visible_turns else None
            if data is not None:
                new_parts.append(ImagePart(image=data))
            else:
                reason = "已被逐出" if turns_after < store.visible_turns else f"距今已 {turns_after} 轮"
                new_parts.append(TextPart(text=f"[截图不可用（{reason}），需要时重新截图]"))
        messages[i] = msg.model_copy(update={"parts": new_parts})


def inline_payload_spans(text: str) -> List[Tuple[int, int]]:
    """装配守卫会因这段文本拒发时，返回其中每串连续 base64 的起止位置；发得出去就返回空列表。

    调用方据此把请求里的二进制换成说明再发，判定规则与守卫同一份。
    """
    if len(text) <= _MAX_INLINE_TEXT_CHARS:
        return []
    return [match.span() for match in _BASE64_RUN.finditer(text)]


def _guard_inline_payload(request: GenerateRequest) -> None:
    """装配守卫：请求文本字段超长且夹带长串 base64 即硬错（宁拒发不静默烧）。

    只扫文本承载字段（system / 文本 part）——注水后的 ImagePart.image 是
    base64 唯一合法形态且经窗口限额；文本里出现长串 base64 说明某个工具
    回执把二进制当文本带进了对话。正常文本再长也放行：它是调用方真要的内容，
    多长由各自的上下文预算与整理机制管，拒发只会让同一条回执每步都失败。
    拒发抛 :class:`UnsendableRequestError`，调用方据此区分"内容发不出去"和暂时性失败。
    """
    candidates: List[str] = []
    if request.system:
        candidates.append(request.system)
    for msg in request.messages:
        for part in msg.parts:
            if isinstance(part, str):
                candidates.append(part)
            elif isinstance(part, TextPart):
                candidates.append(part.text)
    for text in candidates:
        spans = inline_payload_spans(text)
        if spans:
            start, end = spans[0]
            raise UnsendableRequestError(
                f"装配守卫：请求文本含 {len(text)} 字符超长字段（上限 {_MAX_INLINE_TEXT_CHARS}），"
                f"其中有 {end - start} 字符连续 base64，疑似内联大载荷泄漏，拒绝发送；"
                "请检查工具回执是否把 base64 当文本进了对话"
            )


class LLMManager:
    """LLM 管理器 - profile 驱动的多模型故障切换

    核心基础设施服务，与 EventBus 同级。

    职责：
    - 按 profile.model_list 调度模型选择（sequential / balance / random）
    - 实现故障切换（当前模型失败切下一个）与慢调用告警
    - 内置重试（仅在同一模型上重试，不跨模型）
    - 观测出库（两账落库 / 诊断正文出库 / 上下文水位快照）委托
      :class:`~src.modules.llm.call_observer.CallObserver`，成功/失败出口各调一次

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

    def __init__(
        self,
        llm_repo: Optional[LLMRepo] = None,
        live_session_resolver: Optional[Callable[[], Optional[int]]] = None,
        diagnostics_writer: Optional[DiagnosticsWriter] = None,
    ) -> None:
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
        # 上下文窗口（{model_identifier: token 总量}）；0 = 未配置（dashboard 隐藏）
        self._model_context_windows: Dict[str, int] = {}
        self._retry_config = RetryConfig()
        # 观测出库器（两账落库 / 诊断正文出库 / 上下文水位快照），依赖全部注入；
        # 模型索引与价格表按引用共享——setup 重建表时原地更新，观测器引用始终有效
        self._observer = CallObserver(
            llm_repo=llm_repo,
            live_session_resolver=live_session_resolver,
            diagnostics_writer=diagnostics_writer,
            model_index=self._models,
            model_prices=self._model_prices,
        )
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
        # 原地清空不改引用：观测器按引用持有价格表，重绑会使其拿到永久空表
        self._model_prices.clear()
        self._model_context_windows = {}

        provider_configs = config.get("llm_providers") or []
        if not provider_configs:
            raise ValueError("LLMManager.setup 收到空 llm_providers（至少需要 1 个 provider）")

        # provider 客户端注册（每个 provider 一个连接）与模型索引由 bootstrap 承担
        self._providers, self._provider_clients = register_providers(provider_configs, self.logger)
        # 模型索引原地更新：观测器按引用持有该表，重建引用会使其拿到陈旧快照
        models_index = index_models(config.get("llm_models") or [], self._providers)
        self._models.clear()
        self._models.update(models_index)

        # 解析 llm_profiles 快照（封闭集合校验：未知用途在装配期硬错）
        profile_configs = config.get("llm_profiles") or {}
        for pname, pcfg in profile_configs.items():
            validate_profile_binding(pname)
            self._profiles[pname] = build_resolved_profile(pname, pcfg, self._models)
            self._profile_call_counts[pname] = 0
            self._model_call_counts[pname] = {}

        # 价格唯一来源 = model.toml [[llm_models]] 的定价字段
        # 价格表按 model_identifier 键入（费用计算用的是请求实际的 API 模型标识）；
        # 同模型索引一样原地更新，保持观测器持有的引用有效
        model_prices = {
            mcfg.get("model_identifier") or mname: {
                "price_in": mcfg.get("price_in", 0.0),
                "price_out": mcfg.get("price_out", 0.0),
                "cache_price_in": mcfg.get("cache_price_in", 0.0),
                "cache": mcfg.get("cache", "none"),
            }
            for mname, (mcfg, _prov) in self._models.items()
            if mcfg.get("price_in", 0.0) > 0 or mcfg.get("price_out", 0.0) > 0
        }
        self._model_prices.clear()
        self._model_prices.update(model_prices)

        # 上下文窗口按 model_identifier 键入（与价格表同口径）；0 仍保留，
        # dashboard 据此判定是否渲染水位（>0 才展示，0 = 未配置 = 隐藏）
        self._model_context_windows = {
            mcfg.get("model_identifier") or mname: int(mcfg.get("context_window", 0) or 0)
            for mname, (mcfg, _prov) in self._models.items()
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
        strict_tool_arguments: bool = False,
        on_delta: Optional[Callable[[str, str], None]] = None,
        interrupt: Optional[asyncio.Event] = None,
    ) -> Response:
        """执行一次 LLM 调用（对外唯一文本入口，返回中立 payload.Response）。

        签名宽是刻意的容错设计：``input`` 接受裸字符串（单轮用户消息）、
        中立 ``Message`` 列表或 OpenAI 风格 dict 列表——Engine 统一归一化
        到 payload 再调度，消费方不必为迁就契约改自己的输入形状。
        生成温度缺省时回退 profile 档位，完整结构化产物可要求严格解析工具参数；
        响应结束原因原样保留，由调用方拒绝未完成的产物。
        """
        profile_name = self._resolve_profile_name(profile)
        request = _normalize_generate_input(input, system=system, tools=tools, temperature=temperature)
        request.strict_tool_arguments = strict_tool_arguments
        # 图像引用先注水（引用→内联，越窗降级），再守卫（拒发内联大载荷）；
        # 都在故障切换之前——结构性问题重试无意义
        _hydrate_image_refs(request)
        _guard_inline_payload(request)
        result = await self._call_with_failover(
            profile_name,
            method="generate",
            request=request,
            on_delta=on_delta,
            interrupt_flag=interrupt,
        )
        return result

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
        request = _normalize_generate_input(prompt, system=system, tools=None, temperature=None)
        result = await self._call_with_failover(
            profile_name,
            method="generate_vision",
            request=request,
            images=images,
            interrupt_flag=interrupt,
        )
        return result

    async def generate_audio(
        self,
        input: Union[str, List[Union[Message, Dict[str, Any]]]],
        *,
        profile: Optional[str] = None,
        voice: str,
        audio_format: str = "wav",
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        interrupt: Optional[asyncio.Event] = None,
    ) -> Response:
        """执行一次音频输出调用（对外唯一音频入口，返回中立 payload.Response）。

        面向带语音输出模态的大模型（OpenAI 兼容线协议 modalities=audio
        形态）：input 是待合成的定稿台词，voice / audio_format
        为厂商词表音色与封装格式；语音文本经 transcript 回填响应的
        content，音频字节经 audio_base64 携带。
        profile 缺省走 tts 档位（须指向带语音输出模态的模型）。
        """
        if not voice:
            raise ValueError("generate_audio 缺少 voice（音色）参数；请在 [tts.omni_tts].voice 配置厂商音色名")
        if profile is None:
            profile_name = ProfileNames.TTS
        else:
            profile_name = self._resolve_profile_name(profile)
        request = _normalize_generate_input(input, system=system, tools=None, temperature=temperature)
        result = await self._call_with_failover(
            profile_name,
            method="generate_audio",
            request=request,
            voice=voice,
            audio_format=audio_format,
            interrupt_flag=interrupt,
        )
        return result

    # === 内部：profile 解析 ===

    def _resolve_profile_name(self, profile: Optional[str]) -> str:
        """把调用方传入的 profile 参数解析为已配置 profile 名（解析规则见 bootstrap 模块）。"""
        return resolve_profile_name(profile, self._profiles, self.logger)

    # === 公开查询：装配期模型信息（Dashboard 等只读消费方使用） ===

    def get_model_context_window(self, model_identifier: str) -> int:
        """按 model_identifier 取上下文窗口 token 总量；未配置返回 0（= 不展示）。"""
        return self._model_context_windows.get(model_identifier, 0)

    def get_model_context_windows(self) -> Dict[str, int]:
        """返回 ``{model_identifier: context_window}`` 浅拷贝（0 值保留，标识"未配置"）。"""
        return dict(self._model_context_windows)

    def get_model_provider(self, model_identifier: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """按 API 模型标识反查所属 provider 的名称与配置；未知标识返回 None。

        Dashboard 用量面板按模型查平台额度时用它找到 base_url 与 Key。多个注册名
        共用同一标识时取注册表中的第一个。
        """
        for model_name, (model_config, provider_name) in self._models.items():
            if (model_config.get("model_identifier") or model_name) == model_identifier:
                return provider_name, self._providers[provider_name][0]
        return None

    def get_profile_context_window(self, profile: Optional[str]) -> int:
        """该 profile 可用的上下文窗口：名单内已配置窗口的模型取最小值；都未配置返回 0。

        上下文预算按它换算。取最小值是因为故障切换可能落到名单里任何一个模型，
        按最大窗口定预算，切到小窗口模型时请求会被拒收。
        """
        try:
            resolved = self._profiles.get(self._resolve_profile_name(profile))
        except ValueError:
            return 0
        if resolved is None:
            return 0
        windows = [self._model_context_windows.get(model.model_identifier, 0) for model in resolved.models]
        configured = [window for window in windows if window > 0]
        return min(configured) if configured else 0

    def is_profile_visual(self, profile: Optional[str]) -> bool:
        """该 profile 当前主模型是否声明视觉能力（``llm_models[].visual``）。

        能力协商出口：观察组装（工具结果图附不附）与视觉工具
        （VLM 调不调）据此分叉。"当前主模型" = selection_strategy 排序后的
        failover 首位，与实际请求的首选一致。解析失败 / 无模型按 False——
        判错方向的代价是多一次 VLM 转述，而不是 agent 拿到看不懂的图。
        """
        try:
            resolved = self._profiles.get(self._resolve_profile_name(profile))
        except ValueError:
            return False
        if resolved is None or not resolved.models:
            return False
        return bool(self._select_models_for_call(resolved)[0].visual)

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
    ) -> Response:
        """按 model_list 顺序逐个尝试；当前模型失败（超时或异常）切下一个。

        Returns:
            Response：首个成功的模型响应；全部失败则返回 success=False 且
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

            # 每个模型各自筛一遍历史思考：开关与产出模型都按这次实际调用的模型判断
            model_kwargs = dict(kwargs)
            if isinstance(model_kwargs.get("request"), GenerateRequest):
                model_kwargs["request"] = _request_for_model(model_kwargs["request"], resolved_model)
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
                **model_kwargs,
            )

            if response is not None:
                # 派生副本回填引擎侧字段（请求历史键 + 模型名归一 + 思考回传），
                # 不改写 Client 返回的响应对象——它可能被客户端复用，改写会污染后续调用
                response = response.model_copy(
                    update={
                        "request_id": request_id,
                        # 响应 model 是上游自报名（网关会回自己的渠道别名），统一
                        # 归一为引用名，下游落账、展示与计价口径才不随上游漂移
                        "model": resolved_model.model_name,
                        "reasoning_echo": _echo_reasoning(response, resolved_model),
                    }
                )
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
        result = Response(
            success=False,
            content=None,
            error=f"全部模型失败 {attempted_models}: {last_error}",
            request_id=request_id,
        )
        await self._observer.record_call(
            request_id=request_id,
            profile_name=profile_name,
            model_name="",
            request_type=method,
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
    ) -> Tuple[Optional[Response], Optional[str]]:
        """等待当前模型完成，调用方取消时回收整个请求和重试任务。"""
        call_kwargs = dict(kwargs)
        call_kwargs["model"] = model_identifier
        # 完整等待模型生成；只让单次温度/思考强度覆盖用途默认值，主动中断仍立即回收连接。
        profile = self._get_profile(profile_name)
        request = call_kwargs.get("request")
        if isinstance(request, GenerateRequest):
            # 最终生成参数在引擎单点定序后写进请求对象：调用方显式参数（generate 入口
            # 归一进 request.temperature）> 请求字段 > profile 档默认值。Client 只信
            # 请求对象字段，不再各自兜底——优先级只写一处，改序不会两边漂移。
            final_temperature = request.temperature if request.temperature is not None else profile.temperature
            final_effort = (
                request.reasoning_effort
                if isinstance(request.reasoning_effort, str) and request.reasoning_effort
                else profile.reasoning_effort
            )
            if final_temperature != request.temperature or final_effort != request.reasoning_effort:
                call_kwargs["request"] = request.model_copy(
                    update={"temperature": final_temperature, "reasoning_effort": final_effort}
                )
        return await guarded_call(
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
            interrupt_flag=call_kwargs.get("interrupt_flag"),
        )

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
    ) -> Tuple[Optional[Response], Optional[str]]:
        """单模型重试循环：按 ``_RETRY_POLICY`` 分类表驱动重试/切换/中止。"""
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
                attempt_elapsed_ms = int((time.time() - attempt_start) * 1000)
                if attempt_elapsed_ms >= slow_threshold_ms:
                    self.logger.warning(
                        f"[LLM 慢调用告警] profile={profile_name} model={model_name} "
                        f"耗时 {attempt_elapsed_ms}ms（阈值 {slow_threshold_ms}ms，不切换）"
                    )
                if response.success:
                    await self._observer.record_call(
                        request_id=request_id,
                        profile_name=profile_name,
                        model_name=model_name,
                        request_type=method,
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
        self._model_context_windows.clear()
