"""主播表达引擎「谁说的、谁做的」评测：台词有没有把玩家的话、玩家的举动安到别人头上。

第 58 场出过三种错：玩家扔来铁锭，交接稿写成"凭空到手"，她说"这铁锭哪来的，天上掉的"；
玩家在游戏公屏说"我勒个"，她冲上一句刚点过名的女仆说"小百合你'我勒个'啥呀"；玩家说她
"自家地板打洞"，她反过来劝玩家"下回别在菜地里打洞了"。这里把这几种情形写成合成用例，
用生产的 Replyer 渲染输入、真调模型出台词，再按用例的禁用说法判对错。

每条用例给出聊天记录（她的台词、游戏条目、游戏聊天、玩家冲她做的动作、观众弹幕）、本窗
新到的游戏聊天和交接稿。玩家说的话与动作经生产的游戏账本入账、出账，得到交给表达侧的
"游戏里最近找你的人"与回应对象，和线上同一条路径。

量的是误归因率：台词命中用例禁用说法（安错人、说成来路不明、把话里的事安到说话人头上）的
次数 / 总次数。每条用例按 --samples 重复采样。阈值见 README。

dry（默认）不调模型：只渲染每条用例交给模型的输入，检查样本能被生产代码完整渲染；
live（--live）真调模型。用法见 README。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

# 独立脚本由 uv 从任意目录启动，sys.path[0] 是脚本目录：先把仓库根与记忆评测目录插进去
# （连接参数、用量统计与记忆评测共用），下面的 import 必然不在文件顶部，逐行豁免 E402。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _path in (PROJECT_ROOT, PROJECT_ROOT / "scripts" / "memory_eval"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from openai import OpenAI  # noqa: E402
from src.agents.minecraft.player_body_language import spoken_line  # noqa: E402
from src.agents.streamer.game_ledger import GameLedger, keep_recent_notes, sole_speaker_name  # noqa: E402
from src.agents.streamer.plan import DecisionPlan  # noqa: E402
from src.agents.streamer.replyer import Replyer  # noqa: E402
from src.agents.streamer.timeline import PlayerNote, TimelineTurn, note_turn  # noqa: E402
from src.modules.llm.payload import Response  # noqa: E402
from src.modules.logging import configure_from_config  # noqa: E402
from src.modules.prompts import PromptManager  # noqa: E402

import run_eval as base  # noqa: E402

DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "attribution.jsonl"
GROUPS = ("gift", "speaker", "about_her", "control")
MIN_CASES = 10
# 门槛：误归因率（命中禁用说法的台词 / 全部台词）
MAX_MISATTRIBUTION_RATE = 0.1
# 生产 replyer 档位的温度
DEFAULT_TEMPERATURE = 0.2
_GAME = "minecraft"
_ACTION_LABEL = f"遭遇·{_GAME}·player_body_language"
_HISTORY_KINDS = ("speech", "game", "chat", "action", "viewer")


@dataclass(frozen=True)
class AttributionCase:
    """一条用例：聊天记录、交接稿与禁用说法。"""

    case_id: str
    group: str
    note: str
    now: str
    history: tuple[dict[str, Any], ...]
    topic_summary: str
    reply_guidance: str
    forbid: tuple[str, ...]


def _parse_case(line_number: int, raw: dict[str, Any]) -> AttributionCase:
    where = f"{DATASET_PATH.name} 第 {line_number} 行"
    group = str(raw.get("group") or "")
    if group not in GROUPS:
        raise base.DatasetError(f"{where}：group 只能是 {GROUPS}，当前 {group!r}")
    history = tuple(raw.get("history") or ())
    for item in history:
        if item.get("kind") not in _HISTORY_KINDS:
            raise base.DatasetError(f"{where}：聊天记录的 kind 只能是 {_HISTORY_KINDS}，当前 {item.get('kind')!r}")
        if item.get("kind") in ("chat", "action", "game") and not item.get("clock"):
            raise base.DatasetError(f"{where}：游戏条目缺 clock")
    forbid = tuple(str(pattern) for pattern in raw.get("forbid") or ())
    if not forbid:
        raise base.DatasetError(f"{where}：forbid 不能为空")
    for pattern in forbid:
        re.compile(pattern)
    plan = raw.get("plan") or {}
    return AttributionCase(
        case_id=str(raw["id"]),
        group=group,
        note=str(raw.get("note") or ""),
        now=str(raw["now"]),
        history=history,
        topic_summary=str(plan.get("topic_summary") or ""),
        reply_guidance=str(plan.get("reply_guidance") or ""),
        forbid=forbid,
    )


def load_cases() -> list[AttributionCase]:
    """读样本集；解析失败带行号抛出，用例不足 MIN_CASES 条也算样本集不完整。"""
    cases: list[AttributionCase] = []
    with DATASET_PATH.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if raw_line.strip():
                cases.append(_parse_case(line_number, json.loads(raw_line)))
    if len(cases) < MIN_CASES:
        raise base.DatasetError(f"{DATASET_PATH.name} 只有 {len(cases)} 条，至少要 {MIN_CASES} 条")
    return cases


@dataclass(frozen=True)
class PreparedCase:
    """交给生产 Replyer 的一轮材料。"""

    history: list[TimelineTurn]
    game_chat: list[str]
    people: tuple[PlayerNote, ...]
    reply_to: str


def _history_turn(item: dict[str, Any]) -> TimelineTurn:
    """一条样本记录 → 与线上时间线同形的条目（游戏条目的标签、行文与生产入账一致）。"""
    kind = item["kind"]
    text = str(item.get("text") or "")
    if kind == "speech":
        return TimelineTurn(role="assistant", content=text)
    if kind == "viewer":
        return TimelineTurn(role="user", content=text, sender_name=str(item["who"]), message_id=str(item.get("id", "")))
    at_ms = base._clock_to_ms(str(item["clock"]))
    if kind == "chat":
        return note_turn(_GAME, f"玩家 {item['who']}：{text}", at_ms)
    if kind == "action":
        return note_turn(_ACTION_LABEL, f"{spoken_line(text)}（已结束）", at_ms)
    return note_turn(str(item["label"]), text, at_ms)


def _people(case: AttributionCase) -> tuple[tuple[PlayerNote, ...], str]:
    """玩家的话与动作经生产账本入账、出账：较早的与本窗新到的分两窗，按线上同一套规则留最近几条。"""
    earlier, fresh = GameLedger(), GameLedger()
    for item in case.history:
        kind = item["kind"]
        if kind not in ("chat", "action"):
            continue
        ledger = fresh if item.get("fresh") else earlier
        at_ms = base._clock_to_ms(str(item["clock"]))
        who = str(item["who"])
        if kind == "chat":
            ledger.record_chat(_GAME, f"玩家 {who}：{item['text']}", at_ms, speaker=who, who=who)
        else:
            ledger.record_encounter(
                _GAME, "player_body_language", spoken_line(str(item["text"])), True, at_ms, player=who, who=who
            )
    fresh_notes = fresh.drain().player_notes
    kept = keep_recent_notes(earlier.drain().player_notes, (), base._clock_to_ms(case.now))
    return keep_recent_notes(kept, fresh_notes, base._clock_to_ms(case.now)), sole_speaker_name(fresh_notes)


def prepare(case: AttributionCase) -> PreparedCase:
    history = [_history_turn(item) for item in case.history]
    game_chat = [_history_turn(item).content for item in case.history if item.get("fresh")]
    people, reply_to = _people(case)
    return PreparedCase(history=history, game_chat=game_chat, people=people, reply_to=reply_to)


class _OpenAIService:
    """把 Replyer 的 LLM 调用转给 OpenAI 兼容端点（只实现 Replyer 用到的 generate）。"""

    def __init__(self, client: OpenAI, config: base.LiveConfig, usage: base.TokenUsage) -> None:
        self._client = client
        self._config = config
        self._usage = usage
        self.last_input = ""

    async def generate(self, messages: list[dict[str, Any]], *, system: str, **_: Any) -> Response:
        self.last_input = str(messages[-1].get("content") or "")
        response = await asyncio.to_thread(
            self._client.chat.completions.create,
            model=self._config.model,
            messages=[{"role": "system", "content": system}, *messages],
            temperature=self._config.temperature,
        )
        self._usage.add(response.usage)
        return Response(success=True, content=response.choices[0].message.content or "")


class _CaptureService:
    """dry 模式：只记下交给模型的输入，返回一句占位台词。"""

    def __init__(self) -> None:
        self.last_input = ""

    async def generate(self, messages: list[dict[str, Any]], *, system: str, **_: Any) -> Response:
        del system
        self.last_input = str(messages[-1].get("content") or "")
        return Response(success=True, content="（dry 占位台词）")


async def _speak(replyer: Replyer, case: AttributionCase, prepared: PreparedCase) -> str:
    plan = DecisionPlan(
        should_reply=True, target=None, topic_summary=case.topic_summary, reply_guidance=case.reply_guidance
    )
    result = await replyer.generate(
        plan=plan,
        batch=[],
        history=prepared.history,
        game_chat=prepared.game_chat,
        game_people=prepared.people,
        reply_to=prepared.reply_to,
    )
    return str((result or {}).get("speech") or "")


def _violations(case: AttributionCase, speech: str) -> list[str]:
    return [pattern for pattern in case.forbid if re.search(pattern, speech)]


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [sample for item in results for sample in item["samples"]]
    wrong = [sample for sample in samples if sample["violations"]]
    by_group: dict[str, str] = {}
    for group in GROUPS:
        group_samples = [sample for item in results if item["group"] == group for sample in item["samples"]]
        group_wrong = [sample for sample in group_samples if sample["violations"]]
        by_group[group] = f"{len(group_wrong)}/{len(group_samples)}"
    return {
        "samples": len(samples),
        "misattributed": len(wrong),
        "misattribution_rate": round(len(wrong) / len(samples), 3) if samples else 0.0,
        "by_group": by_group,
    }


def _run(args: argparse.Namespace, cases: list[AttributionCase], prompt_manager: PromptManager) -> int:
    usage = base.TokenUsage()
    config: Optional[base.LiveConfig] = None
    service: Any
    if args.live:
        config = base._resolve_live_config(args)
        client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_s)
        service = _OpenAIService(client, config, usage)
    else:
        service = _CaptureService()
    replyer = Replyer(config={}, llm_service=service, prompt_service=prompt_manager)
    samples_per_case = args.samples if args.live else 1
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for case in cases:
        prepared = prepare(case)
        samples: list[dict[str, Any]] = []
        for _ in range(samples_per_case):
            started = time.perf_counter()
            try:
                speech = asyncio.run(_speak(replyer, case, prepared))
            except Exception as exc:  # noqa: BLE001 - 单次采样失败计入报告，不中断整轮
                errors.append(f"{case.case_id}: {type(exc).__name__}: {exc}")
                continue
            violations = _violations(case, speech) if args.live else []
            samples.append(
                {
                    "speech": speech,
                    "violations": violations,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                }
            )
            if args.verbose:
                mark = "错" if violations else "对"
                print(
                    f"{case.case_id}（{case.group}）[{mark}] {speech}" + (f"  命中 {violations}" if violations else "")
                )
        results.append(
            {
                "id": case.case_id,
                "group": case.group,
                "note": case.note,
                "people": [note.line for note in prepared.people],
                "reply_to": prepared.reply_to,
                "input": service.last_input if args.show_input else "",
                "samples": samples,
            }
        )
    metrics = _metrics(results)
    mode = "live" if args.live else "dry"
    passed = not errors and (not args.live or metrics["misattribution_rate"] <= MAX_MISATTRIBUTION_RATE)
    print(f"attribution（{mode}）：{metrics}；{usage.format() if args.live else '未调用模型'}")
    for error in errors:
        print(f"  出错 {error}")
    if args.out is not None:
        report = {
            "mode": mode,
            "model": config.model if config is not None else None,
            "temperature": config.temperature if config is not None else None,
            "metrics": metrics,
            "passed": passed,
            "usage": vars(usage) if args.live else None,
            "errors": errors,
            "cases": results,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="主播表达引擎「谁说的、谁做的」评测（dry 渲染自检 / live 真调模型）")
    parser.add_argument("--live", action="store_true", help="真调模型；省略时只渲染每条用例的输入")
    parser.add_argument("--samples", type=int, default=3, help="live 时每条用例采样几次（默认 3）")
    parser.add_argument("--out", type=Path, default=None, help="JSON 报告路径；live 必填")
    parser.add_argument("--show-input", action="store_true", help="报告里附上每条用例交给模型的输入")
    parser.add_argument("--model", default=None, help="模型名；缺省读环境变量 LLM_MODEL")
    parser.add_argument("--api-key", default=None, help="API Key；缺省读环境变量 LLM_API_KEY（不写进报告）")
    parser.add_argument("--base-url", default=None, help="OpenAI 兼容端点；缺省读环境变量 LLM_BASE_URL")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help="采样温度（默认同生产 0.2）")
    parser.add_argument("--timeout", type=float, default=base.DEFAULT_TIMEOUT_S, help="单次调用超时秒数")
    parser.add_argument("--verbose", action="store_true", help="逐条打印台词")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    base._force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.live and args.out is None:
        parser.error("--live 需要 --out 指定 JSON 报告路径")
    if args.samples < 1:
        parser.error("--samples 至少为 1")
    configure_from_config({"enabled": False, "console_level": "WARNING"})
    try:
        cases = load_cases()
    except (base.DatasetError, json.JSONDecodeError, re.error) as exc:
        print(f"样本集读取失败：{exc}", file=sys.stderr)
        return 2
    prompt_manager = PromptManager(auto_scan_src=True)
    prompt_manager.load_all()
    return _run(args, cases, prompt_manager)


if __name__ == "__main__":
    sys.exit(main())
