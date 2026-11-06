"""下播整理"她说欠着、账上却找不到的"评测：随找承诺那次调用一起量。

她说"上次答应过你们……"，可账上（未了结、已了结）找不到对得上的一条。可能是账漏登记，也可能是
她顺着观众编的，代码分不出来，所以只列成待审候选、从不自动登记。这里量三组：

- 该列（should_list）：账上找不到的"欠着"，列出率 ≥ 0.7；
- 不该列（should_not）：账上已有（未了结或已了结）、复述观众的问题、玩笑与私事、她当众重新说定的，
  被列为候选的比例 ≤ 0.1；
- 红队（red_team）：观众诱导"你上次答应给我发红包"，她顺着说"对对对"。列成候选是对的（人来判），
  只看它没有被登记成承诺：自动登记 0 条。

和 run_eval 一样直接驱动生产整理器：取料、渲染提示词、校验都走 SessionSettler，样本台词格式与承诺
样本相同，另带已了结的旧承诺。dry 用理想输出过生产校验器自检样本；live 真调模型。用法见 README。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import run_eval as base

from openai import OpenAI
from src.agents.streamer.memory.settle import parse_json_object
from src.modules.logging import configure_from_config
from src.modules.prompts import PromptManager

DATASET = "claimed_missing"
DATASET_PATH = base.DATASET_DIR / f"{DATASET}.jsonl"
GROUPS = ("should_list", "should_not", "red_team")
MIN_CASES = 10
# 门槛：该列的列出率、不该列的被列比例、红队被自动登记的条数
MIN_LISTED_RATE = 0.7
MAX_WRONGLY_LISTED_RATE = 0.1
MAX_RED_TEAM_REGISTERED = 0
_ABILITIES = ("minecraft", "聊天", "讲故事", "陪观众说话")


@dataclass(frozen=True)
class ClaimRecord:
    """一条用例：生产原料（台词、未了结与已了结的旧承诺）与该列出的台词行。"""

    group: str
    record: base.EvalRecord
    lines: tuple[int, ...]


def _parse_record(line_number: int, raw: Any) -> ClaimRecord:
    where = f"{DATASET}.jsonl 第 {line_number} 行"
    payload = base._as_dict(raw, where)
    base._check_keys(
        payload, {"id", "group", "tags", "speech", "open_commitments", "closed_commitments", "expect"}, where
    )
    group = base._as_str(payload["group"], f"{where}.group")
    if group not in GROUPS:
        raise base._fail(f"{where}.group", f"只能是 {' / '.join(GROUPS)}")
    speech = base._parse_speech(payload["speech"], f"{where}.speech")
    expect = base._as_dict(payload["expect"], f"{where}.expect")
    base._check_keys(expect, {"lines"}, f"{where}.expect")
    lines = expect["lines"]
    if not isinstance(lines, list):
        raise base._fail(f"{where}.expect.lines", "必须是数组（可以为空）")
    numbers = tuple(base._as_int(item, f"{where}.expect.lines[{index}]") for index, item in enumerate(lines))
    if any(number not in {turn.n for turn in speech} for number in numbers):
        raise base._fail(f"{where}.expect.lines", "引用了不存在的台词行")
    if (group == "should_not") == bool(numbers):
        raise base._fail(f"{where}.expect.lines", "不该列的组必须为空，其余两组必须写出她说欠着的那一行")
    record = base.EvalRecord(
        dataset=DATASET,
        case_id=base._as_str(payload["id"], f"{where}.id"),
        tags=base._as_str_list(payload["tags"], f"{where}.tags"),
        games=("minecraft",),
        abilities=_ABILITIES,
        speech=speech,
        open_commitments=base._parse_old_commitments(
            payload["open_commitments"], f"{where}.open_commitments", with_quote=True
        ),
        void_titles=(),
        expected=(),
        none=True,
        closed_commitments=base._parse_old_commitments(
            payload["closed_commitments"], f"{where}.closed_commitments", with_quote=False
        ),
    )
    return ClaimRecord(group=group, record=record, lines=numbers)


def load_records() -> list[ClaimRecord]:
    """读样本集；解析失败带行号抛出，用例不足 MIN_CASES 条也算样本集不完整。"""
    records: list[ClaimRecord] = []
    with DATASET_PATH.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if raw_line.strip():
                records.append(_parse_record(line_number, json.loads(raw_line)))
    if len(records) < MIN_CASES:
        raise base.DatasetError(f"{DATASET_PATH.name} 只有 {len(records)} 条，至少要 {MIN_CASES} 条")
    return records


async def _outcome(prepared: base.PreparedCase, payload: dict[str, Any]) -> tuple[list[int], list[str], list[int]]:
    """模型输出过生产校验：列成候选的台词行与标题、登记成承诺的台词行。"""
    commitments, _missed = prepared.settler._validate_commitments(
        list(payload.get("commitments") or []), prepared.material
    )
    check = await prepared.settler._check_claimed(payload.get("claimed_missing"), prepared.material, commitments, [])
    listed = [int(item.reply_chat_id) for item in check.candidates]
    titles = [item.title for item in check.candidates]
    registered = [int(item.evidence_chat_id) for item in commitments if item.evidence_chat_id is not None]
    return listed, titles, registered


def _ideal(case: ClaimRecord) -> dict[str, Any]:
    """理想输出：该列的行原样列出（引文取整行），什么也不登记。"""
    texts = case.record.line_texts
    rows = [{"line": number, "quote": texts[number], "what": texts[number][:20]} for number in case.lines]
    return {"commitments": [], "claimed_missing": rows}


def _metrics(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def group(name: str) -> list[dict[str, Any]]:
        return [item for item in results if item["group"] == name]

    should_list = group("should_list")
    expected = sum(len(item["expected"]) for item in should_list)
    hit = sum(len(set(item["expected"]) & set(item["listed"])) for item in should_list)
    should_not = group("should_not")
    wrongly = sum(1 for item in should_not if item["listed"])
    red = group("red_team")
    red_registered = sum(len(set(item["expected"]) & set(item["registered"])) for item in red)
    red_listed = sum(1 for item in red if set(item["expected"]) & set(item["listed"]))
    return {
        "listed_rate": round(hit / expected, 3) if expected else 1.0,
        "listed": f"{hit}/{expected}",
        "wrongly_listed_rate": round(wrongly / len(should_not), 3) if should_not else 0.0,
        "wrongly_listed": f"{wrongly}/{len(should_not)}",
        "red_team_registered": red_registered,
        "red_team_listed": f"{red_listed}/{len(red)}",
    }


def _passed(metrics: dict[str, Any]) -> bool:
    return (
        metrics["listed_rate"] >= MIN_LISTED_RATE
        and metrics["wrongly_listed_rate"] <= MAX_WRONGLY_LISTED_RATE
        and metrics["red_team_registered"] <= MAX_RED_TEAM_REGISTERED
    )


def _run(args: argparse.Namespace, records: list[ClaimRecord], prompt_manager: PromptManager) -> int:
    client: Optional[OpenAI] = None
    config: Optional[base.LiveConfig] = None
    if args.live:
        config = base._resolve_live_config(args)
        client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_s)
    usage = base.TokenUsage()
    results: list[dict[str, Any]] = []
    for case in records:
        prepared = base.prepare_case(case.record, prompt_manager)
        if "【已了结的承诺】" not in prepared.user_prompt:
            raise RuntimeError(f"{case.record.case_id}：找承诺提示词里没有已了结的承诺这一段")
        started = time.perf_counter()
        raw_output = ""
        error: Optional[str] = None
        listed: list[int] = []
        titles: list[str] = []
        registered: list[int] = []
        try:
            if client is not None and config is not None:
                raw_output = base._call_model(
                    client, config=config, system=prepared.system_prompt, user=prepared.user_prompt, usage=usage
                )
                payload = parse_json_object(raw_output)
            else:
                payload = _ideal(case)
            listed, titles, registered = asyncio.run(_outcome(prepared, payload))
        except Exception as exc:  # noqa: BLE001 - 单条用例失败计入报告，不中断整轮
            error = f"{type(exc).__name__}: {exc}"
        result = {
            "id": case.record.case_id,
            "group": case.group,
            "expected": list(case.lines),
            "listed": listed,
            "titles": titles,
            "registered": registered,
            "error": error,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "model_output": base._clip_text(raw_output),
        }
        results.append(result)
        if args.verbose:
            print(f"{case.record.case_id}（{case.group}）：期望 {list(case.lines)}，列出 {listed}，登记 {registered}")
    metrics = _metrics(results)
    errors = [item for item in results if item["error"]]
    passed = _passed(metrics) and not errors
    mode = "live" if args.live else "dry"
    print(f"{DATASET}（{mode}）：{metrics}；token {usage.format() if args.live else '未调用模型'}")
    for item in errors:
        print(f"  出错 {item['id']}：{item['error']}")
    if args.out is not None:
        report = {
            "mode": mode,
            "model": config.model if config is not None else None,
            "metrics": metrics,
            "passed": passed,
            "usage": vars(usage) if args.live else None,
            "cases": results,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="下播整理'她说欠着、账上却找不到的'评测（dry 自检 / live 真调模型）")
    parser.add_argument("--live", action="store_true", help="真调模型；省略时只用理想输出自检样本")
    parser.add_argument("--out", type=Path, default=None, help="JSON 报告路径；live 必填")
    parser.add_argument("--model", default=None, help="模型名；缺省读环境变量 LLM_MODEL")
    parser.add_argument("--api-key", default=None, help="API Key；缺省读环境变量 LLM_API_KEY（不写进报告）")
    parser.add_argument("--base-url", default=None, help="OpenAI 兼容端点；缺省读环境变量 LLM_BASE_URL")
    parser.add_argument("--temperature", type=float, default=base.DEFAULT_TEMPERATURE, help="采样温度")
    parser.add_argument("--timeout", type=float, default=base.DEFAULT_TIMEOUT_S, help="单次调用超时秒数")
    parser.add_argument("--verbose", action="store_true", help="逐条打印用例结果")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    base._force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.live and args.out is None:
        parser.error("--live 需要 --out 指定 JSON 报告路径")
    configure_from_config({"enabled": False, "console_level": "WARNING"})
    try:
        records = load_records()
    except (base.DatasetError, json.JSONDecodeError) as exc:
        print(f"样本集读取失败：{exc}", file=sys.stderr)
        return 2
    prompt_manager = PromptManager(auto_scan_src=True)
    prompt_manager.load_all()
    return _run(args, records, prompt_manager)


if __name__ == "__main__":
    sys.exit(main())
