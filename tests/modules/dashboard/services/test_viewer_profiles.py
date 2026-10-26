"""viewer_profiles 服务层单测：内存假仓储直测装配逻辑（不经 HTTP 层）。

覆盖仓储行 → 响应模型的投影、跨仓储编排（档案串四表、待确认补候选昵称、
互动分析聚合）、对话分页游标口径，与仓储不可用异常。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from src.modules.dashboard.schemas.viewers import (
    ViewerBrief,
    ViewerDetailResponse,
    ViewerListItem,
)
from src.modules.dashboard.services.viewer_profiles import (
    RepoUnavailableError,
    build_contributions,
    build_dialogue,
    build_insights,
    build_pending_viewers,
    build_viewer_detail,
    build_viewer_sessions,
    list_viewers,
    require_chat_repo,
    require_identity_repo,
    require_viewer_repo,
)


class _FakeViewerRepo:
    """观众统计仓储桩：dict 行原样返回，记录调用。"""

    def __init__(
        self,
        *,
        stats_rows: Optional[List[Dict[str, Any]]] = None,
        viewers: Optional[Dict[int, Dict[str, Any]]] = None,
        names: Optional[Dict[int, List[Dict[str, Any]]]] = None,
        buckets: Optional[Dict[str, int]] = None,
    ) -> None:
        self.stats_rows = stats_rows or []
        self.viewers = viewers or {}
        self.names = names or {}
        self.buckets = buckets or {}
        self.list_names_calls: List[int] = []

    async def list_viewer_stats(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        if kwargs.get("order_by") not in (None, "message_count", "gift_count"):
            raise ValueError(f"非法排序字段: {kwargs.get('order_by')}")
        return self.stats_rows, len(self.stats_rows)

    async def get_viewer(self, viewer_id: int) -> Optional[Dict[str, Any]]:
        return self.viewers.get(viewer_id)

    async def list_names(self, viewer_id: int) -> List[Dict[str, Any]]:
        self.list_names_calls.append(viewer_id)
        return self.names.get(viewer_id, [])

    async def viewer_insight_buckets(self) -> Dict[str, int]:
        return self.buckets


class _FakeChatRepo:
    """直播明细仓储桩。"""

    def __init__(
        self,
        *,
        daily: Optional[List[Dict[str, Any]]] = None,
        dialogue: Optional[List[Dict[str, Any]]] = None,
        bounds: Optional[tuple] = None,
        summary: Optional[Dict[str, Any]] = None,
        gifts: Optional[List[Dict[str, Any]]] = None,
        super_chats: Optional[List[Dict[str, Any]]] = None,
        sessions: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.daily = daily or []
        self.dialogue = dialogue or []
        self.bounds = bounds
        self.summary = summary or {}
        self.gifts = gifts or []
        self.super_chats = super_chats or []
        self.sessions = sessions or []

    async def daily_danmaku_counts(self, *, days: int) -> List[Dict[str, Any]]:
        return self.daily

    async def list_viewer_dialogue(self, **kwargs: Any) -> List[Dict[str, Any]]:  # type: ignore[no-untyped-def]
        return self.dialogue

    async def get_viewer_activity_bounds(self, viewer_id: int) -> Optional[tuple]:
        return self.bounds

    async def summarize_viewer_contributions(self, viewer_id: int) -> Dict[str, Any]:
        return self.summary

    async def list_viewer_gifts(self, viewer_id: int, *, limit: int) -> List[Dict[str, Any]]:
        return self.gifts

    async def list_viewer_super_chats(self, viewer_id: int, *, limit: int) -> List[Dict[str, Any]]:
        return self.super_chats

    async def list_viewer_sessions(self, viewer_id: int) -> List[Dict[str, Any]]:
        return self.sessions


class _FakeIdentityRepo:
    """身份仓储桩：待确认行原样返回。"""

    def __init__(self, rows: List[Dict[str, Any]], total: int = 0) -> None:
        self.rows = rows
        self.total = total

    async def list_pending(self, *, limit: int, offset: int):  # type: ignore[no-untyped-def]
        return self.total, self.rows


class _BareServer:
    """极简启动形态：仓储属性全部存在且为 None（与 DashboardServer 声明属性一致）。"""

    viewer_repo = None
    viewer_identity_repo = None
    chat_repo = None


# require_*：仓储不可用


def test_仓储未装配时抛领域异常() -> None:
    server = _BareServer()
    for require in (require_viewer_repo, require_identity_repo, require_chat_repo):
        with pytest.raises(RepoUnavailableError):
            require(server)  # type: ignore[arg-type]


def test_仓储已装配时原样返回() -> None:
    repo = object()
    server = _BareServer()
    server.viewer_repo = repo  # type: ignore[attr-defined]
    assert require_viewer_repo(server) is repo


# list_viewers


async def test_列表行投影与总数() -> None:
    repo = _FakeViewerRepo(
        stats_rows=[
            {
                "id": 7,
                "platform": "bilibili",
                "user_id": "u1",
                "user_name": "观众",
                "link_status": "pending",
                "message_count": 3,
                "gift_count": 1,
                "replied_count": 2,
                "interaction_count": 6,
                "paid_count": 1,
                "paid_amount": 1000,
                "last_active_ms": 123,
            }
        ]
    )
    body = await list_viewers(repo, platform=" bilibili ", search=" 观众 ", limit=20, offset=0, order_by="gift_count")
    assert body.total == 1
    row = body.items[0]
    assert isinstance(row, ViewerListItem)
    assert (row.viewer_id, row.platform, row.user_id, row.link_status) == (7, "bilibili", "u1", "pending")
    assert (row.message_count, row.paid_amount, row.last_active_ms) == (3, 1000, 123)


async def test_非法排序字段上抛ValueError() -> None:
    repo = _FakeViewerRepo()
    with pytest.raises(ValueError):
        await list_viewers(repo, platform=None, search=None, limit=20, offset=0, order_by="user_id; DROP")


async def test_空值字段补缺省() -> None:
    repo = _FakeViewerRepo(
        stats_rows=[
            {
                "id": 1,
                "platform": None,
                "user_id": None,
                "user_name": None,
                "link_status": None,
                "message_count": None,
                "gift_count": None,
                "replied_count": None,
                "interaction_count": None,
                "paid_count": None,
                "paid_amount": None,
                "last_active_ms": None,
            }
        ]
    )
    row = (await list_viewers(repo, platform=None, search=None, limit=1, offset=0, order_by="message_count")).items[0]
    assert (row.platform, row.user_id, row.user_name, row.link_status) == ("", "", "", "confirmed")
    assert (row.message_count, row.gift_count, row.paid_amount, row.last_active_ms) == (0, 0, 0, 0)


# build_insights：跨仓储聚合


async def test_互动分析聚合两仓储() -> None:
    viewer_repo = _FakeViewerRepo(buckets={"total": 10, "active_today": 2, "active_week": 4, "gift_viewers": 1})
    chat_repo = _FakeChatRepo(daily=[{"day": "2026-10-25", "count": 7}])
    body = await build_insights(viewer_repo, chat_repo, days=30)
    assert (body.total_viewers, body.active_today, body.active_week) == (10, 2, 4)
    # 桶缺键补零
    assert (body.active_month, body.active_older, body.never_replied, body.gift_viewers) == (0, 0, 0, 1)
    assert [(p.day, p.count) for p in body.daily_danmaku] == [("2026-10-25", 7)]


# build_pending_viewers：N+1 补候选昵称


async def test_待确认行补候选昵称与无候选行() -> None:
    pending_row = {
        "viewer_id": 20,
        "platform": "bilibili",
        "user_name": "新人",
        "account_id": None,
        "open_id": "open-1",
        "message_count": 1,
        "interaction_count": 1,
        "paid_amount": 0,
        "last_active_ms": 500,
        "candidate_id": 5,
        "candidate_name": "老观众",
        "candidate_account_id": "1001",
        "candidate_open_id": None,
        "candidate_name_seen_ms": 400,
        "candidate_message_count": 9,
        "candidate_interaction_count": 12,
        "candidate_paid_amount": 3000,
        "candidate_last_active_ms": 450,
    }
    no_candidate_row = {
        "viewer_id": 21,
        "platform": "bilibili",
        "user_name": "孤立",
        "account_id": "1002",
        "open_id": None,
        "message_count": 0,
        "interaction_count": 0,
        "paid_amount": 0,
        "last_active_ms": 100,
        "candidate_id": None,
        "candidate_name": None,
        "candidate_account_id": None,
        "candidate_open_id": None,
        "candidate_name_seen_ms": None,
        "candidate_message_count": None,
        "candidate_interaction_count": None,
        "candidate_paid_amount": None,
        "candidate_last_active_ms": None,
    }
    viewer_repo = _FakeViewerRepo(names={5: [{"user_name": "老观众"}, {"user_name": "曾用名"}]})
    body = await build_pending_viewers(
        _FakeIdentityRepo([pending_row, no_candidate_row], total=2), viewer_repo, limit=50, offset=0
    )

    assert body.total == 2
    with_candidate = body.items[0]
    assert with_candidate.candidate == ViewerBrief(viewer_id=5, user_name="老观众")
    assert with_candidate.candidate_names == ["老观众", "曾用名"]
    assert (with_candidate.candidate_message_count, with_candidate.candidate_paid_amount) == (9, 3000)
    # 候选昵称只为有候选的行取（N+1 循环按需触发）
    assert viewer_repo.list_names_calls == [5]
    bare = body.items[1]
    assert bare.candidate is None
    assert bare.candidate_names == []
    assert (bare.candidate_message_count, bare.candidate_name_seen_ms) == (0, 0)


# build_viewer_detail


async def test_单观众档案串四仓储与候选回查() -> None:
    viewer_repo = _FakeViewerRepo(
        viewers={
            7: {
                "id": 7,
                "platform": "bilibili",
                "user_id": "u1",
                "account_id": "u1",
                "open_id": None,
                "open_app_id": "",
                "user_name": "富观众",
                "link_status": "pending",
                "link_candidate_id": 5,
                "message_count": 2,
                "gift_count": 1,
                "replied_count": 1,
                "interaction_count": 4,
                "paid_count": 1,
                "paid_amount": 30000,
                "last_active_ms": 9000,
            },
            5: {"id": 5, "user_name": "老观众"},
        },
        names={7: [{"user_name": "富观众", "first_seen_ms": 100, "last_seen_ms": 9000}]},
    )
    chat_repo = _FakeChatRepo(
        bounds=(100, 9000),
        summary={"gift_total_count": 2, "gift_total_amount": 5000, "sc_total_amount": 30000, "sc_total_count": 1},
        sessions=[{"live_session_id": 1}],
    )
    detail = await build_viewer_detail(viewer_repo, chat_repo, 7)
    assert isinstance(detail, ViewerDetailResponse)
    assert (detail.viewer_id, detail.user_name, detail.link_status) == (7, "富观众", "pending")
    assert detail.link_candidate == ViewerBrief(viewer_id=5, user_name="老观众")
    assert [(n.user_name, n.first_seen_ms, n.last_seen_ms) for n in detail.names] == [("富观众", 100, 9000)]
    assert detail.first_seen_ms == 100
    # 金额口径：金瓜子 ÷1000 = 元
    assert (detail.gift_total_amount, detail.sc_total_amount, detail.sc_total_count, detail.session_count) == (
        5000, 30.0, 1, 1
    )  # fmt: skip


async def test_观众不存在返回None_候选缺失不炸() -> None:
    assert await build_viewer_detail(_FakeViewerRepo(), _FakeChatRepo(), 999) is None
    # link_candidate_id 指向不存在的观众：候选字段置 None，档案照常返回
    viewer_repo = _FakeViewerRepo(
        viewers={
            7: {
                "id": 7, "platform": "bilibili", "user_id": "u", "account_id": None, "open_id": None,
                "open_app_id": None, "user_name": "孤儿", "link_status": "pending", "link_candidate_id": 404,
                "message_count": 0, "gift_count": 0, "replied_count": 0, "interaction_count": 0,
                "paid_count": 0, "paid_amount": 0, "last_active_ms": 0,
            }
        }
    )  # fmt: skip
    detail = await build_viewer_detail(viewer_repo, _FakeChatRepo(summary={}), 7)
    assert detail is not None and detail.link_candidate is None


# build_dialogue


async def test_对话交织与游标口径() -> None:
    chat_repo = _FakeChatRepo(
        dialogue=[
            {"id": 1, "sender_role": "viewer", "content": "第一条", "timestamp_ms": 100, "live_session_id": 1,
             "message_type": "danmaku", "message_id": "m1", "reply_to_message_id": None, "simulated": False},
            {"id": 2, "sender_role": "viewer", "content": "第二条", "timestamp_ms": 200, "live_session_id": 1,
             "message_type": "danmaku", "message_id": "m2", "reply_to_message_id": None, "simulated": False},
            {"id": 3, "sender_role": "assistant", "content": "谢谢", "timestamp_ms": 900, "live_session_id": 2,
             "message_type": "speech", "message_id": None, "reply_to_message_id": "m2", "simulated": True},
        ]
    )  # fmt: skip
    body = await build_dialogue(chat_repo, 7, before_timestamp_ms=None, limit=2)
    assert [(i.kind, i.id) for i in body.items] == [("viewer", 1), ("viewer", 2), ("reply", 3)]
    # 游标以观众消息为主轴：观众行数达到 limit，游标取批次内最早观众消息时刻
    assert body.next_before == 100
    # 回复行不携带消息类型
    assert body.items[2].message_type is None
    assert body.items[2].simulated is True

    chat_repo.dialogue = chat_repo.dialogue[:3]
    tail = await build_dialogue(chat_repo, 7, before_timestamp_ms=100, limit=3)
    assert tail.next_before is None  # 观众行不足 limit，翻尽


async def test_对话行可空字段补缺省() -> None:
    chat_repo = _FakeChatRepo(
        dialogue=[
            {"id": 9, "sender_role": "viewer", "content": None, "timestamp_ms": 5, "live_session_id": None,
             "message_type": None, "message_id": "", "reply_to_message_id": None, "simulated": False},
        ]
    )  # fmt: skip
    item = (await build_dialogue(chat_repo, 1, before_timestamp_ms=None, limit=1)).items[0]
    assert (item.content, item.live_session_id, item.message_id) == ("", None, None)


# build_contributions / build_viewer_sessions


async def test_贡献装配与金额换算() -> None:
    chat_repo = _FakeChatRepo(
        summary={"gift_total_count": 2, "sc_total_amount": 30000, "sc_total_count": 1},
        gifts=[
            {
                "timestamp_ms": 200,
                "live_session_id": 1,
                "gift_name": "小心心",
                "quantity": 2,
                "total_price": 2000,
                "simulated": False,
            },
        ],  # fmt: skip
        super_chats=[
            {"timestamp_ms": 800, "live_session_id": 2, "total_price": 30000, "message": "加油", "simulated": False}
        ],
    )
    body = await build_contributions(chat_repo, 7, limit=100)
    assert (body.gift_total_count, body.sc_total_amount, body.sc_total_count) == (2, 30.0, 1)
    gift = body.gifts[0]
    assert (gift.gift_name, gift.gift_count, gift.total_price) == ("小心心", 2, 2000)
    sc = body.super_chats[0]
    assert (sc.amount, sc.message) == (30.0, "加油")


async def test_场次行投影() -> None:
    chat_repo = _FakeChatRepo(
        sessions=[{"live_session_id": 3, "title": "夜场", "message_count": 5, "first_ms": 10, "last_ms": 20}]
    )
    body = await build_viewer_sessions(chat_repo, 7)
    row = body.items[0]
    assert (row.live_session_id, row.title, row.message_count, row.first_ms, row.last_ms) == (3, "夜场", 5, 10, 20)
