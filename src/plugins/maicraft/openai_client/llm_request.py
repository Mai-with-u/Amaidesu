import asyncio
import json
from typing import List, Dict, Any, Optional, Union
from openai import AsyncOpenAI
from src.plugins.maicraft.config import MaicraftConfig, load_config_from_dict
from src.utils.logger import get_logger
from src.plugins.maicraft.openai_client.modelconfig import ModelConfig

        
class LLMClient:
    """LLM调用客户端"""
    
    def __init__(self,model_config: Optional[ModelConfig]):
        """初始化LLM客户端
        
        Args:
            config: Maicraft配置对象，如果为None则使用默认配置
        """
        self.model_config = model_config
        self.logger = get_logger("LLMClient")
        
        # 初始化OpenAI客户端
        self.client = AsyncOpenAI(
            api_key=self.model_config.api_key,
            base_url=self.model_config.base_url,
        )
        
        self.logger.info(f"LLM客户端初始化完成，模型: {self.model_config.model_name}")
    
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

