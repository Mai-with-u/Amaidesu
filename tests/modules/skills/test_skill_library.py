"""SkillLibrary 单元测试：加载校验、受众与环境筛选、正文读取、目录渲染"""

from pathlib import Path
from textwrap import dedent

import pytest

from src.modules.skills import SkillLibrary, build_skill_spec, render_catalog


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")
    return path


def _skill(name: str, *, agents: str = "[minecraft]", category: str = "survival", extra: str = "") -> str:
    return f"""
    ---
    name: {name}
    description: {name} 的用途
    agents: {agents}
    category: {category}
    {extra}
    ---

    # {name}

    正文
    """


def _library(root: Path) -> SkillLibrary:
    library = SkillLibrary()
    library.register_scan_root(root)
    library.load_all()
    return library


class TestLoading:
    def test_loads_nested_files_by_declared_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "survival/a.md", _skill("survival_opening"))
        _write(tmp_path, "create/deep/b.md", _skill("create_power", category="create"))

        library = _library(tmp_path)

        assert library.list_skills() == ["create_power", "survival_opening"]

    def test_duplicate_name_fails_fast(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", _skill("same"))
        _write(tmp_path, "b.md", _skill("same"))

        with pytest.raises(ValueError, match="技能名冲突"):
            _library(tmp_path)

    @pytest.mark.parametrize(
        "text",
        [
            # 缺受众
            "---\nname: x\ndescription: d\ncategory: c\n---\n正文",
            # 缺分类
            "---\nname: x\ndescription: d\nagents: [minecraft]\n---\n正文",
            # 拼错字段（require）不能被静默忽略
            "---\nname: x\ndescription: d\nagents: [minecraft]\ncategory: c\nrequire:\n  mods: [create]\n---\n正文",
            # 名字不合规（大写、连字符）
            "---\nname: Bad-Name\ndescription: d\nagents: [minecraft]\ncategory: c\n---\n正文",
            # '*' 与具体名混用
            "---\nname: x\ndescription: d\nagents: ['*', minecraft]\ncategory: c\n---\n正文",
            # 前提类别没有列项
            "---\nname: x\ndescription: d\nagents: [minecraft]\ncategory: c\nrequires:\n  mods: []\n---\n正文",
        ],
    )
    def test_invalid_metadata_fails_fast(self, tmp_path: Path, text: str) -> None:
        (tmp_path / "bad.md").write_text(text, encoding="utf-8")

        with pytest.raises(ValueError, match="技能元数据不合法"):
            _library(tmp_path)

    def test_empty_body_fails_fast(self, tmp_path: Path) -> None:
        (tmp_path / "empty.md").write_text(
            "---\nname: x\ndescription: d\nagents: [minecraft]\ncategory: c\n---\n\n   \n", encoding="utf-8"
        )

        with pytest.raises(ValueError, match="正文为空"):
            _library(tmp_path)

    def test_missing_root_is_skipped(self, tmp_path: Path) -> None:
        library = _library(tmp_path / "absent")

        assert library.list_skills() == []


class TestCatalog:
    def test_audience_filters_catalog(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", _skill("mine_only"))
        _write(tmp_path, "b.md", _skill("everyone", agents="['*']"))
        _write(tmp_path, "c.md", _skill("streamer_only", agents="[streamer]"))
        library = _library(tmp_path)

        assert [e.name for e in library.catalog("minecraft")] == ["everyone", "mine_only"]
        assert [e.name for e in library.catalog("streamer")] == ["everyone", "streamer_only"]

    def test_known_missing_mod_removes_skill_and_unknown_keeps_it_marked(self, tmp_path: Path) -> None:
        _write(tmp_path, "c.md", _skill("create_power", category="create", extra="requires:\n      mods: [create]"))
        _write(tmp_path, "s.md", _skill("survival_opening"))
        library = _library(tmp_path)

        # 已知装了：进目录，无待确认项
        installed = library.catalog("minecraft", {"mods": {"create", "jei"}})
        assert [(e.name, e.unverified) for e in installed] == [("create_power", {}), ("survival_opening", {})]
        # 已知没装：移出目录
        assert [e.name for e in library.catalog("minecraft", {"mods": {"mekanism"}})] == ["survival_opening"]
        # 未知：保留并标注待确认的前提
        unknown = {e.name: e.unverified for e in library.catalog("minecraft")}
        assert unknown == {"create_power": {"mods": ["create"]}, "survival_opening": {}}

    def test_catalog_sorted_by_category_then_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "1.md", _skill("zeta", category="survival"))
        _write(tmp_path, "2.md", _skill("alpha", category="survival"))
        _write(tmp_path, "3.md", _skill("beta", category="create"))
        library = _library(tmp_path)

        assert [e.name for e in library.catalog("minecraft")] == ["beta", "alpha", "zeta"]


class TestRead:
    def test_read_returns_body(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", _skill("survival_opening"))
        library = _library(tmp_path)

        result = library.read("survival_opening", "minecraft")

        assert result["success"] is True
        assert result["category"] == "survival"
        assert result["content"].startswith("# survival_opening")
        assert "unverified" not in result

    def test_read_marks_unverified_requirement(self, tmp_path: Path) -> None:
        _write(tmp_path, "c.md", _skill("create_power", category="create", extra="requires:\n      mods: [create]"))
        library = _library(tmp_path)

        result = library.read("create_power", "minecraft")

        assert result["success"] is True
        assert result["unverified"] == {"mods": ["create"]}

    def test_read_rejects_known_unmet_requirement_with_reason(self, tmp_path: Path) -> None:
        _write(tmp_path, "c.md", _skill("create_power", category="create", extra="requires:\n      mods: [create]"))
        _write(tmp_path, "s.md", _skill("survival_opening"))
        library = _library(tmp_path)

        result = library.read("create_power", "minecraft", {"mods": set()})

        assert result["success"] is False
        assert result["missing"] == {"mods": ["create"]}
        assert result["available"] == ["survival_opening"]

    def test_read_hides_other_agents_skills(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", _skill("streamer_only", agents="[streamer]"))
        library = _library(tmp_path)

        result = library.read("streamer_only", "minecraft")

        assert result["success"] is False
        assert result["available"] == []

    def test_read_unknown_name_lists_available(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", _skill("survival_opening"))
        library = _library(tmp_path)

        result = library.read("survival_openning", "minecraft")

        assert result["success"] is False
        assert result["available"] == ["survival_opening"]


class TestRender:
    def test_render_groups_by_category_and_marks_pending(self, tmp_path: Path) -> None:
        _write(tmp_path, "c.md", _skill("create_power", category="create", extra="requires:\n      mods: [create]"))
        _write(tmp_path, "s.md", _skill("survival_opening"))
        library = _library(tmp_path)

        text = render_catalog(library.catalog("minecraft"))

        assert text.splitlines() == [
            "- create",
            "  - `create_power`：create_power 的用途（前提待确认 mods: create）",
            "- survival",
            "  - `survival_opening`：survival_opening 的用途",
        ]

    def test_render_empty_catalog_is_empty(self) -> None:
        assert render_catalog([]) == ""


def test_skill_spec_is_declared_per_provider() -> None:
    spec = build_skill_spec("minecraft")

    assert spec.full_name == "minecraft_skill"
    assert spec.parameters_schema["required"] == ["name"]
