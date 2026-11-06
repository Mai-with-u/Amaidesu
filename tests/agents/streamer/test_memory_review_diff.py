"""人工改动统计脚本：整理写的行、改标题、删行、放回、人新增的口径。"""

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
