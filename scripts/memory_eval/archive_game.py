"""下播整理「写场次档案」这一步的游戏归属离线评测器。

评的是档案每一行有没有挂对游戏。同一款游戏的不同存档是互不相干的世界：档案行挂了游戏，
代码才会按台词时刻给它记下存档，下次开播只把眼下这个存档里的事端给主播；挂错了（聊天的事
挂上游戏、游戏里的事没挂）就会在别的存档里提起没有的东西，或在该提的时候漏掉。档案挑了哪几件
事、标题写得好不好不在评测范围内。

dry（默认）不调模型：用生产 PromptManager 渲染档案提示词，按用例走生产取料抓取「写档案」那次
调用的完整提示词，再把 expect 反推成理想输出过生产档案校验器，自检样本与指标口径一致。

live（--live）真调模型：同一份生产提示词发给模型，输出经生产档案校验器放行后，只评出自有
期望的台词行的档案行，按游戏归属准确率与可评行占比判定 PASS/FAIL。

复用边界与 run_eval.py 相同：取料、渲染与校验都走生产 SessionSettler；台词解析、取料替身与
模型连接直接复用 run_eval.py 的实现，两个评测器对同一份样本格式的理解不会分叉。

用法与口径见同目录 README.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence, cast

# 同目录的 run_eval 在导入时把仓库根插进 sys.path，必须先于 src 导入
import run_eval as base

from openai import OpenAI
from src.agents.streamer.memory.settle import (
    MAX_ARCHIVE_LINES,
    MAX_QUOTE_CHARS,
    SessionSettler,
    SettleMaterial,
    parse_json_object,
)
from src.modules.logging import configure_from_config
from src.modules.prompts import PromptManager
from src.modules.storage.models.memory import PendingArchiveLine, UnregisteredItem

DATASET = "archive_game"
DATASET_PATH = base.DATASET_DIR / f"{DATASET}.jsonl"
MIN_CASES = 12
ARCHIVE_INSTRUCTION = "请写出这一场的档案。"
NO_GAME_MARKER = "【本场没有玩游戏】"

# 阈值：挂游戏的准确率，与能对上期望的档案行占期望行的比例（太低说明模型挑的行几乎都没评到）
GAME_ACCURACY_MIN = 0.9
COVERAGE_MIN = 0.5


# ---------------------------------------------------------------------------
# 样本
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveExpectation:
    """一行值得写进档案的台词，以及这件事该挂的游戏（None = 不在游戏世界里发生）。"""

    line: int
    quote_contains: str
    game: Optional[str]


@dataclass(frozen=True)
class ArchiveRecord:
    """一条用例：本场玩过的游戏、台词，与哪些行写成档案时该挂哪个游戏。"""

    case_id: str
    tags: tuple[str, ...]
    games: tuple[str, ...]
    speech: tuple[base.SpeechTurn, ...]
    expected: tuple[ArchiveExpectation, ...]


def _parse_expectations(
    raw: Any, speech: tuple[base.SpeechTurn, ...], games: tuple[str, ...], where: str
) -> tuple[ArchiveExpectation, ...]:
    payload = base._as_dict(raw, where)
    base._check_keys(payload, {"archive"}, where)
    items = payload["archive"]
    if not isinstance(items, list) or not items:
        raise base._fail(f"{where}.archive", "必须是非空数组")
    if len(items) > MAX_ARCHIVE_LINES:
        raise base._fail(f"{where}.archive", f"一场最多 {MAX_ARCHIVE_LINES} 行档案（生产校验器只收这么多）")
    line_texts = {turn.n: turn.text for turn in speech}
    expectations: list[ArchiveExpectation] = []
    for index, item in enumerate(items):
        item_where = f"{where}.archive[{index}]"
        entry = base._as_dict(item, item_where)
        base._check_keys(entry, {"line", "quote_contains", "game"}, item_where)
        line = base._as_int(entry["line"], f"{item_where}.line")
        if line not in line_texts:
            raise base._fail(f"{item_where}.line", f"没有第 {line} 行台词")
        if any(existing.line == line for existing in expectations):
            raise base._fail(f"{item_where}.line", "同一行只写一条期望")
        quote = base._as_str(entry["quote_contains"], f"{item_where}.quote_contains")
        if len(quote) > MAX_QUOTE_CHARS or quote not in line_texts[line]:
            raise base._fail(
                f"{item_where}.quote_contains", f"必须是第 {line} 行台词的逐字子串且不超过 {MAX_QUOTE_CHARS} 字"
            )
        game: Optional[str] = None
        if entry["game"] is not None:
            game = base._as_str(entry["game"], f"{item_where}.game")
            if game not in games:
                raise base._fail(f"{item_where}.game", f"{game!r} 不在本场玩过的游戏里")
        expectations.append(ArchiveExpectation(line=line, quote_contains=quote, game=game))
    return tuple(expectations)


def _parse_record(line_number: int, raw: Any) -> ArchiveRecord:
    where = f"{DATASET}.jsonl 第 {line_number} 行"
    payload = base._as_dict(raw, where)
    base._check_keys(payload, {"id", "tags", "session", "speech", "expect"}, where)
    session = base._as_dict(payload["session"], f"{where}.session")
    base._check_keys(session, {"games"}, f"{where}.session")
    raw_games = session["games"]
    if not isinstance(raw_games, list):
        raise base._fail(f"{where}.session.games", "必须是数组（可以为空）")
    games = tuple(base._as_str(game, f"{where}.session.games[{index}]") for index, game in enumerate(raw_games))
    if len(games) != len(set(games)):
        raise base._fail(f"{where}.session.games", "游戏名重复")
    speech = base._parse_speech(payload["speech"], f"{where}.speech")
    return ArchiveRecord(
        case_id=base._as_str(payload["id"], f"{where}.id"),
        tags=base._as_str_list(payload["tags"], f"{where}.tags"),
        games=games,
        speech=speech,
        expected=_parse_expectations(payload["expect"], speech, games, f"{where}.expect"),
    )


def load_records() -> list[ArchiveRecord]:
    """读档案挂游戏样本集；空行跳过，解析失败带行号抛出，用例 id 必须唯一。"""
    if not DATASET_PATH.is_file():
        raise base.DatasetError(f"找不到样本集 {DATASET_PATH}")
    records: list[ArchiveRecord] = []
    for line_number, raw_line in enumerate(DATASET_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise base.DatasetError(f"{DATASET_PATH.name} 第 {line_number} 行不是合法 JSON：{exc}") from exc
        records.append(_parse_record(line_number, payload))
    ids = [record.case_id for record in records]
    if len(ids) != len(set(ids)):
        raise base.DatasetError(f"{DATASET_PATH.name} 用例 id 重复")
    return records


# ---------------------------------------------------------------------------
# 生产取料
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedArchiveCase:
    """一场用例的生产原料与「写档案」调用的完整提示词。"""

    record: ArchiveRecord
    settler: SessionSettler
    material: SettleMaterial
    system_prompt: str
    user_prompt: str


def prepare_case(record: ArchiveRecord, prompt_manager: PromptManager) -> PreparedArchiveCase:
    """按一场用例走生产取料与渲染，抓取第一次（写档案）调用的系统与用户提示词。"""
    llm = base._RecordingLLM()
    settler = SessionSettler(
        sessions_repo=cast(Any, base._UnusedRepo("sessions")),
        chat_repo=cast(Any, base._UnusedRepo("chat")),
        commitments_repo=cast(Any, base._CommitmentsRepoStub([], [])),
        llm_service=llm,
        prompt_manager=prompt_manager,
        event_repo=cast(Any, base._EventsRepoStub(record.games)),
        game_names_provider=lambda: list(record.games),
        clock=lambda: 0,
    )
    # run_eval 的台词转换只读 speech 字段，档案用例与承诺用例同一格式
    material = asyncio.run(settler._collect_material(base._session_stub(), base._speech_lines(cast(Any, record))))
    asyncio.run(settler._extract(material))
    if not llm.calls:
        raise RuntimeError(f"{record.case_id} 的生产整理流程没有发出档案调用")
    call = llm.calls[0]
    return PreparedArchiveCase(
        record=record,
        settler=settler,
        material=material,
        system_prompt=str(call["system"]),
        user_prompt=str(call["prompt"]),
    )


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveCaseResult:
    """一条用例的结果：出自有期望台词行的档案行里，挂对游戏的有几行。"""

    case_id: str
    expected: int
    scored: int
    correct: int
    unscored: int
    mismatches: tuple[str, ...]


@dataclass(frozen=True)
class ArchiveMetrics:
    cases: int
    expected: int
    scored: int
    correct: int
    unscored: int
    accuracy: Optional[float]
    coverage: Optional[float]


def score_case(record: ArchiveRecord, written: Sequence[PendingArchiveLine]) -> ArchiveCaseResult:
    """只评出自有期望台词行的档案行；同一行写了几条只评第一条，其余与无期望的行一起记为不评。"""
    by_line = {item.line: item for item in record.expected}
    seen: set[int] = set()
    correct = 0
    unscored = 0
    mismatches: list[str] = []
    for item in written:
        line = int(item.evidence_chat_id or 0)
        expectation = by_line.get(line)
        if expectation is None or line in seen:
            unscored += 1
            continue
        seen.add(line)
        if (item.game or None) == expectation.game:
            correct += 1
            continue
        mismatches.append(
            f"{record.case_id} 第 {line} 行「{item.text}」挂了 {item.game or '无游戏'}，应为 {expectation.game or '无游戏'}"
        )
    return ArchiveCaseResult(
        case_id=record.case_id,
        expected=len(record.expected),
        scored=len(seen),
        correct=correct,
        unscored=unscored,
        mismatches=tuple(mismatches),
    )


def evaluate(results: Sequence[ArchiveCaseResult]) -> ArchiveMetrics:
    expected = sum(item.expected for item in results)
    scored = sum(item.scored for item in results)
    correct = sum(item.correct for item in results)
    return ArchiveMetrics(
        cases=len(results),
        expected=expected,
        scored=scored,
        correct=correct,
        unscored=sum(item.unscored for item in results),
        accuracy=correct / scored if scored else None,
        coverage=scored / expected if expected else None,
    )


def verdict_failures(metrics: ArchiveMetrics) -> list[str]:
    failures: list[str] = []
    if metrics.accuracy is None or metrics.accuracy < GAME_ACCURACY_MIN:
        failures.append(f"游戏归属准确率 {metrics.accuracy or 0.0:.3f} 低于阈值 {GAME_ACCURACY_MIN:.2f}")
    if metrics.coverage is None or metrics.coverage < COVERAGE_MIN:
        failures.append(f"可评档案行占比 {metrics.coverage or 0.0:.3f} 低于阈值 {COVERAGE_MIN:.2f}")
    return failures


def format_metrics(metrics: ArchiveMetrics) -> str:
    accuracy = f"{metrics.accuracy:.3f}" if metrics.accuracy is not None else "无可评行"
    coverage = f"{metrics.coverage:.3f}" if metrics.coverage is not None else "无期望"
    return "\n".join(
        [
            f"用例 {metrics.cases} 条，期望档案行 {metrics.expected} 行",
            f"游戏归属准确率：{accuracy}（{metrics.correct}/{metrics.scored}），阈值 {GAME_ACCURACY_MIN:.2f}",
            f"可评档案行占比：{coverage}（{metrics.scored}/{metrics.expected}），阈值 {COVERAGE_MIN:.2f}",
            f"不评的档案行（出自没有期望的台词行或同一行重复）：{metrics.unscored} 行",
        ]
    )


# ---------------------------------------------------------------------------
# dry 与 live
# ---------------------------------------------------------------------------


def _check_prompt(prepared: PreparedArchiveCase, expected_system: str) -> list[str]:
    """确认这一场的提示词由生产流程生成，且台词与游戏名单都带上了。"""
    record = prepared.record
    problems: list[str] = []
    if prepared.system_prompt != expected_system:
        problems.append(f"{record.case_id}：档案调用的系统提示词与生产模板渲染结果不一致")
    if ARCHIVE_INSTRUCTION not in prepared.user_prompt:
        problems.append(f"{record.case_id}：用户提示词缺少生产的写档案指令")
    for turn in record.speech:
        if turn.text not in prepared.user_prompt:
            problems.append(f"{record.case_id}：用户提示词缺少第 {turn.n} 行台词原文")
    if not record.games and NO_GAME_MARKER not in prepared.user_prompt:
        problems.append(f"{record.case_id}：本场没有游戏，用户提示词却没有说明")
    for index, game in enumerate(record.games, start=1):
        if f"#{index} {game}" not in prepared.user_prompt:
            problems.append(f"{record.case_id}：用户提示词的游戏名单缺 {game}")
    return problems


def _ideal_rows(record: ArchiveRecord) -> list[dict[str, Any]]:
    """把 expect 反推成「模型完全照做的输出」，用来过生产档案校验器。"""
    return [
        {"line": item.line, "title": f"第 {item.line} 行的事", "quote": item.quote_contains, "game": item.game}
        for item in record.expected
    ]


def _run_dry(args: argparse.Namespace, prompt_manager: PromptManager, records: list[ArchiveRecord]) -> int:
    print("== 档案挂游戏 dry 模式：不调模型，用生产提示词与生产档案校验器自检样本集 ==")
    problems: list[str] = []
    template_key = base.PROMPT_TEMPLATES["archive"]
    expected_system = prompt_manager.render(template_key)
    if base.MIN_CLAUSE_MARKER not in expected_system:
        problems.append(f"模板 {template_key} 缺少只输出 JSON 的约定")
    if not args.limit and len(records) < MIN_CASES:
        problems.append(f"{DATASET} 只有 {len(records)} 条用例，少于约定的 {MIN_CASES} 条")
    results: list[ArchiveCaseResult] = []
    for record in records:
        prepared = prepare_case(record, prompt_manager)
        problems.extend(_check_prompt(prepared, expected_system))
        written, missed = prepared.settler._validate_archive(_ideal_rows(record), prepared.material)
        problems.extend(f"{record.case_id}：期望的档案行没过生产校验（{item.reason}）" for item in missed)
        result = score_case(record, written)
        problems.extend(result.mismatches)
        results.append(result)
    metrics = evaluate(results)
    print(format_metrics(metrics))
    if metrics.scored != metrics.expected or metrics.correct != metrics.scored:
        problems.append("理想输出下准确率或可评占比不是 100%，样本、校验器与指标口径不一致")
    if args.out is not None:
        base._write_report(
            args.out,
            {
                "mode": "dry",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "metrics": asdict(metrics),
                "verdict": {"passed": not problems, "failures": problems},
            },
        )
    if problems:
        print(f"\nFAIL：样本自检发现 {len(problems)} 个问题")
        for problem in problems[:40]:
            print(f"  - {problem}")
        return 1
    print("\nPASS（dry）：样本、生产档案校验器与指标口径一致；本次未调用模型")
    return 0


def _run_live(args: argparse.Namespace, prompt_manager: PromptManager, records: list[ArchiveRecord]) -> int:
    try:
        config = base._resolve_live_config(args)
    except ValueError as exc:
        print(f"live 参数错误：{exc}", file=sys.stderr)
        return 2
    print(f"== 档案挂游戏 live 模式：真调模型 {config.model} ==")
    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_s)
    results: list[ArchiveCaseResult] = []
    case_reports: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        prepared = prepare_case(record, prompt_manager)
        started = time.perf_counter()
        raw_output = ""
        written: list[PendingArchiveLine] = []
        missed: list[UnregisteredItem] = []
        error: Optional[str] = None
        try:
            raw_output = base._call_model(
                client, config=config, system=prepared.system_prompt, user=prepared.user_prompt
            )
            rows = parse_json_object(raw_output).get("archive") or []
            if not isinstance(rows, list):
                raise ValueError("模型输出的 archive 不是列表")
            written, missed = prepared.settler._validate_archive(rows, prepared.material)
        except Exception as exc:  # noqa: BLE001 - 单条用例失败不中断整轮评测，计入报告
            error = f"{type(exc).__name__}: {exc}"
            errors.append(f"{record.case_id}: {error}")
        result = score_case(record, written)
        results.append(result)
        case_reports.append(
            {
                "case_id": record.case_id,
                "tags": list(record.tags),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "expected": [asdict(item) for item in record.expected],
                "written": [
                    {"line": item.evidence_chat_id, "title": item.text, "quote": item.quote, "game": item.game}
                    for item in written
                ],
                "mismatches": list(result.mismatches),
                "validator_rejections": [asdict(item) for item in missed],
                "model_output": base._clip_text(raw_output),
                "error": error,
            }
        )
        if args.verbose:
            print(f"  [{index}/{len(records)}] {record.case_id}：挂对 {result.correct}/{result.scored}")
    metrics = evaluate(results)
    failures = verdict_failures(metrics)
    if errors:
        failures.append(f"{len(errors)} 条用例调用或解析失败，结果不完整")
    base._write_report(
        args.out,
        {
            "mode": "live",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "thresholds": {"game_accuracy_min": GAME_ACCURACY_MIN, "coverage_min": COVERAGE_MIN},
            "model": {"model": config.model, "base_url": config.base_url, "temperature": config.temperature},
            "prompt": {"template": base.PROMPT_TEMPLATES["archive"]},
            "metrics": asdict(metrics),
            "verdict": {"passed": not failures, "failures": failures},
            "errors": errors,
            "cases": case_reports,
        },
    )
    print("\n" + format_metrics(metrics))
    for result in results:
        for mismatch in result.mismatches:
            print(f"  - {mismatch}")
    print(f"\n报告已写入 {args.out}")
    if not failures:
        print("\nPASS：两项阈值全部达标")
        return 0
    print("\nFAIL：")
    for failure in failures:
        print(f"  - {failure}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="下播整理「写档案」游戏归属离线评测器：默认 dry 自检样本，--live 真调模型按阈值判定",
        epilog="live 的模型连接参数与 run_eval.py 相同：命令行优先，其次环境变量 LLM_MODEL / LLM_API_KEY / LLM_BASE_URL。",
    )
    parser.add_argument("--live", action="store_true", help="真调模型；省略时只做 dry 自检")
    parser.add_argument("--limit", type=int, default=0, help="最多跑几条用例（0 = 全部；调试用）")
    parser.add_argument("--out", type=Path, default=None, help="JSON 报告路径；live 必填，dry 可选")
    parser.add_argument("--model", default=None, help="模型名；缺省读环境变量 LLM_MODEL")
    parser.add_argument("--api-key", default=None, help="API Key；缺省读环境变量 LLM_API_KEY（不要写进报告）")
    parser.add_argument("--base-url", default=None, help="OpenAI 兼容端点；缺省读环境变量 LLM_BASE_URL")
    parser.add_argument("--temperature", type=float, default=base.DEFAULT_TEMPERATURE, help="采样温度")
    parser.add_argument("--timeout", type=float, default=base.DEFAULT_TIMEOUT_S, help="单次调用超时秒数")
    parser.add_argument("--verbose", action="store_true", help="live 模式逐条打印用例结果")
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
    if args.live:
        return _run_live(args, prompt_manager, records)
    return _run_dry(args, prompt_manager, records)


if __name__ == "__main__":
    sys.exit(main())
