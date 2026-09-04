"""升级 Hook 单元测试（每条 hook 配测试，旧→新结构断言）

AGENTS.md 钩子契约：
- 原地修改：hook 接收 dict 并直接修改
- 幂等：重复执行结果一致
- 返回变更路径列表
"""

from __future__ import annotations

from src.modules.config.upgrade_hooks import (
    CONFIG_UPGRADE_HOOKS,
    _migrate_agents_2_0_0,
    _migrate_agents_2_0_1,
    _migrate_agents_2_0_3,
    _migrate_background_2_0_0,
    _migrate_core_2_0_0,
    _migrate_core_2_0_1,
    _migrate_core_2_0_2,
    _migrate_core_2_0_4,
    _migrate_mainosaba_to_text_adv_game,
    _migrate_memory_2_0_0,
    _migrate_model_2_0_0,
    _migrate_model_2_0_3,
    _migrate_storage_2_0_0,
    _migrate_tools_2_0_0,
    _migrate_tools_2_0_9,
    _strip_pipelines_2_0_4,
    _version_in_range,
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


class TestWave6Hooks2_0_1:
    """收尾清理钩子（2.0.0 → 2.0.1）。"""

    def test_core_2_0_1_strips_text_adv_game(self):
        """core.toml 2.0.1：剥离残留的 text_adv_game 字段。"""

        data = {"text_adv_game": {"full_screen": True}, "pipelines": {}}
        changed = _migrate_core_2_0_1(data)
        assert "text_adv_game" not in data
        assert "text_adv_game" in changed

    def test_core_2_0_1_idempotent(self):
        """idempotent：无 text_adv_game 时不报错。"""

        data = {"pipelines": {}}
        changed = _migrate_core_2_0_1(data)
        assert changed == []

    def test_agents_2_0_1_strips_reply_probability(self):
        """agents.toml 2.0.1：剥离残留的 reply_probability 字段。"""

        data = {"streamer": {"planner_llm": "llm_fast", "reply_probability": 0.7}}
        changed = _migrate_agents_2_0_1(data)
        assert "reply_probability" not in data["streamer"]
        assert "streamer.reply_probability" in changed

    def test_agents_2_0_1_preserves_other_fields(self):
        """剥离 reply_probability 时其他字段保留。"""

        data = {"streamer": {"planner_llm": "llm_fast", "bot_name": "麦麦"}}
        changed = _migrate_agents_2_0_1(data)
        assert changed == []
        assert data["streamer"]["planner_llm"] == "llm_fast"
        assert data["streamer"]["bot_name"] == "麦麦"

    def test_2_0_1_hooks_registered(self):
        """2.0.1 升级钩子已注册。"""
        v2_0_1_hooks = [h for h in CONFIG_UPGRADE_HOOKS if h.target_version == "2.0.1"]
        files = {h.config_file for h in v2_0_1_hooks}
        assert files == {"core.toml", "agents.toml"}

    def test_core_2_0_2_strips_mcp_section(self):
        """core.toml 2.0.2：剥离残留的 [mcp] 段（MCP 桥接服务已随 v2 移除）。"""

        data = {"mcp": {"enabled": True}, "logging": {"level": "DEBUG"}}
        changed = _migrate_core_2_0_2(data)
        assert "mcp" not in data
        assert "mcp" in changed
        assert data["logging"] == {"level": "DEBUG"}

    def test_core_2_0_2_idempotent(self):
        """idempotent：无 [mcp] 段时不报错、无变更。"""

        data = {"general": {}, "logging": {}}
        changed = _migrate_core_2_0_2(data)
        assert changed == []

    def test_2_0_2_hook_registered(self):
        """2.0.2 升级钩子已注册且作用于 core.toml。"""
        v2_0_2_hooks = [h for h in CONFIG_UPGRADE_HOOKS if h.target_version == "2.0.2"]
        assert len(v2_0_2_hooks) == 1
        assert v2_0_2_hooks[0].config_file == "core.toml"

    def test_model_2_0_3_fixes_invalid_provider(self):
        """model.toml 2.0.3：无效 provider（默认 'default'）重写为首个可用 provider。"""

        data = {
            "llm_providers": [{"name": "deepseek"}, {"name": "local"}],
            "llm_agenda": {"provider": "default"},
            "llm": {"provider": "deepseek"},
        }
        changed = _migrate_model_2_0_3(data)
        assert data["llm_agenda"]["provider"] == "deepseek"
        assert data["llm"]["provider"] == "deepseek"
        assert "llm_agenda.provider" in changed
        assert "llm.provider" not in changed

    def test_model_2_0_3_handles_llm_outline_rename(self):
        """自包含：残留 llm_outline 时先改名再修 provider（版本门控下 2.0.0 钩子不再触发）。"""

        data = {
            "llm_providers": [{"name": "siliconflow"}],
            "llm_outline": {"provider": "default"},
        }
        changed = _migrate_model_2_0_3(data)
        assert "llm_outline" not in data
        assert data["llm_agenda"]["provider"] == "siliconflow"
        assert "llm_agenda" in changed

    def test_model_2_0_3_idempotent(self):
        """幂等：全部合法时无变更。"""

        data = {
            "llm_providers": [{"name": "deepseek"}],
            "llm_agenda": {"provider": "deepseek"},
        }
        changed = _migrate_model_2_0_3(data)
        assert changed == []

    def test_agents_2_0_3_filters_enabled(self):
        """agents.toml 2.0.3：过滤失效 Agent 类型，空后回退 streamer。"""

        data = {"agents": {"enabled": ["maibot", "amaidesu"]}}
        changed = _migrate_agents_2_0_3(data)
        assert data["agents"]["enabled"] == ["streamer"]
        assert "agents.enabled" in changed

    def test_agents_2_0_3_keeps_valid(self):
        """幂等：仅合法值时无变更。"""

        data = {"agents": {"enabled": ["streamer", "game"]}}
        changed = _migrate_agents_2_0_3(data)
        assert data["agents"]["enabled"] == ["streamer", "game"]
        assert changed == []

    def test_2_0_3_hooks_registered(self):
        """2.0.3 升级钩子已注册（model + agents）。"""
        v2_0_3_hooks = [h for h in CONFIG_UPGRADE_HOOKS if h.target_version == "2.0.3"]
        files = {h.config_file for h in v2_0_3_hooks}
        assert files == {"model.toml", "agents.toml"}


class TestCoreHook2_0_4:
    """core.toml 2.0.4：``[pipelines]`` → ``[interceptors]`` 正名（收官）。"""

    def test_pipelines_renamed_to_interceptors(self):
        data: dict = {
            "pipelines": {
                "input": {
                    "rate_limit": {"limit": 5, "priority": 1},
                    "similar_filter": {"enabled": True, "priority": 2},
                },
                "output": {"profanity_filter": {"priority": 0}},
            }
        }
        changed = _migrate_core_2_0_4(data)
        assert "pipelines" not in data
        assert set(data.get("interceptors", {})) == {"rate_limit", "similar_filter"}
        assert "profanity_filter" not in data.get("interceptors", {})
        assert "priority" not in data["interceptors"]["rate_limit"]
        assert changed == ["interceptors"]

    def test_flat_pipelines_compat(self):
        """v1 遗留：``pipelines`` 根级扁平键也迁移。"""

        data: dict = {"pipelines": {"rate_limit": {"limit": 3}}}
        changed = _migrate_core_2_0_4(data)
        assert data.get("interceptors") == {"rate_limit": {"limit": 3}}
        assert changed == ["interceptors"]

    def test_idempotent(self):
        """重复执行无副作用。"""

        data: dict = {
            "pipelines": {"input": {"rate_limit": {"limit": 1}}},
            "interceptors": {"existing": {"x": 1}},
        }
        _migrate_core_2_0_4(data)
        second = dict(data)
        changed = _migrate_core_2_0_4(data)
        assert changed == []
        assert data == second  # 幂等：二次执行不改数据

    def test_merge_preserves_existing_interceptors(self):
        """已有 [interceptors] 键时迁移项并入，不整体覆盖。"""

        data: dict = {
            "pipelines": {"input": {"rate_limit": {"limit": 1}}},
            "interceptors": {"existing": {"x": 1}},
        }
        changed = _migrate_core_2_0_4(data)
        assert data["interceptors"]["existing"] == {"x": 1}
        assert data["interceptors"]["rate_limit"] == {"limit": 1}
        assert changed == ["interceptors"]

    def test_2_0_4_hook_registered(self):
        """2.0.4 升级钩子已注册（core 正名 + input/output 防御剥离）。"""
        v2_0_4_hooks = [h for h in CONFIG_UPGRADE_HOOKS if h.target_version == "2.0.4"]
        assert {h.config_file for h in v2_0_4_hooks} == {"core.toml", "input.toml", "output.toml"}


class TestStageStripHook2_0_4:
    """input/output.toml 2.0.4：剥离遗留 [pipelines] 死段。"""

    def test_input_strips_pipelines(self):
        data = {"collectors": {"enabled": ["console_input"]}, "pipelines": {"input": {"rate_limit": {}}}}
        changed = _strip_pipelines_2_0_4(data)
        assert "pipelines" not in data
        assert changed == ["pipelines"]

    def test_strip_idempotent(self):
        data = {"collectors": {"enabled": ["console_input"]}}
        changed = _strip_pipelines_2_0_4(data)
        assert changed == []


class TestToolsHook2_0_9:
    """tools.toml 2.0.9：剥离 [tools.perception.config.read_pingmu] 的 VLM 自管字段。

    审计 D1 收编：VLM 调用统一走 ``LLMManager.chat_vision``，原 aiohttp 路径的
    api_key/base_url/model_name 三字段失去消费者，剥离避免漂移告警与误导。
    """

    def test_strips_three_dead_keys(self):
        data = {
            "tools": {
                "enabled": ["perception"],
                "perception": {
                    "enabled": True,
                    "provider": "builtin",
                    "config": {
                        "enabled": ["read_pingmu"],
                        "read_pingmu": {
                            "api_key": "",
                            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                            "model_name": "qwen2.5-vl-72b-instruct",
                            "screenshot_interval": 0.3,
                            "diff_threshold": 25.0,
                            "max_cached_images": 5,
                        },
                    },
                },
            },
        }
        changed = _migrate_tools_2_0_9(data)
        assert "tools.perception.config.read_pingmu.api_key" in changed
        assert "tools.perception.config.read_pingmu.base_url" in changed
        assert "tools.perception.config.read_pingmu.model_name" in changed

        read_pingmu = data["tools"]["perception"]["config"]["read_pingmu"]
        assert "api_key" not in read_pingmu
        assert "base_url" not in read_pingmu
        assert "model_name" not in read_pingmu
        # 其他字段保留
        assert read_pingmu["screenshot_interval"] == 0.3
        assert read_pingmu["max_cached_images"] == 5

    def test_idempotent_when_keys_already_absent(self):
        data = {
            "tools": {
                "perception": {
                    "config": {
                        "read_pingmu": {
                            "screenshot_interval": 0.3,
                            "max_cached_images": 5,
                        },
                    },
                },
            },
        }
        changed = _migrate_tools_2_0_9(data)
        assert changed == []
        # 二次执行仍幂等
        changed_again = _migrate_tools_2_0_9(data)
        assert changed_again == []

    def test_noop_when_path_missing(self):
        """tools/perception/config/read_pingmu 任一层缺失均安全返回。"""

        assert _migrate_tools_2_0_9({}) == []
        assert _migrate_tools_2_0_9({"tools": {"perception": None}}) == []
        assert _migrate_tools_2_0_9({"tools": {"perception": {}}}) == []
        assert _migrate_tools_2_0_9({"tools": {"perception": {"config": {"read_pingmu": "not-a-dict"}}}}) == []
