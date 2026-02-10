"""
LLMPDecisionProvider - LLM决策提供者

职责:
- 使用 LLM Service 进行决策
- 支持自定义 prompt 模板
- 错误处理和降级机制
"""

from typing import TYPE_CHECKING, Any, Dict, Literal, Optional

from pydantic import Field

from src.modules.types import Intent
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.context import MessageRole
from src.modules.logging import get_logger
from src.modules.prompts import get_prompt_manager
from src.modules.types import ActionType, EmotionType, IntentAction
from src.modules.types.base.decision_provider import DecisionProvider

if TYPE_CHECKING:
    from src.modules.context import ContextService
    from src.modules.events.event_bus import EventBus
    from src.modules.llm.manager import LLMManager
    from src.modules.types.base.normalized_message import NormalizedMessage


class LLMPDecisionProvider(DecisionProvider):
    """
    LLM决策提供者

    使用 LLM Service 统一接口进行决策。

    配置示例:
        ```toml
        [llm]
        client = "llm"  # 使用的 LLM 客户端（llm, llm_fast, vlm）
        fallback_mode = "simple"
        ```

    属性:
        client: 使用的 LLM 客户端
        fallback_mode: 降级模式（"simple"返回简单响应，"error"抛出异常）
    """

    class ConfigSchema(BaseProviderConfig):
        """LLM决策Provider配置Schema

        使用LLM Service统一接口进行决策。
        """

        type: Literal["llm"] = "llm"
        client: Literal["llm", "llm_fast", "vlm"] = Field(default="llm", description="使用的LLM客户端名称")
        fallback_mode: Literal["simple", "echo", "error"] = Field(default="simple", description="降级模式")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 LLMPDecisionProvider

        Args:
            config: 配置字典
        """
        # 使用 Pydantic Schema 验证配置
        self.typed_config = self.ConfigSchema(**config)
        self.logger = get_logger("LLMPDecisionProvider")

        # LLM Manager 引用（通过 setup 注入）
        self._llm_service: Optional["LLMManager"] = None

        # ConfigService 引用（通过 setup 注入）
        self._config_service = None

        # ContextService 引用（通过 setup 注入）
        self._context_service: Optional["ContextService"] = None

        # LLM 配置
        self.client_type = self.typed_config.client  # 使用的客户端类型

        # 降级模式配置
        self.fallback_mode = self.typed_config.fallback_mode

        # 统计信息
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0

        # EventBus 引用（用于事件通知）
        self._event_bus: Optional["EventBus"] = None

    async def setup(
        self,
        event_bus: "EventBus",
        config: Dict[str, Any],
        dependencies: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        设置 LLMPDecisionProvider

        Args:
            event_bus: EventBus 实例
            config: Provider 配置（使用 __init__ 传入的 config）
            dependencies: 依赖注入字典，必须包含 llm_service
        """
        self._event_bus = event_bus
        self.logger.info("初始化 LLMPDecisionProvider...")

        # 从依赖注入中获取 LLMManager
        if dependencies and "llm_service" in dependencies:
            self._llm_service = dependencies["llm_service"]
            self.logger.info("LLMManager 已从依赖注入中获取")
        else:
            self.logger.warning("LLMManager 未通过依赖注入提供，决策功能将不可用")

        # 从依赖注入中获取 ConfigService
        if dependencies and "config_service" in dependencies:
            self._config_service = dependencies["config_service"]
            self.logger.info("ConfigService 已从依赖注入中获取")
        else:
            self.logger.warning("ConfigService 未通过依赖注入提供，将使用默认人设")

        # 从依赖注入中获取 ContextService
        if dependencies and "context_service" in dependencies:
            self._context_service = dependencies["context_service"]
            self.logger.info("ContextService 已从依赖注入中获取")
        else:
            self.logger.warning("ContextService 未通过依赖注入提供，将使用无状态模式")

        self.logger.info(f"LLMPDecisionProvider 初始化完成 (Client: {self.client_type})")

    def _get_persona_config(self) -> Dict[str, Any]:
        """获取 VTuber 人设配置

        Returns:
            人设配置字典，如果无法获取则返回空字典
        """
        if self._config_service is None:
            return {}

        try:
            persona_config = self._config_service.get_section("persona", {})
            return persona_config
        except Exception as e:
            self.logger.warning(f"读取 persona 配置失败: {e}")
            return {}

    async def decide(self, normalized_message: "NormalizedMessage") -> Intent:
        """
        进行决策（通过 LLM 生成响应）

        新增：使用 ContextService 管理对话历史

        Args:
            normalized_message: 标准化消息

        Returns:
            Intent: 决策意图（LLM 生成的响应）

        Raises:
            RuntimeError: 如果所有重试失败且 fallback_mode 为 "error"
        """
        if self._llm_service is None:
            raise RuntimeError("LLM Manager 未注入！请确保在 setup 中正确配置。")

        self._total_requests += 1

        # 获取 session_id（使用 normalized_message.source）
        session_id = normalized_message.source  # 如 "console_input", "bili_danmaku"
        self.logger.debug(f"使用 session_id: {session_id}")

        # 保存用户消息到上下文
        if self._context_service:
            try:
                await self._context_service.add_message(
                    session_id=session_id,
                    role=MessageRole.USER,
                    content=normalized_message.text,
                )
                self.logger.debug(f"已保存用户消息到上下文 (session: {session_id})")
            except Exception as e:
                self.logger.warning(f"保存用户消息到上下文失败: {e}")

        # 读取 persona 配置（带默认值）
        persona_config = self._get_persona_config()

        # 获取历史上下文用于构建 prompt
        history_context = []
        if self._context_service:
            try:
                # 获取最近10条历史消息
                history = await self._context_service.get_history(session_id, limit=10)
                # 转换为 OpenAI 格式（排除刚保存的当前用户消息，避免重复）
                history_context = [
                    {"role": msg.role.value, "content": msg.content}
                    for msg in history[:-1]  # 排除刚刚添加的用户消息
                ]
                self.logger.debug(f"历史上下文: {len(history_context)} 条消息")
            except Exception as e:
                self.logger.warning(f"获取历史上下文失败: {e}")

        # 构建 prompt（使用 PromptManager 渲染模板）
        # 如果有历史上下文，可以将其注入到 prompt 中
        prompt = get_prompt_manager().render_safe(
            "decision/llm",
            text=normalized_message.text,
            bot_name=persona_config.get("bot_name", "爱德丝"),
            personality=persona_config.get("personality", "活泼开朗，有些调皮，喜欢和观众互动"),
            style_constraints=persona_config.get(
                "style_constraints", "口语化，使用网络流行语，避免机械式回复，适当使用emoji"
            ),
            user_name=persona_config.get("user_name", "大家"),
            max_length=persona_config.get("max_response_length", 50),
            # 可选：传递历史上下文用于 prompt 模板
            history=history_context if history_context else [],
        )

        try:
            # 使用 LLM Service 进行调用
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self.client_type,
            )

            if not response.success:
                self._failed_requests += 1
                self.logger.error(f"LLM 调用失败: {response.error}")
                # 使用降级策略
                return self._handle_fallback(normalized_message)

            self._successful_requests += 1

            # 保存助手回复到上下文
            if self._context_service:
                try:
                    await self._context_service.add_message(
                        session_id=session_id,
                        role=MessageRole.ASSISTANT,
                        content=response.content,
                    )
                    self.logger.debug(f"已保存助手回复到上下文 (session: {session_id})")
                except Exception as e:
                    self.logger.warning(f"保存助手回复到上下文失败: {e}")

            return self._create_intent(response.content, normalized_message)

        except Exception as e:
            self._failed_requests += 1
            self.logger.error(f"LLM 调用异常: {e}", exc_info=True)
            # 使用降级策略
            return self._handle_fallback(normalized_message)

    def _handle_fallback(self, normalized_message: "NormalizedMessage") -> Intent:
        """处理降级逻辑"""
        if self.fallback_mode == "simple":
            # 简单降级：返回原始文本
            return self._create_intent(normalized_message.text, normalized_message)
        elif self.fallback_mode == "echo":
            # 回声降级：重复用户输入
            return self._create_intent(f"你说：{normalized_message.text}", normalized_message)
        else:
            # 错误降级：抛出异常
            raise RuntimeError("LLM 请求失败，且未配置降级模式")

    def _create_intent(self, text: str, normalized_message: "NormalizedMessage") -> Intent:
        """
        创建Intent对象

        Args:
            text: 响应文本
            normalized_message: 原始NormalizedMessage

        Returns:
            Intent实例
        """
        # 简单实现：默认中性情感，眨眼动作
        return Intent(
            original_text=normalized_message.text,
            response_text=text,
            emotion=EmotionType.NEUTRAL,
            actions=[IntentAction(type=ActionType.BLINK, params={}, priority=30)],
            metadata={"parser": "llm"},
        )

    async def cleanup(self) -> None:
        """
        清理资源

        输出统计信息。
        """
        self.logger.info("清理LLMPDecisionProvider...")

        # 输出统计信息
        success_rate = self._successful_requests / self._total_requests * 100 if self._total_requests > 0 else 0
        self.logger.info(
            f"统计: 总请求={self._total_requests}, 成功={self._successful_requests}, "
            f"失败={self._failed_requests}, 成功率={success_rate:.1f}%"
        )

        self.logger.info("LLMPDecisionProvider已清理")

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取运行时统计信息

        Returns:
            统计信息字典
        """
        success_rate = self._successful_requests / self._total_requests * 100 if self._total_requests > 0 else 0

        return {
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "failed_requests": self._failed_requests,
            "success_rate": round(success_rate, 1),
            "client_type": self.client_type,
            "fallback_mode": self.fallback_mode,
        }

    def get_info(self) -> Dict[str, Any]:
        """
        获取 Provider 配置信息

        Returns:
            Provider 信息字典（静态配置）
        """
        return {
            "name": "LLMPDecisionProvider",
            "version": "1.0.0",
            "client_type": self.client_type,
            "template_name": "decision/llm",
            "fallback_mode": self.fallback_mode,
        }
