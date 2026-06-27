"""相似文本过滤管道（Input 阶段）

过滤短时间内内容高度相似的重复消息。
"""

import difflib
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from src.modules.pipeline import Pipeline, pipeline
from src.modules.types.base.normalized_message import NormalizedMessage


@pipeline("similar_filter")
class SimilarFilterInputPipeline(Pipeline[NormalizedMessage]):
    """相似文本过滤管道。"""

    priority = 500

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self._similarity_threshold = self.config.get("similarity_threshold", 0.85)
        self._time_window = self.config.get("time_window", 5.0)
        self._min_text_length = self.config.get("min_text_length", 3)
        self._cross_user_filter = self.config.get("cross_user_filter", True)

        self._text_cache: Dict[str, deque] = defaultdict(deque)
        self._last_cleanup_time = time.time()

        self.logger.info(
            f"SimilarFilterInputPipeline 初始化: "
            f"相似度阈值={self._similarity_threshold}, "
            f"时间窗口={self._time_window}秒, "
            f"跨用户过滤={self._cross_user_filter}"
        )

    async def _process(self, item: NormalizedMessage) -> Optional[NormalizedMessage]:
        self._clean_expired_texts()

        if len(item.text) < self._min_text_length:
            self.logger.debug(f"文本长度 {len(item.text)} 小于最小要求 {self._min_text_length}，跳过过滤")
            return item

        user_id = item.user_id or "unknown"
        group_id = item.source or "default"
        now = time.time()

        if self._has_similar_text(group_id, user_id, item.text):
            self._stats.dropped_count += 1
            self.logger.info(
                f"相似文本过滤: text_preview='{item.text[:50]}{'...' if len(item.text) > 50 else ''}', "
                f"user_id={user_id}, group_id={group_id}"
            )
            return None

        self._text_cache[group_id].append((now, item.text, user_id))
        self.logger.debug(f"文本通过过滤并添加到缓存: {item.text[:30]!r}")
        return item

    def _clean_expired_texts(self) -> None:
        now = time.time()

        if now - self._last_cleanup_time < self._time_window / 2:
            return

        self._last_cleanup_time = now
        cutoff_time = now - self._time_window

        for group_id in list(self._text_cache.keys()):
            while self._text_cache[group_id] and self._text_cache[group_id][0][0] < cutoff_time:
                self._text_cache[group_id].popleft()

            if not self._text_cache[group_id]:
                del self._text_cache[group_id]

    def _has_similar_text(self, group_id: str, user_id: str, text: str) -> bool:
        if group_id not in self._text_cache:
            return False

        now = time.time()
        cutoff_time = now - self._time_window

        for cached_ts, cached_text, cached_user_id in self._text_cache[group_id]:
            if cached_ts < cutoff_time:
                continue

            if not self._cross_user_filter and cached_user_id != user_id:
                continue

            similarity = self._calculate_similarity(text, cached_text)

            if similarity >= self._similarity_threshold:
                self.logger.debug(
                    f"发现相似文本 (相似度={similarity:.2f}): '{text[:30]}...' vs '{cached_text[:30]}...'"
                )
                return True

        return False

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        similarity = difflib.SequenceMatcher(None, text1, text2).ratio()

        if text1 in text2 or text2 in text1:
            longer = max(len(text1), len(text2))
            shorter = min(len(text1), len(text2))
            if shorter > 0 and shorter >= longer * 0.5:
                contained_similarity = shorter / longer
                similarity = max(similarity, contained_similarity)

        return similarity

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update(
            {
                "similarity_threshold": self._similarity_threshold,
                "time_window": self._time_window,
                "min_text_length": self._min_text_length,
                "cross_user_filter": self._cross_user_filter,
                "cache_groups": len(self._text_cache),
                "cached_texts": sum(len(q) for q in self._text_cache.values()),
            }
        )
        return info

    async def reset(self) -> None:
        self._text_cache.clear()
        self._last_cleanup_time = time.time()
        self.reset_stats()
        self.logger.debug("SimilarFilterInputPipeline 已重置状态")
