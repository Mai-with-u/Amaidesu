"""测试代码 patch 私有成员棘轮守护：tests/ 下指向前导下划线成员的 patch 只减不增

项目纪律（tests/AGENTS.md）：需要 patch 私有成员（前导下划线）才能搭测试，视为生产模块
缺接缝的缺陷，应上报修接缝而非绕过。历史存量约 30+ 处，本守护做棘轮——存量只允许减少
不允许新增：

- 拦截的调用形态（AST 识别）：
  1. ``unittest.mock.patch("pkg.mod._attr", ...)``——字符串目标末段以下划线开头，
     含模块级私有变量（如 ``setattr(module, "_CLIENT_DISPATCH", ...)`` 同类）；
  2. ``patch.object(obj, "_attr", ...)``——属性名（位置或关键字）以下划线开头；
  3. ``monkeypatch.setattr(target, "_attr", ...)``——target 可为对象表达式或
     "pkg.mod" 字符串路径，属性名为第二位置参数或 ``name=`` 关键字；
  4. ``monkeypatch.setattr("pkg.mod._attr", value)``——单字符串形态，末段私有。
- setattr 到非下划线名不算违规；属性名来自变量（运行期才可解析）无法静态判定，
  不在守护范围。
- ALLOWED_PRIVATE_PATCHES 为双向棘轮：新违规不在名单内 → 红；名单条目对应的
  patch 调用已消失（他线清理了存量）→ 同样红，防止名单腐烂。
- 名单按当前 dev 分支持有存量建立；并行清理线（LLM / Minecraft 等）合并时清掉了
  存量导致条目失效，由合并者删除对应条目即可转绿——这是预期协作方式。
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

# 允许名单："相对路径:行号 → 棘轮键（被 patch 目标的规范表示）→ 理由"
# 行号随代码漂移会使条目失效（本测试红），届时按新位置更新或随存量清理删除。
# 名单为当前 dev 分支持有的存量底账；并行清理线（LLM / Minecraft 等）清掉对应存量后，
# 条目会因"调用消失"而红，由合并者删除条目转绿——预期协作方式。
ALLOWED_PRIVATE_PATCHES: dict[str, str] = {
    # agents/test_minecraft_attention_collector.py：采集器内部就绪检查，无公有接缝
    "agents/test_minecraft_attention_collector.py:448": "collector._ensure_ready",
    # agents/text_adv/test_input.py：键鼠输入原语为 ctypes 私有函数，公有入口难以逐键注入
    "agents/text_adv/test_input.py:97": "input_mod._send_key",
    "agents/text_adv/test_input.py:104": "input_mod._send_unicode_char",
    "agents/text_adv/test_input.py:116": "input_mod._send_key",
    "agents/text_adv/test_input.py:134": "input_mod._move_cursor",
    "agents/text_adv/test_input.py:135": "input_mod._send_mouse_button",
    # dashboard/test_system_api_v2.py：系统信息装配的内部聚合项与 changelog 路径常量
    "dashboard/test_system_api_v2.py:213": "system_api._build_component_items",
    "dashboard/test_system_api_v2.py:232": "system_api._CHANGELOG_PATH",
    # integration/*_wiring.py：入口模块的模块级路径常量，接线测试需重定向工作目录
    "integration/test_dashboard_wiring.py:48": "main._BASE_DIR",
    "integration/test_simulator_wiring.py:69": "main._BASE_DIR",
    # modules/collectors/test_collector_framework.py：stdin 交互检测私有判定
    "modules/collectors/test_collector_framework.py:191": "console_mod._stdin_interactive",
    "modules/collectors/test_collector_framework.py:211": "console_mod._stdin_interactive",
    "modules/collectors/test_collector_framework.py:230": "console_mod._stdin_interactive",
    # modules/dashboard/test_avatar_assets_route.py：字符串路径形式 patch 服务器单例持有者
    "modules/dashboard/test_avatar_assets_route.py:48": "src.modules.dashboard.dependencies._dashboard_server",
    "modules/dashboard/test_avatar_assets_route.py:108": "src.modules.dashboard.dependencies._dashboard_server",
    # modules/llm/test_context_meter.py：tiktoken 编码器懒加载缓存
    "modules/llm/test_context_meter.py:39": "cm._get_encoding",
    # modules/subtitle/test_outline_label_render.py：emoji 字体懒加载
    "modules/subtitle/test_outline_label_render.py:161": "controlled._load_emoji_font",
    # modules/test_windows_dpi.py：ctypes DLL 句柄，DPI 检测无法真实调用系统 API
    "modules/test_windows_dpi.py:64": "windows_dpi._user32",
    "modules/test_windows_dpi.py:65": "windows_dpi._shcore",
    "modules/test_windows_dpi.py:120": "windows_dpi._user32",
    "modules/test_windows_dpi.py:121": "windows_dpi._shcore",
    # modules/tts/test_engine_contract.py：edge-tts 私有四步生成流程
    "modules/tts/test_engine_contract.py:69": "provider._edge_tts_synthesize",
    "modules/tts/test_engine_contract.py:84": "provider._create_generation",
    "modules/tts/test_engine_contract.py:86": "provider._wait_for_completion",
    "modules/tts/test_engine_contract.py:87": "provider._download_audio",
    "modules/tts/test_engine_contract.py:249": "provider._edge_tts_synthesize",
    # modules/vision/test_mss_capture.py：mss 依赖与导入错误占位的注入点
    "modules/vision/test_mss_capture.py:49": "mss_capture_module._mss",
    "modules/vision/test_mss_capture.py:72": "mss_capture_module._mss",
    "modules/vision/test_mss_capture.py:78": "mss_capture_module._mss",
    "modules/vision/test_mss_capture.py:145": "mss_capture_module._mss",
    "modules/vision/test_mss_capture.py:146": "mss_capture_module._MSS_IMPORT_ERROR",
    "modules/vision/test_mss_capture.py:218": "mss_capture_module._per_monitor_dpi_thread",
}


class _PrivatePatchFinder(ast.NodeVisitor):
    """收集 patch 调用中目标含前导下划线成员的位置

    命中记录为 (行号, 棘轮键)。棘轮键用被 patch 目标的源码表示（字符串目标用
    字符串原值，对象目标用 ast.unparse），不含行号即可在行号漂移时保持可对照。
    """

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    @staticmethod
    def _is_private(name: str) -> bool:
        return name.startswith("_")

    @staticmethod
    def _dotted_tail_private(dotted: str) -> bool:
        return bool(dotted) and _PrivatePatchFinder._is_private(dotted.rsplit(".", 1)[-1])

    def _record_string_target(self, node: ast.Call, dotted: str) -> None:
        if self._dotted_tail_private(dotted):
            self.hits.append((node.lineno, dotted))

    def _record_member_target(self, node: ast.Call, target_expr: ast.expr, attr: str) -> None:
        if self._is_private(attr):
            self.hits.append((node.lineno, f"{ast.unparse(target_expr)}.{attr}"))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        callee = ast.unparse(func)

        # unittest.mock.patch("pkg.mod._attr", ...) / patch("pkg.mod._attr", value=...)
        if callee.rsplit(".", 1)[-1] == "patch" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self._record_string_target(node, first.value)

        # patch.object(obj, "_attr", ...)，属性名可位置可关键字
        if callee.endswith("patch.object"):
            attr_arg: str | None = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                target_expr, attr_arg = node.args[0], node.args[1].value
            else:
                for kw in node.keywords:
                    if (
                        kw.arg in ("new_name", "attribute", "attr")
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ):
                        target_expr, attr_arg = node.args[0], kw.value.value
            if attr_arg is not None:
                self._record_member_target(node, target_expr, attr_arg)

        # monkeypatch.setattr(...)：双参（target, name）、三参（target, name, value）
        # 与单字符串（setattr("pkg.mod._attr", value)）形态；关键字 name= 同覆盖
        if callee.endswith("setattr") and callee.rsplit(".", 1)[0].endswith("monkeypatch"):
            name_arg: str | None = None
            target_expr: ast.expr | None = None
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    # 单字符串形态：整条点路径，末段私有即违规
                    self._record_string_target(node, first.value)
                elif len(node.args) >= 2:
                    target_expr = first
                    second = node.args[1]
                    if isinstance(second, ast.Constant) and isinstance(second.value, str):
                        name_arg = second.value
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    target_expr = node.args[0] if node.args else target_expr
                    name_arg = kw.value.value
            if name_arg is not None and target_expr is not None:
                # 双参形态 name 可含点路径（setattr(module, "pkg._attr", v)），取末段判定
                self._record_member_target(node, target_expr, name_arg.rsplit(".", 1)[-1])
                if "." in name_arg:
                    # setattr(module, "pkg.mod._attr", ...) 少见但同违规，补一条字符串键
                    if self._dotted_tail_private(name_arg):
                        self.hits.append((node.lineno, name_arg))

        self.generic_visit(node)


def _current_violations() -> tuple[dict[str, str], set[str]]:
    """返回 (违规清单: 键→棘轮键, 名单中被实际命中的键集合)"""
    violations: dict[str, str] = {}
    matched: set[str] = set()
    for path in sorted(TESTS.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        finder = _PrivatePatchFinder()
        finder.visit(tree)
        rel = path.relative_to(TESTS).as_posix()
        for lineno, target in finder.hits:
            key = f"{rel}:{lineno}"
            if key in ALLOWED_PRIVATE_PATCHES:
                matched.add(key)
            else:
                violations[key] = target
    return violations, matched


def test_no_new_private_member_patching() -> None:
    violations, matched = _current_violations()
    assert not violations, (
        "发现 tests/ 下 patch 前导下划线私有成员的新调用：按项目纪律属生产模块缺接缝的缺陷，"
        "请修接缝（暴露公有接缝或依赖注入）而非绕过；确实暂时无法修的走 "
        "ALLOWED_PRIVATE_PATCHES 逐项登记理由：\n"
        + "\n".join(f"  - {key} → {target}" for key, target in sorted(violations.items()))
    )
    stale = set(ALLOWED_PRIVATE_PATCHES) - matched
    assert not stale, "允许名单存在已失效条目（对应 patch 调用已消失，存量清理完成），请删除条目：\n" + "\n".join(
        f"  - {key}" for key in sorted(stale)
    )
