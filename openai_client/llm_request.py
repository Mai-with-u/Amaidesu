import os
import asyncio
import base64
import mimetypes
from io import BytesIO
from typing import List, Dict, Any, Optional, Union, AsyncGenerator
from openai import AsyncOpenAI
from PIL import Image
from utils.logger import get_logger
from openai_client.modelconfig import ModelConfig
from openai_client.token_usage_manager import TokenUsageManager
from config.config import global_config

logger = get_logger("LLMClient")
        
class LLMClient:
    """LLM调用客户端"""
    
    def __init__(self,model_config: Optional[ModelConfig]):
        """初始化LLM客户端
        
        Args:
            config: Maicraft配置对象，如果为None则使用默认配置
        """
        # 初始化模型配置
        if model_config is None:
            # 从全局配置构建本地模型配置
            self.model_config = ModelConfig(
                model_name=global_config.llm.model,
                api_key=global_config.llm.api_key,
                base_url=global_config.llm.base_url,
                max_tokens=global_config.llm.max_tokens,
                temperature=global_config.llm.temperature,
            )
        else:
            self.model_config = model_config

        # 初始化logger
        self.logger = logger

        # 初始化OpenAI客户端
        self.client = AsyncOpenAI(
            api_key=self.model_config.api_key,
            base_url=self.model_config.base_url,
        )
        
        self.logger.info(f"LLM客户端初始化完成，模型: {self.model_config.model_name}")
        
        # 初始化token使用量管理器
        self.token_manager = TokenUsageManager()
    
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

    def _build_vision_user_content(self, prompt: str, images: Union[str, bytes, List[Union[str, bytes]]]) -> List[Dict[str, Any]]:
        """构建多模态 user content 列表，兼容 OpenAI Chat Completions 识图格式"""
        contents: List[Dict[str, Any]] = []
        contents.append({"type": "text", "text": prompt})
        image_list: List[Union[str, bytes]] = images if isinstance(images, list) else [images]
        for img in image_list:
            url_or_data = self._path_or_url_to_data_url(img)
            contents.append({
                "type": "image_url",
                "image_url": {"url": url_or_data}
            })
        return contents
    
    async def chat_completion(
        self,
        prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """异步聊天完成调用
        
        Args:
            prompt: 用户输入的提示
            tools: 工具列表，格式为OpenAI工具格式
            system_message: 系统消息
            temperature: 温度参数，覆盖配置中的默认值
            max_tokens: 最大token数，覆盖配置中的默认值
            
        Returns:
            包含响应结果的字典
        """
        try:
            # 构建消息列表
            messages = []
            
            if system_message:
                messages.append({"role": "system", "content": system_message})
            
            messages.append({"role": "user", "content": prompt})
            
            # 构建请求参数
            request_params = {
                "model": self.model_config.model_name,
                "messages": messages,
                "temperature": temperature or self.model_config.temperature,
            }
            
            if tools:
                request_params["tools"] = tools
                request_params["tool_choice"] = "auto"
            
            if max_tokens:
                request_params["max_tokens"] = max_tokens
            elif self.model_config.max_tokens:
                request_params["max_tokens"] = self.model_config.max_tokens
            
            self.logger.debug(f"发送LLM请求: {request_params}")
            
            # 异步调用
            response = await self.client.chat.completions.create(**request_params)
            
            # 解析响应
            result = {
                "success": True,
                "content": response.choices[0].message.content,
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                },
                "finish_reason": response.choices[0].finish_reason
            }
            
            # 处理工具调用
            if response.choices[0].message.tool_calls:
                result["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments
                        }
                    }
                    for tool_call in response.choices[0].message.tool_calls
                ]
            
            # 记录token使用量
            self.token_manager.record_usage(
                model_name=response.model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens
            )
            
            return result
            
        except Exception as e:
            error_msg = f"LLM请求失败: {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "content": None
            }
    
    async def simple_chat(self, prompt: str, system_message: Optional[str] = None) -> str:
        """简化的聊天接口，只返回文本内容
        
        Args:
            prompt: 用户输入的提示
            system_message: 系统消息
            
        Returns:
            响应文本内容，失败时返回错误信息
        """
        result = await self.chat_completion(prompt, system_message=system_message)
        
        if result["success"]:
            return result["content"]
        else:
            return f"错误: {result['error']}"
    
    async def call_tool(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        system_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """调用工具的函数
        
        Args:
            prompt: 用户输入的提示
            tools: 工具列表
            system_message: 系统消息
            
        Returns:
            包含工具调用结果的字典
        """
        response = await self.chat_completion(
            prompt=prompt,
            tools=tools,
            system_message=system_message
        )
        
        if not response.get("success"):
            self.logger.error(f"[MaiAgent] LLM调用失败: {response.get('error')}")
            return None
        
        tool_calls = response.get("tool_calls", [])
        
        return tool_calls
        
    
    async def stream_chat(
        self,
        prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[str, None]:
        """异步流式聊天，逐段yield增量内容，支持随时打断。

        Args:
            prompt: 用户输入
            tools: 工具定义（OpenAI工具格式）
            system_message: 系统消息
            temperature: 温度（覆盖配置）
            max_tokens: 最大token（覆盖配置）
            stop_event: 传入的 asyncio.Event，用于外部随时打断

        Yields:
            每个增量内容字符串
        """
        messages: List[Dict[str, Any]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        request_params: Dict[str, Any] = {
            "model": self.model_config.model_name,
            "messages": messages,
            "temperature": temperature or self.model_config.temperature,
            "stream": True,
        }
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = "auto"
        if max_tokens:
            request_params["max_tokens"] = max_tokens
        elif self.model_config.max_tokens:
            request_params["max_tokens"] = self.model_config.max_tokens

        accumulated_text: str = ""
        model_name: str = self.model_config.model_name
        prompt_tokens = 0
        completion_tokens = 0

        stream = None
        try:
            stream = await self.client.chat.completions.create(**request_params)
            async for chunk in stream:
                if stop_event is not None and stop_event.is_set():
                    break

                # 模型名（若流中提供则更新）
                chunk_model = getattr(chunk, "model", None)
                if isinstance(chunk_model, str):
                    model_name = chunk_model

                # 内容增量
                try:
                    delta = chunk.choices[0].delta
                    text_piece = getattr(delta, "content", None)
                    if text_piece:
                        accumulated_text += text_piece
                        yield text_piece
                except Exception:
                    pass

                # usage 仅在最后的chunk中可能出现
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        except Exception as e:
            self.logger.error(f"流式LLM请求失败: {e}")
            return
        finally:
            if stream is not None:
                try:
                    await stream.aclose()
                except Exception:
                    pass

            # 记录token使用量（若可用）
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                try:
                    self.token_manager.record_usage(
                        model_name=model_name,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    )
                except Exception as rec_err:
                    self.logger.warning(f"记录流式用量失败: {rec_err}")

    async def simple_stream(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[str, None]:
        """简化版流式接口：仅输出文本增量，支持 stop_event 打断。"""
        async for piece in self.stream_chat(
            prompt=prompt,
            tools=None,
            system_message=system_message,
            temperature=None,
            max_tokens=None,
            stop_event=stop_event,
        ):
            yield piece

    async def vision_completion(
        self,
        prompt: str,
        images: Union[str, bytes, List[Union[str, bytes]]],
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """异步识图(多模态)调用，支持 URL/本地文件/字节 多图"""
        try:
            messages: List[Dict[str, Any]] = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            user_content = self._build_vision_user_content(prompt, images)
            messages.append({"role": "user", "content": user_content})

            request_params: Dict[str, Any] = {
                "model": self.model_config.model_name,
                "messages": messages,
                "temperature": temperature or self.model_config.temperature,
            }
            if max_tokens:
                request_params["max_tokens"] = max_tokens
            elif self.model_config.max_tokens:
                request_params["max_tokens"] = self.model_config.max_tokens

            # self.logger.debug(f"发送VLM请求: {request_params}")
            response = await self.client.chat.completions.create(**request_params)

            result = {
                "success": True,
                "content": response.choices[0].message.content,
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                },
                "finish_reason": response.choices[0].finish_reason,
                # 添加推理链内容解析
                "reasoning_content": getattr(response.choices[0].message, 'reasoning_content', None)
            }
            return result
        except Exception as e:
            error_msg = f"VLM请求失败: {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "content": None
            }

    async def simple_vision(
        self,
        prompt: str,
        images: Union[str, bytes, List[Union[str, bytes]]],
        system_message: Optional[str] = None
    ) -> str:
        """简化的识图接口，只返回文本内容"""
        result = await self.vision_completion(
            prompt=prompt,
            images=images,
            system_message=system_message
        )
        if result["success"]:
            return result["content"]
        else:
            return f"错误: {result['error']}"

    def get_config_info(self) -> Dict[str, Any]:
        """获取配置信息
        
        Returns:
            配置信息字典
        """
        return {
            "model": self.model_config.model_name,
            "base_url": self.model_config.base_url,
            "temperature": self.model_config.temperature,
            "max_tokens": self.model_config.max_tokens,
            "api_key_set": bool(self.model_config.api_key)
        }
    
    def get_token_usage_summary(self, model_name: Optional[str] = None) -> str:
        """获取token使用量摘要
        
        Args:
            model_name: 模型名称，如果为None则显示所有模型
            
        Returns:
            token使用量摘要字符串
        """
        if model_name:
            return self.token_manager.format_usage_summary(model_name)
        else:
            all_usage = self.token_manager.get_all_models_usage()
            if not all_usage:
                return "暂无任何模型的使用记录"
            
            summaries = []
            for model, usage in all_usage.items():
                summaries.append(self.token_manager.format_usage_summary(model))
            
            return "\n\n".join(summaries)
    
    def get_token_usage_data(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """获取token使用量原始数据
        
        Args:
            model_name: 模型名称，如果为None则返回所有模型数据
            
        Returns:
            token使用量数据字典
        """
        if model_name:
            return self.token_manager.get_usage_summary(model_name)
        else:
            return self.token_manager.get_all_models_usage()
    
    def get_total_cost_summary(self) -> str:
        """获取所有模型的总费用摘要
        
        Returns:
            总费用摘要字符串
        """
        return self.token_manager.format_total_cost_summary()
    
    def get_total_cost_data(self) -> Dict[str, Any]:
        """获取所有模型的总费用数据
        
        Returns:
            总费用数据字典
        """
        return self.token_manager.get_total_cost_summary()

