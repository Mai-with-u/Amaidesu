"""GameCreatureRepo —— 游戏生物名册仓储（game_creatures）。

游戏 Agent 的名册按"游戏 + 世界"整份读出、按变化增量写回：进一个世界时读一次，此后每次观察、
起名、死讯只写有变化的几只。世界编号由游戏报出（游戏没报时调用方给空串），同一个 UUID 只在
同一个世界里有意义，所以读写都带世界。条目不自动删除，保留多久由使用者定；只有主播取消路人的
名字时由游戏 Agent 删掉那一行（路人只因起了名才进名册）。
"""

from __future__ import annotations

from typing import List, Sequence

from src.modules.storage.models.game_creatures import GameCreatureRecord
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

_RECORD_COLUMNS = (
    "creature_key, type, kind, name, skin, owner, nickname, uuid,"
    " last_seen_ms, died_ms, death_note, died_session_id, next_session_id"
)


class GameCreatureRepo(BaseRepo):
    """game_creatures 表的读写入口（游戏 Agent 进世界时读、名册变化时写）。"""

    async def list_creatures(self, *, game: str, world_key: str) -> List[GameCreatureRecord]:
        """一个世界的整份名册，按建档先后（写入先后）排列；没有记录返回空列表。"""

        def _exec() -> List[GameCreatureRecord]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_RECORD_COLUMNS} FROM game_creatures"  # noqa: S608 常量片段
                    " WHERE game=? AND world_key=? ORDER BY rowid",
                    (game, world_key),
                ).fetchall()
                return [GameCreatureRecord.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def save_creatures(
        self,
        *,
        game: str,
        world_key: str,
        creatures: Sequence[GameCreatureRecord],
        removed_keys: Sequence[str] = (),
    ) -> None:
        """写回一个世界里有变化的几只（有就整行覆盖、没有就新建），并删掉被移出名册的；同一事务内完成。"""
        if not creatures and not removed_keys:
            return
        ts = now_ms()
        rows = [
            (
                game,
                world_key,
                record.creature_key,
                record.type,
                record.kind,
                record.name,
                record.skin,
                record.owner,
                record.nickname,
                record.uuid,
                record.last_seen_ms,
                record.died_ms,
                record.death_note,
                record.died_session_id,
                record.next_session_id,
                ts,
            )
            for record in creatures
        ]

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.executemany(
                    "INSERT INTO game_creatures(game, world_key, " + _RECORD_COLUMNS + ", updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(game, world_key, creature_key) DO UPDATE SET"
                    " type=excluded.type, kind=excluded.kind, name=excluded.name, skin=excluded.skin,"
                    " owner=excluded.owner, nickname=excluded.nickname, uuid=excluded.uuid,"
                    " last_seen_ms=excluded.last_seen_ms, died_ms=excluded.died_ms,"
                    " death_note=excluded.death_note, died_session_id=excluded.died_session_id,"
                    " next_session_id=excluded.next_session_id, updated_at_ms=excluded.updated_at_ms",
                    rows,
                )
                conn.executemany(
                    "DELETE FROM game_creatures WHERE game=? AND world_key=? AND creature_key=?",
                    [(game, world_key, key) for key in removed_keys],
                )

        await self._run_in_executor(_exec)


__all__ = ["GameCreatureRepo"]
