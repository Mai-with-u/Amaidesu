"""Amaidesu 首次连通 MaiGO 时的后台低速历史记忆回填（v2：L1 主线 + L0 原件书库 + L2 实体图谱）。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.maigo_sync.client import MaiGOClient, MaiGORejected
from src.modules.maigo_sync.pending_log import format_interaction_fact
from src.modules.storage.privacy import MIN_MASK_NAME_LEN, mask_names_in_text
from src.modules.storage.repos.viewers import is_placeholder_name
from src.modules.utils.time_utils import now_ms

__all__ = ["StartupHistoryBackfiller"]

logger = get_logger("MaiGOBackfill")

_STATE_VERSION = 2
_SPEECH_MIN_CHARS = 8
_MAX_SPEECH_PER_SESSION = 180
_NOISY_GAME_EVENT_TYPES = frozenset({"chat", "body", "safety", "move", "heartbeat"})


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


def _load_known_viewer_names(conn: sqlite3.Connection, tables: Set[str]) -> List[str]:
    """加载库内 2 字以上已知观众昵称（含曾用名），用于跨端归档台词前遮名脱敏。"""
    names: Set[str] = set()
    if "viewers" in tables:
        vcols = _table_columns(conn, "viewers")
        if "user_name" in vcols:
            for row in conn.execute("SELECT user_name FROM viewers WHERE user_name IS NOT NULL").fetchall():
                val = str(row["user_name"] or "").strip()
                if len(val) >= MIN_MASK_NAME_LEN and not is_placeholder_name(val):
                    names.add(val)
    if "viewer_names" in tables:
        ncols = _table_columns(conn, "viewer_names")
        col = "name" if "name" in ncols else ("user_name" if "user_name" in ncols else "")
        if col:
            for row in conn.execute(f"SELECT {col} FROM viewer_names WHERE {col} IS NOT NULL").fetchall():
                val = str(row[col] or "").strip()
                if len(val) >= MIN_MASK_NAME_LEN and not is_placeholder_name(val):
                    names.add(val)
    return sorted(names, key=len, reverse=True)


def _format_work_doc_text(row: sqlite3.Row, cols: Set[str]) -> str:
    """将 game_work_docs 的笔记、未完待办与钉住规矩拼成可读原件文本。"""
    parts: List[str] = []
    notebook = str(_row_val(row, cols, "notebook") or "").strip()
    if notebook:
        parts.append(notebook[:2200])
    raw_todos = str(_row_val(row, cols, "todos") or "").strip()
    if raw_todos.startswith("["):
        try:
            todos = json.loads(raw_todos)
            open_items = [
                str(t.get("content") or "").strip()
                for t in todos
                if isinstance(t, dict)
                and str(t.get("status") or "pending").lower() not in ("completed", "done", "cancelled", "canceled")
                and str(t.get("content") or "").strip()
            ]
            if open_items:
                parts.append("待办：" + "；".join(open_items[:12]))
        except json.JSONDecodeError:
            pass
    raw_rules = str(_row_val(row, cols, "pinned_rules") or "").strip()
    if raw_rules.startswith("["):
        try:
            rules = [str(r).strip() for r in json.loads(raw_rules) if isinstance(r, str) and r.strip()]
            if rules:
                parts.append("规矩：" + "；".join(rules[:8]))
        except json.JSONDecodeError:
            pass
    return "\n".join(parts)[:3500]


class StartupHistoryBackfiller:
    """只读读取本地 SQLite 正式场数据，分三层低速幂等回填至 MaiGO：

    1. L1 事件（``/api/v1/events``）：高信噪比场次档案行与公开承诺；
    2. L0 原件书库（``/api/v1/archive/ingest``）：每场直播目录与概览、游戏事件、分段实录、
       经脱敏遮名后的已播出主播原话，以及世界级工作笔记与生物名册；
    3. L2 实体关系图谱（``/api/v1/knowledge/ingest``）：现行游戏社交事实与带具体 fact 的玩家互动
       （严格跳过宽限遗忘名单玩家）。
    """

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

    def _read_state_sync(self) -> Dict[str, Any]:
        empty: Dict[str, Any] = {
            "state_version": _STATE_VERSION,
            "completed_sessions": set(),
            "completed_commitments": set(),
            "completed_archive_sessions": set(),
            "completed_knowledge_sessions": set(),
            "completed_social_facts": set(),
            "completed_world_keys": set(),
        }
        if not self._state_path.is_file():
            return empty
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"读取 MaiGO 回填状态失败，将从最近窗口重扫（服务端幂等去重）: {exc}")
            return empty
        if not isinstance(data, dict):
            return empty
        # v1 旧状态只保留 completed_sessions / completed_commitments，从而自动触发 v2 L0+L2 增量补齐
        return {
            "state_version": int(data.get("state_version") or 1),
            "completed_sessions": {int(x) for x in (data.get("completed_sessions") or ()) if isinstance(x, (int, float))},
            "completed_commitments": {
                int(x) for x in (data.get("completed_commitments") or ()) if isinstance(x, (int, float))
            },
            "completed_archive_sessions": {
                int(x) for x in (data.get("completed_archive_sessions") or ()) if isinstance(x, (int, float))
            },
            "completed_knowledge_sessions": {
                int(x) for x in (data.get("completed_knowledge_sessions") or ()) if isinstance(x, (int, float))
            },
            "completed_social_facts": {
                int(x) for x in (data.get("completed_social_facts") or ()) if isinstance(x, (int, float))
            },
            "completed_world_keys": {str(x) for x in (data.get("completed_world_keys") or ()) if isinstance(x, str)},
        }

    def _write_state_sync(
        self,
        *,
        new_l1_sids: Sequence[int] = (),
        new_cids: Sequence[int] = (),
        new_arc_sids: Sequence[int] = (),
        new_kg_sids: Sequence[int] = (),
        new_sf_ids: Sequence[int] = (),
        new_world_keys: Sequence[str] = (),
    ) -> None:
        st = self._read_state_sync()
        st["completed_sessions"].update(int(x) for x in new_l1_sids)
        st["completed_commitments"].update(int(x) for x in new_cids)
        st["completed_archive_sessions"].update(int(x) for x in new_arc_sids)
        st["completed_knowledge_sessions"].update(int(x) for x in new_kg_sids)
        st["completed_social_facts"].update(int(x) for x in new_sf_ids)
        st["completed_world_keys"].update(str(x) for x in new_world_keys if x)
        payload = {
            "state_version": _STATE_VERSION,
            "completed_sessions": sorted(st["completed_sessions"]),
            "completed_commitments": sorted(st["completed_commitments"]),
            "completed_archive_sessions": sorted(st["completed_archive_sessions"]),
            "completed_knowledge_sessions": sorted(st["completed_knowledge_sessions"]),
            "completed_social_facts": sorted(st["completed_social_facts"]),
            "completed_world_keys": sorted(st["completed_world_keys"]),
            "updated_at_ms": int(self._clock_ms()),
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _collect_pending_sync(self) -> Dict[str, Any]:
        empty = {
            "l1_events": [],
            "l1_sids": [],
            "l1_cids": [],
            "archive_batches": [],
            "world_records": [],
            "world_keys": [],
            "knowledge_relations": [],
            "kg_sids": [],
            "sf_ids": [],
        }
        if not self._db_path.is_file():
            return empty

        st = self._read_state_sync()
        done_l1_sids: Set[int] = st["completed_sessions"]
        done_cids: Set[int] = st["completed_commitments"]
        done_arc_sids: Set[int] = st["completed_archive_sessions"]
        done_kg_sids: Set[int] = st["completed_knowledge_sessions"]
        done_sf_ids: Set[int] = st["completed_social_facts"]
        done_world_keys: Set[str] = st["completed_world_keys"]

        now_val = int(self._clock_ms())
        cutoff_ms = now_val - self._since_days * 86_400_000 if self._since_days > 0 else 0

        conn = sqlite3.connect(f"file:{self._db_path.resolve().as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        l1_events: List[Dict[str, Any]] = []
        pending_l1_sids: List[int] = []
        pending_cids: List[int] = []
        archive_batches: List[Tuple[int, Dict[str, Any]]] = []
        world_records: List[Dict[str, Any]] = []
        world_keys: List[str] = []
        knowledge_relations: List[Dict[str, Any]] = []
        pending_kg_sids: List[int] = []
        pending_sf_ids: List[int] = []

        try:
            tables = _list_tables(conn)
            viewer_names = _load_known_viewer_names(conn, tables)

            # 1. 扫描窗口内全部正式场元数据（排除 rehearsal 彩排场）
            sessions_by_id: Dict[int, Dict[str, Any]] = {}
            if "live_sessions" in tables:
                scols = _table_columns(conn, "live_sessions")
                clauses = ["started_at_ms >= ?"] if cutoff_ms > 0 and "started_at_ms" in scols else []
                params: List[Any] = [cutoff_ms] if clauses else []
                if "kind" in scols:
                    clauses.append("COALESCE(kind, 'live') != 'rehearsal'")
                where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                for row in conn.execute(f"SELECT * FROM live_sessions {where_sql} ORDER BY started_at_ms ASC", params):
                    sid = int(row["id"])
                    sessions_by_id[sid] = {
                        "id": sid,
                        "title": _clean_text(_row_val(row, scols, "title"), max_len=200),
                        "platform": str(_row_val(row, scols, "platform") or "bilibili").strip() or "bilibili",
                        "started_at_ms": int(_row_val(row, scols, "started_at_ms") or now_val),
                        "ended_at_ms": (
                            int(_row_val(row, scols, "ended_at_ms"))
                            if _row_val(row, scols, "ended_at_ms") is not None
                            else None
                        ),
                        "overview": _clean_text(_row_val(row, scols, "overview"), max_len=3600),
                    }

            pending_l1_sids = [sid for sid in sessions_by_id if sid not in done_l1_sids]
            pending_arc_sids = [sid for sid in sessions_by_id if sid not in done_arc_sids]
            pending_kg_sids = [sid for sid in sessions_by_id if sid not in done_kg_sids]

            # 2. 采集 L1 高信噪比事件（archive_lines + commitments + 无档案回退 timeline_summary）
            l1_target = set(pending_l1_sids)
            sids_with_archive: Set[int] = set()
            if l1_target and "archive_lines" in tables:
                acols = _table_columns(conn, "archive_lines")
                ph = ",".join("?" for _ in l1_target)
                for row in conn.execute(f"SELECT * FROM archive_lines WHERE live_session_id IN ({ph})", tuple(l1_target)):
                    sid = int(row["live_session_id"])
                    text = _clean_text(_row_val(row, acols, "text"), max_len=360)
                    if not text:
                        continue
                    sids_with_archive.add(sid)
                    quote = _clean_text(_row_val(row, acols, "quote"), max_len=1000)
                    save_id = str(_row_val(row, acols, "save_id") or "").strip() or None
                    occ_ms = int(_row_val(row, acols, "created_at_ms") or sessions_by_id[sid]["started_at_ms"])
                    l1_events.append(
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
                    if csid is not None and int(csid) in done_l1_sids:
                        continue
                    if l1_target and csid is not None and int(csid) not in l1_target:
                        continue
                    what = _clean_text(_row_val(row, ccols, "what"), max_len=320)
                    if not what:
                        continue
                    fallback_ts = sessions_by_id.get(int(csid), {}).get("started_at_ms", now_val) if csid is not None else now_val
                    occ_ms = int(
                        _row_val(row, ccols, "updated_at_ms") or _row_val(row, ccols, "created_at_ms") or fallback_ts
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
                    l1_events.append(
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

            legacy_sids = sorted(l1_target - sids_with_archive)
            if legacy_sids and "timeline_summary" in tables:
                tcols = _table_columns(conn, "timeline_summary")
                ph = ",".join("?" for _ in legacy_sids)
                for row in conn.execute(f"SELECT * FROM timeline_summary WHERE live_session_id IN ({ph})", tuple(legacy_sids)):
                    sid = int(row["live_session_id"])
                    summary = _clean_text(_row_val(row, tcols, "summary"), max_len=360)
                    if not summary:
                        continue
                    occ_ms = int(
                        _row_val(row, tcols, "end_ms")
                        or _row_val(row, tcols, "start_ms")
                        or sessions_by_id[sid]["started_at_ms"]
                    )
                    l1_events.append(
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

            # 3. 采集 L0 按场次全量细粒度原件书库（overview + archive_lines + game_events + timeline_summary + 遮名台词）
            for sid in pending_arc_sids:
                meta = sessions_by_id[sid]
                recs: List[Dict[str, Any]] = []
                session_save_id: Optional[str] = None

                if "archive_lines" in tables:
                    acols = _table_columns(conn, "archive_lines")
                    for row in conn.execute("SELECT * FROM archive_lines WHERE live_session_id = ?", (sid,)).fetchall():
                        text = _clean_text(_row_val(row, acols, "text"), max_len=400)
                        if not text:
                            continue
                        quote = _clean_text(_row_val(row, acols, "quote"), max_len=1200)
                        save_id = str(_row_val(row, acols, "save_id") or "").strip() or None
                        if save_id and not session_save_id:
                            session_save_id = save_id
                        recs.append(
                            {
                                "idempotency_key": f"backfill-rec-arc-{int(row['id'])}",
                                "record_type": "archive_line",
                                "actor_role": "self",
                                "visibility": "public",
                                "occurred_at_ms": int(_row_val(row, acols, "created_at_ms") or meta["started_at_ms"]),
                                "save_id": save_id,
                                "title": text[:200],
                                "content": quote or text,
                                "verbatim": quote,
                                "metadata": {"highlight": bool(_row_val(row, acols, "highlight", False))},
                            }
                        )

                if "game_events" in tables:
                    gcols = _table_columns(conn, "game_events")
                    for row in conn.execute(
                        "SELECT * FROM game_events WHERE live_session_id = ? ORDER BY timestamp_ms ASC", (sid,)
                    ).fetchall():
                        etype = str(_row_val(row, gcols, "event_type") or "").strip().lower()
                        if etype in _NOISY_GAME_EVENT_TYPES:
                            continue
                        msg = _clean_text(_row_val(row, gcols, "message"), max_len=800)
                        if not msg:
                            continue
                        game = str(_row_val(row, gcols, "game") or "game").strip()
                        save_id = str(_row_val(row, gcols, "save_id") or "").strip() or None
                        if save_id and not session_save_id:
                            session_save_id = save_id
                        recs.append(
                            {
                                "idempotency_key": f"backfill-rec-ge-{int(row['id'])}",
                                "record_type": "game_event",
                                "actor_role": "self",
                                "visibility": "public",
                                "occurred_at_ms": int(_row_val(row, gcols, "timestamp_ms") or meta["started_at_ms"]),
                                "save_id": save_id,
                                "title": f"{game}:{etype or 'event'}"[:120],
                                "content": msg,
                                "verbatim": "",
                                "metadata": {"game": game, "event_type": etype},
                            }
                        )

                if "timeline_summary" in tables:
                    tcols = _table_columns(conn, "timeline_summary")
                    for row in conn.execute(
                        "SELECT * FROM timeline_summary WHERE live_session_id = ? ORDER BY start_ms ASC", (sid,)
                    ).fetchall():
                        summary = _clean_text(_row_val(row, tcols, "summary"), max_len=1500)
                        if not summary:
                            continue
                        recs.append(
                            {
                                "idempotency_key": f"backfill-rec-tl-{int(row['id'])}",
                                "record_type": "timeline_chunk",
                                "actor_role": "system",
                                "visibility": "public",
                                "occurred_at_ms": int(
                                    _row_val(row, tcols, "end_ms")
                                    or _row_val(row, tcols, "start_ms")
                                    or meta["started_at_ms"]
                                ),
                                "save_id": session_save_id,
                                "title": "分段实录",
                                "content": summary,
                                "verbatim": "",
                                "metadata": {},
                            }
                        )

                if "live_chat" in tables:
                    lcols = _table_columns(conn, "live_chat")
                    sim_guard = "AND COALESCE(simulated, 0) = 0" if "simulated" in lcols else ""
                    drop_guard = "AND dropped_at_ms IS NULL" if "dropped_at_ms" in lcols else ""
                    sql_chat = (
                        "SELECT id, timestamp_ms, content FROM live_chat "
                        f"WHERE live_session_id = ? AND sender_role = 'assistant' {sim_guard} {drop_guard} "
                        "ORDER BY timestamp_ms ASC"
                    )
                    speech_added = 0
                    for row in conn.execute(sql_chat, (sid,)).fetchall():
                        if speech_added >= _MAX_SPEECH_PER_SESSION:
                            break
                        raw_text = _clean_text(row["content"], max_len=600)
                        if len(raw_text) < _SPEECH_MIN_CHARS:
                            continue
                        masked = mask_names_in_text(raw_text, viewer_names, "某位观众")
                        recs.append(
                            {
                                "idempotency_key": f"backfill-rec-speech-{int(row['id'])}",
                                "record_type": "speech",
                                "actor_role": "self",
                                "visibility": "public",
                                "occurred_at_ms": int(row["timestamp_ms"] or meta["started_at_ms"]),
                                "save_id": session_save_id,
                                "title": "主播原话",
                                "content": masked,
                                "verbatim": masked,
                                "metadata": {},
                            }
                        )
                        speech_added += 1

                episode_payload = {
                    "branch_id": self._branch_id,
                    "external_episode_id": str(sid),
                    "kind": "live",
                    "title": meta["title"],
                    "platform": meta["platform"],
                    "save_id": session_save_id,
                    "visibility": "public",
                    "started_at_ms": meta["started_at_ms"],
                    "ended_at_ms": meta["ended_at_ms"],
                    "overview": meta["overview"],
                    "metadata": {"backfill": True},
                }
                archive_batches.append((sid, {"branch_id": self._branch_id, "episode": episode_payload, "records": recs}))

            # 4. 采集世界级游戏工作文档与生物名册写入 L0（按内容更新时刻键去重）
            if "game_work_docs" in tables:
                wcols = _table_columns(conn, "game_work_docs")
                for row in conn.execute("SELECT * FROM game_work_docs").fetchall():
                    game = str(_row_val(row, wcols, "game") or "minecraft").strip()
                    save_id = str(_row_val(row, wcols, "save_id") or "").strip()
                    updated_ms = int(_row_val(row, wcols, "updated_at_ms") or now_val)
                    wkey = f"workdoc:{game}:{save_id}:{updated_ms}"
                    if not save_id or wkey in done_world_keys:
                        continue
                    body = _format_work_doc_text(row, wcols)
                    if not body:
                        continue
                    save_name = _clean_text(_row_val(row, wcols, "save_name"), max_len=80) or save_id
                    world_keys.append(wkey)
                    world_records.append(
                        {
                            "idempotency_key": f"backfill-{wkey}",
                            "record_type": "work_doc",
                            "actor_role": "self",
                            "visibility": "public",
                            "occurred_at_ms": updated_ms,
                            "save_id": save_id,
                            "title": f"存档工作笔记（{save_name}）"[:200],
                            "content": body,
                            "verbatim": "",
                            "metadata": {"game": game},
                        }
                    )

            if "game_creatures" in tables:
                cr_cols = _table_columns(conn, "game_creatures")
                for row in conn.execute("SELECT * FROM game_creatures").fetchall():
                    game = str(_row_val(row, cr_cols, "game") or "minecraft").strip()
                    w_id = str(_row_val(row, cr_cols, "world_key") or "").strip()
                    c_key = str(_row_val(row, cr_cols, "creature_key") or "").strip()
                    updated_ms = int(_row_val(row, cr_cols, "updated_at_ms") or now_val)
                    mark = f"creature:{game}:{w_id}:{c_key}:{updated_ms}"
                    if not c_key or mark in done_world_keys:
                        continue
                    nickname = _clean_text(_row_val(row, cr_cols, "nickname"), max_len=60)
                    name = _clean_text(_row_val(row, cr_cols, "name"), max_len=60)
                    kind = _clean_text(_row_val(row, cr_cols, "kind") or _row_val(row, cr_cols, "type"), max_len=40)
                    label = nickname or name or kind
                    if not label:
                        continue
                    died_ms = _row_val(row, cr_cols, "died_ms")
                    death_note = _clean_text(_row_val(row, cr_cols, "death_note"), max_len=160)
                    status_str = f"已去世（{death_note or '已阵亡'}）" if died_ms else "存活同伴"
                    world_keys.append(mark)
                    world_records.append(
                        {
                            "idempotency_key": f"backfill-{mark}",
                            "record_type": "creature",
                            "actor_role": "self",
                            "visibility": "public",
                            "occurred_at_ms": updated_ms,
                            "save_id": w_id or None,
                            "title": f"同伴名册：{label}"[:200],
                            "content": f"{label}（种类：{kind or '生物'}，状态：{status_str}）",
                            "verbatim": "",
                            "metadata": {"game": game, "died": bool(died_ms)},
                        }
                    )

            # 5. 采集 L2 实体与关系图谱（game_social_facts 现行行 + 非遗忘玩家真实互动）
            if "game_social_facts" in tables:
                fcols = _table_columns(conn, "game_social_facts")
                for row in conn.execute("SELECT * FROM game_social_facts WHERE status = 'active'").fetchall():
                    fid = int(row["id"])
                    if fid in done_sf_ids:
                        continue
                    text = _clean_text(_row_val(row, fcols, "text"), max_len=800)
                    if not text:
                        continue
                    game = str(_row_val(row, fcols, "game") or "minecraft").strip()
                    save_id = str(_row_val(row, fcols, "save_id") or "").strip() or None
                    save_name = _clean_text(_row_val(row, fcols, "save_name"), max_len=80) or save_id or game
                    fkind = str(_row_val(row, fcols, "kind") or "social_fact").strip()
                    quote = _clean_text(_row_val(row, fcols, "quote"), max_len=600)
                    upd_ms = int(_row_val(row, fcols, "updated_at_ms") or _row_val(row, fcols, "created_at_ms") or now_val)
                    csid = _row_val(row, fcols, "updated_session_id") or _row_val(row, fcols, "created_session_id")
                    pending_sf_ids.append(fid)
                    knowledge_relations.append(
                        {
                            "idempotency_key": f"backfill-sf-{fid}-{upd_ms}",
                            "subject": {
                                "entity_type": "project" if save_id else "concept",
                                "canonical_key": f"{game}:{save_id or 'global'}",
                                "name": save_name,
                                "save_id": save_id,
                                "visibility": "public",
                                "seen_at_ms": upd_ms,
                            },
                            "predicate": fkind,
                            "object_text": fkind,
                            "fact_text": text,
                            "quote": quote,
                            "visibility": "public",
                            "save_id": save_id,
                            "external_episode_id": str(int(csid)) if csid is not None else None,
                            "occurred_at_ms": upd_ms,
                        }
                    )

            kg_target = set(pending_kg_sids)
            if kg_target and "game_player_interactions" in tables and "game_players" in tables:
                icols = _table_columns(conn, "game_player_interactions")
                pcols = _table_columns(conn, "game_players")
                ph = ",".join("?" for _ in kg_target)
                forget_guard = "AND p.forget_requested_at_ms IS NULL" if "forget_requested_at_ms" in pcols else ""
                sql = (
                    "SELECT i.*, p.game AS p_game, p.uuid AS p_uuid, p.name AS p_name "
                    "FROM game_player_interactions i JOIN game_players p ON i.player_id = p.id "
                    f"WHERE i.live_session_id IN ({ph}) {forget_guard}"
                )
                for row in conn.execute(sql, tuple(kg_target)).fetchall():
                    sid = int(row["live_session_id"])
                    name = _clean_text(row["p_name"] or "玩家", max_len=48)
                    uuid_str = str(row["p_uuid"] or "").strip()
                    game = str(row["p_game"] or "minecraft").strip() or "minecraft"
                    kind = str(_row_val(row, icols, "kind") or "interaction").strip()
                    fact_text = format_interaction_fact(name, kind, _row_val(row, icols, "fact"))
                    occ_ms = int(_row_val(row, icols, "at_ms") or sessions_by_id[sid]["started_at_ms"])
                    save_id = str(_row_val(row, icols, "save_id") or "").strip() or None
                    knowledge_relations.append(
                        {
                            "idempotency_key": f"backfill-kg-inter-{int(row['id'])}",
                            "subject": {
                                "entity_type": "player",
                                "canonical_key": f"{game.lower()}:{uuid_str or name}",
                                "name": name,
                                "platform": game,
                                "external_id": uuid_str,
                                "save_id": save_id,
                                "visibility": "public",
                                "seen_at_ms": occ_ms,
                            },
                            "predicate": kind or "interacted_with",
                            "fact_text": fact_text,
                            "visibility": "public",
                            "save_id": save_id,
                            "external_episode_id": str(sid),
                            "occurred_at_ms": occ_ms,
                        }
                    )
        finally:
            conn.close()

        l1_events.sort(key=lambda ev: int(ev["occurred_at_ms"]))
        return {
            "l1_events": l1_events,
            "l1_sids": pending_l1_sids,
            "l1_cids": pending_cids,
            "archive_batches": archive_batches,
            "world_records": world_records,
            "world_keys": world_keys,
            "knowledge_relations": knowledge_relations,
            "kg_sids": pending_kg_sids,
            "sf_ids": pending_sf_ids,
        }

    async def run(self) -> int:
        """执行一轮低速幂等三层回填；网络异常时保留未完成进度以便下次重启续传。"""
        async with self._run_lock:
            collected = await asyncio.to_thread(self._collect_pending_sync)
            l1_events: List[Dict[str, Any]] = collected["l1_events"]
            l1_sids: List[int] = collected["l1_sids"]
            l1_cids: List[int] = collected["l1_cids"]
            archive_batches: List[Tuple[int, Dict[str, Any]]] = collected["archive_batches"]
            world_records: List[Dict[str, Any]] = collected["world_records"]
            world_keys: List[str] = collected["world_keys"]
            knowledge_relations: List[Dict[str, Any]] = collected["knowledge_relations"]
            kg_sids: List[int] = collected["kg_sids"]
            sf_ids: List[int] = collected["sf_ids"]

            if not (l1_sids or l1_cids or archive_batches or world_records or knowledge_relations or kg_sids or sf_ids):
                return 0

            # 启动前输出总量概览，便于直播机控制台观察本次历史回填规模
            total_arc_records = sum(len(batch.get("records") or ()) for _, batch in archive_batches)
            logger.info(
                "MaiGO 启动历史回填开始: "
                f"L1 事件 {len(l1_events)} 条（{len(l1_sids)} 场） / "
                f"L0 原件 {len(archive_batches)} 场（{total_arc_records} 条） / "
                f"世界笔记与同伴 {len(world_records)} 条 / "
                f"L2 关系 {len(knowledge_relations)} 条"
            )

            sent_total = 0

            # 1. 回填 L1 主线事件
            if l1_events or l1_sids or l1_cids:
                for idx in range(0, len(l1_events), self._batch_size):
                    chunk = l1_events[idx : idx + self._batch_size]
                    try:
                        await self._client.post_events(chunk)
                        sent_total += len(chunk)
                    except MaiGORejected as exc:
                        logger.warning(f"MaiGO L1 历史回填批次被拒收（跳过该批）: {exc}")
                    except Exception as exc:  # noqa: BLE001 - 服务端不可达保留进度下次续传
                        logger.warning(f"MaiGO 启动历史回填暂停（L1 已送 {sent_total} 条）: {exc}")
                        return sent_total
                    if idx + self._batch_size < len(l1_events) and self._batch_interval_s > 0:
                        await asyncio.sleep(self._batch_interval_s)
                await asyncio.to_thread(self._write_state_sync, new_l1_sids=l1_sids, new_cids=l1_cids)
                if l1_events:
                    logger.info(f"MaiGO L1 主线回填完成: 送达 {len(l1_events)} 条（{len(l1_sids)} 场）")

            # 2. 回填 L0 原件书库（按场次分批写入，每场成功后即时推进状态，支持断点续传）
            ingest_archive_fn = getattr(self._client, "ingest_archive", None)
            if callable(ingest_archive_fn):
                total_sessions = len(archive_batches)
                for ep_idx, (sid, batch_payload) in enumerate(archive_batches, start=1):
                    records = list(batch_payload.get("records") or ())
                    episode = batch_payload.get("episode")
                    ext_ep_id = str((episode or {}).get("external_episode_id") or sid)
                    ep_title = str((episode or {}).get("title") or f"场次 {sid}")
                    try:
                        if not records:
                            await ingest_archive_fn(
                                {"branch_id": self._branch_id, "episode": episode, "records": []}
                            )
                            sent_total += 1
                        else:
                            for idx in range(0, len(records), self._batch_size):
                                sub = records[idx : idx + self._batch_size]
                                # 首片携带完整 episode 建档；后续分片携带 external_episode_id 保持归属场次绑定
                                body: Dict[str, Any] = {
                                    "branch_id": self._branch_id,
                                    "episode": episode if idx == 0 else None,
                                    "records": sub,
                                }
                                if idx > 0:
                                    body["external_episode_id"] = ext_ep_id
                                await ingest_archive_fn(body)
                                sent_total += len(sub)
                                if idx + self._batch_size < len(records) and self._batch_interval_s > 0:
                                    await asyncio.sleep(self._batch_interval_s)
                        await asyncio.to_thread(self._write_state_sync, new_arc_sids=[sid])
                        logger.info(
                            f"MaiGO L0 原件回填进度 [{ep_idx}/{total_sessions}]: "
                            f"场次 {sid}（{ep_title}，本场 {len(records)} 条，累计 {sent_total} 条）"
                        )
                    except MaiGORejected as exc:
                        logger.warning(f"MaiGO L0 原件书库场次 {sid} 被拒收（跳过）: {exc}")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"MaiGO L0 原件书库回填暂停（场次 {sid} 留待续传）: {exc}")
                        return sent_total

                if world_records:
                    for idx in range(0, len(world_records), self._batch_size):
                        sub = world_records[idx : idx + self._batch_size]
                        try:
                            await ingest_archive_fn({"branch_id": self._branch_id, "records": sub})
                            sent_total += len(sub)
                        except MaiGORejected as exc:
                            logger.warning(f"MaiGO 世界笔记/同伴名册批次被拒收（跳过）: {exc}")
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(f"MaiGO 世界笔记/同伴名册回填暂停: {exc}")
                            return sent_total
                        if idx + self._batch_size < len(world_records) and self._batch_interval_s > 0:
                            await asyncio.sleep(self._batch_interval_s)
                    await asyncio.to_thread(self._write_state_sync, new_world_keys=world_keys)
                    logger.info(f"MaiGO 世界笔记与同伴名册回填完成: 送达 {len(world_records)} 条")

            # 3. 回填 L2 实体关系图谱（社交事实与玩家互动）
            ingest_kg_fn = getattr(self._client, "ingest_knowledge", None)
            if callable(ingest_kg_fn) and (knowledge_relations or kg_sids or sf_ids):
                for idx in range(0, len(knowledge_relations), self._batch_size):
                    sub = knowledge_relations[idx : idx + self._batch_size]
                    try:
                        await ingest_kg_fn({"branch_id": self._branch_id, "entities": [], "relations": sub})
                        sent_total += len(sub)
                    except MaiGORejected as exc:
                        logger.warning(f"MaiGO L2 实体关系批次被拒收（跳过）: {exc}")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"MaiGO L2 实体关系回填暂停: {exc}")
                        return sent_total
                    if idx + self._batch_size < len(knowledge_relations) and self._batch_interval_s > 0:
                        await asyncio.sleep(self._batch_interval_s)
                await asyncio.to_thread(self._write_state_sync, new_kg_sids=kg_sids, new_sf_ids=sf_ids)
                if knowledge_relations:
                    logger.info(f"MaiGO L2 实体关系图谱回填完成: 送达 {len(knowledge_relations)} 条")

            if sent_total > 0:
                logger.info(
                    f"MaiGO 启动历史回填完成: 送达 {sent_total} 条"
                    f"（L1 场次 {len(l1_sids)} / L0 原件场次 {len(archive_batches)} / L2 关系 {len(knowledge_relations)}）"
                )
            return sent_total
