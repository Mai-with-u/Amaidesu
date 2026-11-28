"""提示词模板与代码的一致性守护。

提示词 .md 是散文，不逐句锁内容（内容改写属于正常迭代）；这里只断言"模板与代码对得上"：
1. 占位符完整性——模板里的 $变量 在渲染调用点真的会被注入（缺失 = 运行期渲染直接抛错）；
2. 退役能力定向检查——已下线的工具不得在任何模板或技能里教模型调用；
   泛化的"模板点名工具必须存在"检查做过校准但噪声大（工具全名多为运行期拼装、
   骨架字段名易误伤），降级为按退役名单定向检查；
3. 解析格式对齐——模板教的输出格式与代码解析端认识的格式一致。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from string import Template

import pytest

from src.modules.prompts import PromptManager, get_prompt_manager, reset_prompt_manager

REPO_ROOT = Path(__file__).parents[2]
SRC_ROOT = REPO_ROOT / "src"

# 已退役工具名单（来源：git 历史裁定与既有守护测试——能力下线后模板残留会让模型反复调不存在的工具）
RETIRED_TOOL_NAMES = (
    "minecraft_wait",  # Mod 删除的 wait 能力：话轮结束即让出，不再教这个调用
)


@pytest.fixture
def prompt_manager() -> PromptManager:
    """按生产方式加载全部模板（src/**/prompts 约定扫描），用完重置。"""
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


def _render_call_sites() -> dict[str, set[str]]:
    """静态扫描 src/ 全部 .py，推导每个模板名会被注入哪些关键字变量。

    识别形态：`<任意接收者>.render(<模板名>, **kw)`——模板名是字符串字面量，
    或同文件里赋值为字符串常量的名字（`_TEMPLATE = "模板名"`、类属性
    `TEMPLATE_NAME: str = "模板名"`、`self.TEMPLATE_NAME`）。首参推导不出的
    调用点跳过（如经参数转传的间接渲染），对应模板若带占位符会在完整性检查里点名。
    """
    injected: dict[str, set[str]] = {}

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        # 同文件里所有赋值为字符串常量的名字（含类属性与注解赋值）
        const_strs: dict[str, str] = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        const_strs.setdefault(target.id, node.value.value)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "render"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            template_name: str | None = None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                template_name = first.value
            elif isinstance(first, ast.Name) and first.id in const_strs:
                template_name = const_strs[first.id]
            elif isinstance(first, ast.Attribute) and first.attr in const_strs:
                template_name = const_strs[first.attr]
            if template_name is None:
                continue
            for kw in node.keywords:
                if kw.arg is None:
                    continue  # **解包：静态推导不了，留给运行期严格渲染兜底
                injected.setdefault(template_name, set()).add(kw.arg)
    return injected


def _placeholder_names(content: str) -> set[str]:
    """模板正文里的 $占位符 名单（与 string.Template 渲染行为一致，$$ 转义不计）。"""
    names: set[str] = set()
    for match in Template.pattern.finditer(content):
        name = match.group("named") or match.group("braced")
        if name:
            names.add(name)
    return names


# === 检查一：占位符完整性 ===


def test_template_placeholders_are_injected_by_render_call_sites(prompt_manager: PromptManager) -> None:
    """每个模板正文里的 $变量 都必须出现在至少一个针对该模板的渲染调用点实参里。

    PromptManager 是严格渲染：缺变量在运行期直接抛错。按模板名精确推导调用点
    （静态扫描 src/ 的 render 调用）；带占位符却找不到任何调用点的模板按缺陷报出。
    """
    injected = _render_call_sites()
    problems: list[str] = []
    for name in sorted(prompt_manager.list_templates()):
        template = prompt_manager._get_template(name)  # noqa: SLF001
        placeholders = _placeholder_names(template.content)
        if not placeholders:
            continue
        site_args = injected.get(name, set())
        missing = sorted(placeholders - site_args)
        if not site_args:
            problems.append(f"模板 '{name}' 带占位符 {sorted(placeholders)}，但扫描不到针对它的渲染调用点")
        elif missing:
            problems.append(f"模板 '{name}' 的占位符 {missing} 没有任何调用点注入")
    assert not problems, "占位符完整性校验失败：\n" + "\n".join(problems)


# === 检查二：退役能力定向检查 ===


def test_templates_do_not_mention_retired_tools(prompt_manager: PromptManager) -> None:
    """已退役工具不得在任何提示词模板里残留——残留会让模型反复调用不存在的工具。"""
    problems: list[str] = []
    for name in sorted(prompt_manager.list_templates()):
        template = prompt_manager._get_template(name)  # noqa: SLF001
        for retired in RETIRED_TOOL_NAMES:
            if retired in template.content:
                problems.append(f"模板 '{name}' 仍提及已退役工具 '{retired}'（文件: {template.path}）")
    assert not problems, "退役工具残留：\n" + "\n".join(problems)


def test_skill_files_do_not_mention_retired_tools() -> None:
    """技能文件与提示词模板同源喂给模型，退役工具名单同样适用。"""
    problems: list[str] = []
    for skill_file in sorted((SRC_ROOT / "agents").rglob("skills/**/*.md")):
        text = skill_file.read_text(encoding="utf-8")
        for retired in RETIRED_TOOL_NAMES:
            if retired in text:
                problems.append(f"技能 '{skill_file.relative_to(REPO_ROOT)}' 仍提及已退役工具 '{retired}'")
    assert not problems, "技能退役工具残留：\n" + "\n".join(problems)


# === 检查三：解析格式对齐 ===


def _parser_accepted_archive_ops() -> set[str]:
    """从 rolling.py 的解析端 AST 推导认识的 op 取值（`op == "x"` / `op in ("x", "y")`）。"""
    rolling = SRC_ROOT / "agents" / "streamer" / "memory" / "rolling.py"
    tree = ast.parse(rolling.read_text(encoding="utf-8"))
    ops: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not (isinstance(node.left, ast.Name) and node.left.id == "op"):
            continue
        for comp in node.comparators:
            if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                ops.add(comp.value)
            elif isinstance(comp, ast.Tuple):
                for elt in comp.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        ops.add(elt.value)
    return ops


def test_archive_template_taught_ops_are_parsable(prompt_manager: PromptManager) -> None:
    """下播整理模板教的 {"op": ...} 取值，解析端必须都认识（教的格式 ⊆ 解析器认识的格式）。"""
    taught: set[str] = set()
    for name in sorted(prompt_manager.list_templates()):
        template = prompt_manager._get_template(name)  # noqa: SLF001
        taught |= set(re.findall(r'"op":\s*"([a-z_]+)"', template.content))
    assert taught, '没有任何模板教 {"op": ...} 取值：格式约定从模板里消失时这里必须红，不能静默跳过核对'
    accepted = _parser_accepted_archive_ops()
    unparsable = sorted(taught - accepted)
    assert not unparsable, f"模板教的 op 取值 {unparsable} 解析端不认识（解析端只认 {sorted(accepted)}）"


def test_notice_source_labels_are_documented_in_game_prompt() -> None:
    """notice_router 写进任务上下文的来源标记，游戏侧提示词必须逐字写明（对得上，模型才读得懂）。"""
    from src.agents.minecraft.notice_router import PROMPT_SOURCE_LABELS  # 就地导入：对齐目标常量的单一事实源

    reset_prompt_manager()
    try:
        game_prompt = get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()
    missing = [label for label in PROMPT_SOURCE_LABELS.values() if label not in game_prompt]
    assert not missing, f"游戏侧提示词缺少递话来源标记的说明: {missing}"
