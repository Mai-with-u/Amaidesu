"""下播签入成品提取与离线待补推 JSONL 队列管理。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.modules.logging import get_logger

__all__ = [
    "CachedPersonEntry",
    "CachedPromiseEntry",
    "CachedTopicCard",
    "PendingCheckinLog",
    "extract_archive_lines",
    "extract_interactions",
    "extract_promises",
    "extract_session_meta",
    "format_brief_extension",
]

logger = get_logger("MaiGOBridge")


@dataclass(frozen=True)
class CachedPromiseEntry:
    """开播签出缓存的跨端公开约定。"""

    anchor_id: int
    content: str
    status: str
    source_branch: str
    save_id: Optional[str] = None


@dataclass
class CachedTopicCard:
    """开播签出缓存的鲜活话头条目。"""

    seed_id: int
    title: str
    summary: str
    reaction: str
    source_branch: str
    transition_hint: str
    consumed: bool = False


@dataclass(frozen=True)
class CachedPersonEntry:
    """开播签出缓存的跨端熟人本地索引项。"""

    person_id: int
    platform: str
    external_id: str
    display_name: str
    prompt_hint: str


def format_brief_extension(
    *,
    stable_brief: str,
    open_promises: Sequence[CachedPromiseEntry],
    save_brief: str,
) -> str:
    """在开播签出时冻结本场跨端记忆扩展文本，确保整场不变以保护提示词前缀缓存。"""
    sections: List[str] = []
    if stable_brief.strip():
        sections.append(stable_brief.strip())
    active_promises = [p for p in open_promises if p.status == "open" and p.content.strip()]
    if active_promises:
        lines = ["【跨端还惦记的公开约定】"]
        for prom in active_promises:
            branch = prom.source_branch.strip() or "其他分支"
            lines.append(f"- 跨端约#{prom.anchor_id}（来自{branch}）：{prom.content.strip()}")
        sections.append("\n".join(lines))
    if save_brief.strip():
        sections.append(f"【存档跨端变更摘要】\n{save_brief.strip()}")
    return "\n\n".join(sections)


async def extract_session_meta(sessions_repo: Any, sid: int) -> Tuple[str, Optional[int]]:
    """读取本场概览与结束时刻（失败仅记日志并降级空值）。"""
    get_fn = getattr(sessions_repo, "get_live_session", None)
    if not callable(get_fn):
        return "", None
    try:
        session = await get_fn(live_session_id=sid)
    except Exception as exc:  # noqa: BLE001 - 场次元数据读取失败不影响其余签入内容
        logger.warning(f"读取场次 {sid} 概览失败（忽略概览）: {exc}", exc=exc)
        return "", None
    if session is None:
        return "", None
    overview = str(getattr(session, "overview", None) or "").strip()
    ended_at = getattr(session, "ended_at_ms", None)
    return overview, int(ended_at) if ended_at is not None else None


async def extract_archive_lines(
    archive_repo: Any,
    sid: int,
    *,
    fallback_save: Optional[str],
) -> List[Dict[str, Any]]:
    """从本地档案仓储提取本场整理的精炼档案行列表。"""
    if archive_repo is None:
        return []
    list_fn = getattr(archive_repo, "list_lines", None)
    if not callable(list_fn):
        return []
    try:
        rows = await list_fn(live_session_id=sid)
    except Exception as exc:  # noqa: BLE001 - 档案行读取失败跳过该段
        logger.warning(f"读取场次 {sid} 档案行失败（签入不含档案行）: {exc}", exc=exc)
        return []
    items: List[Dict[str, Any]] = []
    for row in rows or ():
        title = str(getattr(row, "text", "") or "").strip()[:200]
        if not title:
            continue
        items.append(
            {
                "title": title,
                "verbatim": str(getattr(row, "quote", "") or "").strip()[:2000],
                "highlight": bool(getattr(row, "highlight", False)),
                "save_id": (str(getattr(row, "save_id", "") or "").strip() or fallback_save),
            }
        )
    return items


def _match_checked_out_anchor(content: str, open_promises: Sequence[CachedPromiseEntry]) -> Optional[int]:
    norm = "".join(content.split())
    if not norm:
        return None
    for prom in open_promises:
        target = "".join(prom.content.split())
        if target and (target == norm or target in norm or norm in target):
            return int(prom.anchor_id)
    return None


async def extract_promises(
    commitments_repo: Any,
    *,
    sid: int,
    started_at_ms: int,
    open_promises: Sequence[CachedPromiseEntry],
    resolved_promises: Dict[int, str],
    fallback_save: Optional[str],
) -> List[Dict[str, Any]]:
    """提取本场新立或结案的公开承诺（自动跳过对特定个人承诺保护隐私）。"""
    entries: List[Dict[str, Any]] = []
    matched_anchor_ids: set[int] = set()
    if commitments_repo is not None and callable(getattr(commitments_repo, "list_commitments", None)):
        try:
            rows = await commitments_repo.list_commitments(limit=200)
        except Exception as exc:  # noqa: BLE001 - 承诺表读取失败仅保留显式回调
            logger.warning(f"读取场次 {sid} 承诺变更失败（跳过仓储承诺）: {exc}", exc=exc)
            rows = []
        for row in rows or ():
            if getattr(row, "has_target", False):
                continue
            created_sid = getattr(row, "created_session_id", None)
            updated_ms = int(getattr(row, "updated_at_ms", 0) or 0)
            status = str(getattr(row, "status", "open") or "open")
            is_created_here = created_sid == sid
            is_closed_here = status in ("closed", "void") and updated_ms >= started_at_ms
            if not is_created_here and not is_closed_here:
                continue
            content = str(getattr(row, "what", "") or "").strip()[:400]
            if not content:
                continue
            anchor_id = _match_checked_out_anchor(content, open_promises)
            if anchor_id is not None:
                matched_anchor_ids.add(anchor_id)
            verbatim = str(getattr(row, "close_quote", None) or getattr(row, "quote", "") or "").strip()[:2000]
            entries.append(
                {
                    "anchor_id": anchor_id,
                    "content": content,
                    "verbatim": verbatim,
                    "status": status if status in ("open", "closed", "void") else "open",
                    "save_id": (str(getattr(row, "save_id", "") or "").strip() or fallback_save),
                    "note": "",
                }
            )

    for anchor_id, status in resolved_promises.items():
        if anchor_id in matched_anchor_ids:
            continue
        ref = next((p for p in open_promises if p.anchor_id == anchor_id), None)
        entries.append(
            {
                "anchor_id": int(anchor_id),
                "content": ref.content if ref is not None else f"跨端约定#{anchor_id}",
                "verbatim": "",
                "status": status,
                "save_id": ref.save_id if ref is not None else fallback_save,
                "note": "直播间已兑现",
            }
        )
    return entries


async def extract_interactions(game_player_repo: Any, sid: int) -> List[Dict[str, Any]]:
    """提取本场游戏内玩家互动并严格剔除任何处于宽限删除状态的玩家。"""
    if game_player_repo is None:
        return []
    list_fn = getattr(game_player_repo, "list_session_interactions", None)
    get_player_fn = getattr(game_player_repo, "get_player", None)
    if not callable(list_fn) or not callable(get_player_fn):
        return []
    try:
        interactions = await list_fn(live_session_id=sid)
    except Exception as exc:  # noqa: BLE001 - 互动记录读取失败跳过该段
        logger.warning(f"读取场次 {sid} 玩家互动失败（跳过玩家互动）: {exc}", exc=exc)
        return []

    players_cache: Dict[int, Any] = {}
    results: List[Dict[str, Any]] = []
    for inter in interactions or ():
        pid = int(getattr(inter, "player_id", 0) or 0)
        if pid <= 0:
            continue
        if pid not in players_cache:
            try:
                players_cache[pid] = await get_player_fn(player_id=pid)
            except Exception as exc:  # noqa: BLE001 - 单人读取失败视为不可用
                logger.warning(f"读取玩家 {pid} 失败（跳过互动）: {exc}", exc=exc)
                players_cache[pid] = None
        player = players_cache[pid]
        if player is None or getattr(player, "forget_requested_at_ms", None) is not None:
            continue
        kind = str(getattr(inter, "kind", "") or "interaction")
        name = str(getattr(player, "name", "") or "玩家")
        results.append(
            {
                "platform": str(getattr(player, "game", "") or "minecraft"),
                "external_id": str(getattr(player, "uuid", "") or ""),
                "display_name": name,
                "summary": f"与 {name} 发生游戏互动（{kind}）"[:1000],
                "visibility": "public",
            }
        )
    return results


class PendingCheckinLog:
    """下播签入失败后的 JSONL 持久化待补推队列。"""

    def __init__(self, path: Optional[Path]) -> None:
        self._path = Path(path) if path is not None else None

    @property
    def enabled(self) -> bool:
        """是否已配置磁盘落盘路径。"""
        return self._path is not None

    def append(self, checkout_id: str, payload: Dict[str, Any]) -> None:
        """向队尾追加一条待补推签入记录（处理半行结尾并创建父目录）。"""
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"checkout_id": checkout_id, "payload": payload}, ensure_ascii=False) + "\n"
        with self._path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    line = "\n" + line
            handle.write(line.encode("utf-8"))

    def read_all(self) -> List[Dict[str, Any]]:
        """读取当前所有待补推记录（自动跳过损坏 JSON 行并记录警告）。"""
        if self._path is None:
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            logger.warning(f"读取 MaiGO 待补推队列失败: {exc}", exc=exc)
            return []
        records: List[Dict[str, Any]] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning(f"跳过损坏的 MaiGO 待补推记录行: {stripped[:80]}")
                continue
            if isinstance(item, dict):
                records.append(item)
        return records

    def rewrite(self, records: Sequence[Dict[str, Any]]) -> None:
        """原子回写剩余未送达记录，队列为空时删除文件。"""
        if self._path is None:
            return
        try:
            if not records:
                if self._path.exists():
                    self._path.unlink()
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            text = "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n"
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning(f"更新 MaiGO 待补推队列失败: {exc}", exc=exc)
