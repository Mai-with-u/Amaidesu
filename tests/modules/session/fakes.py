"""场次域测试替身：LiveSessionManager 的可控替身。

盖章拦截器与 StorageLedger 都只依赖"解析归属场次主键"这一个能力，本替身
把"进行中场次"与"最近一场已结束的场次"分开给出，便于覆盖场后发言（上一场
已结束、本场未开）归到刚结束那一场的场景。
"""

from __future__ import annotations

from typing import Optional


class FakeSessionManager:
    """LiveSessionManager 替身：区分进行中场次与最近一场已结束的场次。

    Args:
        active_pk: 进行中场次主键；None 表示当下没有进行中场次
        last_ended_pk: 最近一场已结束的场次主键；None 表示一场都没结束过
        platform: 装配期平台常量（主播发言行取它）
        raise_on_resolve: 归属解析抛异常（验证拦截器异常放行）
    """

    def __init__(
        self,
        active_pk: Optional[int] = None,
        *,
        last_ended_pk: Optional[int] = None,
        platform: str = "bilibili",
        raise_on_resolve: bool = False,
    ) -> None:
        self.active_pk = active_pk
        self.last_ended_pk = last_ended_pk
        self.platform = platform
        self.raise_on_resolve = raise_on_resolve
        self.resolve_calls = 0

    async def resolve_pk(self) -> Optional[int]:
        """活跃语义：进行中场次主键，没有则 None。"""
        self.resolve_calls += 1
        if self.raise_on_resolve:
            raise RuntimeError("解析失败")
        return self.active_pk

    async def resolve_attribution_pk(self) -> Optional[int]:
        """归属语义：进行中场次优先，否则最近一场已结束的场次。"""
        self.resolve_calls += 1
        if self.raise_on_resolve:
            raise RuntimeError("解析失败")
        if self.active_pk is not None:
            return self.active_pk
        return self.last_ended_pk


__all__ = ["FakeSessionManager"]
