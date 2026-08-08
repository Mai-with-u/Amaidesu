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
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from json_repair import repair_json
from openai import AsyncOpenAI
from PIL import Image

from src.modules.llm.clients.base import BaseLLMClient, register_client
from src.modules.llm.clients.interrupt import await_with_timeout_and_interrupt
from src.modules.llm.clients.openai_compat import build_openai_compatible_client_config
from src.modules.llm.clients.reasoning import ReasoningParseMode, parse_reasoning
from src.modules.llm.manager import LLMResponse
from src.modules.logging import get_logger


class OpenAIClient(BaseLLMClient):
    """OpenAI 兼容 API 客户端。"""

    def __init__(self, config: Dict[str, Any]):
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
        self.model = config.get("model", "gpt-4o-mini")
        self.max_tokens = config.get("max_tokens")
        self.temperature = config.get("temperature", 0.2)
        self.logger.info(f"OpenAI 客户端初始化完成 (模型: {self.model})")

    @classmethod
    def client_type_name(cls) -> str:
        return "openai"

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
        except Exception:
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

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
    ) -> LLMResponse:
        """聊天调用。"""
        try:
            request_params: Dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature or self.temperature,
            }
            if tools:
                request_params["tools"] = tools
                request_params["tool_choice"] = "auto"
            if max_tokens:
                request_params["max_tokens"] = max_tokens
            elif self.max_tokens:
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
            usage = None
            if response.usage is not None:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
            result = LLMResponse(
                success=True, content=content, model=response.model, usage=usage, reasoning_content=reasoning_content
            )
            if message.tool_calls:
                result.tool_calls = []
                for tool_call in message.tool_calls:
                    arguments = tool_call.function.arguments
                    try:
                        parsed_arguments = json.loads(arguments)
                    except (json.JSONDecodeError, TypeError):
                        parsed_arguments = repair_json(arguments, return_objects=True)
                    result.tool_calls.append(
                        {
                            "id": tool_call.id,
                            "type": tool_call.type,
                            "function": {"name": tool_call.function.name, "arguments": parsed_arguments},
                        }
                    )
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError) as e:
            error_msg = f"LLM 请求超时或被中断: {e}"
            self.logger.error(error_msg)
            return LLMResponse(success=False, content=None, error=error_msg)
        except Exception as e:
            error_msg = f"LLM 请求失败: {str(e)}"
            self.logger.error(error_msg)
            return LLMResponse(success=False, content=None, error=error_msg)

    async def stream_chat(  # type: ignore[override]
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """流式聊天。"""
        request_params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "stream": True,
        }
        if max_tokens:
            request_params["max_tokens"] = max_tokens
        elif self.max_tokens:
            request_params["max_tokens"] = self.max_tokens
        stream = None
        try:
            stream = await self.client.chat.completions.create(**request_params)
            async for chunk in stream:
                if (stop_event is not None and stop_event.is_set()) or (
                    interrupt_flag is not None and interrupt_flag.is_set()
                ):
                    break
                try:
                    text_piece = getattr(chunk.choices[0].delta, "content", None)
                    if text_piece:
                        yield text_piece
                except Exception:
                    yield ""
        except Exception as e:
            self.logger.error(f"流式 LLM 请求失败: {e}")
        finally:
            if stream is not None:
                try:
                    await stream.aclose()
                except Exception as e:
                    self.logger.debug(f"关闭流失败（已忽略）: {e}")

    async def vision(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """视觉理解调用。"""
        try:
            user_content = self._build_vision_user_content(messages[-1]["content"], images)
            vision_messages = messages[:-1] + [{"role": "user", "content": user_content}]
            request_params: Dict[str, Any] = {
                "model": self.model,
                "messages": vision_messages,
                "temperature": temperature or self.temperature,
            }
            if max_tokens:
                request_params["max_tokens"] = max_tokens
            elif self.max_tokens:
                request_params["max_tokens"] = self.max_tokens
            response = await self.client.chat.completions.create(**request_params)
            usage = None
            if response.usage is not None:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
            return LLMResponse(
                success=True,
                content=response.choices[0].message.content,
                model=response.model,
                usage=usage,
                reasoning_content=getattr(response.choices[0].message, "reasoning_content", None),
            )
        except Exception as e:
            error_msg = f"VLM 请求失败: {str(e)}"
            self.logger.error(error_msg)
            return LLMResponse(success=False, content=None, error=error_msg)

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "model": self.config.get("model"),
            "base_url": self.config.get("base_url"),
        }


register_client("openai", OpenAIClient)
