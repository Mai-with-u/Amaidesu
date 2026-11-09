"""付费史标注：付过费的观众，其本批消息行带「（付费观众）」标注

三态覆盖：付过费（历史付费记录命中）/ 没付过费（查得到观众但三表无记录）/
查不到观众（外部 ID 未建档）——后两态不加标注。
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.streamer import canonical
from src.agents.streamer.paid_viewers import PAID_VIEWER_NOTE, PaidViewerLookup, identity_key
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


class _FakeViewerRepo:
    """观众仓储替身：外部 ID → 观众 ID 的固定映射（只查不建）。"""

    def __init__(self, mapping: Dict[str, int], id_kind: str = "account") -> None:
        self._mapping = mapping
        self._id_kind = id_kind

    async def find_viewer_ids_by_identity(
        self, *, platform: str, id_kind: str, external_ids: List[str], open_app_id: str = ""
    ) -> Dict[str, int]:
        if id_kind != self._id_kind:
            return {}
        return {ext: self._mapping[ext] for ext in external_ids if ext in self._mapping}


class _FakeChatRepo:
    """明细仓储替身：只回观众仓储查过的付费记录集合。"""

    def __init__(self, paid_viewer_ids: set[int]) -> None:
        self._paid = paid_viewer_ids

    async def list_viewers_with_payment_history(self, *, viewer_ids: Any) -> List[int]:
        return sorted(set(int(v) for v in viewer_ids) & self._paid)


def _message(user_id: str, message_type: str = "danmaku", name: str = "观众甲") -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type=message_type,  # type: ignore[arg-type]
        platform="bilibili",
        user=RoomMessageUser(id=user_id, name=name),
        content="你好",
        timestamp_ms=0,
    )


async def test_paid_viewer_message_row_is_marked() -> None:
    """付过费的观众：消息行昵称后带「（付费观众）」。"""
    lookup = PaidViewerLookup(
        viewer_repo=_FakeViewerRepo({"u1": 7}),
        chat_repo=_FakeChatRepo({7}),
    )
    batch = [_message("u1")]

    identities = await lookup.paid_identities(batch)

    assert identities == {identity_key(batch[0])}
    row = canonical.batch_item_to_message(batch[0], paid=identity_key(batch[0]) in identities)
    assert f"观众甲{PAID_VIEWER_NOTE}" in row["content"]


async def test_viewer_without_payment_history_is_not_marked() -> None:
    """查得到观众但付费三表无记录：不加标注。"""
    lookup = PaidViewerLookup(
        viewer_repo=_FakeViewerRepo({"u1": 7}),
        chat_repo=_FakeChatRepo(set()),
    )
    batch = [_message("u1")]

    identities = await lookup.paid_identities(batch)

    assert identities == set()
    row = canonical.batch_item_to_message(batch[0], paid=identity_key(batch[0]) in identities)
    assert PAID_VIEWER_NOTE not in row["content"]


async def test_unknown_viewer_is_not_marked() -> None:
    """外部 ID 未建档（查不到观众）：不加标注，也不报错。"""
    lookup = PaidViewerLookup(
        viewer_repo=_FakeViewerRepo({}),
        chat_repo=_FakeChatRepo({7}),
    )
    batch = [_message("u_unknown")]

    identities = await lookup.paid_identities(batch)

    assert identities == set()


async def test_paid_source_message_is_marked_without_table_lookup() -> None:
    """醒目留言 / 上舰：来源自带付费事实，不查表也标。"""
    lookup = PaidViewerLookup(viewer_repo=None, chat_repo=None)

    for message_type in ("super_chat", "guard"):
        batch = [_message("u1", message_type)]
        identities = await lookup.paid_identities(batch)
        assert identities == {identity_key(batch[0])}, message_type


async def test_missing_repositories_keep_only_source_based_marking() -> None:
    """仓储缺席：只剩"来源自带"那一路，普通弹幕不加标注。"""
    lookup = PaidViewerLookup(viewer_repo=None, chat_repo=None)

    batch = [_message("u1")]
    assert await lookup.paid_identities(batch) == set()


async def test_account_and_open_ids_are_not_mixed() -> None:
    """同串的账号 ID 与开放平台 ID 是两个人：身份键带上 ID 种类。"""
    lookup = PaidViewerLookup(
        viewer_repo=_FakeViewerRepo({"u1": 7}),
        chat_repo=_FakeChatRepo({7}),
    )
    account = _message("u1")
    open_msg = RoomMessagePayload(
        message_type="danmaku",
        platform="bilibili",
        user=RoomMessageUser(id="u1", name="观众甲", id_kind="open"),
        content="你好",
        timestamp_ms=0,
    )

    identities = await lookup.paid_identities([account, open_msg])

    assert identity_key(account) != identity_key(open_msg)
    assert identities == {identity_key(account)}
