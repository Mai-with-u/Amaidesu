"""Session 选择器

智能选择模拟器使用的 session_id：
- 0 个活跃 session → 返回 fallback_id
- 1 个活跃 session → 使用该 session_id
- 多个活跃 session → 使用 last_active 最新的（最活跃的）

通过 ContextService.list_sessions() 查询，60 秒 TTL 缓存避免频繁查询。
"""

import time
from typing import Optional

from src.modules.context.service import ContextService
from src.modules.logging import get_logger


class SessionSelector:
    """Session 选择器

    根据 ContextService 中活跃的 session 列表，
    智能选择模拟器应该关联的 session_id。

    架构合规：不订阅事件，不写入 ContextService。
    """

    _CACHE_TTL_S = 60.0

    def __init__(self, context_service: Optional[ContextService] = None):
        self._ctx = context_service
        self._logger = get_logger("SessionSelector")

        self._cached_session: Optional[str] = None
        self._cache_expires_at: float = 0.0

    async def select_session(self, fallback_id: str = "simulated_viewers") -> str:
        """选择一个活跃的 session_id

        无 ContextService 时直接返回 fallback_id。
        """
        # 无 ContextService → 直降 fallback
        if self._ctx is None:
            return fallback_id
        # 缓存命中
        now = time.time()
        if self._cached_session is not None and now < self._cache_expires_at:
            return self._cached_session

        try:
            sessions = await self._ctx.list_sessions(active_only=True)
        except Exception as e:
            self._logger.warning(f"查询活跃 session 失败: {e}，使用 fallback")
            self._cached_session = fallback_id
            self._cache_expires_at = time.time() + self._CACHE_TTL_S
            return fallback_id

        if not sessions:
            self._logger.debug("无活跃 session，使用 fallback")
            self._cached_session = fallback_id
        elif len(sessions) == 1:
            self._cached_session = sessions[0].session_id
        else:
            # 多个 session，选 last_active 最新的
            best = max(sessions, key=lambda s: s.last_active)
            self._cached_session = best.session_id

        self._cache_expires_at = time.time() + self._CACHE_TTL_S
        return self._cached_session

    def invalidate_cache(self) -> None:
        """强制下次重新选择"""
        self._cached_session = None
        self._cache_expires_at = 0.0

    def get_current_session(self) -> Optional[str]:
        """获取当前缓存的 session（可能为 None）"""
        if self._cached_session is not None and time.time() < self._cache_expires_at:
            return self._cached_session
        return None
