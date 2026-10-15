"""PromptManager：模板发现与声明式键、严格渲染、frontmatter 告警与 fail-fast、多根扫描、生产单例与全仓键集"""

from pathlib import Path

import pytest
from loguru import logger

from src.modules.prompts import (
    PromptManager,
    PromptTemplate,
    TemplateMetadata,
    get_prompt_manager,
    reset_prompt_manager,
)


def _manager(tmp_path: Path, files: dict[str, str] | None = None) -> PromptManager:
    """在 tmp_path 下写入模板文件（相对路径 → 内容）并加载。"""
    for relative, content in (files or {}).items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    manager = PromptManager(templates_dir=str(tmp_path))
    manager.load_all()
    return manager


def test_templates_are_discovered_by_path_including_nested_and_render_with_variables(tmp_path: Path) -> None:
    assert _manager(tmp_path / "empty").list_templates() == []  # 裸构造不启用 src 约定扫描，保证测试隔离

    manager = _manager(
        tmp_path / "t",
        {
            "decision/intent.md": "---\ndescription: Intent 决策模板\nversion: 1.0\nvariables:\n  - user_name\n---\n用户 $user_name",
            "decision/action.md": "Action: $act",
            "output/speech.md": "Say: $text",
            "decision/subdir/deep.md": "Deep template: $var",
            "static.md": "This is a static template.",
            "price.md": "Price: $$100",  # string.Template 用 $$ 表示转义的 $
        },
    )
    assert set(manager.list_templates()) == {
        "decision/intent",
        "decision/action",
        "output/speech",
        "decision/subdir/deep",
        "static",
        "price",
    }
    assert manager.render("decision/intent", user_name="Alice") == "用户 Alice"  # frontmatter 不进正文
    assert manager.render("decision/subdir/deep", var="test") == "Deep template: test"
    assert manager.render("static") == "This is a static template."
    assert manager.render("price") == "Price: $100"


def test_strict_render_reports_template_and_missing_variables(tmp_path: Path) -> None:
    manager = _manager(tmp_path, {"greet.md": "Hi $name, today is $day."})
    with pytest.raises(KeyError) as exc_info:
        manager.render("greet", name="Alice")
    assert "greet" in str(exc_info.value) and "day" in str(exc_info.value)  # 报错含模板名与缺失变量清单
    with pytest.raises(KeyError, match="模板 'nonexistent' 不存在"):
        manager.render("nonexistent")


def test_declaration_mismatch_only_warns_but_bad_frontmatter_and_duplicate_keys_fail_fast(tmp_path: Path) -> None:
    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(m), level="WARNING")  # 项目日志走 loguru，需挂 sink 捕获告警
    try:
        manager = _manager(
            tmp_path / "drift", {"drift.md": "---\nvariables:\n  - declared_only\n---\nBody: $undeclared_var\n"}
        )
    finally:
        logger.remove(handler_id)
    text = "\n".join(messages)
    assert all(
        word in text for word in ("drift", "undeclared_var", "declared_only")
    )  # frontmatter 声明与正文占位符不一致：告警不阻断
    assert "drift" in manager.list_templates()

    bad = PromptManager(templates_dir=str(tmp_path / "bad"))
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "bad.md").write_text("---\nbroken: [unclosed\n---\n正文内容", encoding="utf-8")
    with pytest.raises(Exception, match="bad.md"):  # 语法错误 fail-fast，不静默降级
        bad.load_all()

    (tmp_path / "dup").mkdir()
    for filename in ("a.md", "b.md"):
        (tmp_path / "dup" / filename).write_text("---\nname: same_key\n---\n内容", encoding="utf-8")
    with pytest.raises(ValueError, match="模板键冲突"):
        PromptManager(templates_dir=str(tmp_path / "dup")).load_all()


def test_frontmatter_name_overrides_the_path_key_and_extra_scan_roots_are_discovered(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path / "named", {"decision/intent.md": "---\nname: my_custom_key\ndescription: 测试\n---\n内容 $var"}
    )
    assert "my_custom_key" in manager.list_templates() and "decision/intent" not in manager.list_templates()

    (tmp_path / "central").mkdir()
    component = tmp_path / "component" / "prompts"
    component.mkdir(parents=True)
    (component / "tool_action.md").write_text("---\nname: tool_action\n---\n动作 $action", encoding="utf-8")
    scanning = PromptManager(templates_dir=str(tmp_path / "central"))
    scanning.register_scan_root(component)
    scanning.load_all()
    assert scanning.render("tool_action", action="挥手") == "动作 挥手"


def test_template_object_renders_directly() -> None:
    template = PromptTemplate(
        name="test",
        content="Hello, $name!",
        raw="Hello, $name!",
        metadata=TemplateMetadata(name="test"),
        path=Path("/fake"),
    )
    assert template.render(name="Bob") == "Hello, Bob!"


def test_singleton_is_shared_resettable_auto_scans_and_loads_exactly_the_declared_keys() -> None:
    reset_prompt_manager()
    try:
        first = get_prompt_manager()
        assert isinstance(first, PromptManager) and get_prompt_manager() is first and first.auto_scan_src is True
        assert set(first.list_templates()) == {  # 全仓声明式键全集，防漂移回归网
            "amaidesu_planner_react",
            "amaidesu_replyer_system",
            "amaidesu_replyer",
            "viewer_message",
            "sc_message",
            "passerby_message",
            "warmup_message",
            "persona_generation",
            "amaidesu_minecraft_agent",
            "summary_system",
            "chapter_system",
            "profile_system",
            "session_recap_system",
            "screen_vlm_system",
            "screen_vlm_prompt",
            "text_adv_vlm_question",
        }
        reset_prompt_manager()
        assert get_prompt_manager() is not first  # 重置后是新实例
    finally:
        reset_prompt_manager()
