"""
OpenAI 客户端实现

从 src/openai_client/llm_request.py 的 LLMClient 重构而来，适配 LLMClient 接口。
"""

import asyncio
import base64
import mimetypes
from io import BytesIO
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from openai import AsyncOpenAI
from PIL import Image

from src.modules.llm.backends.backends.client_base import LLMClient
from src.modules.llm.manager import LLMResponse


class OpenAIClient(LLMClient):
    """
    OpenAI 兼容 API 客户端

    支持的提供商：
    - OpenAI 官方 API
    - SiliconFlow（包括 DeepSeek、Qwen 等模型）
    - 其他 OpenAI 兼容的 API
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        # 验证必需配置
        api_key: str = config.get("api_key") or "sk-dummy"
        if not api_key or api_key == "your-api-key":
            self.logger.warning("API Key 未配置，请在 config.toml 中设置")
            api_key = "sk-dummy"

        # 初始化 AsyncOpenAI 客户端
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=config.get("base_url"),
        )

        self.model = config.get("model", "gpt-4o-mini")
        self.max_tokens = config.get("max_tokens", 1024)
        self.temperature = config.get("temperature", 0.2)

        self.logger.info(f"OpenAI 客户端初始化完成 (模型: {self.model})")

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
                return format_to_mime.get(img.format, "image/png")
        except Exception:
            return "image/png"

    def _path_or_url_to_data_url(self, image: Union[str, bytes]) -> str:
        """将 URL/本地路径/字节 转为 URL 或 data URL"""
        import os

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
        elif isinstance(image, (bytes, bytearray)):
            data = bytes(image)
            mime = self._infer_mime_from_bytes(data)
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"
        else:
            raise TypeError("image 参数必须为 str(路径或URL) 或 bytes")

    def _build_vision_user_content(
        self, prompt: str, images: Union[str, bytes, List[Union[str, bytes]]]
    ) -> List[Dict[str, Any]]:
        """构建多模态 user content 列表，兼容 OpenAI Chat Completions 识图格式"""
        contents: List[Dict[str, Any]] = []
        contents.append({"type": "text", "text": prompt})
        image_list: List[Union[str, bytes]] = images if isinstance(images, list) else [images]
        for img in image_list:
            url_or_data = self._path_or_url_to_data_url(img)
            contents.append({"type": "image_url", "image_url": {"url": url_or_data}})
        return contents

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """聊天调用"""
        try:
            # 构建请求参数
            request_params = {
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

            # 异步调用
            response = await self.client.chat.completions.create(**request_params)

            # 构建 LLMResponse
            result = LLMResponse(
                success=True,
                content=response.choices[0].message.content,
                model=response.model,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
            )

            # 处理工具调用
            if response.choices[0].message.tool_calls:
                result.tool_calls = [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in response.choices[0].message.tool_calls
                ]

            # 处理推理链（如 DeepSeek R1）
            result.reasoning_content = getattr(response.choices[0].message, "reasoning_content", None)

            return result

        except Exception as e:
            error_msg = f"LLM 请求失败: {str(e)}"
            self.logger.error(error_msg)
            return LLMResponse(success=False, content=None, error=error_msg)

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """流式聊天"""
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
                if stop_event is not None and stop_event.is_set():
                    break

                # 内容增量
                try:
                    delta = chunk.choices[0].delta
                    text_piece = getattr(delta, "content", None)
                    if text_piece:
                        yield text_piece
                except Exception:
                    pass

        except Exception as e:
            self.logger.error(f"流式 LLM 请求失败: {e}")
        finally:
            if stream is not None:
                try:
                    await stream.aclose()
                except Exception:
                    pass

    async def vision(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """视觉理解调用"""
        try:
            # 构建 vision 消息
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

            result = LLMResponse(
                success=True,
                content=response.choices[0].message.content,
                model=response.model,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                reasoning_content=getattr(response.choices[0].message, "reasoning_content", None),
            )
            return result

        except Exception as e:
            error_msg = f"VLM 请求失败: {str(e)}"
            self.logger.error(error_msg)
            return LLMResponse(success=False, content=None, error=error_msg)

    async def cleanup(self) -> None:
        """清理资源"""
        # AsyncOpenAI 客户端无需显式清理
        pass
