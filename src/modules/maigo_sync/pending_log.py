"""下播签入成品提取与离线待补推 JSONL 队列管理。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.storage.privacy import mask_names_in_text

__all__ = [
    "CachedPersonEntry",
    "CachedPromiseEntry",
    "CachedTopicCard",
    "PendingCheckinLog",
    "extract_archive_lines",
    "extract_game_events",
    "extract_interactions",
    "extract_key_speeches",
    "extract_promises",
    "extract_session_meta",
    "extract_social_facts",
    "extract_work_docs",
    "format_brief_extension",
    "format_exc",
    "format_interaction_fact",
]

logger = get_logger("MaiGOBridge")

_CROSS_SESSION_MASK_LABEL = "某位观众"
_NOISY_GAME_EVENT_TYPES = frozenset({"chat", "body", "safety", "move", "heartbeat"})
_DEFAULT_BRANCH_ID = "amaidesu"
# 防御性改写服务端简报里的裸约定编号：仅匹配未带“跨端”前缀且紧跟 #/＃ 的编号，避免误伤“约 5 分钟”等口语
_CROSS_PROMISE_REF_RE = re.compile(r"(?<!跨端)约\s*[#＃]\s*(\d+)")
_CROSS_PROMISE_ID_RE = re.compile(r"跨端约#(\d+)")
_PROMISE_SECTION_HEADER_RE = re.compile(r"^(?:【[^】]*约定[^】]*】|#{1,6}\s+.*约定.*)$")
_SECTION_HEADER_RE = re.compile(r"^(?:【[^】]+】|#{1,6}\s+\S)")
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s+")
_SOURCE_TAG_RE = re.compile(r"[（(]\s*来自\s*([^（）()\s]+)\s*[）)]")
_PROMISE_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)、])?\s*(?:[\[【]?\s*跨端约#\d+\s*[\]】]?)?\s*[：:·-]?\s*")
_NON_CONTENT_CHARS_RE = re.compile(r"[\s，。！？、；：,.!?;:·\-—_()（）\[\]【】\"'“”‘’]+")


def format_exc(exc: BaseException) -> str:
    """格式化异常摘要为带类名的可读文本；异常消息为空时仅返回类名，避免日志留悬空冒号。"""
    name = type(exc).__name__
    detail = str(exc).strip()
    return f"{name}: {detail}" if detail else name


def _clean_compact(raw: Any, *, max_len: int = 360) -> str:
    compact = " ".join(str(raw or "").split()).strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1].rstrip() + "…"


def format_interaction_fact(name: str, kind: str, raw_fact: Any) -> str:
    """把玩家互动的结构化 fact（字典、JSON 串或纯文本）转成人类可读的具体事实。"""
    parsed: Any = raw_fact
    if isinstance(raw_fact, str):
        stripped = raw_fact.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = stripped
        else:
            parsed = stripped
    if isinstance(parsed, dict):
        desc = str(
            parsed.get("summary") or parsed.get("text") or parsed.get("note") or parsed.get("what") or ""
        ).strip()
        if desc:
            return _clean_compact(f"{name}：{desc}", max_len=400)
        item = str(parsed.get("item") or "").strip()
        if item:
            cnt = parsed.get("count")
            cnt_part = f"×{cnt}" if isinstance(cnt, (int, float)) and int(cnt) > 1 else ""
            return _clean_compact(f"与 {name} 互动（{kind}：{item}{cnt_part}）", max_len=400)
        count_val = parsed.get("count")
        if isinstance(count_val, (int, float)) and int(count_val) > 1:
            return _clean_compact(f"与 {name} 发生游戏互动（{kind}，共 {int(count_val)} 次）", max_len=400)
    elif isinstance(parsed, str) and parsed:
        return _clean_compact(f"{name}：{parsed}", max_len=400)
    return _clean_compact(f"与 {name} 发生游戏互动（{kind}）", max_len=400)


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


def _rewrite_cross_branch_promise_refs(text: str) -> str:
    """将服务端简报里可能与本地承诺表撞号的 `[约#N]` / `约#N` 统一改写为 `跨端约#N`。"""
    if not text:
        return ""
    return _CROSS_PROMISE_REF_RE.sub(r"跨端约#\1", text)


def _normalize_promise_body(text: str) -> str:
    """抽取约定正文用于同源与跨段去重比对（剥离列表符号、跨端编号、来源标注与标点空白）。"""
    cleaned = _PROMISE_PREFIX_RE.sub("", text or "")
    cleaned = _SOURCE_TAG_RE.sub("", cleaned)
    cleaned = _CROSS_PROMISE_ID_RE.sub("", cleaned)
    return _NON_CONTENT_CHARS_RE.sub("", cleaned).lower()


def _norm_matches_any(norm: str, candidates: Sequence[str]) -> bool:
    """判断归一化后的约定正文是否与候选集合中的某条属于同一件事。"""
    if not norm:
        return False
    for target in candidates:
        if not target:
            continue
        if norm == target:
            return True
        if min(len(norm), len(target)) >= 4 and (norm in target or target in norm):
            return True
    return False


def _line_source_branches(line: str) -> Set[str]:
    """提取行内“（来自xxx）”标注中的分支名集合（小写）。"""
    return {match.group(1).strip().lower() for match in _SOURCE_TAG_RE.finditer(line) if match.group(1).strip()}


def _sanitize_stable_brief(
    stable_brief: str,
    *,
    branch_key: str,
    self_branch_ids: Set[int],
    other_branch_ids: Set[int],
    self_branch_norms: Sequence[str],
) -> Tuple[str, Set[int], List[str]]:
    """清洗服务端慢层简报：改写撞号标签、过滤本端同步回流约定，并收集已呈现的异端约定索引。

    执行顺序：
    1. 逐行把 `[约#N]` / `约#N` 防御性改写为 `跨端约#N`，避免跨端自增编号冒充本地承诺号；
    2. 跳过本端分支回流（来源标注为本端、编号命中本端 open_promises，或未标明他端来源且约定段内正文与本端一致）的条目；
       明确标注其他分支来源或编号属于已知异端 `open_promises` 的条目绝不误删；
    3. 若某个约定分段下的条目被全部滤空，连带移除孤立的空分段标题；
    4. 记录最终保留的跨端约定编号与正文归一化指纹，供后续 `open_promises` 段去重。
    """
    rewritten = _rewrite_cross_branch_promise_refs(stable_brief.strip())
    if not rewritten:
        return "", set(), []

    raw_lines = rewritten.splitlines()
    kept_blocks: List[Tuple[Optional[str], List[str], bool]] = []
    current_header: Optional[str] = None
    current_lines: List[str] = []
    in_promise_section = False

    def _flush_block() -> None:
        nonlocal current_header, current_lines, in_promise_section
        if current_header is not None or current_lines:
            kept_blocks.append((current_header, list(current_lines), in_promise_section))
        current_header = None
        current_lines = []
        in_promise_section = False

    for line in raw_lines:
        stripped = line.strip()
        if stripped and _SECTION_HEADER_RE.match(stripped):
            _flush_block()
            current_header = line
            in_promise_section = bool(_PROMISE_SECTION_HEADER_RE.match(stripped))
            continue

        if stripped:
            line_ids = {int(m.group(1)) for m in _CROSS_PROMISE_ID_RE.finditer(line)}
            is_bullet = bool(_BULLET_LINE_RE.match(line))
            line_sources = _line_source_branches(line)
            line_norm = _normalize_promise_body(line)
            # 若该行明确标注来自其他分支（如“（来自maibot）”），或编号属于已知异端约定，绝不因模糊子串误删
            is_explicit_other_branch = bool(
                (line_sources and branch_key not in line_sources)
                or (line_ids and (line_ids & other_branch_ids) and not (line_ids & self_branch_ids))
            )
            # 识别并剔除本端同步上去又经服务端回流的约定条目，避免与本地【还惦记的事】重复且贴错号
            is_self_echo = False
            if branch_key and not is_explicit_other_branch and (is_bullet or in_promise_section):
                if branch_key in line_sources and (in_promise_section or bool(line_ids)):
                    is_self_echo = True
                elif line_ids and (line_ids & self_branch_ids):
                    is_self_echo = True
                elif in_promise_section and _norm_matches_any(line_norm, self_branch_norms):
                    is_self_echo = True
            if is_self_echo:
                continue

        current_lines.append(line)

    _flush_block()

    output_lines: List[str] = []
    seen_anchor_ids: Set[int] = set()
    seen_promise_norms: List[str] = []

    for header, body_lines, is_prom_sec in kept_blocks:
        has_content = any(item.strip() for item in body_lines)
        if is_prom_sec and not has_content:
            # 约定段内条目全部属于本端回流并被滤掉时，不留孤立空标题
            continue
        if header is not None:
            output_lines.append(header)
        for item in body_lines:
            output_lines.append(item)
            item_ids = [int(m.group(1)) for m in _CROSS_PROMISE_ID_RE.finditer(item)]
            if item_ids:
                seen_anchor_ids.update(item_ids)
            if item.strip() and (is_prom_sec or item_ids):
                norm = _normalize_promise_body(item)
                if norm:
                    seen_promise_norms.append(norm)

    cleaned_text = "\n".join(output_lines).strip()
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
    return cleaned_text, seen_anchor_ids, seen_promise_norms


def format_brief_extension(
    *,
    stable_brief: str,
    open_promises: Sequence[CachedPromiseEntry],
    save_brief: str,
    current_branch_id: str = _DEFAULT_BRANCH_ID,
) -> str:
    """在开播签出时冻结本场跨端记忆扩展文本，确保整场不变以保护提示词前缀缓存。

    触发条件：开播签出解析服务端响应时调用。
    执行顺序：先按本端分支标识识别回流约定 -> 清洗 `stable_brief`（改写 `[约#N]` 为 `[跨端约#N]` 并剔除本端回流）
    -> 对 `open_promises` 过滤本端回流与 `stable_brief` 已涵盖的条目，仅补挂尚未出现的异端公开约定 -> 拼入存档摘要。
    """
    branch_key = (current_branch_id or "").strip().lower()
    self_branch_ids: Set[int] = {
        int(p.anchor_id)
        for p in open_promises
        if branch_key and p.anchor_id > 0 and p.source_branch.strip().lower() == branch_key
    }
    other_branch_ids: Set[int] = {
        int(p.anchor_id)
        for p in open_promises
        if branch_key and p.anchor_id > 0 and p.source_branch.strip() and p.source_branch.strip().lower() != branch_key
    }
    self_branch_norms: List[str] = [
        norm
        for p in open_promises
        if branch_key and p.source_branch.strip().lower() == branch_key
        for norm in (_normalize_promise_body(p.content),)
        if norm
    ]

    sections: List[str] = []
    cleaned_brief, seen_anchor_ids, seen_promise_norms = _sanitize_stable_brief(
        stable_brief,
        branch_key=branch_key,
        self_branch_ids=self_branch_ids,
        other_branch_ids=other_branch_ids,
        self_branch_norms=self_branch_norms,
    )
    if cleaned_brief:
        sections.append(cleaned_brief)

    # 仅保留真正来自其他分支、且尚未在 stable_brief 中出现过的未了结公开约定，防止双重重复注入
    extra_promises: List[CachedPromiseEntry] = []
    for prom in open_promises:
        if prom.status != "open":
            continue
        content = prom.content.strip()
        if not content:
            continue
        src_branch = prom.source_branch.strip().lower()
        if branch_key and src_branch == branch_key:
            continue
        if prom.anchor_id > 0 and int(prom.anchor_id) in seen_anchor_ids:
            continue
        norm = _normalize_promise_body(content)
        if _norm_matches_any(norm, seen_promise_norms):
            continue
        if prom.anchor_id > 0:
            seen_anchor_ids.add(int(prom.anchor_id))
        if norm:
            seen_promise_norms.append(norm)
        extra_promises.append(prom)

    if extra_promises:
        lines = ["【跨端还惦记的公开约定】"]
        for prom in extra_promises:
            branch = prom.source_branch.strip() or "其他分支"
            lines.append(f"- 跨端约#{prom.anchor_id}（来自{branch}）：{prom.content.strip()}")
        sections.append("\n".join(lines))

    cleaned_save = _rewrite_cross_branch_promise_refs(save_brief.strip())
    if cleaned_save:
        sections.append(f"【存档跨端变更摘要】\n{cleaned_save}")
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
        fact_text = format_interaction_fact(name, kind, getattr(inter, "fact", None))
        results.append(
            {
                "platform": str(getattr(player, "game", "") or "minecraft"),
                "external_id": str(getattr(player, "uuid", "") or ""),
                "display_name": name,
                "summary": fact_text[:1000],
                "kind": kind[:64],
                "fact": fact_text[:1000],
                "visibility": "public",
            }
        )
    return results


async def extract_game_events(
    event_repo: Any,
    sid: int,
    *,
    fallback_save: Optional[str] = None,
    limit: int = 120,
) -> List[Dict[str, Any]]:
    """提取本场游戏进展与里程碑事件写入 L0 原件书库。"""
    if event_repo is None:
        return []
    list_fn = getattr(event_repo, "list_session_game_events", None)
    if not callable(list_fn):
        return []
    try:
        rows = await list_fn(live_session_id=sid, limit=limit)
    except Exception as exc:  # noqa: BLE001 - 单源失败不阻断签入
        logger.warning(f"读取场次 {sid} 游戏事件失败（签入跳过游戏原件）: {exc}", exc=exc)
        return []
    items: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows or ()):
        etype = str(getattr(row, "event_type", "") or "").strip().lower()
        if etype in _NOISY_GAME_EVENT_TYPES:
            continue
        msg = _clean_compact(getattr(row, "message", ""), max_len=800)
        if not msg:
            continue
        game = str(getattr(row, "game", "") or "game").strip()
        ts = int(getattr(row, "timestamp_ms", 0) or 0)
        save_id = str(getattr(row, "save_id", "") or "").strip() or fallback_save
        items.append(
            {
                "idempotency_key": f"checkin-ge-{sid}-{ts}-{idx}",
                "record_type": "game_event",
                "actor_role": "self",
                "visibility": "public",
                "occurred_at_ms": ts,
                "save_id": save_id,
                "title": f"{game}:{etype or 'event'}"[:120],
                "content": msg,
                "verbatim": "",
                "metadata": {"game": game, "event_type": etype},
            }
        )
    return items


async def extract_key_speeches(
    chat_repo: Any,
    sid: int,
    *,
    viewer_repo: Any = None,
    fallback_save: Optional[str] = None,
    min_chars: int = 8,
    max_items: int = 150,
) -> List[Dict[str, Any]]:
    """提取本场已播出的主播高信噪比台词（剔除未播出 dropped 行并遮去观众昵称）写入 L0。"""
    if chat_repo is None:
        return []
    list_fn = getattr(chat_repo, "list_session_speech_for_settle", None)
    if not callable(list_fn):
        return []
    try:
        rows = await list_fn(live_session_id=sid)
    except Exception as exc:  # noqa: BLE001 - 单源失败不阻断签入
        logger.warning(f"读取场次 {sid} 主播台词失败（签入跳过台词原件）: {exc}", exc=exc)
        return []

    known_names: List[str] = []
    names_fn = getattr(viewer_repo, "list_known_names", None) if viewer_repo is not None else None
    if callable(names_fn):
        try:
            known_names = [str(n) for n in (await names_fn() or ()) if n]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取已知观众昵称失败（继续按原台词脱敏）: {exc}", exc=exc)

    items: List[Dict[str, Any]] = []
    for row in rows or ():
        if len(items) >= max_items:
            break
        if getattr(row, "dropped_at_ms", None) is not None:
            continue
        raw_text = _clean_compact(getattr(row, "content", ""), max_len=600)
        if len(raw_text) < min_chars:
            continue
        masked = mask_names_in_text(raw_text, known_names, _CROSS_SESSION_MASK_LABEL)
        chat_id = int(getattr(row, "id", 0) or 0)
        ts = int(getattr(row, "timestamp_ms", 0) or 0)
        items.append(
            {
                "idempotency_key": f"checkin-speech-{sid}-{chat_id or len(items)}",
                "record_type": "speech",
                "actor_role": "self",
                "visibility": "public",
                "occurred_at_ms": ts,
                "save_id": fallback_save,
                "title": "主播原话",
                "content": masked,
                "verbatim": masked,
                "metadata": {},
            }
        )
    return items


async def extract_work_docs(
    work_docs_repo: Any,
    sid: int,
    *,
    save_id: Optional[str],
    game: str = "minecraft",
    occurred_at_ms: int = 0,
) -> List[Dict[str, Any]]:
    """提取当前游戏存档的工作笔记、未完待办与钉住规矩作为 L0 存档原件。"""
    if work_docs_repo is None or not save_id:
        return []
    get_fn = getattr(work_docs_repo, "get_work_docs", None)
    if not callable(get_fn):
        return []
    try:
        doc = await get_fn(game=game, save_id=save_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取存档 {save_id} 工作笔记失败（跳过）: {exc}", exc=exc)
        return []
    if doc is None:
        return []
    parts: List[str] = []
    notebook = str(getattr(doc, "notebook", "") or "").strip()
    if notebook:
        parts.append(notebook[:2200])
    todos = getattr(doc, "todos", None) or ()
    open_todos = [
        str(t.get("content") or "").strip()
        for t in todos
        if isinstance(t, dict)
        and str(t.get("status") or "pending").lower() not in ("completed", "done", "cancelled", "canceled")
        and str(t.get("content") or "").strip()
    ]
    if open_todos:
        parts.append("待办：" + "；".join(open_todos[:12]))
    rules = [str(r).strip() for r in (getattr(doc, "pinned_rules", None) or ()) if isinstance(r, str) and r.strip()]
    if rules:
        parts.append("规矩：" + "；".join(rules[:8]))
    body = "\n".join(parts)[:3500]
    if not body:
        return []
    save_name = _clean_compact(getattr(doc, "save_name", ""), max_len=80) or save_id
    ts = int(getattr(doc, "updated_at_ms", 0) or occurred_at_ms)
    return [
        {
            "idempotency_key": f"checkin-workdoc-{sid}-{save_id}-{ts}",
            "record_type": "work_doc",
            "actor_role": "self",
            "visibility": "public",
            "occurred_at_ms": ts,
            "save_id": save_id,
            "title": f"存档工作笔记（{save_name}）"[:200],
            "content": body,
            "verbatim": "",
            "metadata": {"game": game},
        }
    ]


async def extract_social_facts(
    social_fact_repo: Any,
    *,
    sid: int,
    save_id: Optional[str] = None,
    limit: int = 80,
) -> List[Dict[str, Any]]:
    """提取当前游戏环境现行社交事实，映射为 MaiGO L2 实体关系事实边。"""
    if social_fact_repo is None:
        return []
    list_fn = getattr(social_fact_repo, "list_active_facts", None)
    if not callable(list_fn):
        return []
    try:
        rows = await list_fn(save_id=save_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取场次 {sid} 游戏社交事实失败（跳过）: {exc}", exc=exc)
        return []
    items: List[Dict[str, Any]] = []
    for fact in rows or ():
        text = _clean_compact(getattr(fact, "text", ""), max_len=800)
        if not text:
            continue
        fid = int(getattr(fact, "id", 0) or 0)
        game = str(getattr(fact, "game", "") or "minecraft").strip()
        f_save = str(getattr(fact, "save_id", "") or "").strip() or save_id
        save_name = _clean_compact(getattr(fact, "save_name", ""), max_len=80) or f_save or game
        fkind = str(getattr(fact, "kind", "") or "social_fact").strip()
        quote = _clean_compact(getattr(fact, "quote", ""), max_len=600)
        upd_ms = int(getattr(fact, "updated_at_ms", 0) or getattr(fact, "created_at_ms", 0) or 0)
        items.append(
            {
                "idempotency_key": f"amaidesu-sf-{fid}-{upd_ms}",
                "subject": {
                    "entity_type": "project" if f_save else "concept",
                    "canonical_key": f"{game}:{f_save or 'global'}",
                    "name": save_name,
                    "save_id": f_save,
                    "visibility": "public",
                    "seen_at_ms": upd_ms,
                },
                "predicate": fkind,
                "object_text": fkind,
                "fact_text": text,
                "quote": quote,
                "visibility": "public",
                "save_id": f_save,
                "external_episode_id": str(sid),
                "occurred_at_ms": upd_ms,
            }
        )
    return items


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
