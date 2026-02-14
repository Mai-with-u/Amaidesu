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

    def __init__(self, config: Dict[str, Any], context: "ProviderContext" = None):
        """
        初始化 LLMPDecisionProvider

        Args:
            config: 配置字典
            context: 依赖注入上下文
        """
        super().__init__(config, context)

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

    async def init(self) -> None:
        """
        初始化 LLMPDecisionProvider

        从依赖注入中获取必要的服务。
        """
        self.logger.info("初始化 LLMPDecisionProvider...")

        # 从依赖注入中获取 LLMManager
        if self._dependencies and "llm_service" in self._dependencies:
            self._llm_service = self._dependencies["llm_service"]
            self.logger.info("LLMManager 已从依赖注入中获取")
        else:
            self.logger.warning("LLMManager 未通过依赖注入提供，决策功能将不可用")

        # 从依赖注入中获取 ConfigService
        if self._dependencies and "config_service" in self._dependencies:
            self._config_service = self._dependencies["config_service"]
            self.logger.info("ConfigService 已从依赖注入中获取")
        else:
            self.logger.warning("ConfigService 未通过依赖注入提供，将使用默认人设")

        # 从依赖注入中获取 ContextService
        if self._dependencies and "context_service" in self._dependencies:
            self._context_service = self._dependencies["context_service"]
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

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        """
        进行决策（通过 LLM 生成完整 Intent）

        使用 ContextService 管理对话历史，使用结构化 prompt 模板，
        对 LLM 返回的 JSON 进行清理和解析，构造完整 Intent（含 emotion/actions）。
        处理完成后通过 event_bus 发布 decision.intent 事件。

        Args:
            normalized_message: 标准化消息

        Raises:
            RuntimeError: 如果所有重试失败且 fallback_mode 为 "error"
        """
        if self._llm_service is None:
            raise RuntimeError("LLM Manager 未注入！请确保在 setup 中正确配置。")

        self._total_requests += 1

        # 获取 session_id（使用 normalized_message.source）
        session_id = normalized_message.source
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
                # 转换为字符串格式（排除刚保存的当前用户消息，避免重复）
                history_items = []
                for msg in history[:-1]:  # 排除刚刚添加的用户消息
                    role_name = "用户" if msg.role.value == "user" else "助手"
                    history_items.append(f"{role_name}: {msg.content}")
                history_context = history_items
                self.logger.debug(f"历史上下文: {len(history_context)} 条消息")
            except Exception as e:
                self.logger.warning(f"获取历史上下文失败: {e}")

        # 构建历史文本（用于 prompt 模板）
        history_text = "\n".join(history_context) if history_context else ""

        # 获取 prompt_service（依赖注入）
        if not self.context or not self.context.prompt_service:
            raise ValueError("prompt_service 未注入，请检查 Provider 初始化配置")
        prompt_manager = self.context.prompt_service

        # 构建 prompt（使用 PromptManager 渲染结构化模板）
        prompt = prompt_manager.render_safe(
            "decision/llm_structured",
            text=normalized_message.text,
            bot_name=persona_config.get("bot_name", "爱德丝"),
            personality=persona_config.get("personality", "活泼开朗，有些调皮，喜欢和观众互动"),
            style_constraints=persona_config.get(
                "style_constraints", "口语化，使用网络流行语，避免机械式回复，适当使用emoji"
            ),
            history=history_text,
        )

        try:
            # 使用 LLM Service 进行调用（不使用 response_format，因为该参数不存在）
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self.client_type,
            )

            if not response.success:
                self._failed_requests += 1
                self.logger.error(f"LLM 调用失败: {response.error}")
                # 使用降级策略
                await self._handle_fallback(normalized_message)
                return

            self._successful_requests += 1

            # 清理和解析 LLM 返回的 JSON
            cleaned_json = self._clean_llm_json(response.content)

            try:
                # 解析 JSON
                import json

                parsed_data = json.loads(cleaned_json)

                # 构造完整 Intent
                intent = self._create_full_intent(
                    parsed_data=parsed_data,
                    normalized_message=normalized_message,
                )

                # 保存助手回复到上下文（保存 response_text）
                if self._context_service:
                    try:
                        await self._context_service.add_message(
                            session_id=session_id,
                            role=MessageRole.ASSISTANT,
                            content=intent.response_text,
                        )
                        self.logger.debug(f"已保存助手回复到上下文 (session: {session_id})")
                    except Exception as e:
                        self.logger.warning(f"保存助手回复到上下文失败: {e}")

                # 发布 decision.intent 事件
                await self._publish_intent(intent, normalized_message)

            except json.JSONDecodeError as e:
                self.logger.error(f"JSON 解析失败: {e}, 清理后的内容: {cleaned_json[:200]}")
                # 使用降级策略
                await self._handle_fallback(normalized_message)
                return

        except Exception as e:
            self._failed_requests += 1
            self.logger.error(f"LLM 调用异常: {e}", exc_info=True)
            # 使用降级策略
            await self._handle_fallback(normalized_message)
            return

    def _clean_llm_json(self, raw_output: str) -> str:
        """
        清理 LLM 返回的 JSON 字符串（与 MaiCore 一致）

        三步清理：
        1. 移除 ```json 或 ``` 代码块
        2. 截取第一个 { 到最后一个 }
        3. 去掉尾逗号

        Args:
            raw_output: LLM 原始输出

        Returns:
            清理后的 JSON 字符串
        """
        import re

        # 第一步：移除 ```json 或 ``` 代码块标记
        cleaned = raw_output.strip()
        # 移除开头的 ```json 或 ```
        cleaned = re.sub(r"^```json\s*", "", cleaned)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        # 移除结尾的 ```
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        # 第二步：截取第一个 { 到最后一个 }
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]

        # 第三步：去掉尾逗号（在 } 前的 ,）
        cleaned = re.sub(r",\s*}", "}", cleaned)
        cleaned = re.sub(r",\s*]", "]", cleaned)

        return cleaned

    def _create_full_intent(self, parsed_data: Dict[str, Any], normalized_message: "NormalizedMessage") -> Intent:
        """
        从解析的 JSON 数据创建完整 Intent

        Args:
            parsed_data: LLM 返回的解析后 JSON 数据
            normalized_message: 原始 NormalizedMessage

        Returns:
            完整的 Intent 对象（含 emotion/actions）
        """
        # 提取字段
        text = parsed_data.get("text", "")
        emotion_str = parsed_data.get("emotion", "neutral").lower()
        actions_data = parsed_data.get("actions", [])

        # 解析 emotion
        try:
            emotion = EmotionType(emotion_str)
        except ValueError:
            self.logger.warning(f"无效的情感类型: {emotion_str}, 使用默认 NEUTRAL")
            emotion = EmotionType.NEUTRAL

        # 解析 actions
        actions = []
        for action_data in actions_data:
            try:
                action_type_str = action_data.get("type", "none").lower()
                params = action_data.get("params", {})
                priority = action_data.get("priority", 50)

                # 映射 action type
                action_type = self._map_action_type(action_type_str)
                actions.append(IntentAction(type=action_type, params=params, priority=priority))
            except Exception as e:
                self.logger.warning(f"解析动作失败: {e}, 跳过该动作")

        # 如果没有动作，添加默认眨眼动作
        if not actions:
            actions.append(IntentAction(type=ActionType.BLINK, params={}, priority=30))

        # 创建 Intent
        return Intent(
            original_text=normalized_message.text,
            response_text=text,
            emotion=emotion,
            actions=actions,
            metadata={"parser": "llm_structured"},
        )

    def _map_action_type(self, type_str: str) -> ActionType:
        """
        映射动作类型字符串到 ActionType 枚举

        Args:
            type_str: 动作类型字符串

        Returns:
            ActionType 枚举值
        """
        type_mapping = {
            "expression": ActionType.EXPRESSION,
            "hotkey": ActionType.HOTKEY,
            "emoji": ActionType.EMOJI,
            "blink": ActionType.BLINK,
            "nod": ActionType.NOD,
            "shake": ActionType.SHAKE,
            "wave": ActionType.WAVE,
            "clap": ActionType.CLAP,
            "sticker": ActionType.STICKER,
            "motion": ActionType.MOTION,
            "custom": ActionType.CUSTOM,
            "game_action": ActionType.GAME_ACTION,
            "none": ActionType.NONE,
            "speak": ActionType.EXPRESSION,  # speak 映射到 expression
            "gesture": ActionType.EXPRESSION,  # gesture 映射到 expression
        }

        return type_mapping.get(type_str, ActionType.NONE)

    async def _publish_intent(self, intent: Intent, normalized_message: "NormalizedMessage") -> None:
        """
        通过 event_bus 发布 decision.intent 事件

        Args:
            intent: 解析后的 Intent
            normalized_message: 原始标准化消息
        """
        from src.modules.events.names import CoreEvents
        from src.modules.events.payloads import IntentPayload
        from src.modules.types import SourceContext

        if not self.event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return

        # 构建 SourceContext
        source_context = SourceContext(
            source=normalized_message.source,
            data_type=normalized_message.data_type,
            user_id=normalized_message.user_id,
            user_nickname=normalized_message.metadata.get("user_nickname"),
            importance=normalized_message.importance,
        )
        intent.source_context = source_context

        await self.event_bus.emit(
            CoreEvents.DECISION_INTENT,
            IntentPayload.from_intent(intent, "llm"),
            source="LLMPDecisionProvider",
        )

        self.logger.info("已发布 decision.intent 事件")

    async def _handle_fallback(self, normalized_message: "NormalizedMessage") -> None:
        """
        处理降级逻辑

        保留 fallback_mode 配置：
        - simple: 简单回复
        - echo: 复读用户输入
        - error: 抛异常
        """
        if self.fallback_mode == "simple":
            # 简单降级：返回原始文本
            await self._create_intent(normalized_message.text, normalized_message)
        elif self.fallback_mode == "echo":
            # 回声降级：重复用户输入
            await self._create_intent(f"你说：{normalized_message.text}", normalized_message)
        else:
            # 错误降级：抛出异常
            raise RuntimeError("LLM 请求失败，且未配置降级模式")

    async def _create_intent(self, text: str, normalized_message: "NormalizedMessage") -> None:
        """
        创建简单 Intent 并发布（用于降级场景）

        Args:
            text: 响应文本
            normalized_message: 原始 NormalizedMessage
        """
        intent = Intent(
            original_text=normalized_message.text,
            response_text=text,
            emotion=EmotionType.NEUTRAL,
            actions=[IntentAction(type=ActionType.BLINK, params={}, priority=30)],
            metadata={"parser": "llm_fallback"},
        )

        # 发布 decision.intent 事件
        await self._publish_intent(intent, normalized_message)

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
