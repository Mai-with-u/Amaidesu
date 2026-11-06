"""GamePlayerRepo —— 游戏里的人仓储（game_players、互动、公屏聊天、观众关联）。

她在游戏里遇到的别的玩家按"游戏 + UUID"认：见到一次就更新最近的名字、见过的名字与存档、最近一次
见到的场次。只记和她发生过的互动；打招呼按场合并成一行计数，免得一位玩家蹲起一小时写出几百行。
公屏聊天只落公屏（私聊不进），认不出是谁说的只占一行位置、不存原话。观众与玩家的对应只由运营者
确认后写入，只用来连带删除。

谁能建玩家（有 UUID、在线核对过、不在忘记名单上）由调用方判断；仓储只管读写。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.modules.storage.models.game_players import (
    GAME_CHAT_PLAYER,
    INTERACTION_GREET,
    INTERACTION_KINDS,
    GameChatDirection,
    GameChatLine,
    GamePlayer,
    PlayerInteraction,
    PlayerSave,
    ViewerPlayerLink,
)
from src.modules.storage.repos._base import BaseRepo

_PLAYER_COLUMNS = (
    "id, game, uuid, name, names, saves, first_session_id, first_seen_ms, last_session_id, last_seen_ms,"
    " forget_requested_at_ms"
)
_INTERACTION_COLUMNS = "id, player_id, live_session_id, save_id, save_name, kind, fact, at_ms"
_CHAT_COLUMNS = "id, live_session_id, game, direction, player_id, content, save_id, save_name, at_ms"


def _json_list(raw: Any) -> List[Any]:
    """读 JSON 列表列；写坏的当空列表（这几列只由本仓储写）。"""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _player(row: Any) -> GamePlayer:
    names = [str(name) for name in _json_list(row["names"]) if str(name)]
    saves = [
        PlayerSave(save_id=str(item.get("save_id") or ""), save_name=str(item.get("save_name") or ""))
        for item in _json_list(row["saves"])
        if isinstance(item, dict) and item.get("save_id")
    ]
    return GamePlayer(
        id=int(row["id"]),
        game=str(row["game"]),
        uuid=str(row["uuid"]),
        name=str(row["name"]),
        names=names,
        saves=saves,
        first_session_id=row["first_session_id"],
        first_seen_ms=int(row["first_seen_ms"]),
        last_session_id=row["last_session_id"],
        last_seen_ms=int(row["last_seen_ms"]),
        forget_requested_at_ms=row["forget_requested_at_ms"],
    )


def _interaction(row: Any) -> PlayerInteraction:
    try:
        fact = json.loads(row["fact"] or "{}")
    except (TypeError, ValueError):
        fact = {}
    return PlayerInteraction(
        id=int(row["id"]),
        player_id=int(row["player_id"]),
        live_session_id=int(row["live_session_id"]),
        save_id=str(row["save_id"] or ""),
        save_name=str(row["save_name"] or ""),
        kind=str(row["kind"]),
        fact=fact if isinstance(fact, dict) else {},
        at_ms=int(row["at_ms"]),
    )


def _chat(row: Any) -> GameChatLine:
    return GameChatLine.model_validate(dict(row))


def _find_player_row(conn: sqlite3.Connection, *, game: str, uuid: str) -> Any:
    return conn.execute(
        f"SELECT {_PLAYER_COLUMNS} FROM game_players WHERE game=? AND uuid=?",  # noqa: S608 常量列清单
        (game, uuid),
    ).fetchone()


class GamePlayerRepo(BaseRepo):
    """游戏里的人的读写入口：见到玩家、记互动、公屏聊天落库、观众关联。"""

    async def meet_player(
        self,
        *,
        game: str,
        uuid: str,
        name: str,
        live_session_id: int,
        save_id: str = "",
        save_name: str = "",
        at_ms: int,
    ) -> GamePlayer:
        """见到一位玩家：没见过就建一行；见过就更新名字、见过的名字与存档、最近一次见到的场次。

        同一个 UUID 改了名仍是同一行，新名字追加进见过的名字；同名不同 UUID 是两行。
        """
        uuid_key = uuid.strip().lower()
        shown = name.strip()

        def _exec() -> GamePlayer:
            with self._manager.transaction(immediate=True) as conn:
                row = _find_player_row(conn, game=game, uuid=uuid_key)
                if row is None:
                    # 第一次见到：首次与最近一次都是这一场
                    saves = [{"save_id": save_id, "save_name": save_name}] if save_id else []
                    conn.execute(
                        "INSERT INTO game_players(game, uuid, name, names, saves, first_session_id, first_seen_ms,"
                        " last_session_id, last_seen_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            game,
                            uuid_key,
                            shown,
                            json.dumps([shown] if shown else [], ensure_ascii=False),
                            json.dumps(saves, ensure_ascii=False),
                            int(live_session_id),
                            int(at_ms),
                            int(live_session_id),
                            int(at_ms),
                        ),
                    )
                else:
                    # 见过：改了名追加进见过的名字，新存档追加进见过的存档（存档名变了按最新的写）
                    current = _player(row)
                    names = list(current.names)
                    if shown and shown not in names:
                        names.append(shown)
                    saves = [item.model_dump() for item in current.saves]
                    if save_id:
                        known = next((item for item in saves if item["save_id"] == save_id), None)
                        if known is None:
                            saves.append({"save_id": save_id, "save_name": save_name})
                        elif save_name:
                            known["save_name"] = save_name
                    conn.execute(
                        "UPDATE game_players SET name=?, names=?, saves=?, last_session_id=?, last_seen_ms=?"
                        " WHERE id=?",
                        (
                            shown or current.name,
                            json.dumps(names, ensure_ascii=False),
                            json.dumps(saves, ensure_ascii=False),
                            int(live_session_id),
                            max(int(at_ms), current.last_seen_ms),
                            current.id,
                        ),
                    )
                return _player(_find_player_row(conn, game=game, uuid=uuid_key))

        return await self._run_in_executor(_exec)

    async def find_player(self, *, game: str, uuid: str) -> Optional[GamePlayer]:
        """按游戏与 UUID 找玩家；没见过返回 None。"""

        def _exec() -> Optional[GamePlayer]:
            with self._manager.transaction() as conn:
                row = _find_player_row(conn, game=game, uuid=uuid.strip().lower())
                return _player(row) if row is not None else None

        return await self._run_in_executor(_exec)

    async def get_player(self, *, player_id: int) -> Optional[GamePlayer]:
        """按主键取玩家；不存在返回 None。"""

        def _exec() -> Optional[GamePlayer]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    f"SELECT {_PLAYER_COLUMNS} FROM game_players WHERE id=?",  # noqa: S608 常量列清单
                    (int(player_id),),
                ).fetchone()
                return _player(row) if row is not None else None

        return await self._run_in_executor(_exec)

    async def list_players(self, *, game: Optional[str] = None, limit: int = 200) -> List[GamePlayer]:
        """玩家列表，最近见到的在前。"""

        def _exec() -> List[GamePlayer]:
            where, params = ("WHERE game=?", [game]) if game else ("", [])
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_PLAYER_COLUMNS} FROM game_players {where}"  # noqa: S608 常量片段
                    " ORDER BY last_seen_ms DESC, id DESC LIMIT ?",
                    (*params, int(limit)),
                ).fetchall()
                return [_player(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def record_greeting(
        self,
        *,
        player_id: int,
        live_session_id: int,
        actions: Sequence[str] = (),
        save_id: str = "",
        save_name: str = "",
        at_ms: int,
    ) -> None:
        """记一次打招呼：同一位玩家一场合并成一行，次数加一，这一次做过的每种动作各加一，时刻取最近的一次。"""

        def _exec() -> None:
            with self._manager.transaction(immediate=True) as conn:
                row = conn.execute(
                    f"SELECT {_INTERACTION_COLUMNS} FROM game_player_interactions"  # noqa: S608 常量列清单
                    " WHERE player_id=? AND live_session_id=? AND kind=?",
                    (int(player_id), int(live_session_id), INTERACTION_GREET),
                ).fetchone()
                if row is None:
                    fact: Dict[str, Any] = {"count": 0, "actions": {}}
                else:
                    fact = _interaction(row).fact
                done = dict(fact.get("actions") or {})
                for action in dict.fromkeys(item for item in actions if item):
                    done[action] = int(done.get(action) or 0) + 1
                merged = {"count": int(fact.get("count") or 0) + 1, "actions": done}
                payload = json.dumps(merged, ensure_ascii=False)
                if row is None:
                    conn.execute(
                        "INSERT INTO game_player_interactions(player_id, live_session_id, save_id, save_name, kind,"
                        " fact, at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            int(player_id),
                            int(live_session_id),
                            save_id,
                            save_name,
                            INTERACTION_GREET,
                            payload,
                            int(at_ms),
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE game_player_interactions SET fact=?, at_ms=MAX(at_ms, ?) WHERE id=?",
                        (payload, int(at_ms), int(row["id"])),
                    )

        await self._run_in_executor(_exec)

    async def session_player_named(self, *, game: str, name: str, live_session_id: int) -> Optional[int]:
        """本场见过的、此刻叫这个名字的玩家；正好一位时返回主键，没有或不止一位返回 None（不猜）。

        发过"忘了我"、还在宽限期里的玩家不算：他已经不想被记了。
        """

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT id FROM game_players WHERE game=? AND name=? AND last_session_id=?"
                    " AND forget_requested_at_ms IS NULL LIMIT 2",
                    (game, name.strip(), int(live_session_id)),
                ).fetchall()
                return int(rows[0]["id"]) if len(rows) == 1 else None

        return await self._run_in_executor(_exec)

    async def add_interaction(
        self,
        *,
        player_id: int,
        live_session_id: int,
        kind: str,
        fact: Mapping[str, Any],
        save_id: str = "",
        save_name: str = "",
        at_ms: int,
    ) -> int:
        """记一次送东西、切磋、一起做的事或帮过忙；打招呼走 ``record_greeting``。"""
        if kind not in INTERACTION_KINDS or kind == INTERACTION_GREET:
            raise ValueError(f"未知或须合并记录的互动种类: {kind}")
        payload = json.dumps(dict(fact), ensure_ascii=False)

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO game_player_interactions(player_id, live_session_id, save_id, save_name, kind,"
                    " fact, at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(player_id), int(live_session_id), save_id, save_name, kind, payload, int(at_ms)),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_interactions(self, *, player_id: int, limit: int = 50) -> List[PlayerInteraction]:
        """一位玩家和她的互动，最近的在前。"""

        def _exec() -> List[PlayerInteraction]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_INTERACTION_COLUMNS} FROM game_player_interactions"  # noqa: S608 常量列清单
                    " WHERE player_id=? ORDER BY at_ms DESC, id DESC LIMIT ?",
                    (int(player_id), int(limit)),
                ).fetchall()
                return [_interaction(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def list_history(
        self, *, player_id: int, exclude_session_id: Optional[int] = None, limit: int = 50
    ) -> List[PlayerInteraction]:
        """玩家卡用的往来：正式场里和她的互动，最近的在前；不含 ``exclude_session_id`` 那一场（本场的她自己知道）。

        彩排场的互动不算：彩排里的事不进正式的记忆。
        """

        def _exec() -> List[PlayerInteraction]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT i.id, i.player_id, i.live_session_id, i.save_id, i.save_name, i.kind, i.fact, i.at_ms"
                    " FROM game_player_interactions i JOIN live_sessions s ON s.id = i.live_session_id"
                    " WHERE i.player_id=? AND s.kind='live' AND i.live_session_id IS NOT ?"
                    " ORDER BY i.at_ms DESC, i.id DESC LIMIT ?",
                    (int(player_id), exclude_session_id, int(limit)),
                ).fetchall()
                return [_interaction(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def replied_player_ids(self, *, live_session_id: int) -> List[int]:
        """本场她播出的台词里回应过的玩家（台词落库时记下的回应对象）。"""

        def _exec() -> List[int]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT reply_player_id FROM live_chat WHERE live_session_id=? AND sender_role='assistant'"
                    " AND reply_player_id IS NOT NULL AND dropped_at_ms IS NULL",
                    (int(live_session_id),),
                ).fetchall()
                return [int(row["reply_player_id"]) for row in rows]

        return await self._run_in_executor(_exec)

    async def add_game_chat(
        self,
        *,
        live_session_id: int,
        game: str,
        direction: GameChatDirection,
        content: str,
        player_id: Optional[int] = None,
        save_id: str = "",
        save_name: str = "",
        at_ms: int,
    ) -> int:
        """落一条公屏聊天。玩家说的必须挂玩家；认不出是谁说的不存原话，只占位。"""
        if direction == GAME_CHAT_PLAYER and player_id is None:
            raise ValueError("玩家说的公屏聊天必须挂玩家")
        text = content if direction != "other" else ""
        owner = int(player_id) if direction == GAME_CHAT_PLAYER and player_id is not None else None

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO game_chat(live_session_id, game, direction, player_id, content, save_id, save_name,"
                    " at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (int(live_session_id), game, direction, owner, text, save_id, save_name, int(at_ms)),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_session_game_chat(self, *, live_session_id: int) -> List[GameChatLine]:
        """一场的公屏聊天，按时间先后。"""

        def _exec() -> List[GameChatLine]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_CHAT_COLUMNS} FROM game_chat WHERE live_session_id=?"  # noqa: S608 常量列清单
                    " ORDER BY at_ms, id",
                    (int(live_session_id),),
                ).fetchall()
                return [_chat(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def link_viewer(self, *, viewer_id: int, player_id: int, at_ms: int) -> None:
        """运营者确认"这位玩家是直播间的哪位观众"：记一行关联（已有就更新确认时刻）。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewer_player_links(viewer_id, player_id, confirmed_at_ms) VALUES (?, ?, ?)"
                    " ON CONFLICT(viewer_id, player_id) DO UPDATE SET confirmed_at_ms=excluded.confirmed_at_ms",
                    (int(viewer_id), int(player_id), int(at_ms)),
                )

        await self._run_in_executor(_exec)

    async def unlink_viewer(self, *, viewer_id: int, player_id: int) -> bool:
        """撤掉一行关联；本来就没有返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "DELETE FROM viewer_player_links WHERE viewer_id=? AND player_id=?",
                    (int(viewer_id), int(player_id)),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def list_links(
        self, *, viewer_id: Optional[int] = None, player_id: Optional[int] = None
    ) -> List[ViewerPlayerLink]:
        """观众与玩家的关联；可按观众或玩家筛。"""

        def _exec() -> List[ViewerPlayerLink]:
            clauses: List[str] = []
            params: List[int] = []
            if viewer_id is not None:
                clauses.append("viewer_id=?")
                params.append(int(viewer_id))
            if player_id is not None:
                clauses.append("player_id=?")
                params.append(int(player_id))
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT viewer_id, player_id, confirmed_at_ms FROM viewer_player_links {where}"  # noqa: S608 常量片段
                    " ORDER BY confirmed_at_ms DESC",
                    params,
                ).fetchall()
                return [ViewerPlayerLink.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)


__all__ = ["GamePlayerRepo"]
