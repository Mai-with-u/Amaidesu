"""日志隔离契约测试。

门面（ModuleLogger）之外的业务代码不得使用 stdlib 风格异常参数，
也不得直接导入 loguru——loguru 细节收拢在 src/modules/logging/ 内。
"""

import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
LOGGING_DIR = SRC_ROOT / "modules" / "logging"

# 允许使用 loguru 的目录（日志基础设施本体）
LOGURU_ALLOWED_DIRS = (LOGGING_DIR,)


def _src_python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def test_no_stdlib_style_exc_info_in_src():
    """src/ 内不得出现 exc_info：该写法对 loguru 静默无效，应走门面 exc 参数。"""
    offenders = [p for p in _src_python_files() if "exc_info" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"src/ 内出现 exc_info 残留（应迁移到 ModuleLogger 的 exc 参数）：{offenders}"


def test_loguru_import_confined_to_logging_module():
    """loguru 只允许在 src/modules/logging/ 内导入。"""
    import_pattern = re.compile(r"^\s*(?:from loguru import|import loguru)\b", re.MULTILINE)
    offenders = [
        p
        for p in _src_python_files()
        if p.resolve().parent not in LOGURU_ALLOWED_DIRS and import_pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"loguru 导入泄漏到日志模块之外：{offenders}"
