"""Skill 库：发现、加载与按受众/环境筛选技能

加载方式与 PromptManager 同构：技能文档内聚在消费方包内的 ``skills/`` 目录，
按 ``src/**/skills/`` 约定发现；frontmatter ``name`` 是声明式注册键，与文件位置解耦。
坏文档（frontmatter 无法解析、字段不合法、正文为空、键重复）在加载期 fail-fast。

按需加载分两级：
- 目录：只有名字、一句话用途与分类，消费方注入系统提示词，常驻成本低
- 正文：模型判断需要时经消费方自有的读取工具按名取用
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import frontmatter
from pydantic import ValidationError

from src.modules.logging import get_logger

from .models import Skill, SkillEntry, SkillEnvironment, SkillMetadata

# 约定目录名：组件把技能文档放进自己包内的 skills/ 子目录即可被发现
SKILLS_DIR_NAME = "skills"

_EMPTY_ENVIRONMENT: SkillEnvironment = {}


class SkillLibrary:
    """技能库

    职责：
    - 从多个扫描根发现并加载全部 ``.md`` 技能文档（扫描根下每个 ``.md`` 都是一项技能）
    - 校验元数据并以 frontmatter ``name`` 注册（重复即抛 ``ValueError``）
    - 按受众 Agent 与已知环境事实给出目录、读取正文

    扫描根：
    - 显式注册的额外根（``register_scan_root``，测试注入或非常规布局）
    - 约定内聚目录 ``src/**/skills/``（排除本包自身），仅 ``auto_scan_src=True`` 时启用
      ——全局单例默认开启；裸构造默认关闭，保证单元测试与真实仓库技能隔离
    """

    def __init__(self, auto_scan_src: bool = False) -> None:
        self.logger = get_logger("SkillLibrary")
        self.auto_scan_src = auto_scan_src
        self._extra_roots: list[Path] = []
        self._skills: dict[str, Skill] = {}

    def register_scan_root(self, path: Path | str) -> None:
        """注册额外扫描根（在 ``load_all`` 之前调用）。"""
        self._extra_roots.append(Path(path))

    def _discover_scan_roots(self) -> list[Path]:
        """发现全部扫描根（去重、排序保证确定性）。"""
        seen: set[Path] = set()
        roots: list[Path] = []

        def _add(candidate: Path) -> None:
            try:
                resolved = candidate.resolve()
            except OSError:
                return
            if resolved not in seen:
                seen.add(resolved)
                roots.append(candidate)

        for extra in self._extra_roots:
            _add(extra)

        if self.auto_scan_src:
            package_dir = Path(__file__).resolve().parent  # src/modules/skills
            src_root = package_dir.parents[1]  # src/
            for skills_dir in sorted(src_root.rglob(SKILLS_DIR_NAME)):
                if not skills_dir.is_dir() or skills_dir == package_dir:
                    continue
                # 嵌套在另一扫描根下的同名目录已随父根递归加载，再加一次会被判成重复键
                if any(parent.name == SKILLS_DIR_NAME for parent in skills_dir.relative_to(src_root).parents):
                    continue
                _add(skills_dir)

        return roots

    def load_all(self) -> None:
        """加载全部扫描根下的技能文档（任一文档不合法即抛出）。"""
        self._skills.clear()
        for root in self._discover_scan_roots():
            if not root.exists():
                self.logger.debug(f"技能扫描根不存在，跳过: {root}")
                continue
            for md_file in sorted(root.rglob("*.md")):
                self._load_skill(md_file)
        self.logger.info(f"已加载 {len(self._skills)} 个技能")

    def _load_skill(self, path: Path) -> None:
        """加载单个技能文档（解析失败、字段非法、正文为空、键冲突均抛 ``ValueError``）。"""
        raw = path.read_text(encoding="utf-8")
        try:
            post = frontmatter.loads(raw)
        except Exception as exc:  # noqa: BLE001 - 第三方解析异常类型不固定，统一转为带路径的加载错误
            raise ValueError(f"技能 frontmatter 解析失败 ({path}): {exc}") from exc
        try:
            metadata = SkillMetadata(**dict(post.metadata))
        except ValidationError as exc:
            raise ValueError(f"技能元数据不合法 ({path}): {exc}") from exc
        content = post.content.strip()
        if not content:
            raise ValueError(f"技能正文为空 ({path})")
        existing = self._skills.get(metadata.name)
        if existing is not None:
            raise ValueError(f"技能名冲突: '{metadata.name}' 已由 {existing.path} 注册，拒绝加载重复文件 {path}")
        self._skills[metadata.name] = Skill(metadata=metadata, content=content, path=path)
        self.logger.debug(f"已加载技能: {metadata.name}")

    # === 查询接口 ===

    def list_skills(self) -> list[str]:
        """全部已加载技能名（不区分受众，供管理与测试使用）。"""
        return sorted(self._skills)

    def get(self, name: str) -> Optional[Skill]:
        """按名取已加载技能（不区分受众与环境，供管理与测试使用）。"""
        return self._skills.get(name)

    def catalog(self, agent: str, environment: Optional[SkillEnvironment] = None) -> list[SkillEntry]:
        """某个 Agent 在当前环境下可用的技能目录（按分类、名字排序）。

        已知不满足前提的技能不进目录；前提尚未确认的技能保留，并带上待确认项。
        """
        env = environment if environment is not None else _EMPTY_ENVIRONMENT
        entries: list[SkillEntry] = []
        for skill in self._skills.values():
            meta = skill.metadata
            if not meta.visible_to(agent):
                continue
            status, related = meta.applicability(env)
            if status == "unmet":
                continue
            entries.append(
                SkillEntry(
                    name=meta.name,
                    description=meta.description,
                    category=meta.category,
                    unverified=related if status == "unverified" else {},
                )
            )
        entries.sort(key=lambda entry: (entry.category, entry.name))
        return entries

    def read(self, name: str, agent: str, environment: Optional[SkillEnvironment] = None) -> dict[str, Any]:
        """按名读取技能正文，结果直接作为读取工具的观察返回。

        不存在、不属于该 Agent、或已知不满足前提时返回 ``success=False`` 与原因，
        并附上当前可读的技能名，模型据此改正名字而不是反复试错。
        """
        env = environment if environment is not None else _EMPTY_ENVIRONMENT
        skill = self._skills.get(name.strip())
        available = [entry.name for entry in self.catalog(agent, env)]
        if skill is None or not skill.metadata.visible_to(agent):
            return {"success": False, "error": f"没有名为 {name!r} 的技能", "available": available}
        status, related = skill.metadata.applicability(env)
        if status == "unmet":
            return {
                "success": False,
                "error": f"技能 {skill.name!r} 的前提在当前环境不满足",
                "missing": related,
                "available": available,
            }
        result: dict[str, Any] = {
            "success": True,
            "name": skill.name,
            "category": skill.metadata.category,
            "content": skill.content,
        }
        if status == "unverified":
            result["unverified"] = related
        return result


def render_catalog(entries: list[SkillEntry]) -> str:
    """把目录渲染成按分类分组的 Markdown 列表（空目录返回空串，消费方据此省略整段）。"""
    if not entries:
        return ""
    grouped: dict[str, list[SkillEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.category].append(entry)
    lines: list[str] = []
    for category in sorted(grouped):
        lines.append(f"- {category}")
        for entry in grouped[category]:
            line = f"  - `{entry.name}`：{entry.description}"
            if entry.unverified:
                pending = "；".join(f"{kind}: {', '.join(items)}" for kind, items in sorted(entry.unverified.items()))
                line += f"（前提待确认 {pending}）"
            lines.append(line)
    return "\n".join(lines)


# === 全局单例 ===

_skill_library_singleton: Optional[SkillLibrary] = None


def get_skill_library() -> SkillLibrary:
    """SkillLibrary 全局单例（启用 ``src/**/skills/`` 约定扫描，惰性加载）。"""
    global _skill_library_singleton
    if _skill_library_singleton is None:
        library = SkillLibrary(auto_scan_src=True)
        library.load_all()
        _skill_library_singleton = library
    return _skill_library_singleton


def reset_skill_library() -> None:
    """重置全局单例（用于测试）。"""
    global _skill_library_singleton
    _skill_library_singleton = None


__all__ = [
    "SKILLS_DIR_NAME",
    "SkillLibrary",
    "get_skill_library",
    "render_catalog",
    "reset_skill_library",
]
