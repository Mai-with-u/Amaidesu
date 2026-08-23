"""升级 Hook 单元测试（v2.0.0：每条 hook 配测试，旧→新结构断言）

AGENTS.md 钩子契约：
- 原地修改：hook 接收 dict 并直接修改
- 幂等：重复执行结果一致
- 返回变更路径列表
"""

from __future__ import annotations

from src.modules.config.upgrade_hooks import (
    CONFIG_UPGRADE_HOOKS,
    _migrate_agents_2_0_0,
    _migrate_background_2_0_0,
    _migrate_core_2_0_0,
    _migrate_mainosaba_to_text_adv_game,
    _migrate_memory_2_0_0,
    _migrate_model_2_0_0,
    _migrate_storage_2_0_0,
    _migrate_tools_2_0_0,
    apply_upgrade_hooks,
)


class TestCoreHook2_0_0:
    def test_maicore_removed(self):
        data = {"maicore": {"host": "127.0.0.1", "port": 8000}}
        changed = _migrate_core_2_0_0(data)
        assert "maicore" in changed
        assert "maicore" not in data

    def test_context_replaced(self):
        data = {"context": {"storage_type": "memory", "max_messages_per_session": 100}}
        changed = _migrate_core_2_0_0(data)
        assert "context" in changed
        assert "max_messages_per_session" not in data["context"]
        assert "memory_recall_viewers" in data["context"]
        assert data["context"]["enabled"] is True

    def test_context_added_when_missing(self):
        data: dict = {}
        changed = _migrate_core_2_0_0(data)
        assert "context" in changed
        assert data["context"]["enabled"] is True

    def test_idempotent(self):
        data = {"maicore": {"port": 8000}, "context": {"storage_type": "memory"}}
        _migrate_core_2_0_0(data)
        changed_again = _migrate_core_2_0_0(data)
        assert "maicore" not in changed_again
        assert "context" not in changed_again


class TestModelHook2_0_0:
    def test_llm_outline_renamed_to_llm_agenda(self):
        data = {"llm_outline": {"provider": "default", "model": "x"}}
        changed = _migrate_model_2_0_0(data)
        assert "llm_outline" not in data
        assert "llm_agenda" in data
        assert data["llm_agenda"]["model"] == "x"

    def test_does_not_overwrite_existing_agenda(self):
        """若用户已有 llm_agenda 配置，不覆盖。"""
        data = {
            "llm_outline": {"provider": "default", "model": "old"},
            "llm_agenda": {"provider": "default", "model": "new"},
        }
        _migrate_model_2_0_0(data)
        assert data["llm_agenda"]["model"] == "new"

    def test_idempotent(self):
        data = {"llm_outline": {"model": "x"}}
        _migrate_model_2_0_0(data)
        changed_again = _migrate_model_2_0_0(data)
        assert "llm_outline" not in changed_again
        assert "llm_agenda" not in changed_again


class TestToolsHook2_0_0:
    def test_enabled_list_default(self):
        data: dict = {}
        changed = _migrate_tools_2_0_0(data)
        assert "tools" in changed
        assert "perception" in data["tools"]["enabled"]

    def test_existing_enabled_preserved(self):
        data = {"tools": {"enabled": ["perception", "output"]}}
        changed = _migrate_tools_2_0_0(data)
        assert "tools.enabled" not in changed

    def test_idempotent(self):
        data: dict = {}
        _migrate_tools_2_0_0(data)
        changed_again = _migrate_tools_2_0_0(data)
        assert "tools" not in changed_again


class TestAgentsHook2_0_0:
    def test_default_streamer_config(self):
        data: dict = {}
        changed = _migrate_agents_2_0_0(data)
        assert "agents" in changed
        assert "streamer" in data["agents"]["enabled"]

    def test_existing_enabled_preserved(self):
        data = {"agents": {"enabled": ["game"]}}
        changed = _migrate_agents_2_0_0(data)
        assert "agents.enabled" not in changed

    def test_idempotent(self):
        data: dict = {}
        _migrate_agents_2_0_0(data)
        changed_again = _migrate_agents_2_0_0(data)
        assert "agents" not in changed_again


class TestMemoryHook2_0_0:
    def test_backend_default(self):
        data: dict = {}
        changed = _migrate_memory_2_0_0(data)
        assert "memory" in changed
        assert data["memory"]["backend"] == "simple"

    def test_existing_backend_preserved(self):
        data = {"memory": {"backend": "amemorix"}}
        changed = _migrate_memory_2_0_0(data)
        assert "memory.backend" not in changed

    def test_idempotent(self):
        data: dict = {}
        _migrate_memory_2_0_0(data)
        changed_again = _migrate_memory_2_0_0(data)
        assert "memory" not in changed_again


class TestStorageHook2_0_0:
    def test_sqlite_defaults(self):
        data: dict = {}
        changed = _migrate_storage_2_0_0(data)
        assert "storage" in changed
        assert data["storage"]["sqlite"]["db_path"] == "data/amaidesu.db"

    def test_idempotent(self):
        data: dict = {}
        _migrate_storage_2_0_0(data)
        changed_again = _migrate_storage_2_0_0(data)
        assert "storage" not in changed_again


class TestBackgroundHook2_0_0:
    def test_defaults(self):
        data: dict = {}
        changed = _migrate_background_2_0_0(data)
        assert "background" in changed
        assert data["background"]["light_tick_ms"] == 5000

    def test_idempotent(self):
        data: dict = {}
        _migrate_background_2_0_0(data)
        changed_again = _migrate_background_2_0_0(data)
        assert "background" not in changed_again


class TestMainosabaHookLegacy:
    """0.5.4 历史 hook（保留供回滚）。"""

    def test_mainosaba_migrated_to_text_adv_game(self):
        data = {"collectors": {"mainosaba": {"full_screen": True}, "enabled": ["mainosaba"]}}
        changed = _migrate_mainosaba_to_text_adv_game(data)
        assert "mainosaba" not in data["collectors"]
        assert "text_adv_game" in data["collectors"]
        assert "text_adv_game" in data["collectors"]["enabled"]

    def test_idempotent(self):
        data = {"collectors": {"text_adv_game": {"x": 1}, "enabled": ["text_adv_game"]}}
        changed = _migrate_mainosaba_to_text_adv_game(data)
        assert changed == []


class TestApplyUpgradeHooks:
    def test_version_range_filters_hooks(self):
        """apply_upgrade_hooks 应只触发 (old_ver, target_ver] 范围内的 hook。"""
        from src.modules.config.upgrade_hooks import _version_in_range

        assert _version_in_range("0.5.4", "2.0.0", "2.0.0") is True
        assert _version_in_range("0.5.3", "2.0.0", "2.0.0") is True
        assert _version_in_range("2.0.0", "2.0.0", "2.0.0") is False
        assert _version_in_range("2.0.0", "2.0.0", "2.0.1") is False

    def test_apply_runs_matching_hooks(self):
        data = {"llm_outline": {"model": "x"}}
        result = apply_upgrade_hooks(data, "model.toml", "0.5.4", "2.0.0")
        assert result.migrated is True
        assert "llm_agenda" in data

    def test_apply_no_match(self):
        data = {"foo": "bar"}
        result = apply_upgrade_hooks(data, "model.toml", "2.0.0", "2.0.0")
        assert result.migrated is False
        assert result.reasons == []

    def test_apply_wrong_file_skipped(self):
        """model.toml 专用 hook 不应在 core.toml 调用时触发。"""
        data: dict = {"meta": {"version": "0.5.4"}}
        result = apply_upgrade_hooks(data, "core.toml", "0.5.4", "2.0.0")
        assert "llm_agenda" not in data
        assert all("llm_" not in r for r in result.reasons)


class TestHookRegistry:
    """所有 2.0.0 hook 必须已注册。"""

    def test_all_2_0_0_hooks_registered(self):
        target_versions = {h.target_version for h in CONFIG_UPGRADE_HOOKS}
        assert "2.0.0" in target_versions

    def test_all_2_0_0_files_have_hook(self):
        """7 个核心配置文件 + agents/tools/memory/storage/background 都要有 2.0.0 hook。"""
        files = {h.config_file for h in CONFIG_UPGRADE_HOOKS if h.target_version == "2.0.0"}
        for expected in (
            "core.toml",
            "model.toml",
            "agents.toml",
            "tools.toml",
            "memory.toml",
            "storage.toml",
            "background.toml",
        ):
            assert expected in files, f"{expected} 缺少 2.0.0 升级 hook"