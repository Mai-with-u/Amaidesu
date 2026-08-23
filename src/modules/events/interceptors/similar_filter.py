"""
SimilarFilterInterceptor —— 相似文本过滤事件拦截器（v2 / Wave 5）

由 ``src/stages/input/pipelines/similar_filter/pipeline.py`` 转换（Pipeline → Interceptor）。

差异：旧版是 ``Pipeline[NormalizedMessage]._process``；新版拦截事件 payload，
对其中文本字段做相似度匹配，超阈值返回 ``None`` 丢弃事件。

verbatim 边界：相似度算法（difflib SequenceMatcher + 包含关系加权）、
跨用户过滤策略 —— 未改动。
仅调整：基类（Pipeline → EventInterceptor）、接口签名（item → event_name/payload/source）、
group_id 改为 source 字段。
"""

from __future__ import annotations

import difflib
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional, Tuple

from src.modules.events.interceptors.base import EventInterceptor
from src.modules.logging import get_logger

_USER_ID_KEYS = ("user_id", "open_id", "uid", "sender_id")
_TEXT_KEYS = ("text", "content", "msg", "message")


class SimilarFilterInterceptor(EventInterceptor):
    """
    相似文本过滤事件拦截器

    - 按 (source, user_id) 分组的滑动窗口缓存
    - 与缓存中任一文本相似度 ≥ ``similarity_threshold`` 则丢弃（返回 ``None``）
    - 低于 ``min_text_length`` 跳过过滤直接放行

    配置参数：
        similarity_threshold (float): 相似度阈值 0-1（默认 0.85）
        time_window (float): 缓存窗口大小（秒，默认 5.0）
        min_text_length (int): 最小处理文本长度（默认 3）
        cross_user_filter (bool): 是否跨用户比较（默认 True）
    """

    def __init__(
        self,
        similarity_threshold: float = 0.85,
        time_window: float = 5.0,
        min_text_length: int = 3,
        cross_user_filter: bool = True,
    ) -> None:
        self._similarity_threshold = similarity_threshold
        self._time_window = time_window
        self._min_text_length = min_text_length
        self._cross_user_filter = cross_user_filter

        self._text_cache: Dict[str, Deque[Tuple[float, str, str]]] = defaultdict(deque)
        self._last_cleanup_time = time.time()

        self.logger = get_logger("SimilarFilterInterceptor")
        self.logger.info(
            f"SimilarFilterInterceptor 初始化: "
            f"阈值={self._similarity_threshold}, 窗口={self._time_window}s, "
            f"跨用户={self._cross_user_filter}"
        )

    @property
    def name(self) -> str:
        return "similar_filter"

    async def intercept(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        text = self._extract_text(payload)
        if not text or len(text) < self._min_text_length:
            self.logger.debug(f"文本长度 {len(text) if text else 0} 小于最小要求 {self._min_text_length}，跳过过滤")
            return payload

        user_id = self._extract_user_id(payload)
        group_id = source or "default"
        now = time.time()

        self._clean_expired_texts()

        if self._has_similar_text(group_id, user_id, text):
            self.logger.info(
                f"相似文本过滤: text_preview='{text[:50]}{'...' if len(text) > 50 else ''}', "
                f"user_id={user_id}, group_id={group_id}"
            )
            return None

        self._text_cache[group_id].append((now, text, user_id))
        return payload

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

    @staticmethod
    def _calculate_similarity(text1: str, text2: str) -> float:
        similarity = difflib.SequenceMatcher(None, text1, text2).ratio()
        if text1 in text2 or text2 in text1:
            longer = max(len(text1), len(text2))
            shorter = min(len(text1), len(text2))
            if shorter > 0 and shorter >= longer * 0.5:
                contained_similarity = shorter / longer
                similarity = max(similarity, contained_similarity)
        return similarity

    @staticmethod
    def _extract_user_id(payload: Dict[str, Any]) -> str:
        for key in _USER_ID_KEYS:
            value = payload.get(key)
            if value is not None and value != "":
                return str(value)
        return "unknown"

    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> str:
        for key in _TEXT_KEYS:
            value = payload.get(key)
            if isinstance(value, str):
                return value
        user = payload.get("user")
        if isinstance(user, dict):
            name = user.get("name", "")
            if name:
                return f"[{name}]"
        return ""

    async def reset(self) -> None:
        """重置缓存（便于测试）"""
        self._text_cache.clear()
        self._last_cleanup_time = time.time()
        self.logger.debug("SimilarFilterInterceptor 已重置")


__all__ = ["SimilarFilterInterceptor"]
