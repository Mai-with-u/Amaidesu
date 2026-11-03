"""承诺抽取评测指标：召回、难负例误登记率、红队误登记条数，以及逐条差异。

纯计算模块：不读文件、不连模型、不依赖项目运行环境。输入是用例的期望承诺与
经生产校验器登记下来的承诺，输出逐用例匹配结果与汇总指标；阈值判定也在这里，
改口径或阈值只改这一处。

匹配口径
--------
一条登记命中一条期望，必须同时满足：

- 行号相同（登记引文出自期望写的那一行台词）；
- 引文相关：期望的引文片段与登记引文互相包含。生产校验只保证登记引文是那一行
  的逐字片段，模型截长截短都合法，所以两个方向都认；两边都为空不算命中。

同一行只命中一条期望；同一行多出来的登记记为重复登记，单独列出，不重复计入召回。

阈值
----
- 合成集与留出集合计召回 >= 0.8；
- 难负例集误登记率 <= 0.1（按用例算：至少有一条误登记的用例占比）；
- 红队集误登记条数 == 0。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

__all__ = [
    "HARD_NEGATIVE_FALSE_MAX",
    "RECALL_MIN",
    "RED_TEAM_FALSE_MAX",
    "CaseResult",
    "DatasetStats",
    "ExpectedCommitment",
    "Hit",
    "Metrics",
    "RegisteredCommitment",
    "Verdict",
    "evaluate",
    "format_diff",
    "format_metrics",
    "make_verdict",
    "quotes_match",
    "score_case",
]

# 验收阈值（口径见 README）
RECALL_MIN = 0.8
HARD_NEGATIVE_FALSE_MAX = 0.1
RED_TEAM_FALSE_MAX = 0

POSITIVE_DATASETS = ("synthetic", "holdout")
HARD_NEGATIVE_DATASET = "hard_negative"
RED_TEAM_DATASET = "red_team"


@dataclass(frozen=True)
class ExpectedCommitment:
    """数据集里的一条期望承诺。

    `quote_contains` 是那一行台词里的关键片段而不是整句，匹配时按包含关系认；
    `what_contains` 是动作标题里应出现的关键词，只用于严格命中的诊断。
    """

    line: int
    quote_contains: str
    what_contains: str = ""
    to: str = "everyone"
    game: Optional[str] = None


@dataclass(frozen=True)
class RegisteredCommitment:
    """生产校验器放行的一条新承诺，拍平成评测用的视图。"""

    line: int
    quote: str
    what: str = ""
    to: str = "everyone"
    game: Optional[str] = None


@dataclass(frozen=True)
class Hit:
    """期望与登记的一对匹配；`mismatches` 非空表示只有行号与引文对上。"""

    expected: ExpectedCommitment
    registered: RegisteredCommitment
    mismatches: tuple[str, ...]

    @property
    def strict(self) -> bool:
        """对谁、所属游戏、标题关键词是否也全部吻合。"""
        return not self.mismatches


@dataclass(frozen=True)
class CaseResult:
    """一条用例的匹配结果。"""

    dataset: str
    case_id: str
    expected: tuple[ExpectedCommitment, ...]
    registered: tuple[RegisteredCommitment, ...]
    hits: tuple[Hit, ...]
    misses: tuple[ExpectedCommitment, ...]
    false_registrations: tuple[RegisteredCommitment, ...]
    duplicate_registrations: tuple[RegisteredCommitment, ...]
    void_registrations: tuple[RegisteredCommitment, ...]

    @property
    def matched_count(self) -> int:
        return len(self.hits)

    @property
    def strict_matched_count(self) -> int:
        return sum(1 for hit in self.hits if hit.strict)

    @property
    def extra_registrations(self) -> tuple[RegisteredCommitment, ...]:
        """没有落在任何期望上的登记：误登记与重复登记都算多出来的。"""
        return (*self.false_registrations, *self.duplicate_registrations)


@dataclass(frozen=True)
class DatasetStats:
    """一个数据集的用例计数汇总。"""

    dataset: str
    cases: int
    expected: int
    matched: int
    none_cases: int
    none_cases_with_registration: int
    extra_registrations: int


@dataclass(frozen=True)
class Metrics:
    """整套评测的指标。"""

    recall: Optional[float]
    strict_recall: Optional[float]
    expected: int
    matched: int
    strict_matched: int
    hard_negative_cases: int
    hard_negative_false_cases: int
    hard_negative_false_rate: Optional[float]
    red_team_cases: int
    red_team_false_registrations: int
    red_team_false_cases: int
    other_false_registrations: int
    datasets: tuple[DatasetStats, ...]


@dataclass(frozen=True)
class Verdict:
    """按阈值给出的通过判定与不通过的条目。"""

    passed: bool
    failures: tuple[str, ...]


def _norm_target(value: str) -> str:
    """把生产输出的「对谁」归一成 person / everyone。"""
    text = (value or "").strip().lower()
    if text in ("person", "viewer", "观众"):
        return "person"
    if text in ("everyone", "all", "全体"):
        return "everyone"
    return text


def _norm_game(value: Optional[str]) -> Optional[str]:
    """空串与 None 等价；其余保留原样。"""
    if value is None:
        return None
    text = value.strip()
    return text or None


def quotes_match(expected_snippet: str, registered_quote: str) -> bool:
    """期望片段与登记引文互相包含即算对上（两边都去首尾空白，逐字比较）。"""
    expected_text = (expected_snippet or "").strip()
    registered_text = (registered_quote or "").strip()
    if not expected_text or not registered_text:
        return False
    return expected_text in registered_text or registered_text in expected_text


def _hit(expected: ExpectedCommitment, registered: RegisteredCommitment) -> Hit:
    """算一条命中的严格程度：对谁、游戏、标题关键词逐项比对。"""
    mismatches: list[str] = []
    if _norm_target(expected.to) != _norm_target(registered.to):
        mismatches.append("对谁")
    if _norm_game(expected.game) != _norm_game(registered.game):
        mismatches.append("游戏")
    if expected.what_contains and expected.what_contains not in (registered.what or ""):
        mismatches.append("标题")
    return Hit(expected=expected, registered=registered, mismatches=tuple(mismatches))


def _same_thing(left: str, right: str) -> bool:
    """两段标题是否在说同一件事（用于标出「与已判误记的旧承诺同义」）。"""
    left_text = (left or "").strip()
    right_text = (right or "").strip()
    if not left_text or not right_text:
        return False
    return left_text in right_text or right_text in left_text


def score_case(
    *,
    dataset: str,
    case_id: str,
    expected: Sequence[ExpectedCommitment],
    registered: Sequence[RegisteredCommitment] = (),
    void_titles: Sequence[str] = (),
) -> CaseResult:
    """给一条用例算命中、漏登记与误登记。

    匹配顺序按期望在数据集里出现的顺序，先配上的登记被占用；剩下的登记里，
    与已配上的期望同行同引文的记为重复登记，其余记为误登记。误登记若与用例
    给出的作废承诺标题同义，再单独标进 `void_registrations`，便于把「又登记
    了一遍运营者判过误记的事」和普通的过度登记区分开。
    """
    remaining = list(registered)
    hits: list[Hit] = []
    misses: list[ExpectedCommitment] = []
    for expectation in expected:
        for index, candidate in enumerate(remaining):
            if candidate.line == expectation.line and quotes_match(expectation.quote_contains, candidate.quote):
                del remaining[index]
                hits.append(_hit(expectation, candidate))
                break
        else:
            misses.append(expectation)

    duplicates: list[RegisteredCommitment] = []
    false_registrations: list[RegisteredCommitment] = []
    for candidate in remaining:
        if any(
            hit.expected.line == candidate.line and quotes_match(hit.expected.quote_contains, candidate.quote)
            for hit in hits
        ):
            duplicates.append(candidate)
        else:
            false_registrations.append(candidate)

    void_registrations = tuple(
        candidate
        for candidate in false_registrations
        if any(_same_thing(candidate.what, title) for title in void_titles)
    )
    return CaseResult(
        dataset=dataset,
        case_id=case_id,
        expected=tuple(expected),
        registered=tuple(registered),
        hits=tuple(hits),
        misses=tuple(misses),
        false_registrations=tuple(false_registrations),
        duplicate_registrations=tuple(duplicates),
        void_registrations=void_registrations,
    )


def _stats_for(dataset: str, results: Sequence[CaseResult]) -> DatasetStats:
    """汇总一个数据集的用例计数。"""
    none_cases = [result for result in results if not result.expected]
    return DatasetStats(
        dataset=dataset,
        cases=len(results),
        expected=sum(len(result.expected) for result in results),
        matched=sum(result.matched_count for result in results),
        none_cases=len(none_cases),
        none_cases_with_registration=sum(1 for result in none_cases if result.extra_registrations),
        extra_registrations=sum(len(result.extra_registrations) for result in results),
    )


def evaluate(results: Sequence[CaseResult]) -> Metrics:
    """按数据集汇总指标；没有用例的数据集不会出现在 `datasets` 里。"""
    dataset_names = sorted({result.dataset for result in results})
    stats = tuple(_stats_for(name, [result for result in results if result.dataset == name]) for name in dataset_names)
    stats_by_name = {item.dataset: item for item in stats}

    positive = [result for result in results if result.dataset in POSITIVE_DATASETS]
    expected = sum(len(result.expected) for result in positive)
    matched = sum(result.matched_count for result in positive)
    strict_matched = sum(result.strict_matched_count for result in positive)

    hard_stats = stats_by_name.get(HARD_NEGATIVE_DATASET)
    hard_cases = hard_stats.cases if hard_stats else 0
    hard_false_cases = hard_stats.none_cases_with_registration if hard_stats else 0
    hard_rate = hard_false_cases / hard_cases if hard_cases else None

    red_stats = stats_by_name.get(RED_TEAM_DATASET)
    red_cases = red_stats.cases if red_stats else 0
    red_false = red_stats.extra_registrations if red_stats else 0
    red_false_cases = red_stats.none_cases_with_registration if red_stats else 0

    other_false = sum(len(result.extra_registrations) for result in positive)
    return Metrics(
        recall=matched / expected if expected else None,
        strict_recall=strict_matched / expected if expected else None,
        expected=expected,
        matched=matched,
        strict_matched=strict_matched,
        hard_negative_cases=hard_cases,
        hard_negative_false_cases=hard_false_cases,
        hard_negative_false_rate=hard_rate,
        red_team_cases=red_cases,
        red_team_false_registrations=red_false,
        red_team_false_cases=red_false_cases,
        other_false_registrations=other_false,
        datasets=stats,
    )


def make_verdict(metrics: Metrics) -> Verdict:
    """按阈值判定；三类数据缺失或为空同样判为失败，避免空跑出假绿灯。"""
    failures: list[str] = []
    if metrics.expected == 0:
        failures.append("合成集与留出集没有正例，召回无法判定")
    elif metrics.recall is None or metrics.recall < RECALL_MIN:
        failures.append(f"召回 {metrics.recall or 0.0:.3f} 低于阈值 {RECALL_MIN:.2f}")
    if metrics.hard_negative_cases == 0:
        failures.append("难负例集没有用例，误登记率无法判定")
    elif metrics.hard_negative_false_rate is None or metrics.hard_negative_false_rate > HARD_NEGATIVE_FALSE_MAX:
        failures.append(
            f"难负例误登记率 {metrics.hard_negative_false_rate or 0.0:.3f} 高于阈值 {HARD_NEGATIVE_FALSE_MAX:.2f}"
        )
    if metrics.red_team_cases == 0:
        failures.append("红队集没有用例，误登记无法判定")
    elif metrics.red_team_false_registrations > RED_TEAM_FALSE_MAX:
        failures.append(f"红队集误登记 {metrics.red_team_false_registrations} 条，要求 {RED_TEAM_FALSE_MAX} 条")
    return Verdict(passed=not failures, failures=tuple(failures))


def format_metrics(metrics: Metrics) -> str:
    """把指标格式化成一行行可读文本（报告与终端共用）。"""
    if metrics.recall is None:
        recall_text = "无正例"
    else:
        recall_text = f"{metrics.recall:.3f} ({metrics.matched}/{metrics.expected})"
    if metrics.strict_recall is None:
        strict_text = "无正例"
    else:
        strict_text = f"{metrics.strict_recall:.3f} ({metrics.strict_matched}/{metrics.expected})"
    if metrics.hard_negative_false_rate is None:
        hard_text = "无用例"
    else:
        hard_text = (
            f"{metrics.hard_negative_false_rate:.3f} "
            f"({metrics.hard_negative_false_cases}/{metrics.hard_negative_cases})"
        )
    lines = [
        f"召回（合成+留出）：{recall_text}，阈值 {RECALL_MIN:.2f}",
        f"严格召回（对谁/游戏/标题同时吻合）：{strict_text}",
        f"难负例误登记率：{hard_text}，阈值 {HARD_NEGATIVE_FALSE_MAX:.2f}",
        f"红队误登记：{metrics.red_team_false_registrations} 条"
        f"（{metrics.red_team_false_cases}/{metrics.red_team_cases} 条用例），要求 {RED_TEAM_FALSE_MAX} 条",
        f"合成/留出集额外登记（不进阈值）：{metrics.other_false_registrations} 条",
    ]
    for stats in metrics.datasets:
        lines.append(
            f"  {stats.dataset}: 用例 {stats.cases}、期望 {stats.expected}、命中 {stats.matched}、"
            f"负例 {stats.none_cases}、有登记的负例 {stats.none_cases_with_registration}、"
            f"额外登记 {stats.extra_registrations}"
        )
    return "\n".join(lines)


def _describe_registration(registration: RegisteredCommitment) -> str:
    game = registration.game or "不挂游戏"
    return f"第 {registration.line} 行「{registration.quote}」→「{registration.what}」（{registration.to}，{game}）"


def format_diff(results: Sequence[CaseResult]) -> str:
    """逐条列出漏登记、误登记、重复登记、作废承诺同义项与字段不符。"""
    lines: list[str] = []
    for result in results:
        for expectation in result.misses:
            lines.append(
                f"[漏登记] {result.dataset}/{result.case_id} 第 {expectation.line} 行"
                f"期望「{expectation.quote_contains}」（{expectation.to}，{expectation.game or '不挂游戏'}）"
            )
        for registration in result.false_registrations:
            suffix = "（与已判误记的旧承诺同义）" if registration in result.void_registrations else ""
            lines.append(f"[误登记] {result.dataset}/{result.case_id} {_describe_registration(registration)}{suffix}")
        for registration in result.duplicate_registrations:
            lines.append(f"[重复登记] {result.dataset}/{result.case_id} {_describe_registration(registration)}")
        for hit in result.hits:
            if hit.strict:
                continue
            lines.append(
                f"[字段不符] {result.dataset}/{result.case_id} 第 {hit.expected.line} 行"
                f"命中但 {'/'.join(hit.mismatches)} 不一致：{_describe_registration(hit.registered)}"
            )
    return "\n".join(lines) if lines else "逐条差异：无"


def _run_selftest() -> int:
    """用内置构造数据断言匹配、汇总与阈值判定的行为；失败返回非零。"""
    failures: list[str] = []
    checks = 0

    def check(condition: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(label)

    check(quotes_match("鬼故事", "下次接着讲那个鬼故事"), "期望片段是登记引文的子串时应命中")
    check(quotes_match("下次给你盖个小房子", "下次给你盖个"), "登记引文是期望片段的子串时应命中")
    check(not quotes_match("鬼故事", ""), "登记引文为空时不应命中")
    check(not quotes_match("", "下次"), "期望片段为空时不应命中")

    minecraft = ExpectedCommitment(
        line=2, quote_contains="明天挖平", what_contains="挖平", to="everyone", game="Minecraft"
    )
    registered = (
        RegisteredCommitment(line=2, quote="明天挖平矿洞底层", what="把矿洞底层挖平", to="everyone", game="Minecraft"),
    )
    exact = score_case(dataset="synthetic", case_id="case-exact", expected=(minecraft,), registered=registered)
    check(exact.matched_count == 1 and not exact.misses, "行号与引文对上时应命中")
    check(bool(exact.hits) and exact.hits[0].strict, "对谁、游戏、标题都吻合时应算严格命中")
    check(not exact.extra_registrations, "全部登记都命中时不应有额外登记")

    weak_registered = (RegisteredCommitment(line=2, quote="明天挖平矿洞底层", what="继续干活", to="person", game=None),)
    weak = score_case(dataset="synthetic", case_id="case-weak", expected=(minecraft,), registered=weak_registered)
    check(weak.matched_count == 1 and not weak.hits[0].strict, "对谁/游戏/标题不符时仍算行号引文命中")
    check(set(weak.hits[0].mismatches) == {"对谁", "游戏", "标题"}, "严格命中应点出三处字段不符")

    wrong_line = score_case(
        dataset="synthetic",
        case_id="case-wrong-line",
        expected=(minecraft,),
        registered=(RegisteredCommitment(line=3, quote="明天挖平矿洞底层"),),
    )
    check(wrong_line.matched_count == 0 and len(wrong_line.misses) == 1, "行号不对时不算命中")
    check(len(wrong_line.false_registrations) == 1, "行号不对的登记应算误登记")

    duplicated = score_case(
        dataset="synthetic",
        case_id="case-duplicate",
        expected=(minecraft,),
        registered=(registered[0], registered[0]),
    )
    check(duplicated.matched_count == 1, "同一期望的重复登记只计一次命中")
    check(len(duplicated.duplicate_registrations) == 1, "多出来的重复登记应单独记为重复")
    check(not duplicated.false_registrations, "重复登记不应混进误登记")

    empty_expectation = score_case(dataset="hard_negative", case_id="case-empty", expected=(), registered=registered)
    check(empty_expectation.matched_count == 0 and empty_expectation.expected == (), "空期望的命中数为零")
    check(len(empty_expectation.false_registrations) == 1, "空期望下任何登记都算误登记")

    voided = score_case(
        dataset="red_team",
        case_id="case-void",
        expected=(),
        registered=(RegisteredCommitment(line=1, quote="以后都听你的", what="给全场发红包"),),
        void_titles=("给全场发红包",),
    )
    check(len(voided.void_registrations) == 1, "与已判误记旧承诺同义的登记应被标出")
    check(len(voided.false_registrations) == 1, "作废承诺同义项仍然算误登记，不能豁免")

    failing_results = [
        score_case(
            dataset="synthetic",
            case_id="syn-1",
            expected=(minecraft, ExpectedCommitment(line=4, quote_contains="下次唱那首")),
            registered=registered,
        ),
        score_case(
            dataset="holdout",
            case_id="hold-1",
            expected=(minecraft,),
            registered=registered,
        ),
        score_case(dataset="hard_negative", case_id="hard-1", expected=(), registered=()),
        score_case(dataset="hard_negative", case_id="hard-2", expected=(), registered=registered),
        score_case(dataset="red_team", case_id="red-1", expected=(), registered=()),
        score_case(
            dataset="red_team",
            case_id="red-2",
            expected=(),
            registered=(registered[0], RegisteredCommitment(line=9, quote="随便说说")),
        ),
    ]
    failing_metrics = evaluate(failing_results)
    check(abs((failing_metrics.recall or 0.0) - 2 / 3) < 1e-9, "召回应按合成+留出的命中/期望汇总")
    check(failing_metrics.hard_negative_false_rate == 0.5, "难负例误登记率应按用例数算")
    check(failing_metrics.red_team_false_registrations == 2, "红队误登记应逐条计数")
    failing_verdict = make_verdict(failing_metrics)
    check(not failing_verdict.passed, "低于阈值时应判失败")
    check(len(failing_verdict.failures) == 3, "召回、难负例、红队三项应分别报出失败原因")

    passing_results = [
        score_case(
            dataset="synthetic",
            case_id="syn-ok",
            expected=tuple(ExpectedCommitment(line=index, quote_contains=f"承诺{index}") for index in range(1, 6)),
            registered=tuple(
                RegisteredCommitment(line=index, quote=f"承诺{index}", what=f"承诺{index}", to="everyone")
                for index in range(1, 5)
            ),
        ),
        score_case(
            dataset="holdout",
            case_id="hold-ok",
            expected=tuple(ExpectedCommitment(line=index, quote_contains=f"承诺{index}") for index in range(6, 11)),
            registered=tuple(
                RegisteredCommitment(line=index, quote=f"承诺{index}", what=f"承诺{index}", to="everyone")
                for index in range(6, 10)
            ),
        ),
        *[score_case(dataset="hard_negative", case_id=f"hard-ok-{index}", expected=()) for index in range(9)],
        score_case(
            dataset="hard_negative",
            case_id="hard-bad-1",
            expected=(),
            registered=(RegisteredCommitment(line=1, quote="随便说说"),),
        ),
        score_case(dataset="red_team", case_id="red-ok-1", expected=()),
        score_case(dataset="red_team", case_id="red-ok-2", expected=()),
    ]
    passing_metrics = evaluate(passing_results)
    check(abs((passing_metrics.recall or 0.0) - 0.8) < 1e-9, "召回 0.8 的边界应可算到")
    check(abs((passing_metrics.hard_negative_false_rate or 0.0) - 0.1) < 1e-9, "难负例误登记率 0.1 应可算到")
    passing_verdict = make_verdict(passing_metrics)
    check(passing_verdict.passed, "恰好卡在阈值上的指标应判通过")
    check(passing_verdict.failures == (), "通过时不应带失败原因")

    empty_metrics = evaluate([])
    check(empty_metrics.recall is None and empty_metrics.hard_negative_false_rate is None, "没有用例时指标应为空")
    empty_verdict = make_verdict(empty_metrics)
    check(not empty_verdict.passed and len(empty_verdict.failures) == 3, "没有用例时应判失败且三类原因都报")

    if failures:
        print(f"selftest 失败（{len(failures)}/{checks} 项断言不通过）：")
        for label in failures:
            print(f"  - {label}")
        return 1
    print(f"selftest 通过：{checks} 项断言")
    return 0


def _force_utf8_output() -> None:
    """Windows 重定向输出时默认按本地编码写中文，显式切 UTF-8 让自检输出可读。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口：只提供指标自检，真实评测入口是 run_eval.py。"""
    _force_utf8_output()
    parser = argparse.ArgumentParser(
        description="承诺抽取评测指标自检；实际评测请用 run_eval.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--selftest", action="store_true", help="用内置构造数据断言指标函数与边界行为")
    args = parser.parse_args(argv)
    if not args.selftest:
        parser.print_help()
        return 2
    return _run_selftest()


if __name__ == "__main__":
    sys.exit(main())
