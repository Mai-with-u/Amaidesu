"""
Emotion Analyzer - Layer 5: 情感分析（统一入口）

职责:
- 统一的情感分析入口（Layer 5 唯一情感分析位置）
- 合并原有的 Layer 5 情感判断逻辑
- 合并 Avatar.TriggerStrategyEngine 的 LLM 分析功能
- 支持规则分析（快速、确定性）
- 支持 LLM 分析（可选、智能）
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, TYPE_CHECKING

from src.layers.understanding.intent import EmotionType
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.llm_service import LLMService


@dataclass
class EmotionResult:
    """情感分析结果

    Attributes:
        emotion: 情感类型
        confidence: 置信度 (0.0-1.0)
        source: 结果来源 ("rule", "llm", "default")
    """

    emotion: EmotionType
    confidence: float
    source: str


class EmotionAnalyzer:
    """统一的情感分析器

    合并原来的：
    - Layer 5 的情感判断逻辑
    - Avatar.TriggerStrategyEngine 的 LLM 分析
    """

    def __init__(self, config: Dict[str, Any], llm_service: Optional["LLMService"] = None):
        self.config = config
        self.llm_service = llm_service
        self.logger = get_logger("EmotionAnalyzer")

        # 可配置的分析策略
        self.use_llm = config.get("use_llm", False)
        self.use_rules = config.get("use_rules", True)

        # 规则分析器
        if self.use_rules:
            self._init_rule_analyzer()

        # LLM 分析配置
        if self.use_llm:
            self.llm_type = config.get("llm_type", "llm_fast")
            self.llm_temperature = config.get("llm_temperature", 0.3)

        self.logger.info(f"EmotionAnalyzer 初始化完成 (rules={self.use_rules}, llm={self.use_llm})")

    def _init_rule_analyzer(self):
        """初始化规则分析器"""
        # 情感关键词规则
        self.emotion_keywords: Dict[EmotionType, list[str]] = {
            EmotionType.HAPPY: [
                "开心",
                "快乐",
                "高兴",
                "哈哈",
                "呵呵",
                "嘻嘻",
                "棒",
                "太好了",
                "耶",
                "开心",
                "幸福",
                "喜欢",
                "不错",
                "赞",
                "爱",
                "满意",
                "好",
                "yes",
                "good",
            ],
            EmotionType.SAD: [
                "难过",
                "悲伤",
                "伤心",
                "哭泣",
                "哭",
                "呜呜",
                "失望",
                "沮丧",
                "痛苦",
                "不好",
                "糟糕",
                "遗憾",
            ],
            EmotionType.ANGRY: [
                "生气",
                "愤怒",
                "恼火",
                "讨厌",
                "讨厌",
                "恨",
                "烦",
                "烦人",
                "气死",
                "滚",
                "去死",
                "可恶",
            ],
            EmotionType.SURPRISED: [
                "惊讶",
                "意外",
                "哇",
                "天啊",
                "天哪",
                "啊",
                "什么",
                "居然",
                "真的吗",
                "不敢相信",
                "震惊",
            ],
            EmotionType.EXCITED: [
                "激动",
                "兴奋",
                "期待",
                "太棒了",
                "太好了",
                "终于",
                "成功",
                "胜利",
                "胜利",
                "赢了",
            ],
            EmotionType.CONFUSED: [
                "疑惑",
                "不懂",
                "什么意思",
                "为什么",
                "怎么",
                "不明白",
                "困惑",
                "晕",
                "复杂",
                "难理解",
            ],
            EmotionType.LOVE: [
                "爱",
                "喜欢",
                "爱慕",
                "心动",
                "亲",
                "抱抱",
                "想你了",
                "想念",
                "宝贝",
                "亲爱的",
                "爱你",
            ],
        }

    def _analyze_by_rules(self, text: str) -> EmotionResult:
        """基于规则的情感分析

        Args:
            text: 输入文本

        Returns:
            情感分析结果
        """
        text_lower = text.lower()

        # 匹配情感关键词
        for emotion, keywords in self.emotion_keywords.items():
            for keyword in keywords:
                if keyword in text_lower:
                    self.logger.debug(f"规则匹配: {emotion} (关键词: {keyword})")
                    return EmotionResult(
                        emotion=emotion,
                        confidence=0.8,
                        source="rule",
                    )

        # 默认中性
        return EmotionResult(
            emotion=EmotionType.NEUTRAL,
            confidence=0.5,
            source="rule",
        )

    async def _analyze_by_llm(self, text: str, context: Optional[Dict] = None) -> EmotionResult:
        """基于 LLM 的情感分析

        Args:
            text: 输入文本
            context: 上下文信息

        Returns:
            情感分析结果
        """
        if not self.llm_service:
            self.logger.warning("LLM 服务不可用，回退到规则分析")
            return self._analyze_by_rules(text)

        try:
            prompt = f"""分析以下文本的情感，选择最合适的情感类型。

文本: {text}

可用的情感类型:
- neutral: 中性、平淡
- happy: 开心、快乐、高兴
- sad: 悲伤、难过、伤心
- angry: 生气、愤怒、恼火
- surprised: 惊讶、意外
- excited: 激动、兴奋
- confused: 困惑、疑惑
- love: 喜爱、喜欢

请严格按照以下 JSON 格式返回，不要包含其他内容：
{{
    "emotion": "happy",
    "confidence": 0.9
}}
"""

            result = await self.llm_service.chat(
                prompt=prompt,
                backend=self.llm_type,
                temperature=self.llm_temperature,
            )

            if not result.success:
                self.logger.warning(f"LLM 调用失败: {result.error}")
                return EmotionResult(
                    emotion=EmotionType.NEUTRAL,
                    confidence=0.3,
                    source="llm_error",
                )

            # 解析 JSON 结果
            import json

            content = result.content.strip() if result.content else ""

            # 提取 JSON（处理可能的 markdown 代码块）
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.rfind("```")
                content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.rfind("```")
                content = content[start:end].strip()

            try:
                llm_result = json.loads(content)
                emotion_str = llm_result.get("emotion", "neutral")
                confidence = llm_result.get("confidence", 0.5)

                # 映射字符串到 EmotionType
                emotion = EmotionType(emotion_str.lower())

                return EmotionResult(
                    emotion=emotion,
                    confidence=min(max(confidence, 0.0), 1.0),
                    source="llm",
                )

            except (json.JSONDecodeError, ValueError):
                self.logger.error(f"LLM 返回的 JSON 无效: {content}")
                return EmotionResult(
                    emotion=EmotionType.NEUTRAL,
                    confidence=0.3,
                    source="llm_parse_error",
                )

        except Exception as e:
            self.logger.error(f"LLM 情感分析失败: {e}", exc_info=True)
            return EmotionResult(
                emotion=EmotionType.NEUTRAL,
                confidence=0.3,
                source="llm_error",
            )

    async def analyze(self, text: str, context: Optional[Dict] = None) -> EmotionResult:
        """分析文本情感（唯一入口）

        Args:
            text: 输入文本
            context: 上下文信息

        Returns:
            情感分析结果
        """
        if not text or not text.strip():
            return EmotionResult(
                emotion=EmotionType.NEUTRAL,
                confidence=1.0,
                source="default",
            )

        # 1. 规则分析（快速、确定性）
        if self.use_rules:
            rule_result = self._analyze_by_rules(text)
            if rule_result.confidence > 0.8:
                return rule_result

        # 2. LLM 分析（可选、智能）
        if self.use_llm:
            return await self._analyze_by_llm(text, context)

        # 3. 默认回退
        return EmotionResult(
            emotion=EmotionType.NEUTRAL,
            confidence=0.5,
            source="default",
        )
