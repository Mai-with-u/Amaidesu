"""下播整理「档案快照」的多场序列离线评测器：量几场下来稳定的事有没有被忘掉。

一条用例是几场连着的直播：前面一场立下稳定的事（给伙伴起的名字、住处、和观众定下的设定），
中间夹着又薄又乱的短场（卡顿、重开、设备出岔子），最后看这些事还在不在快照里。同时检查：
留在快照里的旧行逐字节不变（快照只引用原行）、运营者删掉的误记行不会再回来、没有本场证据的
旧行（一次性的小事也算）不会被丢、建造进度这类进展不被标成稳定的事（标了会挤掉前几场真正稳定的事）、
被这一场改写或了结的事被换掉或去掉。

样本里可以写"手松的模型"会给的操作（``sloppy``：没指证据、或拿"一次性遭遇"当理由的去掉），
dry 照样交给生产整理器，量的是代码兜底：这类去掉必须不被采纳，旧行照留。

每条用例在临时目录里起一份真实的 SQLite 库，按场次写入台词、跑生产 SessionSettler（快照的
写入与取舍都走生产代码），只有模型调用是替身：「写档案」那次调用 dry 用 expect 反推的理想
输出、live 真调模型；找承诺与了结两次调用不在评测范围内，一律回空。

dry（默认）自检样本、生产整理器与指标三者一致，所有指标应为满分；live（--live）按阈值判定。
连接参数与 run_eval.py 相同。用法与口径见同目录 README.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

# 同目录的 run_eval 在导入时把仓库根插进 sys.path，必须先于 src 导入
import run_eval as base

from openai import OpenAI
from src.agents.streamer.memory.settle import SessionSettler
from src.modules.logging import configure_from_config
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import ArchiveLine

DATASET = "archive_rolling"
DATASET_PATH = base.DATASET_DIR / f"{DATASET}.jsonl"
MIN_CASES = 6
ARCHIVE_MARKER = "【上一份档案】"
EMPTY_OTHER_REPLY = '{"commitments": [], "closed": []}'
SESSION_START_CLOCK = clock_time(19, 50)
SESSION_LENGTH_MS = 3 * 3_600_000

# 阈值：稳定的事遗忘率（序列末尾不在快照里的比例）、留下的旧行字节变化数、删掉的误记行回来的条数、
# 没有本场证据却被丢掉的旧行条数、进展被标成稳定的事的条数
FORGET_RATE_MAX = 0.1
BYTE_MISMATCH_MAX = 0
DELETED_RETURNED_MAX = 0
UNEVIDENCED_LOST_MAX = 0
WRONGLY_LASTING_MAX = 0

_BASE_ROW_RE = re.compile(r"^旧(\d+) \[[^\]]+\] .*?（原话「(.*?)」")


# ---------------------------------------------------------------------------
# 样本
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionCase:
    """序列里的一场：台词、本场玩过的游戏、理想输出要写的新事与旧行操作、整理后运营者删哪些行。"""

    games: Tuple[str, ...]
    speech: Tuple[base.SpeechTurn, ...]
    archive: Tuple[Dict[str, Any], ...]
    previous: Tuple[Dict[str, Any], ...]
    delete_after: Tuple[str, ...]


@dataclass(frozen=True)
class SequenceRecord:
    """一条用例：连着的几场，与序列末尾该在、不该在快照里的事（按键名）。

    ``present`` 是稳定的事（量遗忘率）；``kept`` 是哪一场都没有证据说它了结的旧行（多是一次性的
    小事），进过快照的序列末尾也必须还在；模型当初就没把它写成档案的不算"被丢"，另行报告。
    ``not_lasting`` 是建造进度、家具、种地、做菜这类进展，任何一场的快照里都不该标成稳定的事。
    """

    case_id: str
    tags: Tuple[str, ...]
    sessions: Tuple[SessionCase, ...]
    present: Tuple[str, ...]
    absent: Tuple[str, ...]
    kept: Tuple[str, ...] = ()
    not_lasting: Tuple[str, ...] = ()


def _parse_session(raw: Any, where: str) -> SessionCase:
    payload = base._as_dict(raw, where)
    base._check_keys(payload, {"games", "speech", "expect"}, where)
    expect = base._as_dict(payload["expect"], f"{where}.expect")
    base._check_keys(expect, {"archive", "previous", "delete_after"}, f"{where}.expect")
    speech = base._parse_speech(payload["speech"], f"{where}.speech")
    texts = {turn.n: turn.text for turn in speech}
    items = [base._as_dict(item, f"{where}.expect.archive") for item in expect["archive"]]
    ops = [base._as_dict(item, f"{where}.expect.previous") for item in expect["previous"]]
    for item in [*items, *ops]:
        quote = item.get("quote_contains")
        if quote is not None and quote not in texts.get(int(item["line"]), ""):
            raise base._fail(where, f"quote_contains「{quote}」不是第 {item['line']} 行台词的逐字片段")
    return SessionCase(
        games=base._as_str_list(payload["games"], f"{where}.games") if payload["games"] else (),
        speech=speech,
        archive=tuple(items),
        previous=tuple(ops),
        delete_after=tuple(str(key) for key in expect["delete_after"]),
    )


def load_records() -> List[SequenceRecord]:
    """读多场序列样本；格式不对抛 DatasetError。"""
    records: List[SequenceRecord] = []
    for line_number, raw_line in enumerate(DATASET_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        where = f"{DATASET}:{line_number}"
        raw = base._as_dict(json.loads(raw_line), where)
        base._check_keys(raw, {"id", "tags", "sessions", "final"}, where)
        final = base._as_dict(raw["final"], f"{where}.final")
        sessions = tuple(
            _parse_session(item, f"{where}.sessions[{index}]") for index, item in enumerate(raw["sessions"])
        )
        if len(sessions) < 2:
            raise base._fail(where, "多场序列至少要两场")
        records.append(
            SequenceRecord(
                case_id=base._as_str(raw["id"], f"{where}.id"),
                tags=base._as_str_list(raw["tags"], f"{where}.tags"),
                sessions=sessions,
                present=tuple(str(key) for key in final.get("present", [])),
                absent=tuple(str(key) for key in final.get("absent", [])),
                kept=tuple(str(key) for key in final.get("kept", [])),
                not_lasting=tuple(str(key) for key in final.get("not_lasting", [])),
            )
        )
    return records


# ---------------------------------------------------------------------------
# 一条序列怎么跑
# ---------------------------------------------------------------------------


class _ArchiveOnlyLLM:
    """整理器要的模型接口：「写档案」那次交给 ``answer``，找承诺与了结两次回空。"""

    def __init__(self, answer: Callable[[str, str], str]) -> None:
        self._answer = answer
        self.outputs: List[str] = []

    async def generate(self, prompt: str, **kwargs: Any) -> Any:
        if ARCHIVE_MARKER not in prompt:
            return SimpleNamespace(ok=True, content=EMPTY_OTHER_REPLY)
        content = self._answer(str(kwargs.get("system") or ""), prompt)
        self.outputs.append(content)
        return SimpleNamespace(ok=True, content=content)


def _row_signature(line: ArchiveLine) -> Tuple[Any, ...]:
    """快照要求一个字节都不变的那几项；人改过标题的行（human_locked）标题由人定，不比。"""
    title = None if line.human_locked else line.text
    return (title, line.quote, line.evidence_chat_id, line.live_session_id, line.game, line.save_id, line.save_name)


def _day_start_ms(days_ago: int) -> int:
    day = datetime.now().date() - timedelta(days=days_ago)
    return int(datetime.combine(day, clock_time(0, 0)).timestamp() * 1000)


@dataclass
class SequenceResult:
    """一条序列的结果：各项计数、每场的模型输出与快照、出错信息。"""

    case_id: str
    present_total: int = 0
    present_found: int = 0
    stale_total: int = 0
    stale_removed: int = 0
    deleted_returned: int = 0
    byte_mismatches: int = 0
    kept_total: int = 0
    unevidenced_lost: List[str] = field(default_factory=list)
    kept_unwritten: List[str] = field(default_factory=list)
    not_lasting_total: int = 0
    wrongly_lasting: List[str] = field(default_factory=list)
    forgotten: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    sessions: List[Dict[str, Any]] = field(default_factory=list)


def _oracle(case: SessionCase, key_quotes: Dict[str, str]) -> Callable[[str, str], str]:
    """dry 的理想输出：新事照 expect 写；旧行操作按旧行清单里原话对上键名找编号。

    ``sloppy`` 的操作（手松的模型会给的去掉）照原样给出，没写 line 的就不带 line。
    """

    def answer(_system: str, prompt: str) -> str:
        numbers: Dict[str, int] = {}
        section = prompt.split(ARCHIVE_MARKER, 1)[1].split("【本场她说的话】", 1)[0]
        for row in section.splitlines():
            matched = _BASE_ROW_RE.match(row.strip())
            if matched:
                numbers[matched.group(2)] = int(matched.group(1))
        ops: List[Dict[str, Any]] = []
        for op in case.previous:
            number = numbers.get(key_quotes.get(str(op["key"]), ""))
            if number is None:
                continue
            item: Dict[str, Any] = {"old": number, "op": op["op"]}
            if "line" in op:
                item["line"] = op["line"]
            if op["op"] == "drop":
                item["reason"] = op["reason"]
            if op["op"] == "replace":
                item.update(title=op["title"], quote=op["quote_contains"], game=op["game"], lasting=op["lasting"])
            ops.append(item)
        archive = [
            {
                "line": a["line"],
                "title": a["title"],
                "quote": a["quote_contains"],
                "game": a["game"],
                "lasting": a["lasting"],
            }
            for a in case.archive
        ]
        return json.dumps({"previous": ops, "archive": archive}, ensure_ascii=False)

    return answer


def _keys_of_session(case: SessionCase) -> Dict[int, str]:
    """本场每行台词写成档案行时对应的键名（新事与替换出的新行）。"""
    keys = {int(item["line"]): str(item["key"]) for item in case.archive}
    keys.update({int(op["line"]): str(op["new_key"]) for op in case.previous if op["op"] == "replace"})
    return keys


async def run_sequence(
    record: SequenceRecord,
    prompt_manager: PromptManager,
    answer_for: Callable[[SessionCase, Dict[str, str]], Callable[[str, str], str]],
) -> SequenceResult:
    """在临时库里按场次跑完一条序列，返回各项计数。"""
    result = SequenceResult(case_id=record.case_id)
    key_quotes: Dict[str, str] = {}
    row_keys: Dict[int, str] = {}
    originals: Dict[int, Tuple[Any, ...]] = {}
    # 进过任何一场快照的键：没进过的是模型当初没写成档案，不算"被丢"
    seen_keys: Set[str] = set()
    games = sorted({game for case in record.sessions for game in case.games})
    with tempfile.TemporaryDirectory(prefix="archive-rolling-") as folder:
        store = SQLiteDatabase(Path(folder) / "eval.db")
        await store.initialize()
        try:
            last_session: Optional[int] = None
            for index, case in enumerate(record.sessions):
                day = _day_start_ms(len(record.sessions) - index)
                started = day + (SESSION_START_CLOCK.hour * 60 + SESSION_START_CLOCK.minute) * 60_000
                session_id = await store.sessions.insert_live_session(started_at_ms=started, kind="live")
                await store.sessions.close_live_session(
                    live_session_id=session_id, ended_at_ms=started + SESSION_LENGTH_MS
                )
                chat_lines: Dict[int, int] = {}
                for turn in case.speech:
                    hours, minutes = turn.clock.split(":")
                    chat_id = await store.chat.insert_live_chat(
                        live_session_id=session_id,
                        timestamp_ms=day + (int(hours) * 60 + int(minutes)) * 60_000,
                        sender_role="assistant",
                        content=turn.text,
                        message_type="speech",
                    )
                    chat_lines[chat_id] = turn.n
                for item in case.archive:
                    key_quotes[str(item["key"])] = str(item["quote_contains"])
                for op in case.previous:
                    if op["op"] == "replace":
                        key_quotes[str(op["new_key"])] = str(op["quote_contains"])
                llm = _ArchiveOnlyLLM(answer_for(case, dict(key_quotes)))
                settler = SessionSettler(
                    sessions_repo=store.sessions,
                    chat_repo=store.chat,
                    commitments_repo=store.commitments,
                    event_repo=store.events,
                    archive_repo=store.archive,
                    llm_service=llm,
                    prompt_manager=prompt_manager,
                    game_names_provider=lambda: games,
                )
                if not await settler.settle_session(session_id):
                    row = await store.sessions.get_live_session(live_session_id=session_id)
                    result.problems.append(f"第 {index + 1} 场整理失败：{row.settle_report if row else '场次不见了'}")
                    continue
                last_session = session_id
                line_keys = _keys_of_session(case)
                for line in await store.archive.list_lines(live_session_id=session_id):
                    originals[line.id] = _row_signature(line)
                    key = line_keys.get(chat_lines.get(int(line.evidence_chat_id), -1))
                    if key is not None:
                        row_keys[line.id] = key
                for key in case.delete_after:
                    for row_id, row_key in list(row_keys.items()):
                        if row_key == key:
                            await store.archive.delete_line(line_id=row_id)
                snapshot = await store.archive.get_snapshot(live_session_id=session_id)
                rows = snapshot.lines if snapshot is not None else []
                seen_keys.update(key for key in (row_keys.get(item.line.id) for item in rows) if key is not None)
                # 进展类的行在任何一场的快照里被标成稳定的事，都记一次（同一个键只记一次）
                for item in rows:
                    key = row_keys.get(item.line.id)
                    if key in record.not_lasting and item.lasting and key not in result.wrongly_lasting:
                        result.wrongly_lasting.append(key)
                for item in rows:
                    if item.line.id in originals and originals[item.line.id] != _row_signature(item.line):
                        result.byte_mismatches += 1
                result.sessions.append(
                    {
                        "session": index + 1,
                        "model_output": base._clip_text(llm.outputs[-1] if llm.outputs else ""),
                        "snapshot": [
                            {"key": row_keys.get(item.line.id), "title": item.line.text, "lasting": item.lasting}
                            for item in rows
                        ],
                    }
                )
            final = await store.archive.get_snapshot(live_session_id=last_session) if last_session else None
        finally:
            await store.close()
    final_keys = {row_keys.get(item.line.id) for item in (final.lines if final is not None else [])}
    deleted = {key for case in record.sessions for key in case.delete_after}
    result.present_total = len(record.present)
    result.present_found = sum(1 for key in record.present if key in final_keys)
    result.forgotten = [key for key in record.present if key not in final_keys]
    stale = [key for key in record.absent if key not in deleted]
    result.stale_total = len(stale)
    result.stale_removed = sum(1 for key in stale if key not in final_keys)
    result.deleted_returned = sum(1 for key in record.absent if key in deleted and key in final_keys)
    written = [key for key in record.kept if key in seen_keys]
    result.kept_total = len(written)
    result.unevidenced_lost = [key for key in written if key not in final_keys]
    result.kept_unwritten = [key for key in record.kept if key not in seen_keys]
    result.not_lasting_total = sum(1 for key in record.not_lasting if key in seen_keys)
    return result


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollingMetrics:
    """整轮评测的汇总。"""

    sequences: int
    present_total: int
    present_found: int
    forget_rate: float
    stale_total: int
    stale_removed: int
    deleted_returned: int
    byte_mismatches: int
    kept_total: int
    unevidenced_lost: int
    kept_unwritten: int
    not_lasting_total: int
    wrongly_lasting: int
    problems: int


def evaluate(results: Sequence[SequenceResult]) -> RollingMetrics:
    present_total = sum(item.present_total for item in results)
    present_found = sum(item.present_found for item in results)
    return RollingMetrics(
        sequences=len(results),
        present_total=present_total,
        present_found=present_found,
        forget_rate=round(1 - present_found / present_total, 4) if present_total else 0.0,
        stale_total=sum(item.stale_total for item in results),
        stale_removed=sum(item.stale_removed for item in results),
        deleted_returned=sum(item.deleted_returned for item in results),
        byte_mismatches=sum(item.byte_mismatches for item in results),
        kept_total=sum(item.kept_total for item in results),
        unevidenced_lost=sum(len(item.unevidenced_lost) for item in results),
        kept_unwritten=sum(len(item.kept_unwritten) for item in results),
        not_lasting_total=sum(item.not_lasting_total for item in results),
        wrongly_lasting=sum(len(item.wrongly_lasting) for item in results),
        problems=sum(len(item.problems) for item in results),
    )


def verdict_failures(metrics: RollingMetrics, *, dry: bool) -> List[str]:
    """不达标的项；dry 要求全部满分（样本、生产整理器、指标三者自洽）。"""
    failures: List[str] = []
    forget_max = 0.0 if dry else FORGET_RATE_MAX
    if metrics.forget_rate > forget_max:
        failures.append(f"稳定的事遗忘率 {metrics.forget_rate:.2f} > {forget_max:.2f}")
    if metrics.byte_mismatches > BYTE_MISMATCH_MAX:
        failures.append(f"留下的旧行有 {metrics.byte_mismatches} 处字节变化")
    if metrics.deleted_returned > DELETED_RETURNED_MAX:
        failures.append(f"删掉的误记行回来了 {metrics.deleted_returned} 条")
    if metrics.unevidenced_lost > UNEVIDENCED_LOST_MAX:
        failures.append(f"没有本场证据的旧行被丢了 {metrics.unevidenced_lost} 条")
    if metrics.wrongly_lasting > WRONGLY_LASTING_MAX:
        failures.append(f"进展被标成稳定的事 {metrics.wrongly_lasting} 条")
    if dry and metrics.kept_unwritten:
        failures.append(f"该留住的旧行有 {metrics.kept_unwritten} 条一开始就没写成档案")
    if dry and metrics.stale_removed != metrics.stale_total:
        failures.append(f"被改写或了结的事只换掉 {metrics.stale_removed}/{metrics.stale_total}")
    if metrics.problems:
        failures.append(f"{metrics.problems} 场整理失败")
    return failures


def format_metrics(metrics: RollingMetrics) -> str:
    return "\n".join(
        [
            f"序列 {metrics.sequences} 条",
            f"稳定的事：留住 {metrics.present_found}/{metrics.present_total}，遗忘率 {metrics.forget_rate:.2f}"
            f"（阈值 ≤ {FORGET_RATE_MAX}）",
            f"被改写或了结的事换掉 {metrics.stale_removed}/{metrics.stale_total}（只报告，不设阈值）",
            f"删掉的误记行回来 {metrics.deleted_returned} 条（阈值 {DELETED_RETURNED_MAX}）",
            f"没有本场证据的旧行被丢 {metrics.unevidenced_lost}/{metrics.kept_total} 条（阈值 {UNEVIDENCED_LOST_MAX}；"
            f"另有 {metrics.kept_unwritten} 条模型当初没写成档案，不计入）",
            f"进展被标成稳定的事 {metrics.wrongly_lasting}/{metrics.not_lasting_total} 条（阈值 {WRONGLY_LASTING_MAX}）",
            f"留下的旧行字节变化 {metrics.byte_mismatches} 处（阈值 {BYTE_MISMATCH_MAX}）",
        ]
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _live_answer(
    args: argparse.Namespace, usage: base.TokenUsage
) -> Callable[[SessionCase, Dict[str, str]], Callable[[str, str], str]]:
    config = base._resolve_live_config(args)
    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_s)

    # 每场整理里"写档案"那次调用都把用量累加到同一本账上，整轮结束后打印并写进报告
    def answer_for(_case: SessionCase, _keys: Dict[str, str]) -> Callable[[str, str], str]:
        return lambda system, prompt: base._call_model(client, config=config, system=system, user=prompt, usage=usage)

    return answer_for


def _run(args: argparse.Namespace, prompt_manager: PromptManager, records: List[SequenceRecord]) -> int:
    dry = not args.live
    if dry and len(records) < MIN_CASES:
        print(f"样本不足：{len(records)} < {MIN_CASES}", file=sys.stderr)
        return 1
    usage = base.TokenUsage()
    try:
        answer_for = _oracle if dry else _live_answer(args, usage)
    except ValueError as exc:
        print(f"live 参数错误：{exc}", file=sys.stderr)
        return 2
    print(f"== 档案快照多场序列 {'dry 自检' if dry else 'live'} ==")
    results: List[SequenceResult] = []
    for record in records:
        started = time.perf_counter()
        result = asyncio.run(run_sequence(record, prompt_manager, answer_for))
        results.append(result)
        if args.verbose or result.forgotten or result.unevidenced_lost or result.problems:
            print(
                f"  {record.case_id}：留住 {result.present_found}/{result.present_total}"
                f"（忘了 {result.forgotten or '无'}；没证据被丢 {result.unevidenced_lost or '无'}），"
                f"{int((time.perf_counter() - started) * 1000)} ms"
            )
            for problem in result.problems:
                print(f"    - {problem}")
    metrics = evaluate(results)
    failures = verdict_failures(metrics, dry=dry)
    print("\n" + format_metrics(metrics))
    if not dry:
        print(usage.format())
    if args.out is not None:
        report: Dict[str, Any] = {
            "mode": "dry" if dry else "live",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "thresholds": {
                "forget_rate_max": FORGET_RATE_MAX,
                "byte_mismatch_max": BYTE_MISMATCH_MAX,
                "deleted_returned_max": DELETED_RETURNED_MAX,
                "unevidenced_lost_max": UNEVIDENCED_LOST_MAX,
                "wrongly_lasting_max": WRONGLY_LASTING_MAX,
            },
            "metrics": asdict(metrics),
            "verdict": {"passed": not failures, "failures": failures},
            "cases": [asdict(item) for item in results],
        }
        # dry 不调模型，没有用量可记；live 才把整轮 token 写进报告，改提示词时对照成本
        if not dry:
            report["usage"] = asdict(usage)
        base._write_report(args.out, report)
        print(f"\n报告已写入 {args.out}")
    if not failures:
        print("\nPASS")
        return 0
    print("\nFAIL：")
    for failure in failures:
        print(f"  - {failure}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="下播整理「档案快照」多场序列评测器：默认 dry 自检样本，--live 真调模型按阈值判定",
        epilog="live 的模型连接参数与 run_eval.py 相同：命令行优先，其次环境变量 LLM_MODEL / LLM_API_KEY / LLM_BASE_URL。",
    )
    parser.add_argument("--live", action="store_true", help="真调模型；省略时只做 dry 自检")
    parser.add_argument("--limit", type=int, default=0, help="最多跑几条序列（0 = 全部；调试用）")
    parser.add_argument("--out", type=Path, default=None, help="JSON 报告路径；live 必填，dry 可选")
    parser.add_argument("--model", default=None, help="模型名；缺省读环境变量 LLM_MODEL")
    parser.add_argument("--api-key", default=None, help="API Key；缺省读环境变量 LLM_API_KEY（不要写进报告）")
    parser.add_argument("--base-url", default=None, help="OpenAI 兼容端点；缺省读环境变量 LLM_BASE_URL")
    parser.add_argument("--temperature", type=float, default=base.DEFAULT_TEMPERATURE, help="采样温度")
    parser.add_argument("--timeout", type=float, default=base.DEFAULT_TIMEOUT_S, help="单次调用超时秒数")
    parser.add_argument("--verbose", action="store_true", help="逐条打印序列结果")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    base._force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit 不能为负数")
    if args.live and args.out is None:
        parser.error("--live 需要 --out 指定 JSON 报告路径")
    configure_from_config({"enabled": False, "console_level": "WARNING"})
    try:
        records = load_records()
    except base.DatasetError as exc:
        print(f"样本集读取失败：{exc}", file=sys.stderr)
        return 2
    if args.limit:
        records = records[: args.limit]
    prompt_manager = PromptManager(auto_scan_src=True)
    prompt_manager.load_all()
    return _run(args, prompt_manager, records)


if __name__ == "__main__":
    sys.exit(main())
