"""
RuleEngineDecisionProvider - 规则引擎决策提供者

职责:
- 本地规则引擎
- 关键词匹配
- 正则表达式匹配
- 规则配置加载（JSON/YAML）
"""

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

from pydantic import Field

from src.domains.decision.intent import Intent
from src.modules.config.schemas.schemas.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types import ActionType, EmotionType, IntentAction
from src.modules.types.base.decision_provider import DecisionProvider

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.types.base.normalized_message import NormalizedMessage


class RuleEngineDecisionProvider(DecisionProvider):
    """
    规则引擎决策提供者

    使用本地规则进行决策，无需外部API。

    配置示例:
        ```toml
        [decision.rule_engine]
        rules_file = "config/rules.json"
        default_response = "我不理解你的意思"
        case_sensitive = false
        match_mode = "any"  # "any" 或 "all"
        ```

    规则文件示例 (JSON):
        ```json
        {
            "rules": [
                {
                    "name": "问候",
                    "keywords": ["你好", "hello", "hi"],
                    "response": "你好！很高兴见到你~",
                    "priority": 100
                },
                {
                    "name": "感谢",
                    "regex": "^(谢谢|感谢|多谢)",
                    "response": "不客气！",
                    "priority": 90
                }
            ]
        }
        ```

    属性:
        rules_file: 规则文件路径
        default_response: 默认响应
        case_sensitive: 是否区分大小写
        match_mode: 匹配模式（"any" 或 "all"）
    """

    class ConfigSchema(BaseProviderConfig):
        """规则引擎决策Provider配置Schema

        使用本地规则进行决策，无需外部API。
        """

        type: Literal["rule_engine"] = "rule_engine"
        rules_file: str = Field(default="config/rules.json", description="规则文件路径（JSON或YAML格式）")
        default_response: str = Field(default="我不理解你的意思", description="默认响应文本")
        case_sensitive: bool = Field(default=False, description="是否区分大小写")
        match_mode: Literal["any", "all"] = Field(default="any", description="匹配模式")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化RuleEngineDecisionProvider

        Args:
            config: 配置字典
        """
        # 使用 Pydantic Schema 验证配置
        self.typed_config = self.ConfigSchema(**config)
        self.logger = get_logger("RuleEngineDecisionProvider")

        # 规则配置
        self.rules_file = self.typed_config.rules_file
        self.default_response = self.typed_config.default_response
        self.case_sensitive = self.typed_config.case_sensitive
        self.match_mode = self.typed_config.match_mode

        # 规则列表
        self.rules: List[Dict[str, Any]] = []

        # 统计信息
        self._total_requests = 0
        self._matched_rules = 0
        self._unmatched_requests = 0

        # EventBus引用（用于事件通知）
        self._event_bus: Optional["EventBus"] = None

    async def setup(
        self, event_bus: "EventBus", config: Dict[str, Any], dependencies: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        设置RuleEngineDecisionProvider

        Args:
            event_bus: EventBus实例
            config: Provider配置（忽略，使用__init__传入的config）
            dependencies: 可选的依赖注入（未使用）
        """
        self._event_bus = event_bus
        self.logger.info("初始化RuleEngineDecisionProvider...")

        # 加载规则文件
        try:
            await self._load_rules()
            self.logger.info(f"成功加载 {len(self.rules)} 条规则")
        except Exception as e:
            self.logger.error(f"加载规则文件失败: {e}", exc_info=True)
            # 使用空规则列表，但仍能响应默认响应
            self.rules = []

        # 验证配置
        if self.match_mode not in ["any", "all"]:
            self.logger.warning(f"match_mode配置无效: {self.match_mode}, 使用默认值 'any'")
            self.match_mode = "any"

        self.logger.info("RuleEngineDecisionProvider初始化完成")

    async def _load_rules(self):
        """
        加载规则文件

        支持JSON和YAML格式。

        Raises:
            FileNotFoundError: 如果规则文件不存在
            ValueError: 如果规则格式无效
        """
        import os

        if not os.path.exists(self.rules_file):
            raise FileNotFoundError(f"规则文件不存在: {self.rules_file}")

        with open(self.rules_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 根据文件扩展名判断格式
        if self.rules_file.endswith(".json"):
            data = json.loads(content)
        elif self.rules_file.endswith((".yml", ".yaml")):
            import yaml

            data = yaml.safe_load(content)
        else:
            raise ValueError(f"不支持的规则文件格式: {self.rules_file}")

        # 验证数据结构
        if "rules" not in data:
            raise ValueError("规则文件必须包含 'rules' 字段")

        # 验证每条规则
        for i, rule in enumerate(data["rules"]):
            if "keywords" not in rule and "regex" not in rule:
                raise ValueError(f"规则 #{i} 必须包含 'keywords' 或 'regex' 字段")

            if "response" not in rule:
                raise ValueError(f"规则 #{i} 必须包含 'response' 字段")

        self.rules = data["rules"]

        # 按优先级排序（降序）
        self.rules.sort(key=lambda r: r.get("priority", 0), reverse=True)

    async def decide(self, normalized_message: "NormalizedMessage") -> "Intent":
        """
        进行决策（通过规则引擎匹配）

        Args:
            normalized_message: 标准化消息

        Returns:
            Intent: 决策意图（规则匹配的响应）
        """
        self._total_requests += 1
        text = normalized_message.text

        # 准备匹配文本
        match_text = text if self.case_sensitive else text.lower()

        # 按优先级尝试匹配规则
        for rule in self.rules:
            if await self._match_rule(rule, match_text, normalized_message):
                self._matched_rules += 1
                self.logger.debug(f"匹配规则: {rule.get('name', 'unnamed')}")
                response_text = rule["response"]
                return self._create_intent(response_text, normalized_message)

        # 没有匹配的规则，使用默认响应
        self._unmatched_requests += 1
        self.logger.debug("没有匹配的规则，使用默认响应")
        return self._create_intent(self.default_response, normalized_message)

    async def _match_rule(self, rule: Dict[str, Any], text: str, normalized_message: "NormalizedMessage") -> bool:
        """
        检查规则是否匹配

        Args:
            rule: 规则字典
            text: 要匹配的文本
            normalized_message: NormalizedMessage（用于元数据匹配）

        Returns:
            bool: 是否匹配
        """
        # 关键词匹配
        if "keywords" in rule:
            keywords = rule["keywords"]
            if not isinstance(keywords, list):
                keywords = [keywords]

            # 准备关键词
            match_keywords = keywords if self.case_sensitive else [k.lower() for k in keywords]

            # 检查匹配
            if self.match_mode == "any":
                if any(keyword in text for keyword in match_keywords):
                    return True
            elif self.match_mode == "all":
                if all(keyword in text for keyword in match_keywords):
                    return True

        # 正则表达式匹配
        if "regex" in rule:
            regex_pattern = rule["regex"]
            flags = 0 if self.case_sensitive else re.IGNORECASE

            try:
                if re.search(regex_pattern, text, flags):
                    return True
            except re.error as e:
                self.logger.error(f"正则表达式错误: {regex_pattern}, {e}")

        # 元数据匹配
        if "metadata_match" in rule:
            metadata_rules = rule["metadata_match"]
            for key, value in metadata_rules.items():
                if normalized_message.metadata.get(key) != value:
                    return False
            return True

        return False

    def _create_intent(self, text: str, normalized_message: "NormalizedMessage") -> Intent:
        """
        创建Intent对象

        Args:
            text: 响应文本
            normalized_message: 原始NormalizedMessage

        Returns:
            Intent实例
        """
        # 简单规则：默认中性情感，眨眼动作
        return Intent(
            original_text=normalized_message.text,
            response_text=text,
            emotion=EmotionType.NEUTRAL,
            actions=[IntentAction(type=ActionType.BLINK, params={}, priority=30)],
            metadata={"parser": "rule_engine"},
        )

    async def cleanup(self) -> None:
        """
        清理资源

        输出统计信息。
        """
        self.logger.info("清理RuleEngineDecisionProvider...")

        # 输出统计信息
        match_rate = self._matched_rules / self._total_requests * 100 if self._total_requests > 0 else 0
        self.logger.info(
            f"统计: 总请求={self._total_requests}, "
            f"匹配={self._matched_rules}, 未匹配={self._unmatched_requests}, "
            f"匹配率={match_rate:.1f}%"
        )

        self.logger.info("RuleEngineDecisionProvider已清理")

    def get_info(self) -> Dict[str, Any]:
        """
        获取Provider信息

        Returns:
            Provider信息字典
        """
        return {
            "name": "RuleEngineDecisionProvider",
            "version": "1.0.0",
            "rules_file": self.rules_file,
            "rules_count": len(self.rules),
            "case_sensitive": self.case_sensitive,
            "match_mode": self.match_mode,
            "total_requests": self._total_requests,
            "matched_rules": self._matched_rules,
            "unmatched_requests": self._unmatched_requests,
        }
