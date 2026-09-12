"""多文件加载器和生成器测试（六文件布局）"""

import shutil

import pytest

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import (
    _CONFIG_FILES,
    generate_default_configs,
    get_config_version,
    load_config_dir,
    needs_generation,
)


@pytest.fixture
def temp_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir)


class TestGeneration:
    def test_needs_generation_for_missing_dir(self, temp_config_dir):
        assert needs_generation(temp_config_dir) is True

    def test_generate_creates_6_files(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        files = sorted(f.name for f in temp_config_dir.glob("*.toml"))
        assert files == sorted(_CONFIG_FILES)

    def test_needs_generation_false_after_generation(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        assert needs_generation(temp_config_dir) is False

    def test_generated_agents_has_sections(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        agents_content = (temp_config_dir / "agents.toml").read_text(encoding="utf-8-sig")
        assert "[meta]" in agents_content
        assert "[agents]" in agents_content
        assert "[agents.streamer]" in agents_content
        assert "[agents.streamer.persona]" in agents_content
        assert "[agents.streamer.context]" in agents_content
        assert "[agents.streamer.background]" in agents_content

    def test_generated_model_has_three_layers(self, temp_config_dir):
        """model.toml 生成形态——三层结构（providers / models / profiles）

        旧结构 ``[llm]`` / ``[vlm]`` / ``[llm_agenda]`` 等单一 profile 段位
        已废除（§6.2 重构）；新结构按 llm_providers / llm_models / llm_profiles
        三层装配。
        """
        generate_default_configs(temp_config_dir)
        model_content = (temp_config_dir / "model.toml").read_text(encoding="utf-8-sig")
        assert "[[llm_providers]]" in model_content
        assert "[[llm_models]]" in model_content
        # dict 形态的 llm_profiles 按 key 展开为子表（每用途一段）
        for profile in ("planner", "replyer", "summary", "minecraft", "vision", "simulator"):
            assert f"[llm_profiles.{profile}]" in model_content

    def test_generated_infra_has_sections(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        infra_content = (temp_config_dir / "infra.toml").read_text(encoding="utf-8-sig")
        for section in ("[tts]", "[subtitle]", "[events]", "[interceptors.rate_limit]", "[dashboard]", "[logging]", "[simulator]"):
            assert section in infra_content

    def test_generated_tools_has_tools_section(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        tools_content = (temp_config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        assert "[tools]" in tools_content

    def test_generated_files_carry_baseline_version(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        for fname in _CONFIG_FILES:
            content = (temp_config_dir / fname).read_text(encoding="utf-8-sig")
            assert f'version = "{CONFIG_BASELINE_VERSION}"' in content, fname


class TestLoading:
    def test_load_returns_all_scopes(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, report = load_config_dir(temp_config_dir)
        for scope in ("agents", "collectors", "tools", "model", "storage", "infra"):
            assert scope in config

    def test_load_no_drift_on_generated(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, report = load_config_dir(temp_config_dir)
        assert not report.redundant
        assert not report.missing

    def test_load_preserves_values(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, _ = load_config_dir(temp_config_dir)
        # persona 段已归位到 [agents.streamer.persona]，emotion_intensity/user_name
        # 等死字段已删；保留字段默认值在 [agents.streamer.persona] 下。
        assert config["agents"]["agents"]["streamer"]["persona"]["bot_name"] == "麦麦"
        assert config["agents"]["agents"]["streamer"]["persona"]["audience_salutation"] == "大家"
        assert config["infra"]["dashboard"]["port"] == 60214

    def test_meta_stripped_from_merged_view(self, temp_config_dir):
        """meta 隔离：[meta] 是文件私有元数据，不进入合并命名空间。"""
        generate_default_configs(temp_config_dir)
        config, _ = load_config_dir(temp_config_dir)
        for scope_data in config.values():
            assert "meta" not in scope_data

    def test_per_file_versions_independently_readable(self, temp_config_dir):
        """两文件版本互不覆盖：改 agents 版本不影响 infra 的读取。"""
        generate_default_configs(temp_config_dir)
        agents_path = temp_config_dir / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        content = content.replace(
            f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.32"', 1
        )
        agents_path.write_text(content, encoding="utf-8-sig")

        assert get_config_version(temp_config_dir, "agents.toml") == "2.0.32"
        assert get_config_version(temp_config_dir, "infra.toml") == CONFIG_BASELINE_VERSION

    def test_drift_fixed_on_load(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        agents_path = temp_config_dir / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        content += '\n[dg_lab]\napi = "stale"\n'
        agents_path.write_text(content, encoding="utf-8-sig")
        config, report = load_config_dir(temp_config_dir)
        assert not report.redundant
        assert "dg_lab" not in agents_path.read_text(encoding="utf-8-sig")
