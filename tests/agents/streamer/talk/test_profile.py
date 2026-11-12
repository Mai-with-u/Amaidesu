"""结构化人物设定档案（PersonaProfile）解析、多形象切换与 900 字硬上限测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.streamer.talk.profile import (
    MAX_PROFILE_CHARS,
    PERSONA_PROFILE_TEMPLATE_MD,
    load_persona_profile,
    parse_persona_profile_markdown,
    parse_persona_profile_toml,
)

SAMPLE_MARKDOWN_PROFILE = """\
<!-- 注释里的说明文字不应进入渲染结果 -->
## 身份与来历
- 四亿年前萨卡班甲鱼与橙子混合诞生，靠维生素 C 抗氧化活到今天。
- 2011 年苹果压过橙子后化身 AI 主播苟活。

## 舞台形象
### vts_2_0：2.0 舞台形象
- 头顶两片绿叶，橙色波浪短卷发，蓝紫色眼睛，脸颊四叶草印花。
- 脖子挂白橙耳机，腰间有调节麦麦参数的旋钮。

### sacabambaspis：潜水艇形态
- 上半身橙色下半身白色，尾部四叶草推进器。

## 喜好与执念
- 喜欢橙子与维 C，对苹果压过橙子耿耿于怀。

## 身边的人与直播日常
- 平时直播聊天与玩沙盒游戏，偶尔和联机伙伴搭机器。
"""


class TestParsePersonaProfile:
    """Markdown 与 TOML 档案解析、多形象选择及注释剥离。"""

    def test_markdown_selects_active_outfit_and_moves_rest_to_other_forms(self) -> None:
        profile = parse_persona_profile_markdown(SAMPLE_MARKDOWN_PROFILE, active_outfit="vts_2_0")

        assert "萨卡班甲鱼" in profile.identity
        assert "注释里的说明文字" not in profile.identity
        assert profile.active_outfit_name == "2.0 舞台形象"
        assert "调节麦麦参数的旋钮" in profile.active_outfit
        assert "潜水艇形态" in profile.other_forms
        assert "四叶草推进器" in profile.other_forms
        assert "苹果压过橙子" in profile.likes_and_quirks
        assert "沙盒游戏" in profile.companions_and_routine

    def test_markdown_switches_to_submarine_form_when_requested(self) -> None:
        profile = parse_persona_profile_markdown(SAMPLE_MARKDOWN_PROFILE, active_outfit="sacabambaspis")

        assert profile.active_outfit_name == "潜水艇形态"
        assert "四叶草推进器" in profile.active_outfit
        assert "2.0 舞台形象" in profile.other_forms
        assert "调节麦麦参数的旋钮" in profile.other_forms

    def test_unknown_outfit_falls_back_to_first_entry(self) -> None:
        profile = parse_persona_profile_markdown(SAMPLE_MARKDOWN_PROFILE, active_outfit="non_existent_v9")

        assert profile.active_outfit_name == "2.0 舞台形象"
        assert "调节麦麦参数的旋钮" in profile.active_outfit

    def test_template_constant_parses_cleanly_without_personal_info(self) -> None:
        profile = parse_persona_profile_markdown(PERSONA_PROFILE_TEMPLATE_MD, active_outfit="default_2d")

        assert not profile.is_empty()
        assert profile.active_outfit_name == "默认舞台形象"
        assert "迷你形态" in profile.other_forms

    def test_toml_format_parses_equivalently(self) -> None:
        toml_text = """
identity = ["四亿年萨卡班甲鱼混橙子", "2011 年化身 AI 主播"]
likes_and_quirks = "最爱橙子补充维 C"
companions_and_routine = "在直播间聊天与探险"

[outfits.vts_2_0]
label = "2.0 舞台形象"
items = ["头顶两片绿叶", "腰间参数旋钮"]

[outfits.chibi]
label = "团子形态"
body = "圆滚滚的小甲鱼"
"""
        profile = parse_persona_profile_toml(toml_text, active_outfit="vts_2_0")

        assert "四亿年萨卡班甲鱼混橙子" in profile.identity
        assert profile.active_outfit_name == "2.0 舞台形象"
        assert "腰间参数旋钮" in profile.active_outfit
        assert "团子形态：圆滚滚的小甲鱼" in profile.other_forms


class TestRenderAndLoadProfile:
    """渲染视角、防背书守则、900 字硬上限与路径查找。"""

    def test_render_perspectives_are_deterministic_and_within_900_chars(self) -> None:
        profile = parse_persona_profile_markdown(SAMPLE_MARKDOWN_PROFILE, active_outfit="vts_2_0")

        planner_text = profile.render_for_planner()
        replyer_text = profile.render_for_replyer()

        assert len(planner_text) <= MAX_PROFILE_CHARS
        assert len(replyer_text) <= MAX_PROFILE_CHARS
        assert "观众此刻看到的她：2.0 舞台形象" in planner_text
        assert "观众眼里的你：2.0 舞台形象" in replyer_text
        assert "真实底子" in planner_text
        assert "真实底子" in replyer_text
        # 连续两次渲染完全一致，保证整场前缀缓存命中
        assert profile.render_for_planner() == planner_text
        assert profile.render_for_replyer() == replyer_text

    def test_oversized_profile_is_strictly_capped_at_max_chars(self) -> None:
        huge_md = "\n\n".join(
            [
                "## 身份与来历\n" + "这是一段非常详细的远古来历说明，用于测试字数裁剪边界。" * 18,
                "## 舞台形象\n### vts_2_0：2.0 形象\n" + "身上有许多精致的配饰细节与机关说明。" * 18,
                "## 其他形态\n" + "还有其他不同时期的形态变化记录。" * 15,
                "## 喜好与执念\n" + "对橙子和维生素 C 有着跨越四亿年的执着。" * 15,
                "## 身边的人与直播日常\n" + "平时直播间里的日常安排与互动习惯。" * 15,
            ]
        )
        profile = parse_persona_profile_markdown(huge_md, active_outfit="vts_2_0")

        planner_rendered = profile.render_for_planner()
        replyer_rendered = profile.render_for_replyer()

        assert 0 < len(planner_rendered) <= MAX_PROFILE_CHARS
        assert 0 < len(replyer_rendered) <= MAX_PROFILE_CHARS
        assert "真实底子" in planner_rendered
        assert "身份与来历" in planner_rendered

    @pytest.mark.parametrize("empty_value", ["", "   "])
    def test_load_returns_none_when_unconfigured(self, empty_value: str) -> None:
        assert load_persona_profile(empty_value) is None

    def test_load_resolves_relative_to_config_dir_and_handles_missing_gracefully(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        md_file = cfg_dir / "persona_profile.md"
        md_file.write_text(SAMPLE_MARKDOWN_PROFILE, encoding="utf-8")

        loaded = load_persona_profile("persona_profile.md", active_outfit="vts_2_0", base_dirs=[cfg_dir, tmp_path])
        assert loaded is not None
        assert "调节麦麦参数的旋钮" in loaded.active_outfit

        missing = load_persona_profile("missing_profile.md", base_dirs=[cfg_dir, tmp_path])
        assert missing is None
