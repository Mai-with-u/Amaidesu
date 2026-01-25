"""
虚拟形象控制管理器
核心控制器，负责管理所有适配器、处理 LLM 交互和执行表情控制"""

import asyncio
import json
from typing import Dict, List, Optional, Any

from src.utils.logger import get_logger
from src.openai_client.llm_request import LLMClient
from openai.types.chat import ChatCompletionToolParam
from .adapter_base import AvatarAdapter, ParameterMetadata, ActionMetadata
from .semantic_actions import SemanticActionMapper
from .tool_generator import ToolGenerator
from .llm_executor import LLMExecutor


class AvatarControlManager:
    """虚拟形象控制管理器
    负责管理多个平台适配器，提供统一的控制接口。
    主要功能：
    1. 管理适配器实例（注册、连接、断开）
    2. 根据文本自动分析并设置表情（LLM 驱动）
    3. 执行语义动作控制
    4. 处理语义动作到平台参数的映射

    使用示例：
        # 自动分析文本并设置表情
        await core.avatar.set_expression_from_text("今天天气真好！")

        # 手动设置表情
        await core.avatar.set_semantic_action("happy_expression", 0.8)

        # 注册适配器
        core.avatar.register_adapter(vts_adapter)
    """

    def __init__(self, core, config: Optional[Dict[str, Any]] = None):
        """
        初始化管理器

        Args:
            core: AmaidesuCore 实例
            config: 配置字典
        """
        self.core = core
        self.config = config or {}
        self.logger = get_logger("AvatarControlManager")

        # 适配器管理
        self._adapters: Dict[str, AvatarAdapter] = {}
        self._active_adapter: Optional[str] = None

        # 组件初始化
        semantic_config = self.config.get("semantic_actions", {})
        self.semantic_mapper = SemanticActionMapper(semantic_config)
        self.tool_generator = ToolGenerator()
        self.llm_executor = LLMExecutor(self)

        # 加载平台特定的覆盖配置
        self._load_platform_overrides()

        # LLM 客户端初始化
        self._llm_client: Optional[LLMClient] = None
        self._llm_config = self.config.get("llm", {})
        self._llm_enabled = self._llm_config.get("enabled", True)

        # 自动表情配置
        auto_config = self.config.get("auto_expression", {})
        self._auto_expression_enabled = auto_config.get("enabled", False)
        self._auto_min_text_length = auto_config.get("min_text_length", 2)

        # 新增：初始化触发策略引擎
        if self._auto_expression_enabled:
            from .trigger_strategy import TriggerStrategyEngine

            self._trigger_strategy = TriggerStrategyEngine(auto_config, self)
        else:
            self._trigger_strategy = None

        self.logger.info("AvatarControlManager 初始化完成")
        if self._auto_expression_enabled:
            strategy_info = []
            if auto_config.get("simple_reply_filter_enabled", True):
                strategy_info.append("简单回复过滤")
            if auto_config.get("time_interval_enabled", True):
                strategy_info.append(f"时间间隔({auto_config.get('min_time_interval', 5.0)}s)")
            if auto_config.get("llm_judge_enabled", True):
                strategy_info.append("LLM智能判断")

            self.logger.info(
                f"自动表情已启用（最小长度: {self._auto_min_text_length}）"
                + (f"，触发策略: {', '.join(strategy_info)}" if strategy_info else "")
            )

    def _load_platform_overrides(self) -> None:
        """加载配置中的平台特定映射覆盖"""
        if not self.config:
            return

        semantic_config = self.config.get("semantic_actions", {})
        for action_name, action_config in semantic_config.items():
            if isinstance(action_config, dict) and "platforms" in action_config:
                platforms = action_config["platforms"]
                if isinstance(platforms, dict):
                    for platform_name, mapping in platforms.items():
                        if isinstance(mapping, dict):
                            self.semantic_mapper.add_platform_override(platform_name, action_name, mapping)
                            self.logger.debug(f"加载平台覆盖: {platform_name}.{action_name}")

    def register_adapter(self, adapter: AvatarAdapter) -> bool:
        """注册适配器
        Args:
            adapter: 适配器实例
        Returns:
            是否注册成功
        """
        name = adapter.adapter_name
        if name in self._adapters:
            self.logger.warning(f"适配器 '{name}' 已存在，将被覆盖")

        self._adapters[name] = adapter
        self.logger.info(f"适配器 '{name}' 已注册")
        return True

    def set_active_adapter(self, adapter_name: str) -> bool:
        """设置活跃适配器
        Args:
            adapter_name: 适配器名称
        Returns:
            是否设置成功
        """
        if adapter_name not in self._adapters:
            self.logger.error(f"适配器 '{adapter_name}' 不存在")
            return False

        self._active_adapter = adapter_name
        self.logger.info(f"活跃适配器设置为: {adapter_name}")
        return True

    def get_active_adapter(self) -> Optional[AvatarAdapter]:
        """获取当前活跃的适配器
        Returns:
            活跃适配器实例，如果未设置返回 None
        """
        if self._active_adapter is None:
            return None
        return self._adapters.get(self._active_adapter)

    def get_adapter(self, adapter_name: str) -> Optional[AvatarAdapter]:
        """获取指定适配器
        Args:
            adapter_name: 适配器名称
        Returns:
            适配器实例，如果不存在返回 None
        """
        return self._adapters.get(adapter_name)

    def list_adapters(self) -> List[str]:
        """列出所有已注册的适配器
        Returns:
            适配器名称列表
        """
        return list(self._adapters.keys())

    async def connect_adapter(self, adapter_name: str) -> bool:
        """连接适配器
        Args:
            adapter_name: 适配器名称
        Returns:
            是否连接成功
        """
        adapter = self.get_adapter(adapter_name)
        if not adapter:
            self.logger.error(f"适配器 '{adapter_name}' 不存在")
            return False

        try:
            success = await adapter.connect()
            if success:
                adapter._is_connected = True
                self.logger.info(f"适配器 '{adapter_name}' 连接成功")
            else:
                self.logger.error(f"适配器 '{adapter_name}' 连接失败")
            return success
        except Exception as e:
            self.logger.error(f"连接适配器 '{adapter_name}' 时出错: {e}", exc_info=True)
            return False

    async def disconnect_adapter(self, adapter_name: str) -> bool:
        """断开适配器连接
        Args:
            adapter_name: 适配器名称
        Returns:
            是否断开成功
        """
        adapter = self.get_adapter(adapter_name)
        if not adapter:
            self.logger.error(f"适配器 '{adapter_name}' 不存在")
            return False

        try:
            success = await adapter.disconnect()
            adapter._is_connected = False
            self.logger.info(f"适配器 '{adapter_name}' 已断开")
            return success
        except Exception as e:
            self.logger.error(f"断开适配器 '{adapter_name}' 时出错: {e}", exc_info=True)
            return False

    async def connect_all_adapters(self) -> Dict[str, bool]:
        """连接所有适配器
        Returns:
            适配器名到连接结果的映射
        """
        results = {}
        for name in self._adapters.keys():
            results[name] = await self.connect_adapter(name)
        return results

    def get_all_registered_parameters(self) -> Dict[str, ParameterMetadata]:
        """获取所有适配器注册的参数

        Returns:
            参数名到元数据的映射
        """
        all_params = {}
        for adapter in self._adapters.values():
            all_params.update(adapter.get_registered_parameters())
        return all_params

    def get_all_registered_actions(self) -> Dict[str, ActionMetadata]:
        """获取所有适配器注册的动作

        Returns:
            动作名到元数据的映射
        """
        all_actions = {}
        for adapter in self._adapters.values():
            all_actions.update(adapter.get_registered_actions())
        return all_actions

    async def generate_llm_tools(self) -> List[ChatCompletionToolParam]:
        """生成 LLM 工具定义

        根据所有适配器注册的参数和动作，生成符合 OpenAI 工具格式的定义。

        Returns:
            符合 OpenAI 官方类型的工具定义列表
        """
        parameters = self.get_all_registered_parameters()
        actions = self.get_all_registered_actions()
        semantic_actions = list(self.semantic_mapper.list_semantic_actions().keys())

        return self.tool_generator.generate_tools(parameters, actions, semantic_actions)

    async def execute_tool_call(
        self, function_name: str, arguments: Dict[str, Any], adapter_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """执行 LLM 返回的工具调用
        Args:
            function_name: 函数/工具名称
            arguments: 参数字典
            adapter_name: 目标适配器（None 则使用活跃适配器）

        Returns:
            执行结果 {"success": bool, "message": str, ...}
        """
        return await self.llm_executor.execute(function_name, arguments, adapter_name)

    async def set_semantic_action(
        self, action_name: str, intensity: float = 1.0, adapter_name: Optional[str] = None
    ) -> bool:
        """执行语义动作（如 happy_expression）
        Args:
            action_name: 语义动作名称
            intensity: 强度 (0.0-1.0)
            adapter_name: 目标适配器
        Returns:
            是否执行成功
        """
        adapter = self._get_target_adapter(adapter_name)
        if not adapter:
            self.logger.error(f"未找到适配器: {adapter_name or 'active'}")
            return False

        # 获取语义动作映射
        mapping = self.semantic_mapper.get_mapping(action_name, adapter.adapter_name)
        if not mapping:
            self.logger.warning(f"未找到语义动作 '{action_name}' 的映射")
            return False

        # 应用强度系数
        params_to_set = self.semantic_mapper.apply_intensity(mapping, intensity)

        # 执行参数设置
        return await adapter.set_parameters(params_to_set)

    def list_semantic_actions(self) -> Dict[str, str]:
        """列出所有可用的语义动作

        Returns:
            动作名到描述的映射
        """
        return self.semantic_mapper.list_semantic_actions()

    def _get_target_adapter(self, adapter_name: Optional[str]) -> Optional[AvatarAdapter]:
        """获取目标适配器
        Args:
            adapter_name: 适配器名称，None 则使用活跃适配器
        Returns:
            适配器实例，如果未找到返回 None
        """
        if adapter_name:
            return self.get_adapter(adapter_name)
        return self.get_active_adapter()

    # ==================== LLM 集成方法 ====================

    def _get_llm_client(self) -> Optional[LLMClient]:
        """获取 LLM 客户端
        Returns:
            LLM 客户端实例，如果未启用或初始化失败返回 None
        """
        if not self._llm_enabled:
            return None

        if self._llm_client is None:
            try:
                # 使用快速 LLM 配置
                llm_type = self._llm_config.get("type", "llm_fast")

                # 从 core 获取全局 LLM 客户端（不创建独立客户端）
                self._llm_client = self.core.get_llm_client(llm_type)
                self.logger.info(f"使用核心的 LLM 客户端 ({llm_type})")
                return self._llm_client
            except AttributeError:
                self.logger.error("核心未提供 get_llm_client() 方法，Avatar 模块无法使用 LLM 功能")
                self.logger.error("Avatar 模块需要核心提供 LLM 客户端，已禁用 LLM 功能")
                self._llm_enabled = False
                return None

            except ValueError as e:
                # LLM 配置错误（如 API Key 未配置）
                self.logger.error(f"LLM 配置错误: {e}")
                self.logger.error("Avatar 模块已禁用 LLM 功能")
                self._llm_enabled = False
                return None

            except Exception as e:
                self.logger.error(f"获取 LLM 客户端失败: {e}", exc_info=True)
                self._llm_enabled = False
                return None

        return self._llm_client

    async def set_expression_from_text(
        self, text: str, adapter_name: Optional[str] = None, fallback_expression: str = "neutral"
    ) -> Dict[str, Any]:
        """根据文本自动分析并设置表情
        这是主要的高层接口，其他插件只需调用此方法即可让虚拟形象根据文本做出表情。
        Args:
            text: 输入文本
            adapter_name: 目标适配器（None 则使用活跃适配器）
            fallback_expression: 当 LLM 分析失败时使用的默认表情

        Returns:
            执行结果 {"success": bool, "expression": str, "message": str}
        """
        if not self._llm_enabled:
            # LLM 未启用，使用中性表情
            self.logger.warning("LLM 功能未启用，使用中性表情")
            return await self._set_fallback_expression(fallback_expression, adapter_name)

        # 检查是否有活跃适配器
        adapter = self._get_target_adapter(adapter_name)
        if not adapter:
            self.logger.warning("没有可用的适配器")
            return {"success": False, "error": "no_adapter", "message": "没有可用的适配器"}

        # 获取 LLM 客户端
        llm_client = self._get_llm_client()
        if not llm_client:
            self.logger.warning("LLM 客户端不可用，使用备用表情")
            return await self._set_fallback_expression(fallback_expression, adapter_name)

        # 生成工具定义
        tools = await self.generate_llm_tools()
        if not tools:
            self.logger.warning("没有可用的工具定义")
            return await self._set_fallback_expression(fallback_expression, adapter_name)

        try:
            # 构建提示词（工具定义已包含表情列表，无需重复）
            prompt = f"""分析以下文本的情感和语气，为虚拟形象设置最合适的表情。

文本: {text}

注意：
- 根据文本的情感强度调整表情的 intensity 参数（0.0-1.0）
- 如果文本平淡或中性，请使用 "neutral" 表情
- 如果文本没有明确情感倾向，使用较低的强度（0.3-0.5）"""

            # 调用 LLM
            result = await llm_client.chat_completion(
                prompt=prompt,
                tools=tools,
                temperature=0.3,  # 较低的温度以获得更稳定的结果
            )

            if not result.get("success"):
                self.logger.warning(f"LLM 调用失败: {result.get('error')}")
                return await self._set_fallback_expression(fallback_expression, adapter_name)

            # 检查是否有工具调用
            tool_calls = result.get("tool_calls")
            if not tool_calls or len(tool_calls) == 0:
                # 没有 tool_call，尝试从文本中解析
                content = result.get("content", "")
                if not content:
                    return await self._set_fallback_expression(fallback_expression, adapter_name)

                self.logger.info(f"LLM 返回了内容而非工具调用: {content}")
                # 简单的回退：使用中性表情
                return await self._set_fallback_expression("neutral", adapter_name)
            # 执行工具调用
            tool_call = tool_calls[0]
            function_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])

            execution_result = await self.execute_tool_call(function_name, arguments, adapter_name)

            if execution_result.get("success"):
                self.logger.info(f"表情设置成功: {function_name} -> {execution_result.get('message')}")
            else:
                self.logger.warning(f"表情设置失败: {execution_result.get('error')}")

            return execution_result

        except Exception as e:
            self.logger.error(f"分析文本并设置表情时出错: {e}", exc_info=True)
            return await self._set_fallback_expression(fallback_expression, adapter_name)

    async def _set_fallback_expression(self, expression: str, adapter_name: Optional[str]) -> Dict[str, Any]:
        """设置备用表情

        Args:
            expression: 表情名称
            adapter_name: 适配器名称
        Returns:
            执行结果
        """
        success = await self.set_semantic_action(expression, 1.0, adapter_name)
        return {"success": success, "expression": expression, "message": f"使用备用表情: {expression}"}

    def enable_llm(self) -> None:
        """启用 LLM 功能"""
        self._llm_enabled = True
        self.logger.info("LLM 功能已启用")

    def disable_llm(self) -> None:
        """禁用 LLM 功能"""
        self._llm_enabled = False
        self.logger.info("LLM 功能已禁用")

    # ==================== 自动表情功能 ====================

    async def try_auto_expression(self, text: str) -> bool:
        """尝试根据文本自动设置表情

        此方法封装了所有自动触发的判断逻辑，由 AmaidesuCore 调用。
        如果满足触发条件，将在后台异步设置表情，不阻塞调用者。

        Args:
            text: 输入文本

        Returns:
            是否成功触发（如果触发条件不满足，返回 False 但不算错误）
        """
        # 检查是否启用自动表情
        if not self._auto_expression_enabled:
            return False

        # 检查是否有活跃适配器
        if not self.get_active_adapter():
            return False

        # 检查文本长度
        text_stripped = text.strip() if text else ""
        if len(text_stripped) < self._auto_min_text_length:
            return False

        # 新增：使用触发策略引擎判断（异步）
        if self._trigger_strategy:
            should_trigger, filter_reason, llm_result = await self._trigger_strategy.should_trigger(text_stripped)
            if not should_trigger:
                self.logger.debug(f"自动表情触发被过滤: {filter_reason}")
                return False

            # 记录LLM返回的情感信息（如果有）
            if llm_result:
                emotion = llm_result.get("emotion", "neutral")
                intensity = llm_result.get("intensity", 1.0)
                self._trigger_strategy.record_trigger(emotion, intensity, text_stripped)

        # 通过所有检查，创建后台任务异步执行表情设置
        self.logger.info(f"自动触发虚拟形象控制: {text_stripped}")
        asyncio.create_task(self._set_expression_async(text_stripped))

        return True

    async def _set_expression_async(self, text: str):
        """异步设置表情（后台执行）

        此方法在后台任务中执行，用于自动设置表情。
        任何错误都会被捕获并记录，不会影响主流程。

        Args:
            text: 输入文本
        """
        try:
            result = await self.set_expression_from_text(text)
            if result.get("success"):
                self.logger.debug(f"自动表情设置成功: {result.get('expression', 'unknown')}")
            else:
                self.logger.debug(f"自动表情设置失败: {result.get('message', 'unknown')}")
        except Exception as e:
            self.logger.error(f"自动设置表情时出错: {e}", exc_info=True)
