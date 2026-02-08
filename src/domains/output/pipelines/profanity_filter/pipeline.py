"""
脏话过滤管道（OutputPipeline 版本）

过滤输出文本中的敏感词，适用于 TTS 和字幕输出。

3域架构中的位置：
- Output Domain (Output Domain): 在 ExpressionParameters → OutputProvider 转换中处理文本
- 在 ExpressionGenerator.generate() 之后、OutputProvider.render() 之前调用
- 用于过滤输出文本中的脏话和敏感词

功能：
- 过滤中文脏话
- 支持自定义敏感词列表
- 支持从文件加载敏感词
- 替换为自定义文本（默认：【已过滤】）
- 同时处理 tts_text 和 subtitle_text
"""

import os
from typing import Any, Dict, Optional, Set

from src.domains.output.parameters.render_parameters import ExpressionParameters
from src.domains.output.pipelines.base import OutputPipelineBase, PipelineStats


class ProfanityFilterOutputPipeline(OutputPipelineBase):
    """
    敏感词过滤管道（OutputPipeline 版本）

    检测并过滤 ExpressionParameters 中的敏感词。
    支持从配置或文件加载敏感词列表。
    """

    priority = 100  # 高优先级，确保在输出前过滤

    # 扩展统计信息，添加过滤计数
    class ExtendedPipelineStats(PipelineStats):
        """扩展的统计信息，包含过滤计数"""

        filtered_words_count: int = 0

    def __init__(self, config: Dict[str, Any]):
        """
        初始化脏话过滤管道

        Args:
            config: 配置字典，支持以下键:
                words (List[str]): 敏感词列表
                replacement (str): 替换文本 (默认: "【已过滤】")
                words_file (str): 敏感词文件路径（每行一个词）
                filter_tts (bool): 是否过滤TTS文本 (默认: True)
                filter_subtitle (bool): 是否过滤字幕文本 (默认: True)
                case_sensitive (bool): 是否区分大小写 (默认: False)
                drop_on_match (bool): 匹配到敏感词是否丢弃整个消息 (默认: False)
        """
        super().__init__(config)

        self._replacement = self.config.get("replacement", "【已过滤】")
        self._filter_tts = self.config.get("filter_tts", True)
        self._filter_subtitle = self.config.get("filter_subtitle", True)
        self._case_sensitive = self.config.get("case_sensitive", False)
        self._drop_on_match = self.config.get("drop_on_match", False)

        # 使用扩展的统计信息
        self._stats = self.ExtendedPipelineStats()

        # 加载敏感词列表
        self._profanity_words: Set[str] = set()
        self._load_profanity_words()

        self.logger.info(
            f"ProfanityFilterOutputPipeline 初始化: "
            f"敏感词数量={len(self._profanity_words)}, "
            f"替换文本='{self._replacement}', "
            f"过滤TTS={self._filter_tts}, "
            f"过滤字幕={self._filter_subtitle}, "
            f"丢弃消息={self._drop_on_match}"
        )

    def _load_profanity_words(self) -> None:
        """加载敏感词列表"""
        # 从配置加载
        words_from_config = self.config.get("words", [])
        if isinstance(words_from_config, list):
            self._profanity_words.update(words_from_config)

        # 从文件加载（支持两种配置键：words_file 和 wordlist_file）
        words_file = self.config.get("words_file") or self.config.get("wordlist_file")
        if words_file:
            file_path = os.path.abspath(words_file)
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        words_from_file = [line.strip() for line in f if line.strip()]
                        self._profanity_words.update(words_from_file)
                        self.logger.info(f"从文件加载敏感词: {file_path} ({len(words_from_file)} 个)")
                except Exception as e:
                    self.logger.error(f"加载敏感词文件失败: {e}", exc_info=True)
            else:
                self.logger.warning(f"敏感词文件不存在: {file_path}")

        # 处理大小写
        if not self._case_sensitive:
            self._profanity_words = {word.lower() for word in self._profanity_words}

    async def _process(self, params: ExpressionParameters) -> Optional[ExpressionParameters]:
        """
        处理参数，过滤敏感词

        Args:
            params: 待处理的 ExpressionParameters

        Returns:
            处理后的 ExpressionParameters，或 None（如果配置了 drop_on_match）
        """
        has_match = False

        # 过滤 TTS 文本
        if self._filter_tts and params.tts_text:
            filtered_text, tts_has_match = self._filter_text(params.tts_text)
            if tts_has_match:
                has_match = True
                params.tts_text = filtered_text
                self.logger.debug(f"TTS文本已过滤: '{filtered_text}'")

        # 过滤字幕文本
        if self._filter_subtitle and params.subtitle_text:
            filtered_text, subtitle_has_match = self._filter_text(params.subtitle_text)
            if subtitle_has_match:
                has_match = True
                params.subtitle_text = filtered_text
                self.logger.debug(f"字幕文本已过滤: '{filtered_text}'")

        # 如果匹配到敏感词且配置了丢弃
        if has_match and self._drop_on_match:
            self._stats.dropped_count += 1
            self.logger.info("敏感词过滤: 消息已丢弃（配置了 drop_on_match）")
            return None

        return params

    def _filter_text(self, text: str) -> tuple[str, bool]:
        """
        过滤文本中的敏感词

        Args:
            text: 待过滤的文本

        Returns:
            (过滤后的文本, 是否匹配到敏感词)
        """
        if not self._profanity_words:
            return text, False

        filtered_text = text
        has_match = False
        matched_words = set()

        # 检查每个敏感词
        for profanity_word in sorted(self._profanity_words, key=len, reverse=True):
            search_text = filtered_text if self._case_sensitive else filtered_text.lower()
            search_word = profanity_word if self._case_sensitive else profanity_word.lower()

            if search_word in search_text:
                has_match = True
                matched_words.add(profanity_word)
                # 替换敏感词（保持原文大小写）
                if self._case_sensitive:
                    filtered_text = filtered_text.replace(profanity_word, self._replacement)
                else:
                    # 不区分大小写时，需要更复杂的替换逻辑
                    import re

                    pattern = re.compile(re.escape(profanity_word), re.IGNORECASE)
                    filtered_text = pattern.sub(self._replacement, filtered_text)

        if has_match:
            self._stats.filtered_words_count += len(matched_words)
            self.logger.debug(f"检测到敏感词: {matched_words}")

        return filtered_text, has_match

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息"""
        info = super().get_info()
        info.update(
            {
                "replacement": self._replacement,
                "filter_tts": self._filter_tts,
                "filter_subtitle": self._filter_subtitle,
                "case_sensitive": self._case_sensitive,
                "drop_on_match": self._drop_on_match,
                "profanity_words_count": len(self._profanity_words),
            }
        )
        return info

    def add_profanity_word(self, word: str) -> None:
        """
        添加敏感词

        Args:
            word: 敏感词
        """
        if not self._case_sensitive:
            word = word.lower()
        self._profanity_words.add(word)
        self.logger.info(f"添加敏感词: '{word}'")

    def remove_profanity_word(self, word: str) -> None:
        """
        移除敏感词

        Args:
            word: 敏感词
        """
        if not self._case_sensitive:
            word = word.lower()
        self._profanity_words.discard(word)
        self.logger.info(f"移除敏感词: '{word}'")

    def get_profanity_words(self) -> Set[str]:
        """获取所有敏感词"""
        return self._profanity_words.copy()
