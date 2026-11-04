"""下播整理「找新承诺」这一步的离线评测器。

dry（默认）不调模型：用生产 PromptManager 渲染三份系统提示词，按用例构造与生产
同构的「找新承诺」用户提示词，再用生产校验器把 expect 反推成理想输出跑一遍，自检
样本是否自洽；同时用理想输出跑一遍指标管线，确认样本、校验器与指标口径互相一致。

live（--live）真调模型：把同一份生产提示词发给模型，输出经生产校验器清洗后按三项
阈值判定 PASS/FAIL，逐条差异写进 --out 指定的 JSON 报告。

复用边界
--------
评测器不复制生产的取料、渲染与校验规则，而是直接驱动 SessionSettler：

- 三份系统提示词由生产 PromptManager 按 src/**/prompts/ 约定加载并渲染；
- 每场用例的台词、游戏、能力、旧承诺先转成生产取料使用的 SettleSpeechLine /
  Commitment，再交给 SessionSettler 的取料与校验方法，生成的用户提示词与生产
  「找新承诺」那次调用同构；
- 模型输出（或 dry 模式由 expect 反推的理想输出）经生产校验器转成
  PendingCommitment，再由 metrics 计算指标。

脚本不装配仓储容器与 LLM 引擎，库存数据用最小桩回放；桩只覆盖渲染与校验路径，
一旦生产实现开始访问未实现的接口会直接报错，避免评测悄悄失真。

用法与口径见同目录 README.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as clock_time
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, Sequence, cast

# 独立脚本由 uv 从任意目录启动，sys.path[0] 是脚本目录而不是仓库根；先把仓库根
# 插进去，下面的 import 必然不在文件顶部，逐行豁免 E402。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI  # noqa: E402
from src.agents.streamer.memory.settle import (  # noqa: E402
    MAX_QUOTE_CHARS,
    MAX_TITLE_CHARS,
    SessionSettler,
    SettleMaterial,
    parse_json_object,
)
from src.modules.llm.payload import Response  # noqa: E402
from src.modules.logging import configure_from_config  # noqa: E402
from src.modules.prompts import PromptManager  # noqa: E402
from src.modules.storage.models.chat import SettleSpeechLine  # noqa: E402
from src.modules.storage.models.memory import (  # noqa: E402
    Commitment,
    PendingCommitment,
    UnregisteredItem,
)

import metrics as eval_metrics  # noqa: E402

DATASET_DIR = Path(__file__).resolve().parent / "datasets"
DATASET_NAMES = ("synthetic", "holdout", "hard_negative", "red_team")
MIN_CASES = {"synthetic": 20, "holdout": 10, "hard_negative": 20, "red_team": 12}
POSITIVE_DATASETS = ("synthetic", "holdout")
NONE_ONLY_DATASETS = ("hard_negative", "red_team")
TARGETS = ("person", "everyone")

# 生产的三份系统提示词（模板键来自 settle.py 的模块常量，这里按用途命名）
PROMPT_TEMPLATES = {
    "archive": "memory_archive_system",
    "commitment": "memory_commitment_system",
    "close": "memory_commitment_close_system",
}
COMMITMENT_INSTRUCTION = "请找出这一场她新答应的、还没兑现的事。"
EMPTY_MODEL_REPLY = '{"archive": []}'

DEFAULT_TEMPERATURE = 0.3
DEFAULT_TIMEOUT_S = 120.0
REPORT_MODEL_OUTPUT_LIMIT = 6000
MIN_CLAUSE_MARKER = "只输出 JSON"

_CLOCK_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class DatasetError(Exception):
    """样本行不符合约定；消息带文件名与行号，方便直接定位改哪一行。"""


# ---------------------------------------------------------------------------
# 样本模型与解析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpeechTurn:
    """一条她说过的台词；回应观众时带上被回应的弹幕与发言人代号来源。"""

    n: int
    clock: str
    text: str
    reply_viewer: Optional[str] = None
    reply_text: Optional[str] = None

    @property
    def has_reply(self) -> bool:
        return self.reply_viewer is not None and self.reply_text is not None


@dataclass(frozen=True)
class OldCommitmentRecord:
    """账上已有的承诺（未了结或已判误记），作为生产原料喂给模型。"""

    id: int
    what: str
    quote: str = ""


@dataclass(frozen=True)
class EvalRecord:
    """一条完整用例。"""

    dataset: str
    case_id: str
    tags: tuple[str, ...]
    games: tuple[str, ...]
    abilities: tuple[str, ...]
    speech: tuple[SpeechTurn, ...]
    open_commitments: tuple[OldCommitmentRecord, ...]
    void_titles: tuple[OldCommitmentRecord, ...]
    expected: tuple[eval_metrics.ExpectedCommitment, ...]
    none: bool

    @property
    def line_texts(self) -> dict[int, str]:
        return {turn.n: turn.text for turn in self.speech}


def _fail(where: str, message: str) -> DatasetError:
    return DatasetError(f"{where}：{message}")


def _check_keys(raw: dict[str, Any], expected: set[str], where: str) -> None:
    """字段名必须与约定完全一致：缺字段与拼写错误都会让样本静默失真。"""
    missing = expected - set(raw)
    unknown = set(raw) - expected
    if missing:
        raise _fail(where, f"缺少字段 {', '.join(sorted(missing))}")
    if unknown:
        raise _fail(where, f"有未知字段 {', '.join(sorted(unknown))}（拼错或多余字段会被拒收）")


def _as_dict(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(where, "必须是 JSON 对象")
    return value


def _as_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, "必须是非空字符串")
    return value


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(where, "必须是整数")
    return value


def _as_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(where, "必须是布尔值")
    return value


def _as_str_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _fail(where, "必须是非空字符串数组")
    return tuple(_as_str(item, f"{where}[{index}]") for index, item in enumerate(value))


def _require_normalized(text: str, where: str) -> None:
    """生产渲染会把连续空白压成一个空格，样本先归一化才能保证逐字断言成立。"""
    if " ".join(text.split()) != text or "\n" in text:
        raise _fail(where, "需要是空白已归一化的单行文本")


def _parse_speech(raw: Any, where: str) -> tuple[SpeechTurn, ...]:
    if not isinstance(raw, list) or not raw:
        raise _fail(where, "必须是非空数组")
    turns: list[SpeechTurn] = []
    for index, item in enumerate(raw):
        item_where = f"{where}[{index}]"
        payload = _as_dict(item, item_where)
        _check_keys(payload, {"n", "clock", "text", "reply"}, item_where)
        number = _as_int(payload["n"], f"{item_where}.n")
        if number != index + 1:
            raise _fail(f"{item_where}.n", f"必须是连续编号 {index + 1}（生产按数组顺序从 1 编号）")
        clock = _as_str(payload["clock"], f"{item_where}.clock")
        if not _CLOCK_RE.match(clock):
            raise _fail(f"{item_where}.clock", f"必须是 HH:MM 形式的 24 小时时刻，当前 {clock!r}")
        text = _as_str(payload["text"], f"{item_where}.text")
        _require_normalized(text, f"{item_where}.text")
        reply_payload = payload["reply"]
        if reply_payload is None:
            turns.append(SpeechTurn(n=number, clock=clock, text=text))
            continue
        reply = _as_dict(reply_payload, f"{item_where}.reply")
        _check_keys(reply, {"viewer", "text"}, f"{item_where}.reply")
        viewer = _as_str(reply["viewer"], f"{item_where}.reply.viewer")
        reply_text = _as_str(reply["text"], f"{item_where}.reply.text")
        _require_normalized(reply_text, f"{item_where}.reply.text")
        turns.append(SpeechTurn(n=number, clock=clock, text=text, reply_viewer=viewer, reply_text=reply_text))
    return tuple(turns)


def _parse_old_commitments(raw: Any, where: str, *, with_quote: bool) -> tuple[OldCommitmentRecord, ...]:
    if not isinstance(raw, list):
        raise _fail(where, "必须是数组（可以为空）")
    records: list[OldCommitmentRecord] = []
    for index, item in enumerate(raw):
        item_where = f"{where}[{index}]"
        payload = _as_dict(item, item_where)
        expected_keys = {"id", "what", "quote"} if with_quote else {"id", "what"}
        _check_keys(payload, expected_keys, item_where)
        commitment_id = _as_int(payload["id"], f"{item_where}.id")
        if commitment_id <= 0:
            raise _fail(f"{item_where}.id", "必须是正整数")
        what = _as_str(payload["what"], f"{item_where}.what")
        if len(what) > MAX_TITLE_CHARS:
            raise _fail(f"{item_where}.what", f"标题不能超过 {MAX_TITLE_CHARS} 字")
        quote = ""
        if with_quote:
            quote = _as_str(payload["quote"], f"{item_where}.quote")
            if len(quote) > MAX_QUOTE_CHARS:
                raise _fail(f"{item_where}.quote", f"原话不能超过 {MAX_QUOTE_CHARS} 字")
        records.append(OldCommitmentRecord(id=commitment_id, what=what, quote=quote))
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        raise _fail(where, "承诺 id 重复")
    return tuple(records)


def _parse_expectations(
    raw: Any, speech: tuple[SpeechTurn, ...], games: tuple[str, ...], where: str
) -> tuple[tuple[eval_metrics.ExpectedCommitment, ...], bool]:
    payload = _as_dict(raw, where)
    _check_keys(payload, {"commitments", "none"}, where)
    none = _as_bool(payload["none"], f"{where}.none")
    raw_commitments = payload["commitments"]
    if not isinstance(raw_commitments, list):
        raise _fail(f"{where}.commitments", "必须是数组（可以为空）")
    if none and raw_commitments:
        raise _fail(where, "none 为 true 时 commitments 必须为空")
    if not none and not raw_commitments:
        raise _fail(where, "none 为 false 时 commitments 不能为空")
    line_texts = {turn.n: turn.text for turn in speech}
    lines_with_reply = {turn.n for turn in speech if turn.has_reply}
    expectations: list[eval_metrics.ExpectedCommitment] = []
    seen_lines: set[int] = set()
    for index, item in enumerate(raw_commitments):
        item_where = f"{where}.commitments[{index}]"
        entry = _as_dict(item, item_where)
        _check_keys(entry, {"line", "quote_contains", "what_contains", "to", "game"}, item_where)
        line = _as_int(entry["line"], f"{item_where}.line")
        if line not in line_texts:
            raise _fail(f"{item_where}.line", f"没有第 {line} 行台词")
        if line in seen_lines:
            raise _fail(f"{item_where}.line", "同一行只登记一条承诺（生产校验器也按一行一条处理）")
        seen_lines.add(line)
        quote_contains = _as_str(entry["quote_contains"], f"{item_where}.quote_contains")
        if len(quote_contains) > MAX_QUOTE_CHARS:
            raise _fail(f"{item_where}.quote_contains", f"片段不能超过 {MAX_QUOTE_CHARS} 字")
        if quote_contains not in line_texts[line]:
            raise _fail(f"{item_where}.quote_contains", f"不是第 {line} 行台词的逐字子串")
        what_contains = _as_str(entry["what_contains"], f"{item_where}.what_contains")
        if len(what_contains) > MAX_TITLE_CHARS:
            raise _fail(f"{item_where}.what_contains", f"关键词不能超过 {MAX_TITLE_CHARS} 字")
        target = _as_str(entry["to"], f"{item_where}.to")
        if target not in TARGETS:
            raise _fail(f"{item_where}.to", f"只能是 {' / '.join(TARGETS)}")
        game_value = entry["game"]
        game: Optional[str] = None
        if game_value is not None:
            game = _as_str(game_value, f"{item_where}.game")
            if game not in games:
                raise _fail(f"{item_where}.game", f"{game!r} 不在本场玩过的游戏里")
        if target == "person" and line not in lines_with_reply:
            raise _fail(f"{item_where}.to", "对某位观众答应时，那一行必须回应一条弹幕")
        expectations.append(
            eval_metrics.ExpectedCommitment(
                line=line, quote_contains=quote_contains, what_contains=what_contains, to=target, game=game
            )
        )
    return tuple(expectations), none


def _parse_record(dataset: str, line_number: int, raw: Any) -> EvalRecord:
    where = f"{dataset}.jsonl 第 {line_number} 行"
    payload = _as_dict(raw, where)
    _check_keys(payload, {"id", "tags", "session", "speech", "open_commitments", "void_titles", "expect"}, where)
    case_id = _as_str(payload["id"], f"{where}.id")
    tags = _as_str_list(payload["tags"], f"{where}.tags")
    session = _as_dict(payload["session"], f"{where}.session")
    _check_keys(session, {"games", "abilities"}, f"{where}.session")
    raw_games = session["games"]
    if not isinstance(raw_games, list):
        raise _fail(f"{where}.session.games", "必须是数组（可以为空）")
    games = tuple(_as_str(game, f"{where}.session.games[{index}]") for index, game in enumerate(raw_games))
    if len(games) != len(set(games)):
        raise _fail(f"{where}.session.games", "游戏名重复")
    abilities = _as_str_list(session["abilities"], f"{where}.session.abilities")
    speech = _parse_speech(payload["speech"], f"{where}.speech")
    open_commitments = _parse_old_commitments(payload["open_commitments"], f"{where}.open_commitments", with_quote=True)
    void_titles = _parse_old_commitments(payload["void_titles"], f"{where}.void_titles", with_quote=False)
    open_ids = {item.id for item in open_commitments}
    void_ids = {item.id for item in void_titles}
    if open_ids & void_ids:
        raise _fail(where, "未了结承诺与作废承诺的 id 不能重复")
    expectations, none = _parse_expectations(payload["expect"], speech, games, f"{where}.expect")
    if dataset in NONE_ONLY_DATASETS and not none:
        raise _fail(where, f"{dataset} 是负例集，expect.none 必须是 true")
    return EvalRecord(
        dataset=dataset,
        case_id=case_id,
        tags=tags,
        games=games,
        abilities=abilities,
        speech=speech,
        open_commitments=open_commitments,
        void_titles=void_titles,
        expected=expectations,
        none=none,
    )


def load_dataset(dataset: str) -> list[EvalRecord]:
    """读一个 jsonl 样本集；空行跳过，解析或校验失败带文件名与行号抛出。"""
    path = DATASET_DIR / f"{dataset}.jsonl"
    if not path.is_file():
        raise DatasetError(f"找不到样本集 {path}")
    records: list[EvalRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{path.name} 第 {line_number} 行不是合法 JSON：{exc}") from exc
            records.append(_parse_record(dataset, line_number, payload))
    if not records:
        raise DatasetError(f"{path.name} 没有用例")
    return records


def load_all_datasets() -> dict[str, list[EvalRecord]]:
    """读齐四份样本集，并检查用例 id 全局唯一。"""
    records = {name: load_dataset(name) for name in DATASET_NAMES}
    seen: dict[str, str] = {}
    for dataset, items in records.items():
        for item in items:
            if item.case_id in seen:
                raise DatasetError(f"用例 id 重复：{item.case_id} 同时出现在 {seen[item.case_id]} 与 {dataset}")
            seen[item.case_id] = dataset
    return records


# ---------------------------------------------------------------------------
# 生产取料所需的桩
# ---------------------------------------------------------------------------


class _UnusedRepo:
    """占位仓储：渲染与校验路径不碰仓储，真被访问说明生产取料方式变了。"""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        raise RuntimeError(f"评测器不应访问 {self._name} 仓储的 {attr!r}")


class _CommitmentsRepoStub:
    """回放用例中的旧承诺：未了结按登记顺序，判误记按最近在前（与生产查询同序）。"""

    def __init__(self, open_rows: Sequence[Commitment], void_rows: Sequence[Commitment]) -> None:
        self._open = list(open_rows)
        self._void = list(void_rows)

    async def list_open_before_session(self, *, live_session_id: int, limit: int = 200) -> list[Commitment]:
        return list(self._open)[:limit]

    async def list_commitments(self, *, statuses: Sequence[str], limit: int) -> list[Commitment]:
        return list(self._void)[:limit]


class _EventsRepoStub:
    """回放本场玩过的游戏（没有游戏事件记录时，生产原料就只有游戏名列表）。"""

    def __init__(self, games: Sequence[str]) -> None:
        self._records = [SimpleNamespace(game=game, message="") for game in games]

    async def list_session_game_events(self, *, live_session_id: int) -> list[Any]:
        return list(self._records)


class _RecordingLLM:
    """记录生产整理发出的每次调用并返回空列表；只用于抓取与生产同构的提示词。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str, *, profile: str, system: str) -> Response:
        self.calls.append({"prompt": prompt, "profile": profile, "system": system})
        return Response(success=True, content=EMPTY_MODEL_REPLY)


@lru_cache(maxsize=1)
def _clock_base_ms() -> int:
    """用例 clock 是本机今天的墙钟时刻；生产渲染用本地 HH:MM，这样两边逐字一致。"""
    today = datetime.now().date()
    return int(datetime.combine(today, clock_time(0, 0)).timestamp() * 1000)


def _clock_to_ms(clock: str) -> int:
    hours, minutes = clock.split(":")
    return _clock_base_ms() + (int(hours) * 60 + int(minutes)) * 60_000


def _speech_lines(record: EvalRecord) -> list[SettleSpeechLine]:
    """用例台词转成生产取料的 SettleSpeechLine；id 直接用行号，便于回映射。"""
    viewer_ids: dict[str, int] = {}
    lines: list[SettleSpeechLine] = []
    for turn in record.speech:
        viewer_id: Optional[int] = None
        if turn.reply_viewer is not None:
            viewer_id = viewer_ids.setdefault(turn.reply_viewer, len(viewer_ids) + 1)
        lines.append(
            SettleSpeechLine(
                id=turn.n,
                timestamp_ms=_clock_to_ms(turn.clock),
                content=turn.text,
                reply_content=turn.reply_text,
                reply_viewer_id=viewer_id,
                reply_simulated=False,
            )
        )
    return lines


def _commitment_row(record: OldCommitmentRecord, *, status: str) -> Commitment:
    return Commitment(
        id=record.id,
        what=record.what,
        quote=record.quote,
        status=status,
        created_at_ms=_clock_base_ms(),
        updated_at_ms=_clock_base_ms(),
    )


def _make_settler(record: EvalRecord, prompt_manager: PromptManager, llm: Any) -> SessionSettler:
    """按一场用例构造生产整理器；仓储只回放用例里的旧承诺与游戏名。"""
    open_rows = [_commitment_row(item, status="open") for item in record.open_commitments]
    void_rows = [_commitment_row(item, status="void") for item in record.void_titles]
    void_rows.sort(key=lambda row: row.id, reverse=True)
    return SessionSettler(
        sessions_repo=cast(Any, _UnusedRepo("sessions")),
        chat_repo=cast(Any, _UnusedRepo("chat")),
        commitments_repo=cast(Any, _CommitmentsRepoStub(open_rows, void_rows)),
        llm_service=llm,
        prompt_manager=prompt_manager,
        event_repo=cast(Any, _EventsRepoStub(record.games)),
        game_names_provider=lambda: list(record.abilities),
        clock=lambda: 0,
    )


def _session_stub() -> Any:
    return SimpleNamespace(id=1, started_at_ms=_clock_base_ms())


@dataclass(frozen=True)
class PreparedCase:
    """一场用例的生产原料与「找新承诺」调用的完整提示词。"""

    record: EvalRecord
    settler: SessionSettler
    material: SettleMaterial
    system_prompt: str
    user_prompt: str


def prepare_case(record: EvalRecord, prompt_manager: PromptManager) -> PreparedCase:
    """走生产取料与渲染流程，抓取承诺那次调用的系统与用户提示词。"""
    llm = _RecordingLLM()
    settler = _make_settler(record, prompt_manager, llm)
    material = asyncio.run(settler._collect_material(_session_stub(), _speech_lines(record)))
    asyncio.run(settler._extract(material))
    if len(llm.calls) < 2:
        raise RuntimeError(f"{record.dataset}/{record.case_id} 的生产整理流程没有发出承诺调用")
    commitment_call = llm.calls[1]
    return PreparedCase(
        record=record,
        settler=settler,
        material=material,
        system_prompt=str(commitment_call["system"]),
        user_prompt=str(commitment_call["prompt"]),
    )


# ---------------------------------------------------------------------------
# 期望、理想输出与登记
# ---------------------------------------------------------------------------


def _ideal_rows(record: EvalRecord) -> list[dict[str, Any]]:
    """把 expect 反推成「模型完全照做的输出」，用来过生产校验器。"""
    return [
        {
            "line": expectation.line,
            "quote": expectation.quote_contains,
            "what": expectation.what_contains,
            "to": expectation.to,
            "game": expectation.game,
            "same_as": None,
        }
        for expectation in record.expected
    ]


def _registered_from_pending(
    pending: Sequence[PendingCommitment],
) -> tuple[eval_metrics.RegisteredCommitment, ...]:
    """生产校验器放行的承诺 → 指标用的登记视图（行号由 evidence_chat_id 回映射）。"""
    return tuple(
        eval_metrics.RegisteredCommitment(
            line=int(item.evidence_chat_id or 0),
            quote=item.quote,
            what=item.what,
            to="person" if item.viewer_id is not None else "everyone",
            game=item.game,
        )
        for item in pending
    )


def _score_record(
    record: EvalRecord, registered: Sequence[eval_metrics.RegisteredCommitment]
) -> eval_metrics.CaseResult:
    return eval_metrics.score_case(
        dataset=record.dataset,
        case_id=record.case_id,
        expected=record.expected,
        registered=registered,
        void_titles=tuple(item.what for item in record.void_titles),
    )


# ---------------------------------------------------------------------------
# 样本自检
# ---------------------------------------------------------------------------


def _check_prompt(record: EvalRecord, prepared: PreparedCase, expected_system: str) -> list[str]:
    """确认这一场的提示词确实由生产流程生成、且把用例原料都带上了。"""
    problems: list[str] = []
    prefix = f"{record.dataset}/{record.case_id}"
    if prepared.system_prompt != expected_system:
        problems.append(f"{prefix}：承诺调用的系统提示词与生产模板渲染结果不一致")
    if COMMITMENT_INSTRUCTION not in prepared.user_prompt:
        problems.append(f"{prefix}：用户提示词缺少生产的登记指令")
    for turn in record.speech:
        marker = f"#{turn.n} {turn.clock} 你"
        if marker not in prepared.user_prompt:
            problems.append(f"{prefix}：用户提示词缺少台词编号与时刻 {marker!r}")
        if turn.text not in prepared.user_prompt:
            problems.append(f"{prefix}：用户提示词缺少第 {turn.n} 行台词原文")
        if turn.reply_text is not None and turn.reply_text not in prepared.user_prompt:
            problems.append(f"{prefix}：用户提示词缺少第 {turn.n} 行被回应的弹幕原文")
    for old in record.open_commitments:
        if old.what not in prepared.user_prompt or old.quote not in prepared.user_prompt:
            problems.append(f"{prefix}：用户提示词缺少旧承诺 #{old.id} 的标题或原话")
    for void in record.void_titles:
        if void.what not in prepared.user_prompt:
            problems.append(f"{prefix}：用户提示词缺作废承诺 #{void.id} 的标题")
    return problems


def _check_expectations(record: EvalRecord, prepared: PreparedCase) -> list[str]:
    """用生产校验器跑理想输出：期望的每条承诺都必须能被放行且字段一致。"""
    problems: list[str] = []
    prefix = f"{record.dataset}/{record.case_id}"
    pending, missed = prepared.settler._validate_commitments(_ideal_rows(record), prepared.material)
    for item in missed:
        problems.append(f"{prefix}：期望的承诺没过生产校验（{item.reason}）")
    if len(pending) != len(record.expected):
        problems.append(f"{prefix}：期望 {len(record.expected)} 条承诺，生产校验只放行 {len(pending)} 条")
    for expectation, registration in zip(record.expected, pending, strict=False):
        if int(registration.evidence_chat_id or 0) != expectation.line:
            problems.append(f"{prefix}：第 {expectation.line} 行期望被登记到了别的台词行")
        if registration.quote != expectation.quote_contains:
            problems.append(f"{prefix}：第 {expectation.line} 行登记的引文与期望片段不一致")
        if registration.what != expectation.what_contains:
            problems.append(f"{prefix}：第 {expectation.line} 行登记的动作标题与期望关键词不一致")
        expected_person = expectation.to == "person"
        actual_person = registration.viewer_id is not None
        if expected_person != actual_person:
            problems.append(f"{prefix}：第 {expectation.line} 行期望的「对谁」与生产校验结果不一致")
        if (expectation.game or None) != (registration.game or None):
            problems.append(f"{prefix}：第 {expectation.line} 行期望的游戏与生产校验结果不一致")
    return problems


# ---------------------------------------------------------------------------
# 报告与输出
# ---------------------------------------------------------------------------


def _force_utf8_output() -> None:
    """Windows 重定向输出时默认按本地编码写中文，显式切 UTF-8 让日志与报告可读。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _clip_text(text: str, limit: int = REPORT_MODEL_OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"…（截断，共 {len(text)} 字）"


def _dataset_summary(selected: dict[str, list[EvalRecord]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for dataset, records in selected.items():
        summary[dataset] = {
            "cases": len(records),
            "positive_cases": sum(1 for record in records if record.expected),
            "none_cases": sum(1 for record in records if not record.expected),
            "expected_commitments": sum(len(record.expected) for record in records),
            "open_commitments": sum(len(record.open_commitments) for record in records),
            "void_titles": sum(len(record.void_titles) for record in records),
        }
    return summary


def _case_status(result: eval_metrics.CaseResult) -> str:
    if result.misses:
        return "miss"
    if result.extra_registrations:
        return "false_registration"
    if any(not hit.strict for hit in result.hits):
        return "weak"
    return "ok"


def _case_report(
    record: EvalRecord,
    result: eval_metrics.CaseResult,
    *,
    prepared: Optional[PreparedCase] = None,
    model_output: Optional[str] = None,
    error: Optional[str] = None,
    elapsed_ms: int = 0,
    rejections: Sequence[UnregisteredItem] = (),
) -> dict[str, Any]:
    """一条用例的报告片段：期望、登记、命中、逐条差异与（live 的）原始输出。"""
    return {
        "dataset": result.dataset,
        "case_id": result.case_id,
        "tags": list(record.tags),
        "status": _case_status(result),
        "elapsed_ms": elapsed_ms,
        "prompt_chars": {
            "system": len(prepared.system_prompt) if prepared else 0,
            "user": len(prepared.user_prompt) if prepared else 0,
        },
        "model_output": _clip_text(model_output) if model_output is not None else None,
        "error": error,
        "expected": [asdict(item) for item in result.expected],
        "registered": [asdict(item) for item in result.registered],
        "hits": [
            {
                "line": hit.expected.line,
                "strict": hit.strict,
                "mismatches": list(hit.mismatches),
                "registered": asdict(hit.registered),
            }
            for hit in result.hits
        ],
        "misses": [asdict(item) for item in result.misses],
        "false_registrations": [asdict(item) for item in result.false_registrations],
        "duplicate_registrations": [asdict(item) for item in result.duplicate_registrations],
        "void_registrations": [asdict(item) for item in result.void_registrations],
        "validator_rejections": [asdict(item) for item in rejections],
        "diff_lines": eval_metrics.format_diff([result]).splitlines(),
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# dry：不调模型，自检样本与指标管线
# ---------------------------------------------------------------------------


def _run_dry(args: argparse.Namespace, prompt_manager: PromptManager, selected: dict[str, list[EvalRecord]]) -> int:
    print("== dry 模式：不调模型，用生产提示词与生产校验器自检样本集 ==")
    problems: list[str] = []
    templates = {name: prompt_manager.render(key) for name, key in PROMPT_TEMPLATES.items()}

    print("\n[1/3] 生产提示词渲染")
    for name, template_key in PROMPT_TEMPLATES.items():
        text = templates[name]
        print(f"  {template_key}：{len(text)} 字")
        if not text.strip():
            problems.append(f"模板 {template_key} 渲染结果为空")
        if MIN_CLAUSE_MARKER not in text:
            problems.append(f"模板 {template_key} 缺少只输出 JSON 的约定")

    print("\n[2/3] 样本自检（生产取料、渲染与校验器）")
    if not args.limit:
        for dataset in DATASET_NAMES:
            records = selected.get(dataset)
            if records is None:
                continue
            if len(records) < MIN_CASES[dataset]:
                problems.append(f"{dataset} 只有 {len(records)} 条用例，少于约定的 {MIN_CASES[dataset]} 条")
    expected_system = templates["commitment"]
    prepared_cases: list[PreparedCase] = []
    for dataset in DATASET_NAMES:
        records = selected.get(dataset)
        if not records:
            continue
        for record in records:
            prepared = prepare_case(record, prompt_manager)
            prepared_cases.append(prepared)
            problems.extend(_check_prompt(record, prepared, expected_system))
            problems.extend(_check_expectations(record, prepared))
        print(f"  {dataset}：{len(records)} 条用例已过生产流程")

    print("\n[3/3] 指标管线自检（理想输出）")
    oracle_results: list[eval_metrics.CaseResult] = []
    case_reports: list[dict[str, Any]] = []
    for prepared in prepared_cases:
        pending, _missed = prepared.settler._validate_commitments(_ideal_rows(prepared.record), prepared.material)
        registered = _registered_from_pending(pending)
        result = _score_record(prepared.record, registered)
        oracle_results.append(result)
        case_reports.append(_case_report(prepared.record, result, prepared=prepared))
    oracle_metrics = eval_metrics.evaluate(oracle_results)
    print(eval_metrics.format_metrics(oracle_metrics))
    diff = eval_metrics.format_diff(oracle_results)
    if diff != "逐条差异：无":
        problems.append(f"理想输出下仍有差异：\n{diff}")
    if oracle_metrics.expected and oracle_metrics.matched != oracle_metrics.expected:
        problems.append("理想输出下召回不是 100%，样本或指标口径不一致")
    if oracle_metrics.hard_negative_false_cases:
        problems.append("理想输出下难负例仍有误登记，说明样本集期望与校验器不一致")
    if oracle_metrics.red_team_false_registrations:
        problems.append("理想输出下红队仍有误登记，说明样本集期望与校验器不一致")

    if args.out is not None:
        report = {
            "mode": "dry",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "datasets": _dataset_summary(selected),
            "prompt": {
                "templates": {
                    name: {"key": template_key, "chars": len(templates[name])}
                    for name, template_key in PROMPT_TEMPLATES.items()
                },
                "commitment_instruction": COMMITMENT_INSTRUCTION,
            },
            "metrics": asdict(oracle_metrics),
            "verdict": {"passed": not problems, "failures": problems},
            "diff": eval_metrics.format_diff(oracle_results),
            "sample_problems": problems,
            "cases": case_reports,
        }
        _write_report(args.out, report)
        print(f"\n报告已写入 {args.out}")

    if problems:
        print(f"\nFAIL：样本自检发现 {len(problems)} 个问题")
        for problem in problems[:40]:
            print(f"  - {problem}")
        if len(problems) > 40:
            print(f"  ……其余 {len(problems) - 40} 个问题略")
        return 1
    print("\nPASS（dry）：样本自检与指标管线一致；本次未调用模型")
    return 0


# ---------------------------------------------------------------------------
# live：真调模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveConfig:
    """一次 live 评测的模型连接参数（API Key 只留在进程内，不写进报告）。"""

    model: str
    api_key: str
    base_url: Optional[str]
    temperature: float
    timeout_s: float


def _resolve_live_config(args: argparse.Namespace) -> LiveConfig:
    model = (args.model or os.environ.get("LLM_MODEL", "")).strip()
    api_key = (args.api_key or os.environ.get("LLM_API_KEY", "")).strip()
    base_url = (args.base_url or os.environ.get("LLM_BASE_URL", "")).strip() or None
    if not model:
        raise ValueError("缺少模型名：传 --model，或设置环境变量 LLM_MODEL")
    if not api_key:
        raise ValueError("缺少 API Key：传 --api-key，或设置环境变量 LLM_API_KEY")
    if not 0.0 <= args.temperature <= 2.0:
        raise ValueError(f"--temperature 必须在 0 到 2 之间，当前 {args.temperature}")
    if args.timeout <= 0:
        raise ValueError(f"--timeout 必须为正数，当前 {args.timeout}")
    return LiveConfig(
        model=model, api_key=api_key, base_url=base_url, temperature=args.temperature, timeout_s=args.timeout
    )


def _call_model(client: OpenAI, *, config: LiveConfig, system: str, user: str) -> str:
    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=config.temperature,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("模型返回空内容")
    return content


def _run_live(args: argparse.Namespace, prompt_manager: PromptManager, selected: dict[str, list[EvalRecord]]) -> int:
    try:
        config = _resolve_live_config(args)
    except ValueError as exc:
        print(f"live 参数错误：{exc}", file=sys.stderr)
        return 2
    system_prompt = prompt_manager.render(PROMPT_TEMPLATES["commitment"])
    print(f"== live 模式：真调模型 {config.model} ==")
    print(f"   base_url：{config.base_url or 'OpenAI 默认'}")
    print(f"   temperature：{config.temperature}，单次超时 {config.timeout_s:g} 秒")
    print(f"   系统提示词 {PROMPT_TEMPLATES['commitment']}：{len(system_prompt)} 字")
    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_s)

    results: list[eval_metrics.CaseResult] = []
    case_reports: list[dict[str, Any]] = []
    errors: list[str] = []
    total = sum(len(records) for records in selected.values())
    index = 0
    for dataset in DATASET_NAMES:
        records = selected.get(dataset)
        if not records:
            continue
        for record in records:
            index += 1
            prepared = prepare_case(record, prompt_manager)
            started = time.perf_counter()
            raw_output = ""
            error: Optional[str] = None
            rejections: list[UnregisteredItem] = []
            registered: tuple[eval_metrics.RegisteredCommitment, ...] = ()
            try:
                raw_output = _call_model(
                    client, config=config, system=prepared.system_prompt, user=prepared.user_prompt
                )
                payload = parse_json_object(raw_output)
                rows = payload.get("commitments")
                if rows is None:
                    rows = []
                if not isinstance(rows, list):
                    raise ValueError("模型输出的 commitments 不是列表")
                pending, missed = prepared.settler._validate_commitments(rows, prepared.material)
                rejections = list(missed)
                registered = _registered_from_pending(pending)
            except Exception as exc:  # noqa: BLE001 - 单条用例失败不中断整轮评测，计入报告
                error = f"{type(exc).__name__}: {exc}"
                errors.append(f"{dataset}/{record.case_id}: {error}")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            result = _score_record(record, registered)
            results.append(result)
            case_reports.append(
                _case_report(
                    record,
                    result,
                    prepared=prepared,
                    model_output=raw_output,
                    error=error,
                    elapsed_ms=elapsed_ms,
                    rejections=rejections,
                )
            )
            if args.verbose:
                suffix = f"（失败：{error}）" if error else ""
                print(f"  [{index}/{total}] {dataset}/{record.case_id}：登记 {len(registered)} 条{suffix}")
        print(f"  {dataset}：{len(records)} 条用例跑完")

    metrics = eval_metrics.evaluate(results)
    verdict = eval_metrics.make_verdict(metrics)
    failures = list(verdict.failures)
    if errors:
        failures.append(f"{len(errors)} 条用例调用或解析失败，结果不完整")
    passed = not failures

    if args.out is None:
        print("live 模式缺少 --out", file=sys.stderr)
        return 2
    report = {
        "mode": "live",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "datasets": _dataset_summary(selected),
        "thresholds": {
            "recall_min": eval_metrics.RECALL_MIN,
            "hard_negative_false_max": eval_metrics.HARD_NEGATIVE_FALSE_MAX,
            "red_team_false_max": eval_metrics.RED_TEAM_FALSE_MAX,
        },
        "model": {
            "provider": "openai 兼容接口（项目已有依赖 openai）",
            "model": config.model,
            "base_url": config.base_url,
            "temperature": config.temperature,
            "timeout_s": config.timeout_s,
            "profile_note": "对应生产整理第二次调用（summary 档位）的系统与用户提示词",
        },
        "prompt": {
            "template": PROMPT_TEMPLATES["commitment"],
            "system_chars": len(system_prompt),
        },
        "metrics": asdict(metrics),
        "verdict": {"passed": passed, "failures": failures},
        "diff": eval_metrics.format_diff(results),
        "errors": errors,
        "cases": case_reports,
    }
    _write_report(args.out, report)
    print("\n" + eval_metrics.format_metrics(metrics))
    print("\n" + eval_metrics.format_diff(results))
    print(f"\n报告已写入 {args.out}")
    if passed:
        print("\nPASS：三项阈值全部达标")
        return 0
    print("\nFAIL：")
    for failure in failures:
        print(f"  - {failure}")
    return 1


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def _select_records(records: dict[str, list[EvalRecord]], args: argparse.Namespace) -> dict[str, list[EvalRecord]]:
    names = DATASET_NAMES if not args.dataset else tuple(args.dataset)
    selected: dict[str, list[EvalRecord]] = {}
    for dataset in DATASET_NAMES:
        if dataset not in names:
            continue
        items = records[dataset]
        if args.limit:
            items = items[: args.limit]
        selected[dataset] = items
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="下播整理「找新承诺」离线评测器：默认 dry 自检样本，--live 真调模型按阈值判定",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "live 的模型连接参数优先取命令行，其次环境变量 LLM_MODEL / LLM_API_KEY / LLM_BASE_URL。\n"
            "示例：uv run python scripts/memory_eval/run_eval.py\n"
            "      uv run python scripts/memory_eval/run_eval.py --live --out reports/live.json"
        ),
    )
    parser.add_argument("--live", action="store_true", help="真调模型；省略时只做 dry 自检")
    parser.add_argument(
        "--dataset",
        action="append",
        choices=DATASET_NAMES,
        default=None,
        help="只跑指定数据集，可重复；默认四份都跑",
    )
    parser.add_argument("--limit", type=int, default=0, help="每个数据集最多跑几条用例（0 = 全部；调试用）")
    parser.add_argument("--out", type=Path, default=None, help="JSON 报告路径；live 必填，dry 可选")
    parser.add_argument("--model", default=None, help="模型名；缺省读环境变量 LLM_MODEL")
    parser.add_argument("--api-key", default=None, help="API Key；缺省读环境变量 LLM_API_KEY（不要写进报告）")
    parser.add_argument("--base-url", default=None, help="OpenAI 兼容端点；缺省读环境变量 LLM_BASE_URL")
    parser.add_argument(
        "--temperature", type=float, default=DEFAULT_TEMPERATURE, help=f"采样温度（默认 {DEFAULT_TEMPERATURE}）"
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S, help=f"单次调用超时秒数（默认 {DEFAULT_TIMEOUT_S:g}）"
    )
    parser.add_argument("--verbose", action="store_true", help="live 模式逐条打印用例结果")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    _force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit 不能为负数")
    if args.live and args.out is None:
        parser.error("--live 需要 --out 指定 JSON 报告路径")
    configure_from_config({"enabled": False, "console_level": "WARNING"})
    try:
        records = load_all_datasets()
    except DatasetError as exc:
        print(f"样本集读取失败：{exc}", file=sys.stderr)
        return 2
    selected = _select_records(records, args)
    prompt_manager = PromptManager(auto_scan_src=True)
    prompt_manager.load_all()
    if args.live:
        return _run_live(args, prompt_manager, selected)
    return _run_dry(args, prompt_manager, selected)


if __name__ == "__main__":
    sys.exit(main())
