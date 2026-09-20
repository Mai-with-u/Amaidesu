"""
OpenAI 客户端实现

OpenAI 兼容 API 客户端，唯一的 LLM 实现。
"""

# pyright: reportImportCycles=false, reportIncompatibleMethodOverride=false

import asyncio
import base64
import json
import mimetypes
import os
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Union

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from PIL import Image

from src.modules.llm.client import BaseLLMClient, LLMResponse
from src.modules.llm.clients.openai.arguments import decode_tool_call
from src.modules.llm.clients.openai.compat import build_openai_compatible_client_config
from src.modules.llm.errors import FatalError, LLMError, LLMTimeoutError, RetryableError
from src.modules.llm.interrupt import await_with_timeout_and_interrupt
from src.modules.llm.payload import GenerateRequest, ImagePart, Message, Response, TextPart, ToolCall, ToolSpec, Usage
from src.modules.llm.reasoning import ReasoningParseMode, parse_reasoning
from src.modules.logging import get_logger

# 具体的 SDK 异常集合：Client 翻译处只捕获这些类型并翻译为分类异常
_TRANSLATABLE_SDK_ERRORS = (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError)

# 非 SDK 契约路径的刻意兜底（流式降级 / legacy 失败包装 / 流关闭清理）：
# 兼容端点的失败形态无法枚举，这些位置任何异常都不得逃出 legacy 语义；
# 错误翻译路径不走此处——那里对 SDK 异常类型的捕获是具体的
_LEGACY_FALLBACK_ERRORS = (Exception,)


class OpenAIClient(BaseLLMClient):
    """OpenAI 兼容 API 客户端。

    按 provider 维度共享：构造时只持有连接信息（base_url/api_key/headers/
    retry/timeout），model 由每次调用通过 ``model`` 参数传入。
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        client_config = build_openai_compatible_client_config(config)
        api_key = client_config.api_key or "sk-dummy"
        if not client_config.api_key or client_config.api_key == "your-api-key":
            self.logger.warning("API Key 未配置，请在 config/model.toml 的 [[llm_providers]] 中设置")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=client_config.base_url,
            default_headers=client_config.default_headers or None,
            default_query=client_config.default_query or None,
        )
        # provider 级默认（profile 显式给值时覆盖）
        self.max_tokens = config.get("max_tokens")
        self.temperature = config.get("temperature", 0.2)
        self.logger.info(f"OpenAI 客户端初始化完成 (端点: {client_config.base_url})")

    def _infer_mime_from_bytes(self, data: bytes) -> str:
        """根据图片字节推断 MIME 类型，默认 image/png"""
        try:
            with Image.open(BytesIO(data)) as img:
                format_to_mime = {
                    "PNG": "image/png",
                    "JPEG": "image/jpeg",
                    "JPG": "image/jpeg",
                    "WEBP": "image/webp",
                    "GIF": "image/gif",
                }
                return format_to_mime.get(str(img.format), "image/png")
        except (OSError, ValueError, TypeError, Image.DecompressionBombError):
            return "image/png"

    def _path_or_url_to_data_url(self, image: Union[str, bytes]) -> str:
        """将 URL/本地路径/字节 转为 URL 或 data URL"""
        if isinstance(image, str):
            if image.startswith("http://") or image.startswith("https://"):
                return image
            if os.path.exists(image) and os.path.isfile(image):
                with open(image, "rb") as f:
                    data = f.read()
                mime, _ = mimetypes.guess_type(image)
                if mime is None:
                    mime = self._infer_mime_from_bytes(data)
                b64 = base64.b64encode(data).decode("ascii")
                return f"data:{mime};base64,{b64}"
            return image
        if isinstance(image, (bytes, bytearray)):
            data = bytes(image)
            mime = self._infer_mime_from_bytes(data)
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"
        raise TypeError("image 参数必须为 str(路径或URL) 或 bytes")

    def _build_vision_user_content(
        self, prompt: str, images: Union[str, bytes, List[Union[str, bytes]]]
    ) -> List[Dict[str, Any]]:
        """构建多模态 user content 列表，兼容 OpenAI Chat Completions 识图格式"""
        contents: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        image_list: List[Union[str, bytes]] = images if isinstance(images, list) else [images]
        for img in image_list:
            contents.append({"type": "image_url", "image_url": {"url": self._path_or_url_to_data_url(img)}})
        return contents

    @staticmethod
    def _normalize_tool_definitions(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把内部扁平工具定义规范化为 OpenAI 协议形态。

        项目内工具定义统一用扁平结构 {name, description, parameters}；
        OpenAI 协议要求 {"type": "function", "function": {...}} 包装。
        部分严格端点会拒绝扁平格式（400: missing field `type`），
        已带 type 字段的定义原样保留。
        """
        normalized: List[Dict[str, Any]] = []
        for tool in tools:
            if "type" in tool:
                normalized.append(tool)
            else:
                normalized.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.get("name", ""),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                        },
                    }
                )
        return normalized

    # === 中立 payload 契约（Engine 入口）：payload ↔ OpenAI 协议形状的双向翻译 ===

    @staticmethod
    def _translate_sdk_error(exc: Exception) -> LLMError:
        """把 OpenAI SDK 异常翻译为错误分类异常（分类产出只在 Client，Engine 只消费）。

        - 超时 → LLMTimeoutError（切下一个模型）
        - 限流 429 / 服务端 5xx / 网络连接失败 → RetryableError（有上限重试）
        - 参数错误 400 / 鉴权失败 401 等其余 HTTP 状态 → FatalError（不重试）
        """
        if isinstance(exc, APITimeoutError):
            return LLMTimeoutError("LLM 请求超时", original=exc)
        if isinstance(exc, RateLimitError):
            return RetryableError("请求过于频繁（429），稍后重试", original=exc)
        if isinstance(exc, APIConnectionError):
            return RetryableError("网络连接失败，请检查网络或端点可达性", original=exc)
        if isinstance(exc, APIStatusError):
            status = exc.status_code
            if status == 429 or status >= 500:
                return RetryableError(f"服务端临时性失败（HTTP {status}）", original=exc)
            return FatalError(f"请求被服务端拒绝（HTTP {status}），重试无意义", original=exc)
        # 防御：调用方应传入 _TRANSLATABLE_SDK_ERRORS 内的类型
        return FatalError(f"未识别的 SDK 异常: {type(exc).__name__}", original=exc)

    @staticmethod
    def _ensure_not_empty(result: LLMResponse) -> None:
        """空响应（无内容且无工具调用）视为临时性失败，归入 Retryable 由 Engine 重试。"""
        if not result.content and not result.tool_calls:
            raise RetryableError("响应内容为空（可能是临时性问题）")

    @staticmethod
    def _part_to_openai(part: Any) -> Dict[str, Any]:
        """单个中立片段 → OpenAI content 片段（文本直出，图像转 image_url）

        裸字符串是中立契约里文本的简写形式（``Part`` 联合类型），必须与
        ``TextPart`` 等价处理——Engine 归一化文本消息时产出的正是这种形态。
        """
        if isinstance(part, str):
            return {"type": "text", "text": part}
        if isinstance(part, TextPart) or (isinstance(part, dict) and part.get("type") == "text"):
            text = part.text if isinstance(part, TextPart) else part.get("text", "")
            return {"type": "text", "text": text}
        if isinstance(part, ImagePart):
            return {"type": "image_url", "image_url": {"url": part.image}}
        raise TypeError(f"未知的消息片段类型: {type(part).__name__}")

    @classmethod
    def _message_to_openai(cls, message: Message) -> Dict[str, Any]:
        """中立 Message → OpenAI 消息 dict（纯文本折叠为字符串 content）

        assistant 既往发起的调用还原为 ``tool_calls``（arguments 回到协议要求
        的 JSON 字符串形态），tool 观察还原 ``tool_call_id``——喂回多轮工具
        循环时这两项是协议合法性前提，缺则服务端拒绝整个消息序列。
        """
        contents = [cls._part_to_openai(p) for p in message.parts]
        if contents and all(c["type"] == "text" for c in contents):
            content: Any = "".join(c["text"] for c in contents)
        else:
            content = contents
        result: Dict[str, Any] = {"role": message.role, "content": content}
        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.raw_arguments
                        if call.raw_arguments is not None
                        else json.dumps(call.arguments, ensure_ascii=False, default=str),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            result["tool_call_id"] = message.tool_call_id
        return result

    @classmethod
    def _request_to_openai_messages(cls, request: GenerateRequest) -> List[Dict[str, Any]]:
        """GenerateRequest → OpenAI messages 列表（system 置于首条）"""
        messages: List[Dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(cls._message_to_openai(m) for m in request.messages)
        return messages

    @staticmethod
    def _tool_spec_to_openai(spec: ToolSpec) -> Dict[str, Any]:
        """中立 ToolSpec → 项目内扁平工具定义（chat 内再做 OpenAI 协议包装）"""
        return {"name": spec.name, "description": spec.description, "parameters": spec.parameters}

    @staticmethod
    def _extract_usage(vendor_usage: Any) -> Optional[Dict[str, int]]:
        """厂商响应 usage → 遗留 usage dict（缓存字段归一化在此完成）。

        缓存上报两种风格都收：
        - OpenAI 风格 ``prompt_tokens_details.cached_tokens`` → ``cache_hit_tokens``（miss 无对应字段，视为未上报）
        - DeepSeek 风格 ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens`` 同名直取
        未上报的字段不进 dict（下游转 payload.Usage 时映射为 None = 未上报）；
        响应对象属性缺失一律用 getattr 兜底，兼容 SDK 模型与测试桩。
        """
        if vendor_usage is None:
            return None

        def _get(name: str, default: Any = None) -> Any:
            if isinstance(vendor_usage, dict):
                return vendor_usage.get(name, default)
            return getattr(vendor_usage, name, default)

        usage: Dict[str, int] = {
            "prompt_tokens": int(_get("prompt_tokens", 0) or 0),
            "completion_tokens": int(_get("completion_tokens", 0) or 0),
            "total_tokens": int(_get("total_tokens", 0) or 0),
        }
        details = _get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else getattr(details, "cached_tokens", None)
        if cached is not None:
            usage["cache_hit_tokens"] = int(cached)
        ds_hit = _get("prompt_cache_hit_tokens")
        if ds_hit is not None:
            usage["cache_hit_tokens"] = int(ds_hit)
        ds_miss = _get("prompt_cache_miss_tokens")
        if ds_miss is not None:
            usage["cache_miss_tokens"] = int(ds_miss)
        return usage

    @staticmethod
    def _usage_to_payload(usage: Optional[Dict[str, int]]) -> Optional[Usage]:
        """遗留 usage dict → 中立 Usage（缓存键缺省视为未上报）"""
        if usage is None:
            return None
        return Usage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cache_hit_tokens=usage.get("cache_hit_tokens"),
            cache_miss_tokens=usage.get("cache_miss_tokens"),
        )

    @classmethod
    def _to_payload_response(cls, result: LLMResponse) -> Response:
        """遗留 LLMResponse → 中立 payload.Response（含 tool_calls 形状转换）"""
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                arguments=tc.get("function", {}).get("arguments", {}),
                raw_arguments=tc.get("raw_arguments"),
                arguments_error=tc.get("arguments_error"),
            )
            for tc in (result.tool_calls or [])
            if isinstance(tc, dict)
        ]
        return Response(
            success=result.success,
            content=result.content,
            tool_calls=tool_calls,
            finish_reason=result.finish_reason,
            usage=cls._usage_to_payload(result.usage),
            model=result.model,
            reasoning_content=result.reasoning_content,
            error=result.error,
            request_id=result.request_id,
        )

    async def generate(
        self,
        request: GenerateRequest,
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        on_delta: Optional[Callable[[str, str], None]] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
    ) -> Response:
        """中立契约聊天请求：payload → OpenAI 协议翻译后复用既有 chat 能力。

        生成参数以 Engine 传入的 profile 档位优先，请求内字段兜底。
        """
        tools = [self._tool_spec_to_openai(t) for t in request.tools] or None
        result = await self._chat_impl(
            self._request_to_openai_messages(request),
            model=model,
            temperature=temperature if temperature is not None else request.temperature,
            max_tokens=max_tokens if max_tokens is not None else request.max_tokens,
            tools=tools,
            interrupt_flag=interrupt_flag,
            on_delta=on_delta,
            omit_output_token_limit=request.omit_output_token_limit,
            strict_tool_arguments=request.strict_tool_arguments,
        )
        return self._to_payload_response(result)

    async def generate_vision(
        self,
        request: GenerateRequest,
        images: List[Union[str, bytes]],
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
    ) -> Response:
        """中立契约视觉请求：payload → OpenAI 协议翻译后复用既有 vision 能力。

        走抛分类异常的实现（``_vision_impl``），错误分类由 Engine 表驱动消费。
        """
        result = await self._vision_impl(
            self._request_to_openai_messages(request),
            images,
            model=model,
            temperature=temperature if temperature is not None else request.temperature,
            max_tokens=max_tokens if max_tokens is not None else request.max_tokens,
        )
        return self._to_payload_response(result)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
        on_delta: Optional[Callable[[str, str], None]] = None,
    ) -> LLMResponse:
        """聊天调用（legacy 包装）：任何失败都折叠为 ``success=False`` 响应。

        中断（CancelledError）原样传播语义在此折叠为失败响应是 legacy 契约；
        错误分类消费走 ``_chat_impl``（payload 契约路径）。
        """
        try:
            return await self._chat_impl(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                interrupt_flag=interrupt_flag,
                on_delta=on_delta,
            )
        except asyncio.CancelledError as e:
            error_msg = f"LLM 请求超时或被中断: {e}"
            self.logger.error(error_msg)
            return LLMResponse(success=False, content=None, error=error_msg)
        except LLMError as e:
            error_msg = f"LLM 请求失败: {e}"
            self.logger.error(error_msg)
            return LLMResponse(success=False, content=None, error=error_msg)

    async def _chat_impl(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
        on_delta: Optional[Callable[[str, str], None]] = None,
        omit_output_token_limit: bool = False,
        strict_tool_arguments: bool = False,
    ) -> LLMResponse:
        """聊天调用实现：SDK 异常翻译为错误分类异常后抛出（不自行重试）。

        on_delta 非 None 时走流式传输：SSE 逐帧接收、边收边回调，流结束后
        组装完整 LLMResponse 返回（传输层流式、语义层整段）。
        流式请求建立失败时自动降级为非流式一次性调用（回调不触发）。
        """
        if on_delta is not None:
            try:
                return await self._chat_streaming(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    interrupt_flag=interrupt_flag,
                    on_delta=on_delta,
                    omit_output_token_limit=omit_output_token_limit,
                    strict_tool_arguments=strict_tool_arguments,
                )
            except asyncio.CancelledError:
                raise
            except _LEGACY_FALLBACK_ERRORS as e:
                self.logger.warning(f"流式请求失败，降级为非流式: {e}")
                # 落到下方非流式路径

        try:
            request_params: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature or self.temperature,
            }
            if tools:
                request_params["tools"] = self._normalize_tool_definitions(tools)
                request_params["tool_choice"] = "auto"
            if not omit_output_token_limit and max_tokens:
                request_params["max_tokens"] = max_tokens
            elif not omit_output_token_limit and self.max_tokens:
                request_params["max_tokens"] = self.max_tokens
            self.logger.debug(f"发送 LLM 请求: {request_params}")
            task = asyncio.create_task(self.client.chat.completions.create(**request_params))
            response = await await_with_timeout_and_interrupt(
                task,
                timeout=float(self.config.get("timeout", 60)),
                interrupt_flag=interrupt_flag,
            )
            message = response.choices[0].message
            content, reasoning_content = parse_reasoning(
                message.content or "",
                getattr(message, "reasoning_content", None),
                ReasoningParseMode(self.config.get("reasoning_parse_mode", "auto")),
            )
            usage = self._extract_usage(response.usage)
            result = LLMResponse(
                success=True,
                content=content,
                model=response.model,
                usage=usage,
                reasoning_content=reasoning_content,
                finish_reason=getattr(response.choices[0], "finish_reason", None),
            )
            if message.tool_calls:
                result.tool_calls = []
                for tool_call in message.tool_calls:
                    result.tool_calls.append(
                        decode_tool_call(
                            tool_call.id,
                            tool_call.function.name,
                            tool_call.function.arguments,
                            strict=strict_tool_arguments,
                        )
                    )
            self._ensure_not_empty(result)
            return result
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as e:
            # 请求级硬超时（await_with_timeout_and_interrupt）→ Timeout 分类
            raise LLMTimeoutError("LLM 请求超时", original=e) from e
        except _TRANSLATABLE_SDK_ERRORS as e:
            raise self._translate_sdk_error(e) from e

    async def _chat_streaming(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        tools: Optional[List[Dict[str, Any]]],
        interrupt_flag: Optional[asyncio.Event],
        on_delta: Callable[[str, str], None],
        omit_output_token_limit: bool = False,
        strict_tool_arguments: bool = False,
    ) -> LLMResponse:
        """流式传输路径：SSE 逐帧接收，reasoning/content 增量实时回调，最终组装完整响应。

        增量三分：reasoning 外发回调；content 外发回调（调用方自行取舍）；
        tool call arguments 碎片只在客户端拼接成完整 JSON，不外发。
        拼接语义与非流式一致：严格调用保留 JSON 解析错误，普通调用沿用语法修复。
        """
        request_params: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "stream": True,
            # usage 随末帧返回；不支持该参数的兼容端点会在 create 阶段抛错，
            # 由调用方（chat）降级为非流式路径兜底
            "stream_options": {"include_usage": True},
        }
        if tools:
            request_params["tools"] = self._normalize_tool_definitions(tools)
            request_params["tool_choice"] = "auto"
        if not omit_output_token_limit and max_tokens:
            request_params["max_tokens"] = max_tokens
        elif not omit_output_token_limit and self.max_tokens:
            request_params["max_tokens"] = self.max_tokens

        stream = await self.client.chat.completions.create(**request_params)
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        # index -> {"id": str, "name": str, "arguments": list[str]}（arguments 碎片按序拼接）
        tool_states: Dict[int, Dict[str, Any]] = {}
        usage: Optional[Dict[str, int]] = None
        model_name: Optional[str] = None
        finish_reason: Optional[str] = None
        try:
            async for chunk in stream:
                if interrupt_flag is not None and interrupt_flag.is_set():
                    # 中断不构成完整响应，不能把已经接收的部分参数交给工具执行器。
                    raise asyncio.CancelledError
                if getattr(chunk, "usage", None) is not None:
                    usage = self._extract_usage(chunk.usage)
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                if getattr(choices[0], "finish_reason", None) is not None:
                    finish_reason = choices[0].finish_reason
                model_name = getattr(chunk, "model", None) or model_name
                delta = choices[0].delta
                # 原生 reasoning 字段（DeepSeek R1 系）；<think> 内嵌型不走此路径，
                # 其思考随 content 外发、最终由 parse_reasoning 统一切分
                reasoning_piece = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning_piece:
                    reasoning_parts.append(reasoning_piece)
                    on_delta("reasoning", reasoning_piece)
                content_piece = getattr(delta, "content", None)
                if content_piece:
                    content_parts.append(content_piece)
                    on_delta("content", content_piece)
                for tc_delta in getattr(delta, "tool_calls", None) or []:
                    state = tool_states.setdefault(tc_delta.index, {"id": "", "name": "", "arguments": []})
                    if tc_delta.id:
                        state["id"] = tc_delta.id
                    fn = getattr(tc_delta, "function", None)
                    if fn is not None and fn.name:
                        state["name"] = fn.name
                    if fn is not None and fn.arguments:
                        state["arguments"].append(fn.arguments)
        finally:
            try:
                await stream.aclose()
            except _LEGACY_FALLBACK_ERRORS as e:
                self.logger.debug(f"关闭流失败（已忽略）: {e}")

        raw_content = "".join(content_parts)
        native_reasoning = "".join(reasoning_parts) or None
        content, reasoning_content = parse_reasoning(
            raw_content,
            native_reasoning,
            ReasoningParseMode(self.config.get("reasoning_parse_mode", "auto")),
        )
        result = LLMResponse(
            success=True,
            content=content,
            model=model_name or model,
            usage=usage,
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
        )
        if tool_states:
            result.tool_calls = []
            for index in sorted(tool_states):
                state = tool_states[index]
                raw_arguments = "".join(state["arguments"])
                result.tool_calls.append(
                    decode_tool_call(state["id"], state["name"], raw_arguments, strict=strict_tool_arguments)
                )
        self._ensure_not_empty(result)
        return result

    async def vision(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """视觉理解调用（legacy 包装）：任何失败都折叠为 ``success=False`` 响应。"""
        try:
            return await self._vision_impl(
                messages,
                images,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except _LEGACY_FALLBACK_ERRORS as e:
            error_msg = f"VLM 请求失败: {e}"
            self.logger.error(error_msg)
            return LLMResponse(success=False, content=None, error=error_msg)

    async def _vision_impl(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """视觉理解实现：SDK 异常翻译为错误分类异常后抛出（不自行重试）。"""
        try:
            user_content = self._build_vision_user_content(messages[-1]["content"], images)
            vision_messages = messages[:-1] + [{"role": "user", "content": user_content}]
            request_params: Dict[str, Any] = {
                "model": model,
                "messages": vision_messages,
                "temperature": temperature or self.temperature,
            }
            if max_tokens:
                request_params["max_tokens"] = max_tokens
            elif self.max_tokens:
                request_params["max_tokens"] = self.max_tokens
            response = await self.client.chat.completions.create(**request_params)
            usage = self._extract_usage(response.usage)
            result = LLMResponse(
                success=True,
                content=response.choices[0].message.content,
                model=response.model,
                usage=usage,
                reasoning_content=getattr(response.choices[0].message, "reasoning_content", None),
            )
            self._ensure_not_empty(result)
            return result
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as e:
            # 请求级硬超时（await_with_timeout_and_interrupt）→ Timeout 分类
            raise LLMTimeoutError("LLM 请求超时", original=e) from e
        except _TRANSLATABLE_SDK_ERRORS as e:
            raise self._translate_sdk_error(e) from e

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "model": self.config.get("model"),
            "base_url": self.config.get("base_url"),
        }
