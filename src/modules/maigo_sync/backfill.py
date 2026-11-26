"""Amaidesu 首次连通 MaiGO 时的后台低速历史记忆回填。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Dict, List, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.maigo_sync.client import MaiGOClient, MaiGORejected
from src.modules.utils.time_utils import now_ms

__all__ = ["StartupHistoryBackfiller"]

logger = get_logger("MaiGOBackfill")


def _list_tables(conn: sqlite3.Connection) -> Set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(r["name"]) for r in rows}


def _table_columns(conn: sqlite3.Connection, table: str) -> Set[str]:
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info([{table}])").fetchall()}


def _row_val(row: sqlite3.Row, cols: Set[str], key: str, default: Any = None) -> Any:
    return row[key] if key in cols else default


def _clean_text(raw: Any, *, max_len: int = 360) -> str:
    compact = " ".join(str(raw or "").split()).strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1].rstrip() + "…"


def _format_interaction_summary(name: str, kind: str, raw_fact: Any) -> str:
    """将游戏互动原始事实（可能是 JSON 字典串或纯文本）转为可读摘要。"""
    text = str(raw_fact or "").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                desc = str(parsed.get("summary") or parsed.get("text") or parsed.get("note") or "").strip()
                if desc:
                    return _clean_text(desc, max_len=320)
                item = str(parsed.get("item") or "").strip()
                if item:
                    return _clean_text(f"与 {name} 发生游戏互动（{kind}：{item}）", max_len=320)
        except json.JSONDecodeError:
            pass
    elif text:
        return _clean_text(text, max_len=320)
    return _clean_text(f"与 {name} 发生游戏互动（{kind}）", max_len=320)


class StartupHistoryBackfiller:
    """只读读取本地 SQLite 最近 N 天正式场高信噪比数据，并分批低速补推至 MaiGO。"""

    def __init__(
        self,
        *,
        db_path: Path,
        state_path: Path,
        client: MaiGOClient,
        branch_id: str = "amaidesu",
        since_days: int = 30,
        batch_size: int = 20,
        batch_interval_ms: int = 500,
        clock_ms: Callable[[], int] = now_ms,
    ) -> None:
        self._db_path = Path(db_path)
        self._state_path = Path(state_path)
        self._client = client
        self._branch_id = (branch_id or "amaidesu").strip().lower() or "amaidesu"
        self._since_days = max(0, int(since_days))
        self._batch_size = max(1, int(batch_size))
        self._batch_interval_s = max(0.0, int(batch_interval_ms) / 1000.0)
        self._clock_ms = clock_ms
        self._run_lock = asyncio.Lock()

    def _read_state_sync(self) -> Tuple[Set[int], Set[int]]:
        if not self._state_path.is_file():
            return set(), set()
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"读取 MaiGO 回填状态失败，将从最近窗口重扫（服务端幂等去重）: {exc}")
            return set(), set()
        if not isinstance(data, dict):
            return set(), set()
        sids = {int(x) for x in (data.get("completed_sessions") or ()) if isinstance(x, (int, float))}
        cids = {int(x) for x in (data.get("completed_commitments") or ()) if isinstance(x, (int, float))}
        return sids, cids

    def _write_state_sync(self, new_sids: Sequence[int], new_cids: Sequence[int]) -> None:
        done_sids, done_cids = self._read_state_sync()
        done_sids.update(int(x) for x in new_sids)
        done_cids.update(int(x) for x in new_cids)
        payload = {
            "completed_sessions": sorted(done_sids),
            "completed_commitments": sorted(done_cids),
            "updated_at_ms": int(self._clock_ms()),
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _collect_pending_sync(self) -> Tuple[List[Dict[str, Any]], List[int], List[int]]:
        if not self._db_path.is_file():
            return [], [], []
        done_sids, done_cids = self._read_state_sync()
        now_val = int(self._clock_ms())
        cutoff_ms = now_val - self._since_days * 86_400_000 if self._since_days > 0 else 0

        conn = sqlite3.connect(f"file:{self._db_path.resolve().as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        events: List[Dict[str, Any]] = []
        pending_sids: List[int] = []
        pending_cids: List[int] = []
        try:
            tables = _list_tables(conn)
            sid_started: Dict[int, int] = {}
            if "live_sessions" in tables:
                scols = _table_columns(conn, "live_sessions")
                clauses = ["started_at_ms >= ?"] if cutoff_ms > 0 and "started_at_ms" in scols else []
                params: List[Any] = [cutoff_ms] if clauses else []
                if "kind" in scols:
                    clauses.append("COALESCE(kind, 'live') != 'rehearsal'")
                where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                for row in conn.execute(f"SELECT * FROM live_sessions {where_sql}", params).fetchall():
                    sid = int(row["id"])
                    if sid in done_sids:
                        continue
                    pending_sids.append(sid)
                    sid_started[sid] = int(_row_val(row, scols, "started_at_ms") or now_val)

            target_sids = set(pending_sids)
            sids_with_archive: Set[int] = set()
            if target_sids and "archive_lines" in tables:
                acols = _table_columns(conn, "archive_lines")
                ph = ",".join("?" for _ in target_sids)
                for row in conn.execute(f"SELECT * FROM archive_lines WHERE live_session_id IN ({ph})", tuple(target_sids)):
                    sid = int(row["live_session_id"])
                    text = _clean_text(_row_val(row, acols, "text"), max_len=360)
                    if not text:
                        continue
                    sids_with_archive.add(sid)
                    quote = _clean_text(_row_val(row, acols, "quote"), max_len=1000)
                    save_id = str(_row_val(row, acols, "save_id") or "").strip() or None
                    occ_ms = int(_row_val(row, acols, "created_at_ms") or sid_started.get(sid, now_val))
                    events.append(
                        {
                            "idempotency_key": f"backfill-amaidesu-archive-{int(row['id'])}",
                            "branch_id": self._branch_id,
                            "occurred_at_ms": occ_ms,
                            "kind": "milestone",
                            "actor_role": "self",
                            "visibility": "public",
                            "context_scope": f"live:session:{sid}",
                            "actors": [],
                            "summary": text,
                            "verbatim": quote,
                            "reaction": "",
                            "save_id": save_id,
                            "metadata": {
                                "highlight": bool(_row_val(row, acols, "highlight", False)),
                                "backfill": True,
                            },
                        }
                    )

            if "commitments" in tables:
                ccols = _table_columns(conn, "commitments")
                for row in conn.execute("SELECT * FROM commitments").fetchall():
                    cid = int(row["id"])
                    if cid in done_cids or _row_val(row, ccols, "viewer_id") is not None:
                        continue
                    csid = _row_val(row, ccols, "created_session_id")
                    if csid is not None and int(csid) in done_sids:
                        continue
                    if target_sids and csid is not None and int(csid) not in target_sids:
                        continue
                    what = _clean_text(_row_val(row, ccols, "what"), max_len=320)
                    if not what:
                        continue
                    occ_ms = int(
                        _row_val(row, ccols, "updated_at_ms")
                        or _row_val(row, ccols, "created_at_ms")
                        or (sid_started.get(int(csid), now_val) if csid is not None else now_val)
                    )
                    if cutoff_ms > 0 and occ_ms < cutoff_ms:
                        continue
                    pending_cids.append(cid)
                    status = str(_row_val(row, ccols, "status") or "open").strip().lower()
                    if status not in ("open", "closed", "void"):
                        status = "open"
                    quote = _clean_text(
                        _row_val(row, ccols, "close_quote") or _row_val(row, ccols, "quote"), max_len=1000
                    )
                    save_id = str(_row_val(row, ccols, "save_id") or "").strip() or None
                    events.append(
                        {
                            "idempotency_key": f"backfill-amaidesu-commitment-{cid}",
                            "branch_id": self._branch_id,
                            "occurred_at_ms": occ_ms,
                            "kind": "promise_update",
                            "actor_role": "self",
                            "visibility": "public",
                            "context_scope": "live:commitments",
                            "actors": [],
                            "summary": what,
                            "verbatim": quote,
                            "reaction": "",
                            "save_id": save_id,
                            "metadata": {"promise_action": status, "promise_content": what, "backfill": True},
                        }
                    )

            if target_sids and "game_player_interactions" in tables and "game_players" in tables:
                icols = _table_columns(conn, "game_player_interactions")
                pcols = _table_columns(conn, "game_players")
                ph = ",".join("?" for _ in target_sids)
                forget_guard = "AND p.forget_requested_at_ms IS NULL" if "forget_requested_at_ms" in pcols else ""
                sql = (
                    "SELECT i.*, p.game AS p_game, p.uuid AS p_uuid, p.name AS p_name "
                    "FROM game_player_interactions i JOIN game_players p ON i.player_id = p.id "
                    f"WHERE i.live_session_id IN ({ph}) {forget_guard}"
                )
                for row in conn.execute(sql, tuple(target_sids)).fetchall():
                    sid = int(row["live_session_id"])
                    name = _clean_text(row["p_name"] or "玩家", max_len=48)
                    uuid = str(row["p_uuid"] or "").strip()
                    game = str(row["p_game"] or "minecraft").strip() or "minecraft"
                    kind = str(_row_val(row, icols, "kind") or "interaction").strip()
                    summary = _format_interaction_summary(name, kind, _row_val(row, icols, "fact"))
                    occ_ms = int(_row_val(row, icols, "at_ms") or sid_started.get(sid, now_val))
                    events.append(
                        {
                            "idempotency_key": f"backfill-amaidesu-interaction-{int(row['id'])}",
                            "branch_id": self._branch_id,
                            "occurred_at_ms": occ_ms,
                            "kind": "interaction",
                            "actor_role": "system",
                            "visibility": "public",
                            "context_scope": f"live:session:{sid}",
                            "actors": [{"platform": game, "external_id": uuid, "display_name": name}] if uuid else [],
                            "summary": summary,
                            "verbatim": "",
                            "reaction": "",
                            "save_id": str(_row_val(row, icols, "save_id") or "").strip() or None,
                            "metadata": {"backfill": True},
                        }
                    )

            legacy_sids = sorted(target_sids - sids_with_archive)
            if legacy_sids and "timeline_summary" in tables:
                tcols = _table_columns(conn, "timeline_summary")
                ph = ",".join("?" for _ in legacy_sids)
                for row in conn.execute(f"SELECT * FROM timeline_summary WHERE live_session_id IN ({ph})", tuple(legacy_sids)):
                    sid = int(row["live_session_id"])
                    summary = _clean_text(_row_val(row, tcols, "summary"), max_len=360)
                    if not summary:
                        continue
                    occ_ms = int(
                        _row_val(row, tcols, "end_ms") or _row_val(row, tcols, "start_ms") or sid_started.get(sid, now_val)
                    )
                    events.append(
                        {
                            "idempotency_key": f"backfill-amaidesu-summary-{int(row['id'])}",
                            "branch_id": self._branch_id,
                            "occurred_at_ms": occ_ms,
                            "kind": "milestone",
                            "actor_role": "system",
                            "visibility": "public",
                            "context_scope": f"live:session:{sid}",
                            "actors": [],
                            "summary": summary,
                            "verbatim": "",
                            "reaction": "",
                            "save_id": None,
                            "metadata": {"backfill": True, "legacy_fallback": True},
                        }
                    )
        finally:
            conn.close()

        events.sort(key=lambda ev: int(ev["occurred_at_ms"]))
        return events, pending_sids, pending_cids

    async def run(self) -> int:
        """执行一轮低速幂等回填；网络异常时保留未完成进度以便下次重启续传。"""
        async with self._run_lock:
            events, new_sids, new_cids = await asyncio.to_thread(self._collect_pending_sync)
            if not new_sids and not new_cids:
                return 0

            sent = 0
            for idx in range(0, len(events), self._batch_size):
                chunk = events[idx : idx + self._batch_size]
                try:
                    await self._client.post_events(chunk)
                    sent += len(chunk)
                except MaiGORejected as exc:
                    logger.warning(f"MaiGO 历史回填批次被服务端拒收（跳过该批）: {exc}")
                except Exception as exc:  # noqa: BLE001 - 服务端不可达时保留未完成进度下次启动续传
                    logger.warning(f"MaiGO 启动历史回填暂停（已送 {sent}/{len(events)} 条，留待下次重启续传）: {exc}")
                    return sent
                if idx + self._batch_size < len(events) and self._batch_interval_s > 0:
                    await asyncio.sleep(self._batch_interval_s)

            await asyncio.to_thread(self._write_state_sync, new_sids, new_cids)
            if sent > 0:
                logger.info(
                    f"MaiGO 启动历史回填完成: 送达 {sent} 条（正式场 {len(new_sids)} 场, 约定 {len(new_cids)} 条）"
                )
            return sent
