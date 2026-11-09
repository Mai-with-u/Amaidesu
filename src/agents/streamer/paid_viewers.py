"""付费观众标注——给消息批里的观众消息行加「（付费观众）」标注。

判据两路：
- **来源自带**：这条消息本身就是付费行为（醒目留言、上舰）——直接算付过费，
  不查表。查表会漏：消息刚到、明细行还没落库时表里查不到。
- **历史付费**：其余观众查付费三表（礼物 / 醒目留言 / 上舰）有无该观众的记录。

标注只挂在"本批"消息行上：本批是要做社交权衡（谁的要求优先满足）的对象。
历史行不带标注——它是已经过去的话，标它只会让模型对着旧话反复权衡。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from src.modules.logging import get_logger

logger = get_logger("PaidViewers")

#: 付费来源的消息类型：这类消息本身就是付费行为
PAID_SOURCE_TYPES = ("super_chat", "guard")

#: 标注文字（附在昵称后）
PAID_VIEWER_NOTE = "（付费观众）"


def identity_key(message: Any) -> str:
    """消息发送者的身份键（平台 + ID 种类 + 外部 ID）。

    同一个人在同平台只有一种外部 ID；ID 种类必须带上——同平台下账号 ID 与
    开放平台 ID 是两套编号，混着比会把两个不同的人认成一人。
    """
    user = getattr(message, "user", None)
    external_id = str(getattr(user, "id", "") or "") if user is not None else ""
    id_kind = str(getattr(user, "id_kind", "") or "") if user is not None else ""
    platform = str(getattr(message, "platform", "") or "")
    return f"{platform}\x00{id_kind}\x00{external_id}"


def is_paid_source(message: Any) -> bool:
    """这条消息本身是不是付费行为（醒目留言 / 上舰）。"""
    return str(getattr(message, "message_type", "") or "") in PAID_SOURCE_TYPES


class PaidViewerLookup:
    """按付费记录查出一批观众里哪些付过费。

    依赖两处只读仓储：观众仓储（外部 ID → 内部观众 ID）与明细仓储
    （哪些观众在付费三表留过记录）。任一依赖缺席时，只保留"来源自带"那一路
    判断（消息本身是 SC / 上舰时照样标）。
    """

    def __init__(self, viewer_repo: Optional[Any] = None, chat_repo: Optional[Any] = None) -> None:
        """初始化。

        Args:
            viewer_repo: ``ViewerRepo``（外部 ID → 观众 ID，只读查询）。
            chat_repo: ``ChatRepo``（付费三表查询）。
        """
        self._viewer_repo = viewer_repo
        self._chat_repo = chat_repo

    async def paid_identities(self, messages: Iterable[Any]) -> Set[str]:
        """返回这批消息里付过费的观众身份键集合。

        Args:
            messages: 弹幕批成员（``RoomMessagePayload`` 鸭子类型）。
        """
        batch: List[Any] = list(messages)
        identities = {identity_key(msg) for msg in batch if is_paid_source(msg)}
        if self._viewer_repo is None or self._chat_repo is None:
            return identities

        viewer_by_identity = await self._resolve_viewer_ids(batch)
        if not viewer_by_identity:
            return identities

        try:
            paid_ids = await self._chat_repo.list_viewers_with_payment_history(
                viewer_ids=set(viewer_by_identity.values())
            )
        except Exception:
            logger.warning("查观众付费记录失败，本批这些观众不标注付费", exc=True)
            return identities

        paid = {int(v) for v in paid_ids}
        for key, viewer_id in viewer_by_identity.items():
            if viewer_id in paid:
                identities.add(key)
        return identities

    async def _resolve_viewer_ids(self, batch: List[Any]) -> Dict[str, int]:
        """查这批消息涉及的观众里，哪些在付费三表留过记录（身份键 → 观众 ID）。

        外部 ID → 内部观众 ID：按（平台, ID 种类）分组批量查——一条消息一次
        查询会拖慢决策窗。
        """
        grouped: Dict[Tuple[str, str], List[str]] = {}
        open_app_ids: Dict[Tuple[str, str], str] = {}
        for msg in batch:
            if is_paid_source(msg):
                continue
            platform, id_kind, external_id = identity_key(msg).split("\x00")
            if not external_id:
                continue
            group = (platform, id_kind)
            grouped.setdefault(group, []).append(external_id)
            user = getattr(msg, "user", None)
            open_app_ids.setdefault(group, str(getattr(user, "open_app_id", "") or "") if user is not None else "")

        viewer_by_identity: Dict[str, int] = {}
        for (platform, id_kind), external_ids in grouped.items():
            try:
                found = await self._viewer_repo.find_viewer_ids_by_identity(
                    platform=platform,
                    id_kind=id_kind,  # type: ignore[arg-type]
                    external_ids=external_ids,
                    open_app_id=open_app_ids.get((platform, id_kind), ""),
                )
            except Exception:
                logger.warning(f"查观众身份失败，本批这些观众不标注付费：{platform}/{id_kind}", exc=True)
                continue
            for external_id, viewer_id in found.items():
                viewer_by_identity[f"{platform}\x00{id_kind}\x00{external_id}"] = int(viewer_id)
        return viewer_by_identity


__all__ = ["PAID_SOURCE_TYPES", "PAID_VIEWER_NOTE", "PaidViewerLookup", "identity_key", "is_paid_source"]
