"""编排页提问箱 API（/api/v1/talk-cards*）：增删改、批量导入、重置已用与存储缺失降级。

用真实 SQLite 仓储（临时库），断言落库结果而不是替身调用记录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
async def database(tmp_path: Path):
    db = SQLiteDatabase(tmp_path / "data" / "cards.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def test_degrades_without_repo(api) -> None:
    client = await api()
    body = (await client.get("/api/v1/talk-cards")).json()
    assert body["success"] is False and "存储未就绪" in body["message"]
    created = (await client.post("/api/v1/talk-cards", json={"kind": "opinion", "title": "x"})).json()
    assert created["success"] is False


async def test_create_list_update_delete(api, database: SQLiteDatabase) -> None:
    client = await api(talk_card_repo=database.talk_cards)
    created = (
        await client.post("/api/v1/talk-cards", json={"kind": "marshmallow", "title": "旋钮能拧吗", "asker": "观众甲"})
    ).json()
    assert created["success"] is True
    card_id = created["cards"][0]["id"]

    listed = (await client.get("/api/v1/talk-cards", params={"kind": "marshmallow"})).json()
    assert [c["id"] for c in listed["cards"]] == [card_id]
    assert (await client.get("/api/v1/talk-cards", params={"kind": "gossip"})).json()["success"] is False

    patched = (await client.patch(f"/api/v1/talk-cards/{card_id}", json={"enabled": False})).json()
    assert patched["success"] is True and patched["cards"][0]["enabled"] is False
    missing = (await client.patch("/api/v1/talk-cards/9999", json={"title": "无"})).json()
    assert missing["success"] is False and "不存在" in missing["message"]

    assert (await client.delete(f"/api/v1/talk-cards/{card_id}")).json()["success"] is True
    assert (await client.delete(f"/api/v1/talk-cards/{card_id}")).json()["success"] is False


async def test_import_validates_and_reset_restores_used(api, database: SQLiteDatabase) -> None:
    client = await api(talk_card_repo=database.talk_cards)
    bad = await client.post("/api/v1/talk-cards/import", json={"cards": [{"kind": "opinion", "title": "  "}]})
    assert bad.status_code == 422  # 空标题被请求校验拦下，整批不进

    imported = (
        await client.post(
            "/api/v1/talk-cards/import",
            json={"cards": [{"kind": "opinion", "title": "甜还是咸"}, {"kind": "episode", "title": "卡进河里"}]},
        )
    ).json()
    assert imported["success"] is True and imported["affected"] == 2
    first_id = imported["cards"][0]["id"]

    await database.talk_cards.mark_used(first_id, session_id=1, at_ms=1_000)
    reset = (await client.post("/api/v1/talk-cards/reset-used", json={})).json()
    assert reset["success"] is True and reset["affected"] == 1
    card = await database.talk_cards.get_card(first_id)
    assert card is not None and card.used_at_ms is None
