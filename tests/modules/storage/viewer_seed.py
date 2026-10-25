"""测试造数：按外部账号 ID 给观众记一笔（先解析观众，再按观众 ID 计数），与记账器的写法一致。"""

from __future__ import annotations

from src.modules.storage.repos import ViewerRepo

# 造数不关心按昵称关联的时限，取一个足够长的值
_WINDOW_MS = 90 * 86_400_000


async def seed_viewer(viewers: ViewerRepo, *, platform: str, user_id: str, user_name: str, timestamp_ms: int) -> int:
    """按账号 ID 解析（必要时新建）观众，返回内部观众 ID。"""
    result = await viewers.resolve_viewer(
        platform=platform,
        id_kind="account",
        external_id=user_id,
        user_name=user_name,
        timestamp_ms=timestamp_ms,
        name_match_window_ms=_WINDOW_MS,
    )
    return result.viewer_id


async def seed_message(viewers: ViewerRepo, *, platform: str, user_id: str, user_name: str, timestamp_ms: int) -> int:
    """观众发言一次。"""
    viewer_id = await seed_viewer(
        viewers, platform=platform, user_id=user_id, user_name=user_name, timestamp_ms=timestamp_ms
    )
    await viewers.record_message(viewer_id=viewer_id, timestamp_ms=timestamp_ms)
    return viewer_id


async def seed_gift(viewers: ViewerRepo, *, platform: str, user_id: str, user_name: str, timestamp_ms: int) -> int:
    """观众送礼一次（只记件数与互动，不记付费）。"""
    viewer_id = await seed_viewer(
        viewers, platform=platform, user_id=user_id, user_name=user_name, timestamp_ms=timestamp_ms
    )
    await viewers.record_gift(viewer_id=viewer_id, timestamp_ms=timestamp_ms)
    return viewer_id


async def seed_replied(viewers: ViewerRepo, *, platform: str, user_id: str, timestamp_ms: int) -> int:
    """主播回复已建档的观众一次。"""
    viewer_id = await viewers.find_viewer_id(platform=platform, external_id=user_id)
    assert viewer_id is not None, f"被回复的观众未建档：{platform}/{user_id}"
    await viewers.record_replied(viewer_id=viewer_id, timestamp_ms=timestamp_ms)
    return viewer_id
