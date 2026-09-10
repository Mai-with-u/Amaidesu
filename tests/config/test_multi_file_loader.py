"""多文件加载器和生成器测试（v2.0.0：7 文件）"""

import shutil

import pytest
import tomlkit

from src.modules.config.multi_file_loader import (
    CONFIG_VERSION,
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

    def test_generate_creates_7_files(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        files = sorted(f.name for f in temp_config_dir.glob("*.toml"))
        assert files == [
            "agents.toml",
            "background.toml",
            "core.toml",
            "memory.toml",
            "model.toml",
            "storage.toml",
            "tools.toml",
        ]

    def test_needs_generation_false_after_generation(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        assert needs_generation(temp_config_dir) is False

    def test_generated_core_has_sections(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        core_content = (temp_config_dir / "core.toml").read_text(encoding="utf-8")
        assert "[meta]" in core_content
        assert "[general]" in core_content
        assert "[persona]" in core_content
        assert "[context]" in core_content
        # v2.0.0: maicore 已删除
        assert "[maicore]" not in core_content

    def test_generated_model_has_llm(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        model_content = (temp_config_dir / "model.toml").read_text(encoding="utf-8")
        assert "[llm]" in model_content
        assert "[vlm]" in model_content
        # v2.0.0: llm_outline 改名为 llm_agenda
        assert "[llm_agenda]" in model_content

    def test_generated_agents_has_enabled(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        agents_content = (temp_config_dir / "agents.toml").read_text(encoding="utf-8")
        assert "[agents]" in agents_content
        assert "enabled" in agents_content

    def test_generated_tools_has_packs(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        tools_content = (temp_config_dir / "tools.toml").read_text(encoding="utf-8")
        assert "[tools]" in tools_content


class TestLoading:
    def test_load_returns_all_categories(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, report = load_config_dir(temp_config_dir)
        assert "core" in config
        assert "model" in config
        assert "agents" in config
        assert "tools" in config
        assert "memory" in config
        assert "storage" in config
        assert "background" in config

    def test_load_no_drift_on_generated(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, report = load_config_dir(temp_config_dir)
        assert not report.redundant
        assert not report.missing

    def test_load_preserves_values(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, _ = load_config_dir(temp_config_dir)
        assert config["core"]["general"]["platform_id"] == "amaidesu"
        assert config["core"]["persona"]["emotion_intensity"] == 7

    def test_get_config_version(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        version = get_config_version(temp_config_dir)
        assert version == CONFIG_VERSION
        # 此处硬编码版本号与 CONFIG_VERSION 锁定一致——升版本时**必须**同步更新，
        # 否则"升了版本但没改测试"会让回归用例失明。
        assert version == "2.0.29"

    def test_drift_fixed_on_load(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        core_path = temp_config_dir / "core.toml"
        content = core_path.read_text(encoding="utf-8")
        content += '\n[dg_lab]\napi = "stale"\n'
        core_path.write_text(content, encoding="utf-8")
        config, report = load_config_dir(temp_config_dir)
        assert not report.redundant
        assert "dg_lab" not in (temp_config_dir / "core.toml").read_text(encoding="utf-8")


class TestAgentOwnedMcPMigration:
    """Agent 私有 MCP（v2.0.27）：``[tools.mcp.config.servers.maicraft]`` 整段
    迁移到 ``[agents.minecraft.mcp]``——通用 MCP 通道下"游戏专属 server"的归属下放。

    迁移语义：整段搬运（用户原 server 配置 dict 全部保留）；源路径删除后再次加载
    因源不存在被跳过，目标已存在不再覆盖——单值移动模式的天然幂等。
    """

    _CORE_TOML = """\
[meta]
version = "2.0.26"

[general]
platform_id = "amaidesu"
"""

    _TOOLS_TOML_OLD = """\
[meta]
version = "2.0.26"

[tools.mcp]
enabled = true

[tools.mcp.config.servers.maicraft]
enabled = true
transport = "http"
url = "http://127.0.0.1:8766/mcp"
timeout_seconds = 45.0
"""

    _AGENTS_TOML_OLD = """\
[meta]
version = "2.0.26"

[agents]
enabled = ["streamer", "minecraft"]

[agents.minecraft]
command_llm = "llm"
max_steps = 50
"""

    def _write_pre_migration_configs(self, temp_config_dir) -> None:
        temp_config_dir.mkdir(parents=True, exist_ok=True)
        (temp_config_dir / "core.toml").write_text(self._CORE_TOML, encoding="utf-8")
        (temp_config_dir / "tools.toml").write_text(self._TOOLS_TOML_OLD, encoding="utf-8")
        (temp_config_dir / "agents.toml").write_text(self._AGENTS_TOML_OLD, encoding="utf-8")
        # 其它域文件占位（让 load_config_dir 不报"文件缺失"）
        for fname in ("model.toml", "memory.toml", "storage.toml", "background.toml"):
            (temp_config_dir / fname).write_text("", encoding="utf-8")

    def test_migrate_maicraft_segment_to_agent_section(self, temp_config_dir):
        """触发迁移：agents.toml 出现 [agents.minecraft.mcp]，tools.toml 源段被删除。"""
        self._write_pre_migration_configs(temp_config_dir)

        load_config_dir(temp_config_dir)

        agents_doc = tomlkit.parse((temp_config_dir / "agents.toml").read_text(encoding="utf-8"))
        agents_root = agents_doc.unwrap()
        minecraft = agents_root["agents"]["minecraft"]
        assert "mcp" in minecraft, "迁移后 [agents.minecraft] 应包含 mcp 子段"
        mcp = minecraft["mcp"]
        assert mcp["url"] == "http://127.0.0.1:8766/mcp"
        assert mcp["enabled"] is True
        assert mcp["transport"] == "http"
        # 用户原 timeout_seconds 非默认值（45.0）必须保留
        assert mcp["timeout_seconds"] == 45.0

        tools_doc = tomlkit.parse((temp_config_dir / "tools.toml").read_text(encoding="utf-8"))
        tools_root = tools_doc.unwrap()
        servers = tools_root["tools"]["mcp"]["config"].get("servers", {})
        assert "maicraft" not in servers, "源段 servers.maicraft 应被删除"

    def test_migration_is_idempotent(self, temp_config_dir):
        """二次加载：源已不存在 → 跳过；目标已存在 → 不重写——幂等。"""
        self._write_pre_migration_configs(temp_config_dir)
        load_config_dir(temp_config_dir)

        load_config_dir(temp_config_dir)

        agents_doc = tomlkit.parse((temp_config_dir / "agents.toml").read_text(encoding="utf-8"))
        assert agents_doc["agents"]["minecraft"]["mcp"]["url"] == "http://127.0.0.1:8766/mcp"

    def test_agent_owned_mcp_schema_field_present(self, temp_config_dir):
        """迁移完成后，MinecraftAgentConfig.mcp 字段生效（load_config_dir 解析 agents.toml 不应抛错）。"""
        self._write_pre_migration_configs(temp_config_dir)
        config, _ = load_config_dir(temp_config_dir)

        minecraft_cfg = config["agents"]["agents"]["minecraft"]
        assert "mcp" in minecraft_cfg
        assert minecraft_cfg["mcp"]["url"] == "http://127.0.0.1:8766/mcp"
