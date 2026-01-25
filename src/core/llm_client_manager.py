"""
LLM 客户端管理器
负责创建、缓存和管理所有 LLM 客户端实例，确保全局只有一个客户端实例
"""

import os
import tomli
from typing import Dict, Optional, Any, List

from src.utils.logger import get_logger
from src.openai_client.llm_request import LLMClient
from src.openai_client.modelconfig import ModelConfig


class LLMClientManager:
    """
    LLM 客户端管理器

    职责：
    1. 创建和缓存 LLM 客户端实例
    2. 从根目录的 config.toml 读取配置
    3. 提供统一的客户端获取接口

    设计原则：
    - 单例模式：全局只有一个管理器实例
    - 延迟创建：客户端在首次请求时才创建
    - 缓存机制：相同配置的客户端只创建一次
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化 LLM 客户端管理器

        Args:
            config_path: 配置文件路径，如果为 None 则使用默认路径
        """
        self.logger = get_logger("LLMClientManager")
        self._clients: Dict[str, LLMClient] = {}

        # 加载配置
        if config_path is None:
            # 获取项目根目录的 config.toml
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            config_path = os.path.join(base_dir, "config.toml")

        self.config_path = config_path
        self._config_data = self._load_config()
        self.logger.info(f"LLMClientManager 初始化完成 (配置: {config_path})")

    def _load_config(self) -> Dict[str, Any]:
        """从配置文件加载 LLM 配置"""
        if not os.path.exists(self.config_path):
            self.logger.warning(f"配置文件不存在: {self.config_path}")
            return {}

        try:
            with open(self.config_path, "rb") as f:
                config = tomli.load(f)
            self.logger.info(f"成功加载配置文件: {self.config_path}")
            return config
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {e}", exc_info=True)
            return {}

    def _get_llm_config(self, config_type: str) -> Dict[str, Any]:
        """获取指定类型的 LLM 配置"""
        # 从配置文件中读取对应部分
        if config_type in self._config_data:
            return self._config_data[config_type]

        # 如果没有找到，返回空字典
        self.logger.warning(f"配置中未找到 [{config_type}] 部分")
        return {}

    def get_client(self, config_type: str = "llm") -> LLMClient:
        """
        获取 LLM 客户端实例

        Args:
            config_type: 配置类型，可选值：
                - "llm": 标准 LLM 配置（默认）
                - "llm_fast": 快速 LLM 配置（低延迟场景）
                - "vlm": 视觉语言模型配置

        Returns:
            LLMClient 实例

        Raises:
            ValueError: 如果 config_type 无效或必需配置缺失
        """
        # 检查缓存
        if config_type in self._clients:
            self.logger.debug(f"从缓存返回 {config_type} 客户端")
            return self._clients[config_type]

        # 验证 config_type
        if config_type not in ["llm", "llm_fast", "vlm"]:
            raise ValueError(f"无效的 config_type: {config_type}，必须是 'llm', 'llm_fast' 或 'vlm'")

        try:
            # 1. 从配置文件获取基础配置
            base_config = self._get_llm_config(config_type)

            # 2. 检查必需字段是否存在
            if not base_config:
                raise ValueError(f"配置文件中未找到 [{config_type}] 部分")

            # 3. 提取配置字段
            config_dict = {
                "model": base_config.get("model", "gpt-4o-mini"),
                "api_key": base_config.get("api_key", ""),
                "base_url": base_config.get("base_url"),
                "max_tokens": base_config.get("max_tokens", 1024),
                "temperature": base_config.get("temperature", 0.2),
            }

            # 4. 验证必需字段
            if not config_dict["api_key"] or config_dict["api_key"] == "your-api-key":
                raise ValueError(f"LLM API Key 未配置！请在 {self.config_path} 的 [{config_type}] 部分设置 api_key。")

            # 4. 创建 ModelConfig
            model_config = ModelConfig(
                model_name=config_dict["model"],
                api_key=config_dict["api_key"],
                base_url=config_dict["base_url"],
                max_tokens=config_dict["max_tokens"],
                temperature=config_dict["temperature"],
            )

            # 5. 创建 LLMClient
            client = LLMClient(model_config)

            # 6. 缓存客户端
            self._clients[config_type] = client

            self.logger.info(
                f"已创建并缓存 {config_type} 客户端 "
                f"(model: {model_config.model_name}, base_url: {model_config.base_url})"
            )

            return client

        except Exception as e:
            error_msg = f"创建 {config_type} 客户端失败: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from e

    def get_standard_client(self) -> LLMClient:
        """获取标准 LLM 客户端（快捷方法）"""
        return self.get_client("llm")

    def get_fast_client(self) -> LLMClient:
        """获取快速 LLM 客户端（快捷方法）"""
        return self.get_client("llm_fast")

    def get_vlm_client(self) -> LLMClient:
        """获取视觉语言模型客户端（快捷方法）"""
        return self.get_client("vlm")

    def clear_cache(self, config_type: Optional[str] = None) -> None:
        """
        清除客户端缓存

        Args:
            config_type: 要清除的配置类型，如果为 None 则清除所有缓存
        """
        if config_type is None:
            self._clients.clear()
            self.logger.info("已清除所有 LLM 客户端缓存")
        elif config_type in self._clients:
            del self._clients[config_type]
            self.logger.info(f"已清除 {config_type} 客户端缓存")

    def get_all_cached_types(self) -> List[str]:
        """获取所有已缓存的客户端类型"""
        return list(self._clients.keys())
