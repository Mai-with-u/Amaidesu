"""下播整理：把一场正式直播整理成承诺账本与场次档案。

触发与调度
----------
live.ended 之后、道别句播完才排进后台，一次整理待整理的场次；进程启动时也调一次，
补上上次没整理完的。只挑已结束、settled_at_ms 为空、自动重试次数没用完的正式场，按
开播时刻从早到晚逐场整理。彩排场永远不在候选里。

一场失败不挡后面的场：任何一次调用失败、输出读不懂或输入超出模型窗口（调用本身报错），
这一场整场不提交，重试次数加一并把原因写进整理报告；次数用满后不再自动补跑，等运营者
在 WebUI 点「重新整理」把次数清零。一场一个事务——半场写进去再失败，重跑时会重复登记，
所以校验全部通过才落进 apply_settlement。

三次调用各只做一种判断
----------------------
1. 场次档案：她的全部播出台词加游戏里的事，挑值得下次开播提起的至多 8 行；
2. 找新承诺：她的全部台词（附被回应的弹幕）加她能做的事，找出当众答应过、还没兑现的事；
3. 了结旧承诺：只在本场之前有未了结承诺时才调，判她说没说明白做完了或不做了。

校验全部是代码：逐字引文（原话必须是所引那一行文本的逐字子串且不超过上限）、所属游戏
（必须是本场玩过的）、对谁（对具体观众时那一行必须回应了真实观众的弹幕）、不重复（与
已有承诺同一件事、或句子里已经提到某条承诺，都不新建）、只改未了结的承诺（运营者已判
误记或已了结的不受她的话影响）。校验没通过的条目连同原话写进整理报告，由运营者判断
要不要在 WebUI 手建。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.memory import (
    Commitment,
    CommitmentClosure,
    PendingArchiveLine,
    PendingCommitment,
    SettleReport,
    UnregisteredItem,
)
from src.modules.storage.models.sessions import LiveSession
from src.modules.types.session_kind import SESSION_KIND_LIVE
from src.modules.utils.time_utils import now_ms

from ..background import SUMMARY_PROFILE

if TYPE_CHECKING:
    from src.modules.storage.repos.chat import ChatRepo
    from src.modules.storage.repos.commitments import CommitmentRepo
    from src.modules.storage.repos.events import EventRepo
    from src.modules.storage.repos.sessions import SessionRepo

__all__ = ["MAX_ATTEMPTS", "SessionSettler", "SettleError", "parse_json_object"]

logger = get_logger("SessionSettler")

# 三份系统提示词（正文见 src/agents/streamer/prompts/）
_ARCHIVE_TEMPLATE = "memory_archive_system"
_COMMITMENT_TEMPLATE = "memory_commitment_system"
_CLOSE_TEMPLATE = "memory_commitment_close_system"

# 自动重试上限：用满后不再补跑，等运营者在 WebUI 点「重新整理」
MAX_ATTEMPTS = 3
# 一次调度最多整理几场（启动补跑时可能有积压）
MAX_SESSIONS_PER_RUN = 4
# 一次整理最多写几行档案（模型多给了就只取前几行）
MAX_ARCHIVE_LINES = 8
# 逐字引文与标题的上限（超出的引文整条丢弃，超出的标题截断）
MAX_QUOTE_CHARS = 80
MAX_TITLE_CHARS = 30
# 交给「找新承诺」的旧承诺标题上限：未了结的全给，判误记的只给最近的这些条
MAX_VOID_TITLES = 40
# 她能做的事里，不需要额外能力就能做的几件（游戏 Agent 名单由外面传进来）
_BASE_ABILITIES = ("聊天", "讲故事", "陪观众说话")

# 说话人代号前缀；本场内按首次出现顺序编号，同一观众整场用同一个代号
_VIEWER_CODE_PREFIX = "观众"


class SettleError(Exception):
    """这一场整理不下去（模型调用失败、输出读不懂、取料失败）。"""


@dataclass(frozen=True)
class SettleMaterial:
    """一次整理要交给模型的三份原料，全部来自库表。"""

    live_session_id: int
    speech_text: str
    lines_by_number: Dict[int, SettleSpeechLine]
    games: List[str]
    games_text: str
    abilities_text: str
    open_commitments: List[Commitment]
    void_titles: List[Tuple[int, str]]
    open_text: str
    void_text: str


@dataclass(frozen=True)
class SettleOutcome:
    """一场整理的结果：要写进去的行、没能登记的条目。"""

    archive_lines: List[PendingArchiveLine] = field(default_factory=list)
    commitments: List[PendingCommitment] = field(default_factory=list)
    closures: List[CommitmentClosure] = field(default_factory=list)
    unregistered: List[UnregisteredItem] = field(default_factory=list)


def parse_json_object(text: str) -> Dict[str, Any]:
    """从模型输出里取出 JSON 对象；取不出抛 SettleError。

    只按第一个左花括号到最后一个右花括号截取：模型常见地加一句开场白、或者把 JSON
    包在代码块里，这两种都能救回来；截出来的不是合法对象就算这一场输出读不懂。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise SettleError("模型返回空内容")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise SettleError("模型输出里没有 JSON 对象")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SettleError(f"模型输出的 JSON 读不懂：{exc}") from exc
    if not isinstance(data, dict):
        raise SettleError("模型输出的不是 JSON 对象")
    return data


def _clip(text: str, limit: int) -> str:
    """按字数截断（标题用；引文不截断，截过的引文已经不是逐字引文）。"""
    cleaned = (text or "").strip()
    return cleaned[:limit] if len(cleaned) > limit else cleaned


def _clock_text(timestamp_ms: int) -> str:
    """毫秒时刻 → 本场内的 HH:MM（原料里只需要相对先后）。"""
    return datetime.fromtimestamp(max(0, int(timestamp_ms)) / 1000).strftime("%H:%M")


def _as_line_number(raw: Any) -> Optional[int]:
    """模型给的台词编号：整数或纯数字字符串才认，其余当没给。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _memory_ref_numbers(memory_refs: Optional[str]) -> Set[int]:
    """这句用过的记忆编号里的承诺号（memory_refs 形如 ["约#7"]；取不出就空集）。"""
    if not memory_refs:
        return set()
    try:
        refs = json.loads(memory_refs)
    except json.JSONDecodeError:
        return set()
    if not isinstance(refs, list):
        return set()
    numbers: Set[int] = set()
    for ref in refs:
        if not isinstance(ref, str):
            continue
        head, _sep, tail = ref.partition("#")
        if head.strip().startswith("约") and tail.strip().isdigit():
            numbers.add(int(tail.strip()))
    return numbers


class SessionSettler:
    """下播整理的执行者：取料 → 三次调用 → 代码校验 → 一个事务写入。"""

    def __init__(
        self,
        *,
        sessions_repo: "SessionRepo",
        chat_repo: "ChatRepo",
        commitments_repo: "CommitmentRepo",
        llm_service: Any,
        prompt_manager: PromptManager,
        event_repo: Optional["EventRepo"] = None,
        game_names_provider: Optional[Callable[[], Sequence[str]]] = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        """仓储、模型与提示词由主播 Agent 构造时注入。

        event_repo 缺失时原料少游戏这一类，整理照常跑；game_names_provider 返回当前
        对主播可见的游戏 Agent 名，用来写「她能做的事」。
        """
        self._sessions = sessions_repo
        self._chat = chat_repo
        self._commitments = commitments_repo
        self._events = event_repo
        self._llm = llm_service
        self._prompt_manager = prompt_manager
        self._game_names_provider = game_names_provider
        self._clock = clock

    # ------------------------------------------------------------------
    # 调度
    # ------------------------------------------------------------------

    async def settle_pending(self) -> int:
        """整理待整理的场次（最多几场），返回成功的场数；单场失败只记录不抛出。"""
        try:
            pending = await self._sessions.list_sessions_pending_settle(
                limit=MAX_SESSIONS_PER_RUN, max_attempts=MAX_ATTEMPTS
            )
        except Exception as exc:  # noqa: BLE001 - 取待办失败不能挡住调用方（下播回调 / 启动）
            logger.warning(f"查询待整理的场次失败，本次不整理: {exc}", exc=exc)
            return 0
        settled = 0
        for session in pending:
            if await self.settle_session(int(session.id)):
                settled += 1
        if pending:
            logger.info(f"下播整理：待整理 {len(pending)} 场，成功 {settled} 场")
        return settled

    async def settle_session(self, live_session_id: int) -> bool:
        """整理一场：成功返回 True；失败或跳过时把原因写进整理报告并返回 False。"""
        session = await self._sessions.get_live_session(live_session_id=int(live_session_id))
        if session is None:
            logger.debug(f"场次 {live_session_id} 不存在，跳过整理")
            return False
        if session.kind != SESSION_KIND_LIVE:
            logger.debug(f"场次 {live_session_id} 不是正式场（{session.kind}），不整理")
            return False
        if session.ended_at_ms is None:
            logger.debug(f"场次 {live_session_id} 还没结束，不整理")
            return False

        try:
            speech = await self._chat.list_session_speech_for_settle(live_session_id=int(live_session_id))
        except Exception as exc:  # noqa: BLE001 - 取料失败按整理失败记账
            await self._record_failure(session, f"取本场台词失败：{exc}")
            return False
        delivered = [line for line in speech if line.dropped_at_ms is None]
        if not delivered:
            await self._mark_done(session, note="本场没有播出台词，不用整理")
            return True

        try:
            material = await self._collect_material(session, delivered)
            outcome = await self._extract(material)
        except SettleError as exc:
            await self._record_failure(session, str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - 取料或模型调用的意外异常同样按失败记账
            logger.warning(f"场次 {live_session_id} 整理异常: {exc}", exc=exc)
            await self._record_failure(session, f"整理异常：{exc}")
            return False

        report = SettleReport(unregistered=outcome.unregistered)
        now = int(self._clock())
        try:
            written = await self._sessions.apply_settlement(
                live_session_id=int(live_session_id),
                archive_lines=outcome.archive_lines,
                commitments=outcome.commitments,
                closures=outcome.closures,
                settled_at_ms=now,
                now_ms_value=now,
                settle_report=report.model_dump_json() if outcome.unregistered else None,
            )
        except Exception as exc:  # noqa: BLE001 - 写入失败按整理失败记账，事务已回滚
            logger.warning(f"场次 {live_session_id} 整理写入失败: {exc}", exc=exc)
            await self._record_failure(session, f"写入整理结果失败：{exc}")
            return False

        logger.info(
            f"场次 {live_session_id} 整理完成：档案 {written.get('archive', 0)} 行、"
            f"新承诺 {written.get('commitments', 0)} 条、了结 {written.get('closed', 0)} 条、"
            f"未登记 {len(outcome.unregistered)} 条"
        )
        return True

    async def _mark_done(self, session: LiveSession, *, note: str) -> None:
        """直接标成已整理（没有播出台词、不用调模型）。"""
        try:
            await self._sessions.mark_settle_done(
                live_session_id=int(session.id),
                settled_at_ms=int(self._clock()),
                settle_report=None,
            )
            logger.info(f"场次 {session.id} {note}")
        except Exception as exc:  # noqa: BLE001 - 落库失败下次调度会重来
            logger.warning(f"场次 {session.id} 标记整理完成失败: {exc}", exc=exc)

    async def _record_failure(self, session: LiveSession, reason: str) -> None:
        """记一次失败：重试次数加一、写原因；次数用满后只告警不再自动补跑。"""
        report = SettleReport(failure_reason=reason)
        try:
            attempts = await self._sessions.record_settle_failure(
                live_session_id=int(session.id),
                settle_report=report.model_dump_json(),
            )
        except Exception as exc:  # noqa: BLE001 - 记账失败也不该让调用方崩掉
            logger.warning(f"场次 {session.id} 记录整理失败原因时出错: {exc}", exc=exc)
            return
        if attempts >= MAX_ATTEMPTS:
            logger.warning(
                f"场次 {session.id} 整理失败（第 {attempts} 次，已达上限，不再自动补跑）：{reason}。"
                "修好模型或提示词后可在 WebUI 点「重新整理」"
            )
        else:
            logger.warning(f"场次 {session.id} 整理失败（第 {attempts} 次，下次调度再试）：{reason}")

    # ------------------------------------------------------------------
    # 取料
    # ------------------------------------------------------------------

    async def _collect_material(self, session: LiveSession, delivered: List[SettleSpeechLine]) -> SettleMaterial:
        """把一场的原料全取出来：她说的话、游戏、她能做的事、旧承诺。"""
        speech_text, lines_by_number = self._render_speech(delivered)
        games, games_text = await self._render_games(session)
        abilities_text = self._render_abilities()
        open_commitments, open_text, void_titles, void_text = await self._load_commitments(session)
        return SettleMaterial(
            live_session_id=int(session.id),
            speech_text=speech_text,
            lines_by_number=lines_by_number,
            games=games,
            games_text=games_text,
            abilities_text=abilities_text,
            open_commitments=open_commitments,
            void_titles=void_titles,
            open_text=open_text,
            void_text=void_text,
        )

    def _render_speech(self, delivered: List[SettleSpeechLine]) -> Tuple[str, Dict[int, SettleSpeechLine]]:
        """台词渲染成带编号的行；回应弹幕的把那句原文与发言人代号附在前面。

        编号是本场内的序号，模型的输出按它指回具体那一行；同一观众整场用同一个代号，
        模特才能说清「对谁答应的」而不用昵称。
        """
        codes: Dict[int, str] = {}
        lines: List[str] = []
        lines_by_number: Dict[int, SettleSpeechLine] = {}
        for number, line in enumerate(delivered, start=1):
            lines_by_number[number] = line
            content = " ".join((line.content or "").split())
            prefix = f"#{number} {_clock_text(int(line.timestamp_ms))} 你"
            reply_note = ""
            reply_viewer_id = line.reply_viewer_id
            if line.reply_content and reply_viewer_id is not None:
                code = codes.setdefault(int(reply_viewer_id), f"{_VIEWER_CODE_PREFIX}{len(codes) + 1}")
                reply_text = " ".join((line.reply_content or "").split())
                reply_note = f"（回复{code}「{reply_text}」）"
            memory_note = ""
            used = sorted(_memory_ref_numbers(line.memory_refs))
            if used:
                memory_note = "（提到" + "、".join(f"约#{item}" for item in used) + "）"
            lines.append(f"{prefix}{reply_note}：{content}{memory_note}")
        return "\n".join(lines), lines_by_number

    async def _render_games(self, session: LiveSession) -> Tuple[List[str], str]:
        """本场玩过的游戏名与游戏里的关键事件；没有游戏事件时两者都空。"""
        if self._events is None:
            return [], ""
        try:
            records = await self._events.list_session_game_events(live_session_id=int(session.id))
        except Exception as exc:  # noqa: BLE001 - 少一类原料不影响整理
            logger.warning(f"场次 {session.id} 读游戏事件失败，原料少游戏这一类: {exc}", exc=exc)
            return [], ""
        games: List[str] = []
        for record in records:
            game = (getattr(record, "game", "") or "").strip()
            if game and game not in games:
                games.append(game)
        if not games:
            return [], ""
        game_lines = [f"#{index} {name}" for index, name in enumerate(games, start=1)]
        event_lines: List[str] = []
        for record in records:
            message = " ".join((getattr(record, "message", "") or "").split())
            if not message:
                continue
            event_lines.append(f"- [{getattr(record, 'game', '')}] {message}")
        text = "【本场玩过的游戏】\n" + "\n".join(game_lines)
        if event_lines:
            text += "\n\n【游戏里的关键事件】\n" + "\n".join(event_lines)
        return games, text

    def _render_abilities(self) -> str:
        """她能做的事：启用的游戏 Agent 名单，加上不需要额外能力就能做的事。"""
        names: List[str] = []
        if self._game_names_provider is not None:
            try:
                names = [str(name) for name in self._game_names_provider() if str(name).strip()]
            except Exception as exc:  # noqa: BLE001 - 拿不到名单只少写几个名字
                logger.warning(f"取游戏 Agent 名单失败: {exc}", exc=exc)
        abilities = list(dict.fromkeys([*names, *_BASE_ABILITIES]))
        return "、".join(abilities)

    async def _load_commitments(self, session: LiveSession) -> Tuple[List[Commitment], str, List[Tuple[int, str]], str]:
        """未了结的旧承诺（本场开播之前登记的）与判误记的旧承诺标题。"""
        started_at_ms = int(session.started_at_ms or 0)
        try:
            open_commitments = await self._commitments.list_open_before(before_ms=started_at_ms)
        except Exception as exc:  # noqa: BLE001 - 取不到就当没有旧承诺，整理照常跑
            logger.warning(f"场次 {session.id} 读未了结承诺失败: {exc}", exc=exc)
            open_commitments = []
        try:
            void_rows = await self._commitments.list_commitments(statuses=["void"], limit=MAX_VOID_TITLES)
        except Exception as exc:  # noqa: BLE001 - 少一类原料不影响整理
            logger.warning(f"场次 {session.id} 读误记承诺失败: {exc}", exc=exc)
            void_rows = []
        void_titles = [(int(row.id), row.what) for row in reversed(void_rows)]
        open_lines = [f"#{row.id} 「{row.what}」（当初的原话「{row.quote or '无'}」）" for row in open_commitments]
        void_lines = [f"#{commitment_id} 「{what}」" for commitment_id, what in void_titles]
        return open_commitments, "\n".join(open_lines), void_titles, "\n".join(void_lines)

    # ------------------------------------------------------------------
    # 三次模型调用
    # ------------------------------------------------------------------

    async def _extract(self, material: SettleMaterial) -> SettleOutcome:
        """跑三次调用并逐条校验；调用失败抛 SettleError（整场不提交）。"""
        archive_rows = await self._ask_for_list(
            template=_ARCHIVE_TEMPLATE,
            prompt=(
                f"【本场她说的话】\n{material.speech_text}\n\n"
                f"{material.games_text or '【本场没有玩游戏】'}\n\n"
                "请写出这一场的档案。"
            ),
            key="archive",
        )
        commitment_rows = await self._ask_for_list(
            template=_COMMITMENT_TEMPLATE,
            prompt=(
                f"【她能做的事】\n{material.abilities_text}\n\n"
                f"{material.games_text or '【本场没有玩游戏】'}\n\n"
                f"【还没了结的承诺】\n{material.open_text or '没有'}\n\n"
                f"【已判误记的承诺（这些话不是承诺）】\n{material.void_text or '没有'}\n\n"
                f"【本场她说的话】\n{material.speech_text}\n\n"
                "请找出这一场她新答应的、还没兑现的事。"
            ),
            key="commitments",
        )
        closure_rows: List[Any] = []
        if material.open_commitments:
            closure_rows = await self._ask_for_list(
                template=_CLOSE_TEMPLATE,
                prompt=(
                    f"【还没了结的承诺】\n{material.open_text}\n\n"
                    f"【本场她说的话】\n{material.speech_text}\n\n"
                    "请判断这些承诺里有哪些在这一场被她明确说做完了、或明确说不做了。"
                ),
                key="closed",
            )
        archive_lines, archive_misses = self._validate_archive(archive_rows, material)
        commitments, commitment_misses = self._validate_commitments(commitment_rows, material)
        closures, closure_misses = self._validate_closures(closure_rows, material)
        return SettleOutcome(
            archive_lines=archive_lines,
            commitments=commitments,
            closures=closures,
            unregistered=[*archive_misses, *commitment_misses, *closure_misses],
        )

    async def _ask_for_list(self, *, template: str, prompt: str, key: str) -> List[Any]:
        """一次调用取一个列表字段；字段缺失当空列表，不是列表就按输出读不懂处理。"""
        data = await self._ask(template=template, prompt=prompt)
        rows = data.get(key)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise SettleError(f"模型输出的 {key} 不是列表")
        return rows

    async def _ask(self, *, template: str, prompt: str) -> Dict[str, Any]:
        """渲染系统提示词并调一次模型，返回解析出的 JSON 对象。"""
        system = self._prompt_manager.render(template)
        try:
            response = await self._llm.generate(prompt, profile=SUMMARY_PROFILE, system=system)
        except Exception as exc:  # noqa: BLE001 - 调用异常统一转成整理失败原因
            raise SettleError(f"模型调用失败：{exc}") from exc
        content = getattr(response, "content", None)
        if not getattr(response, "ok", False) or not content:
            raise SettleError("模型调用失败或返回空内容")
        return parse_json_object(str(content))

    # ------------------------------------------------------------------
    # 校验（全部是代码）
    # ------------------------------------------------------------------

    def _verified_quote(self, line: SettleSpeechLine, quote: Any) -> Optional[str]:
        """逐字引文校验：原话必须是那一行文本的逐字子串、且不超过上限，否则返回 None。"""
        text = str(quote or "").strip()
        if not text or len(text) > MAX_QUOTE_CHARS:
            return None
        return text if text in (line.content or "") else None

    def _line_of(self, raw: Any, material: SettleMaterial) -> Optional[SettleSpeechLine]:
        """模型给的行号换成台词行；行号缺失或不是本场她的台词时返回 None。"""
        number = _as_line_number(raw)
        if number is None:
            return None
        return material.lines_by_number.get(number)

    def _validate_archive(
        self, rows: List[Any], material: SettleMaterial
    ) -> Tuple[List[PendingArchiveLine], List[UnregisteredItem]]:
        """场次档案的校验：逐字引文 + 至多 8 行；没通过的连同原话进未登记清单。"""
        written: List[PendingArchiveLine] = []
        missed: List[UnregisteredItem] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            if len(written) >= MAX_ARCHIVE_LINES:
                break
            title = _clip(str(raw.get("title") or ""), MAX_TITLE_CHARS)
            quote = str(raw.get("quote") or "").strip()
            line = self._line_of(raw.get("line"), material)
            if line is None:
                missed.append(
                    UnregisteredItem(kind="archive", what=title, quote=quote, reason="引用的不是本场她的台词")
                )
                continue
            if not title:
                missed.append(UnregisteredItem(kind="archive", quote=quote, reason="没有标题"))
                continue
            verified = self._verified_quote(line, quote)
            if verified is None:
                missed.append(
                    UnregisteredItem(
                        kind="archive",
                        what=title,
                        quote=quote,
                        reason=f"原话不是第 {raw.get('line')} 行的逐字片段（或超过 {MAX_QUOTE_CHARS} 字）",
                    )
                )
                continue
            written.append(PendingArchiveLine(text=title, quote=verified, evidence_chat_id=int(line.id)))
        return written, missed

    def _validate_commitments(
        self, rows: List[Any], material: SettleMaterial
    ) -> Tuple[List[PendingCommitment], List[UnregisteredItem]]:
        """新承诺的校验：逐字引文、所属游戏、对谁、不重复。"""
        written: List[PendingCommitment] = []
        missed: List[UnregisteredItem] = []
        open_ids = {int(row.id) for row in material.open_commitments}
        void_ids = {commitment_id for commitment_id, _what in material.void_titles}
        used_lines: Set[int] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            what = _clip(str(raw.get("what") or ""), MAX_TITLE_CHARS)
            quote = str(raw.get("quote") or "").strip()
            line = self._line_of(raw.get("line"), material)
            if line is None:
                missed.append(
                    UnregisteredItem(kind="commitment", what=what, quote=quote, reason="引用的不是本场她的台词")
                )
                continue
            if not what:
                missed.append(UnregisteredItem(kind="commitment", quote=quote, reason="没有动作标题"))
                continue
            same_as = _as_line_number(raw.get("same_as"))
            if same_as is not None and same_as in open_ids:
                logger.info(f"场次 {material.live_session_id}：模型认出这是又说了一遍 #{same_as}，不新建")
                continue
            if same_as is not None and same_as in void_ids:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"与已判误记的 #{same_as} 相近，未登记",
                    )
                )
                continue
            if _memory_ref_numbers(line.memory_refs):
                logger.info(f"场次 {material.live_session_id}：第 {raw.get('line')} 行已经提到某条旧承诺，不新建")
                continue
            if int(line.id) in used_lines:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason="同一句台词只登记一条承诺",
                    )
                )
                continue
            verified = self._verified_quote(line, quote)
            if verified is None:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"原话不是第 {raw.get('line')} 行的逐字片段（或超过 {MAX_QUOTE_CHARS} 字）",
                    )
                )
                continue
            target = str(raw.get("to") or "").strip().lower()
            viewer_id: Optional[int] = None
            if target in ("person", "viewer", "观众"):
                reply_viewer_id = line.reply_viewer_id
                if reply_viewer_id is None:
                    missed.append(
                        UnregisteredItem(
                            kind="commitment",
                            what=what,
                            quote=quote,
                            reason="说的是对某位观众答应，但这一行没有回应任何弹幕",
                        )
                    )
                    continue
                if bool(line.reply_simulated):
                    missed.append(
                        UnregisteredItem(
                            kind="commitment",
                            what=what,
                            quote=quote,
                            reason="回应的是模拟观众的弹幕，不当成对真实观众的承诺",
                        )
                    )
                    continue
                viewer_id = int(reply_viewer_id)
            game = raw.get("game")
            game_name = str(game).strip() if isinstance(game, str) and game.strip() else None
            if game_name is not None and game_name not in material.games:
                game_name = None
            written.append(
                PendingCommitment(
                    what=what,
                    quote=verified,
                    evidence_chat_id=int(line.id),
                    viewer_id=viewer_id,
                    game=game_name,
                )
            )
            used_lines.add(int(line.id))
        return written, missed

    def _validate_closures(
        self, rows: List[Any], material: SettleMaterial
    ) -> Tuple[List[CommitmentClosure], List[UnregisteredItem]]:
        """了结的校验：只能了结本场开播之前就未了结的承诺，且引文逐字。"""
        written: List[CommitmentClosure] = []
        missed: List[UnregisteredItem] = []
        open_ids = {int(row.id) for row in material.open_commitments}
        closed_ids: Set[int] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            commitment_id = _as_line_number(raw.get("id"))
            quote = str(raw.get("quote") or "").strip()
            if commitment_id is None or commitment_id not in open_ids:
                logger.debug(f"场次 {material.live_session_id}：模型了结的 #{commitment_id} 不是未了结的旧承诺，跳过")
                continue
            if commitment_id in closed_ids:
                continue
            line = self._line_of(raw.get("line"), material)
            if line is None:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=f"了结 #{commitment_id}",
                        quote=quote,
                        reason="引用的不是本场她的台词",
                    )
                )
                continue
            verified = self._verified_quote(line, quote)
            if verified is None:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=f"了结 #{commitment_id}",
                        quote=quote,
                        reason="原话不是所引那一行的逐字片段",
                    )
                )
                continue
            written.append(
                CommitmentClosure(commitment_id=int(commitment_id), close_chat_id=int(line.id), close_quote=verified)
            )
            closed_ids.add(int(commitment_id))
        return written, missed
