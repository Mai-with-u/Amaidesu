"""记忆页"游戏里的人"端点：玩家列表、观众对号确认与取消、删玩家。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.privacy import PrivacyService


@pytest.fixture
async def privacy(store: SQLiteDatabase, tmp_path: Path):
    service = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await service.initialize()
    yield service
    await service.close()


@pytest.fixture
async def client(api, store: SQLiteDatabase, privacy: PrivacyService):
    return await api(viewer_repo=store.viewers, game_player_repo=store.game_players, privacy_service=privacy)


async def test_players_answer_503_when_not_wired(api) -> None:
    client = await api()
    resp = await client.get("/api/v1/memory/players")
    assert resp.status_code == 503 and resp.json()["detail"]


async def test_viewer_detail_names_the_linked_players(api, store: SQLiteDatabase, privacy: PrivacyService) -> None:
    """观众详情带上确认过关联的游戏玩家：删除对话框据此点名会一并删掉谁。"""
    client = await api(
        viewer_repo=store.viewers, chat_repo=store.chat, game_player_repo=store.game_players, privacy_service=privacy
    )
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=session, at_ms=1_010
    )
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_alex",
        user_name="阿杰",
        timestamp_ms=1_000,
        name_match_window_ms=0,
    )
    assert (await client.get(f"/api/v1/viewers/{viewer.viewer_id}")).json()["linked_players"] == []

    await store.game_players.link_viewer(viewer_id=viewer.viewer_id, player_id=alex.id, at_ms=2_000)
    detail = (await client.get(f"/api/v1/viewers/{viewer.viewer_id}")).json()
    assert detail["linked_players"] == [{"player_id": alex.id, "game": "minecraft", "name": "Alex"}]


async def test_list_link_unlink_and_delete_a_player(client, store: SQLiteDatabase, privacy: PrivacyService) -> None:
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    repo = store.game_players
    await repo.meet_player(game="minecraft", uuid="uuid-a", name="Steve", live_session_id=session, at_ms=1_010)
    alex = await repo.meet_player(game="minecraft", uuid="uuid-a", name="Alex", live_session_id=session, at_ms=1_020)
    await repo.record_greeting(player_id=alex.id, live_session_id=session, actions=["挥手"], at_ms=1_030)
    await repo.add_interaction(
        player_id=alex.id, live_session_id=session, kind="gift", fact={"said": "扔了面包"}, at_ms=1_040
    )
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_alex",
        user_name="阿杰",
        timestamp_ms=1_000,
        name_match_window_ms=0,
    )

    (item,) = (await client.get("/api/v1/memory/players")).json()["items"]
    assert (item["name"], item["former_names"], item["interactions"], item["linked_viewers"]) == (
        "Alex",
        ["Steve"],
        {"greet": 1, "gift": 1},
        [],
    )

    linked = await client.post(f"/api/v1/memory/players/{alex.id}/links", json={"viewer_id": viewer.viewer_id})
    assert linked.status_code == 200
    (item,) = (await client.get("/api/v1/memory/players")).json()["items"]
    assert [(row["viewer_id"], row["user_name"]) for row in item["linked_viewers"]] == [(viewer.viewer_id, "阿杰")]
    missing = await client.post(f"/api/v1/memory/players/{alex.id}/links", json={"viewer_id": 999})
    assert missing.status_code == 404

    assert (await client.delete(f"/api/v1/memory/players/{alex.id}/links/{viewer.viewer_id}")).status_code == 200
    assert (await client.delete(f"/api/v1/memory/players/{alex.id}/links/{viewer.viewer_id}")).status_code == 404

    assert (await client.delete(f"/api/v1/memory/players/{alex.id}")).status_code == 200
    assert (await client.get("/api/v1/memory/players")).json()["items"] == []
    assert await privacy.is_player_forgotten(game="minecraft", uuid="uuid-a") is True
    assert (await client.delete(f"/api/v1/memory/players/{alex.id}")).status_code == 404
    assert await store.viewers.get_viewer(viewer_id=viewer.viewer_id) is not None, "取消关联后删玩家不动观众"
