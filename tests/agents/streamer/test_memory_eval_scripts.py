"""只读真实库的评测脚本自检：人工改动统计、语义门槛核对。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_review_diff_script_self_check_passes() -> None:
    """scripts/memory_eval/review_diff.py 读 memory_edits 的 action 名：审核仓储改了动作名或表结构而
    脚本没跟上，统计出的"标题被改的比例"就成了零，提示词迭代看的指标会悄悄失真。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / "review_diff.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--selftest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]


def test_retrieval_threshold_script_self_check_passes() -> None:
    """scripts/memory_eval/retrieval.py 读库里存下的向量核对语义门槛：向量表或证据列改了而脚本没跟上，
    原话命中与误推率就配错了对，校准出来的门槛不可信。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / "retrieval.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--selftest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]


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
