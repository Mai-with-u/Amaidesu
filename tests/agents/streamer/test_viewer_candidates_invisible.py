"""观众候选批准前对她完全不可见：简报、线索卡、回想、检索索引都不读候选表。

候选是观众的话，人批准了才变成档案行或承诺；在那之前，主播这一侧除了下播整理（写进待审区）
之外的任何代码都不得引用候选表。这里按源码扫：出现表名即失败，免得哪天有人图省事把待审的
设定直接拼进简报或线索卡，让观众能往她的记忆里塞东西。
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
# 只有整理侧写候选（settle 把核过的候选交给存储层写进待审区）
_WRITERS = {"settle.py", "settle_candidates.py"}
# 存储层里她读记忆走的那几个仓储：档案行与快照、承诺、检索索引、线索卡记录
_READ_REPOS = ("archive.py", "commitments.py", "memory_index.py", "memory_cue_log.py")


def test_streamer_code_outside_settlement_never_reads_candidates() -> None:
    offenders = [
        str(path.relative_to(_ROOT))
        for path in sorted((_ROOT / "src" / "agents" / "streamer").rglob("*.py"))
        if path.name not in _WRITERS and "viewer_candidates" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"主播侧引用了观众候选表（批准前必须对她不可见）: {offenders}"


def test_memory_read_repos_never_read_candidates() -> None:
    repos = _ROOT / "src" / "modules" / "storage" / "repos"
    offenders = [name for name in _READ_REPOS if "viewer_candidates" in (repos / name).read_text(encoding="utf-8")]
    assert offenders == [], f"她读记忆的仓储引用了观众候选表: {offenders}"
