"""只读真实库的评测脚本自检：人工改动统计、语义门槛核对、杂谈体检、LongMemEval 跨场召回。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_selftest(script_name: str) -> None:
    """跑一个评测脚本的 --selftest，失败时把输出带进断言消息。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / script_name
    completed = subprocess.run(
        [sys.executable, str(script), "--selftest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]


def test_review_diff_script_self_check_passes() -> None:
    """scripts/memory_eval/review_diff.py 读 memory_edits 的 action 名：审核仓储改了动作名或表结构而
    脚本没跟上，统计出的"标题被改的比例"就成了零，提示词迭代看的指标会悄悄失真。"""
    _run_selftest("review_diff.py")


def test_retrieval_threshold_script_self_check_passes() -> None:
    """scripts/memory_eval/retrieval.py 读库里存下的向量核对语义门槛：向量表或证据列改了而脚本没跟上，
    原话命中与误推率就配错了对，校准出来的门槛不可信。"""
    _run_selftest("retrieval.py")


def test_zatsudan_eval_script_self_check_passes() -> None:
    """scripts/zatsudan_eval.py 读 live_chat / rundown_transitions / llm_requests 出杂谈体检表：
    表结构或列名改了而脚本没跟上，钩子率、空转次数这些成对指标就会悄悄算错。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "zatsudan_eval.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--selftest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]


def test_longmemeval_script_self_check_passes() -> None:
    """scripts/memory_eval/longmemeval.py 用生产检索链路量跨场召回：灌库、索引或判定口径改了而脚本
    没跟上，报出来的召回率就跟实际检索行为对不上，据此做的判断会错。"""
    _run_selftest("longmemeval.py")
