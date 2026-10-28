"""tests/modules 树归属守护：模块层测试不得 import src.agents，防测试树按历史而非源码归属再次分叉

归属规则与例外条款见 tests/AGENTS.md：测试树按被测源码包镜像归属（tests/agents 镜像
src/agents，tests/modules 镜像 src/modules）。Agent 框架与服务层测试可用具体 Agent 作
真实夹具，此类例外在 ALLOWED_AGENT_FIXTURE_FILES 逐项登记理由；名单条目指向的文件
删除或不再 import src.agents 后本测试同样红，防名单腐烂。
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
MODULES_TREE = TESTS / "modules"

# 允许 import src.agents 的例外文件（相对 tests/ 的路径）→ 理由
ALLOWED_AGENT_FIXTURE_FILES: dict[str, str] = {
    "modules/agents/test_companions.py": "Agent 框架 companions 组装测试，真实采集器作被组装夹具",
    "modules/agents/test_stop_cleanup.py": "Agent 框架停止链路测试，真实 MinecraftAgent 作被停对象",
    "modules/agents/test_delegation.py": "Agent 框架委派测试，真实 MinecraftAgent 仅作桩",
    "modules/agents/test_construction_path_parity.py": "工厂装配一致性测试，真实 Agent 验证构造路径",
    "modules/llm/test_capabilities.py": "LLM 能力校验协议测试，MinecraftHistoryCompactor 作真实消费者夹具",
    "modules/storage/test_live_session_store.py": "storage 写链测试，BackgroundMaintainer/RoomState 作真实写方夹具",
    "modules/dashboard/services/test_component_runtime.py": "组件运行时服务测试，Replyer 作重建样例组件",
}


def _imported_src_agents(path: Path) -> list[str]:
    """返回文件内引用的 src.agents 包名列表（import 与 from-import 都算）"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend(alias.name for alias in node.names if alias.name.startswith("src.agents"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src.agents"):
            hits.append(node.module)
    return hits


def test_modules_tree_tests_do_not_import_src_agents() -> None:
    violations: list[str] = []
    matched_allow_keys: set[str] = set()
    for path in sorted(MODULES_TREE.rglob("*.py")):
        rel = path.relative_to(TESTS).as_posix()
        if _imported_src_agents(path):
            if rel in ALLOWED_AGENT_FIXTURE_FILES:
                matched_allow_keys.add(rel)
            else:
                violations.append(rel)
    assert not violations, (
        "tests/modules 树发现 import src.agents 的测试文件：模块层测试只测 src/modules 组件，"
        "测 src/agents 的测试按归属规则放 tests/agents（见 tests/AGENTS.md）；"
        "确属框架/服务测试以具体 Agent 作夹具的，在 ALLOWED_AGENT_FIXTURE_FILES 逐项登记理由：\n"
        + "\n".join(f"  - {item}" for item in violations)
    )
    stale = set(ALLOWED_AGENT_FIXTURE_FILES) - matched_allow_keys
    assert not stale, "归属例外名单存在已失效条目（文件已删或不再 import src.agents），请清理：\n" + "\n".join(
        f"  - {item}" for item in sorted(stale)
    )
