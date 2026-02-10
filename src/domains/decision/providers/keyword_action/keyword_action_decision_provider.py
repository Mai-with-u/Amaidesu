"""
KeywordActionDecisionProvider - 关键词动作决策Provider

基于规则的关键词匹配决策Provider，根据配置的关键词规则生成Intent。
通过 Intent.actions 传递动作，不直接触发 Output Provider。

职责:
- 关键词匹配引擎（精确/前缀/后缀/包含匹配）
- 冷却时间管理
- 生成包含动作的 Intent

配置示例:
    ```toml
    [providers.decision.keyword_action]
    type = "keyword_action"

    [[providers.decision.keyword_action.actions]]
    name = "微笑动作"
    enabled = true
    keywords = ["微笑", "smile", "😊"]
    match_mode = "anywhere"
    cooldown = 3.0
    action_type = "hotkey"
    action_params = { key = "smile" }

    [[providers.decision.keyword_action.actions]]
    name = "打招呼"
    enabled = true
    keywords = ["你好", "hello", "hi"]
    match_mode = "exact"
    cooldown = 5.0
    action_type = "expression"
    action_params = { name = "smile" }
    ```

数据流:
    KeywordActionDecisionProvider.decide()
      -> Intent(actions=[IntentAction(...)])
      -> DECISION_INTENT_GENERATED 事件
      -> OutputCoordinator -> ExpressionGenerator -> ActionMapper
      -> RenderParameters -> OutputProvider.render()
"""

import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.domains.decision.intent import Intent
from src.modules.config.schemas.schemas.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types import ActionType, EmotionType, IntentAction
from src.modules.types.base.decision_provider import DecisionProvider
from src.modules.types.base.normalized_message import NormalizedMessage


class ActionRule(BaseModel):
    """
    动作规则配置

    定义单个关键词匹配规则及其对应的动作。
    """

    name: str = Field(..., description="动作名称（唯一标识符）")
    enabled: bool = Field(default=True, description="是否启用此规则")
    keywords: List[str] = Field(default_factory=list, description="关键词列表")
    match_mode: Literal["exact", "startswith", "endswith", "anywhere"] = Field(
        default="anywhere", description="匹配模式"
    )
    cooldown: float = Field(default=1.0, ge=0.0, description="冷却时间（秒）")
    action_type: ActionType = Field(..., description="动作类型")
    action_params: Dict[str, Any] = Field(default_factory=dict, description="动作参数")
    priority: int = Field(default=50, ge=0, le=100, description="优先级（越高越优先）")

    model_config = ConfigDict(use_enum_values=True)


class KeywordActionDecisionProviderConfig(BaseProviderConfig):
    """
    关键词动作决策Provider配置Schema
    """

    type: Literal["keyword_action"] = "keyword_action"
    actions: List[ActionRule] = Field(default_factory=list, description="动作规则列表")
    global_cooldown: float = Field(default=1.0, ge=0.0, description="全局冷却时间（秒）")
    default_response: str = Field(default="", description="匹配成功时的默认响应文本")


class KeywordActionDecisionProvider(DecisionProvider):
    """
    关键词动作决策Provider

    基于规则的关键词匹配引擎，监听消息并根据配置的关键词规则生成包含动作的Intent。

    特性:
    - 支持多种匹配模式（精确/前缀/后缀/包含）
    - 支持冷却时间管理（全局和单个规则）
    - 通过 Intent.actions 传递动作到 Output Domain
    - 每个消息只触发第一个匹配的动作

    架构约束（3域数据流）:
    - 订阅 NORMALIZATION_MESSAGE_READY 事件（通过 DecisionCoordinator）
    - 生成 Intent并通过 DECISION_INTENT_GENERATED 事件发布
    - 不直接调用 Output Provider
    - 动作由 Output Domain 的 ActionMapper 处理
    """

    # 配置Schema类
    ConfigSchema = KeywordActionDecisionProviderConfig

    def __init__(self, config: dict):
        """
        初始化 KeywordActionDecisionProvider

        Args:
            config: Provider配置（来自 decision.providers.keyword_action 配置）
        """
        # 使用 Pydantic Schema 验证配置
        self.typed_config = self.ConfigSchema(**config)
        self.config = config
        self.logger = get_logger("KeywordActionDecisionProvider")

        # 加载配置
        self.actions: List[ActionRule] = self.typed_config.actions
        self.global_cooldown: float = self.typed_config.global_cooldown
        self.default_response: str = self.typed_config.default_response

        # 状态追踪
        self.last_triggered_times: Dict[str, float] = {}  # Key: action_name, Value: timestamp

        # 统计信息
        self.match_count = 0
        self.cooldown_skip_count = 0

        self.logger.info(f"KeywordActionDecisionProvider初始化完成，加载了 {len(self.actions)} 个动作规则")

    async def _setup_internal(self):
        """内部设置逻辑"""
        enabled_actions = [a for a in self.actions if a.enabled]
        self.logger.info(
            f"已启用 {len(enabled_actions)}/{len(self.actions)} 个动作规则，全局冷却时间: {self.global_cooldown}s"
        )

    async def decide(self, message: NormalizedMessage) -> Intent:
        """
        决策：根据关键词匹配生成Intent

        Args:
            message: 标准化消息

        Returns:
            Intent: 包含动作的决策意图
        """
        if not message.text:
            return self._create_empty_intent(message)

        text_content = message.text.strip()
        current_time = time.time()

        # 按优先级排序动作规则
        sorted_actions = sorted(self.actions, key=lambda a: a.priority, reverse=True)

        for action_rule in sorted_actions:
            if not action_rule.enabled:
                continue

            action_name = action_rule.name
            cooldown = action_rule.cooldown or self.global_cooldown

            # 检查冷却时间
            last_triggered = self.last_triggered_times.get(action_name, 0)
            if current_time - last_triggered < cooldown:
                self.logger.debug(f"动作 '{action_name}' 在冷却中，跳过")
                self.cooldown_skip_count += 1
                continue

            # 检查关键词匹配
            if self._check_keywords(text_content, action_rule.keywords, action_rule.match_mode):
                self.logger.info(
                    f"关键词匹配成功: '{action_name}' (关键词: {action_rule.keywords}, 模式: {action_rule.match_mode})"
                )
                self.last_triggered_times[action_name] = current_time
                self.match_count += 1

                # 生成包含动作的 Intent
                return self._create_action_intent(message, action_rule)

        # 没有匹配的规则，返回空 Intent
        return self._create_empty_intent(message)

    def _check_keywords(self, text: str, keywords: List[str], mode: str) -> bool:
        """
        根据指定的匹配模式检查文本是否包含关键词

        Args:
            text: 待检查的文本
            keywords: 关键词列表
            mode: 匹配模式（exact/startswith/endswith/anywhere）

        Returns:
            是否匹配成功
        """
        if mode == "exact":
            return text in keywords
        elif mode == "startswith":
            return any(text.startswith(kw) for kw in keywords)
        elif mode == "endswith":
            return any(text.endswith(kw) for kw in keywords)
        # 默认模式 "anywhere"
        else:
            return any(kw in text for kw in keywords)

    def _create_action_intent(self, message: NormalizedMessage, action_rule: ActionRule) -> Intent:
        """
        创建包含动作的 Intent

        Args:
            message: 原始消息
            action_rule: 匹配的动作规则

        Returns:
            Intent: 包含动作的决策意图
        """
        # 构建 IntentAction
        intent_action = IntentAction(
            type=action_rule.action_type,
            params=action_rule.action_params,
            priority=action_rule.priority,
        )

        # 使用默认响应文本或原始文本
        response_text = self.default_response or f"触发动作: {action_rule.name}"

        # 构建 Intent
        from src.domains.decision.intent import SourceContext

        intent = Intent(
            original_text=message.text,
            response_text=response_text,
            emotion=EmotionType.NEUTRAL,
            actions=[intent_action],
            source_context=SourceContext(
                source=message.source,
                data_type=message.data_type,
                user_id=message.user_id if hasattr(message, "user_id") else None,
                user_nickname=message.metadata.get("user_nickname"),
                importance=message.importance,
            ),
            metadata={
                "decision_provider": "keyword_action",
                "action_name": action_rule.name,
                "match_mode": action_rule.match_mode,
                "triggered_by": "keyword_match",
            },
        )

        self.logger.debug(
            f"生成 Intent: action_type={intent_action.type}, "
            f"params={intent_action.params}, priority={intent_action.priority}"
        )

        return intent

    def _create_empty_intent(self, message: NormalizedMessage) -> Intent:
        """
        创建空 Intent（无匹配）

        Args:
            message: 原始消息

        Returns:
            Intent: 空动作的决策意图
        """
        from src.domains.decision.intent import SourceContext

        return Intent(
            original_text=message.text,
            response_text=self.default_response or message.text,
            emotion=EmotionType.NEUTRAL,
            actions=[],
            source_context=SourceContext(
                source=message.source,
                data_type=message.data_type,
                user_id=message.user_id if hasattr(message, "user_id") else None,
                user_nickname=message.metadata.get("user_nickname"),
                importance=message.importance,
            ),
            metadata={"decision_provider": "keyword_action", "triggered_by": "none"},
        )

    async def cleanup(self):
        """清理资源"""
        self.logger.info(
            f"KeywordActionDecisionProvider清理完成，匹配次数: {self.match_count}, 冷却跳过: {self.cooldown_skip_count}"
        )

    # ==================== 调试方法 ====================

    def get_match_count(self) -> int:
        """获取匹配次数（用于测试）"""
        return self.match_count

    def get_cooldown_skip_count(self) -> int:
        """获取冷却跳过次数（用于测试）"""
        return self.cooldown_skip_count

    def reset_stats(self):
        """重置统计信息（用于测试）"""
        self.match_count = 0
        self.cooldown_skip_count = 0
        self.last_triggered_times.clear()

    def get_last_triggered_time(self, action_name: str) -> Optional[float]:
        """获取指定动作的最后触发时间（用于测试）"""
        return self.last_triggered_times.get(action_name)
