"""升级 Hook 单元测试（每条 hook 配测试，旧→新结构断言）

AGENTS.md 钩子契约：
- 原地修改：hook 接收 dict 并直接修改
- 幂等：重复执行结果一致
- 返回变更路径列表
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
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
    _migrate_tools_2_0_10,
    _strip_pipelines_2_0_4,
    _version_in_range,
    apply_upgrade_hooks,
)


@pytest.fixture
def config_dir(tmp_path):
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir)


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


class TestToolsHook2_0_10:
    """tools.toml 2.0.10：剥离 TTS 基础设施重塑遗留的 9 个死字段。

    收尾对象：
    - 3 个 OutputHandlersConfig 调度字段（concurrent_rendering / error_handling /
      completion_timeout_ms）
    - 4 个 Provider 的 output_device_name（edge_tts / gptsovits / omni_tts / voicebox）
    - 2 个 OmniTTS 死字段（use_vts_lip_sync / use_subtitle）

    render_timeout_ms 由 CrossFileMigration 移走，不在本钩子职责内。
    """

    def test_strips_nine_dead_fields(self):
        data = {
            "tools": {
                "output": {
                    "config": {
                        "concurrent_rendering": True,
                        "error_handling": "continue",
                        "completion_timeout_ms": 30000,
                        "edge_tts": {
                            "voice": "zh-CN-XiaoxiaoNeural",
                            "output_device_name": "",
                        },
                        "gptsovits": {
                            "host": "127.0.0.1",
                            "output_device_name": "Speaker",
                        },
                        "omni_tts": {
                            "host": "127.0.0.1",
                            "output_device_name": "",
                            "use_vts_lip_sync": True,
                            "use_subtitle": True,
                        },
                        "voicebox": {
                            "host": "127.0.0.1",
                            "output_device_name": "USB Audio",
                        },
                    },
                },
            },
        }
        changed = _migrate_tools_2_0_10(data)
        assert "tools.output.config.concurrent_rendering" in changed
        assert "tools.output.config.error_handling" in changed
        assert "tools.output.config.completion_timeout_ms" in changed
        assert "tools.output.config.edge_tts.output_device_name" in changed
        assert "tools.output.config.gptsovits.output_device_name" in changed
        assert "tools.output.config.omni_tts.output_device_name" in changed
        assert "tools.output.config.voicebox.output_device_name" in changed
        assert "tools.output.config.omni_tts.use_vts_lip_sync" in changed
        assert "tools.output.config.omni_tts.use_subtitle" in changed

        cfg = data["tools"]["output"]["config"]
        assert "concurrent_rendering" not in cfg
        assert "error_handling" not in cfg
        assert "completion_timeout_ms" not in cfg
        for provider_key in ("edge_tts", "gptsovits", "omni_tts", "voicebox"):
            assert "output_device_name" not in cfg[provider_key]
        assert "use_vts_lip_sync" not in cfg["omni_tts"]
        assert "use_subtitle" not in cfg["omni_tts"]
        assert cfg["edge_tts"]["voice"] == "zh-CN-XiaoxiaoNeural"
        assert cfg["gptsovits"]["host"] == "127.0.0.1"
        assert cfg["omni_tts"]["host"] == "127.0.0.1"
        assert cfg["voicebox"]["host"] == "127.0.0.1"

    def test_render_timeout_ms_left_for_cross_file_migration(self):
        """render_timeout_ms 不在钩子职责内，保留供 CrossFileMigration 移走。"""
        data = {
            "tools": {
                "output": {
                    "config": {
                        "render_timeout_ms": 8000,
                        "concurrent_rendering": True,
                    },
                },
            },
        }
        changed = _migrate_tools_2_0_10(data)
        assert "tools.output.config.concurrent_rendering" in changed
        assert not any(c.startswith("tools.output.config.render_timeout_ms") for c in changed)
        assert data["tools"]["output"]["config"]["render_timeout_ms"] == 8000

    def test_idempotent(self):
        """重复执行时死字段已不存在，无事发生。"""
        data = {
            "tools": {
                "output": {
                    "config": {
                        "edge_tts": {"voice": "zh-CN-XiaoxiaoNeural"},
                        "omni_tts": {"host": "127.0.0.1"},
                    },
                },
            },
        }
        _migrate_tools_2_0_10(data)
        changed_again = _migrate_tools_2_0_10(data)
        assert changed_again == []

    def test_noop_when_path_missing(self):
        """tools/output/config 任一层缺失或类型错误均安全返回。"""
        assert _migrate_tools_2_0_10({}) == []
        assert _migrate_tools_2_0_10({"tools": None}) == []
        assert _migrate_tools_2_0_10({"tools": {}}) == []
        assert _migrate_tools_2_0_10({"tools": {"output": None}}) == []
        assert _migrate_tools_2_0_10({"tools": {"output": {}}}) == []
        assert _migrate_tools_2_0_10({"tools": {"output": {"config": None}}}) == []
        assert _migrate_tools_2_0_10({"tools": {"output": {"config": "not-a-dict"}}}) == []

    def test_skips_provider_when_cfg_not_dict(self):
        """Provider 子段非 dict 时跳过该 Provider 的 output_device_name 处理（保留其他字段）。"""
        data = {
            "tools": {
                "output": {
                    "config": {
                        "edge_tts": "invalid-string-config",
                        "concurrent_rendering": True,
                    },
                },
            },
        }
        changed = _migrate_tools_2_0_10(data)
        assert "tools.output.config.concurrent_rendering" in changed
        assert not any("edge_tts.output_device_name" in c for c in changed)
        assert data["tools"]["output"]["config"]["edge_tts"] == "invalid-string-config"

    def test_2_0_10_hook_registered(self):
        """2.0.10 升级钩子已注册且作用于 tools.toml。"""
        v2_0_10_hooks = [h for h in CONFIG_UPGRADE_HOOKS if h.target_version == "2.0.10"]
        assert len(v2_0_10_hooks) == 1
        assert v2_0_10_hooks[0].config_file == "tools.toml"


class TestCrossFileMigrationRenderTimeout:
    """CrossFileMigration 单值深嵌套移动：tools.toml → core.toml [tts].render_timeout_ms。"""

    def test_migration_registered(self):
        """新的 render_timeout_ms 迁移已在 CROSS_FILE_MIGRATIONS 注册。"""
        from src.modules.config.multi_file_loader import CROSS_FILE_MIGRATIONS

        matches = [
            m
            for m in CROSS_FILE_MIGRATIONS
            if m.source_file == "tools.toml"
            and m.target_file == "core.toml"
            and m.target_key == "tts"
            and m.source_nested_path == ("output", "config", "render_timeout_ms")
            and m.target_nested_path == ("render_timeout_ms",)
        ]
        assert len(matches) == 1, "应有且仅有一条 render_timeout_ms 迁移"

    def test_driller_helpers_basic(self):
        """_drill_get / _drill_set / _drill_del 嵌套访问契约。"""
        from src.modules.config.multi_file_loader import _drill_del, _drill_get, _drill_set

        data: dict = {"a": {"b": {"c": 42}}}
        assert _drill_get(data, ("a", "b", "c")) == 42
        assert _drill_get(data, ("a", "missing", "c")) is None
        assert _drill_get(data, ("a", "b")) == {"c": 42}

        _drill_set(data, ("a", "b", "d"), 7)
        assert data["a"]["b"]["d"] == 7
        _drill_set(data, ("x", "y"), "created")
        assert data["x"]["y"] == "created"

        _drill_del(data, ("a", "b", "c"))
        assert "c" not in data["a"]["b"]
        _drill_del(data, ("missing", "path"))
        assert "missing" not in data
        _drill_del(data, ("a", "not-dict", "deep"))
        assert data["a"]["b"] == {"d": 7}


class TestCrossFileMigrationEngineSubSections:
    """TTS 引擎连接/合成参数整体迁移：tools.toml [tools.output.config.<engine>]
    → core.toml [tts.<engine>]；用户原值完整搬运，源子段从 tools.toml 删除。

    覆盖四个引擎（edge_tts / gptsovits / voicebox / omni_tts）；每条迁移走
    CrossFileMigration 单值深嵌套路径，行为同 render_timeout_ms 但目标
    是 dict 而非 scalar（_drill_set 自动创建中间 dict）。
    """

    ENGINES = ("edge_tts", "gptsovits", "voicebox", "omni_tts")

    def test_all_four_engine_migrations_registered(self):
        """四条引擎迁移均已注册（每引擎各一条，路径与 render_timeout_ms 模式一致）。"""
        from src.modules.config.multi_file_loader import CROSS_FILE_MIGRATIONS

        for engine in self.ENGINES:
            matches = [
                m
                for m in CROSS_FILE_MIGRATIONS
                if m.source_file == "tools.toml"
                and m.target_file == "core.toml"
                and m.target_key == "tts"
                and m.source_nested_path == ("output", "config", engine)
                and m.target_nested_path == (engine,)
            ]
            assert len(matches) == 1, f"应有且仅有一条 {engine} 引擎迁移"

    def test_single_engine_move_preserves_user_values(self):
        """单引擎迁移：源子段存在时，目标 [tts.<engine>] 完整承接用户原值。"""
        from src.modules.config.multi_file_loader import _drill_del, _drill_get, _drill_set

        # 模拟 tools.toml 原始结构（含 edge_tts 引擎子段）
        source_doc = {
            "tools": {
                "output": {
                    "config": {
                        "edge_tts": {
                            "type": "edge_tts",
                            "voice": "zh-CN-XiaoxiaoNeural",
                        },
                    },
                },
            },
        }
        # 模拟 core.toml 目标内存数据（已有 [tts] 段）
        target_raw = {"tts": {"enabled": True, "provider": "edge_tts"}}

        user_values = _drill_get(source_doc, ("tools", "output", "config", "edge_tts"))
        assert user_values == {"type": "edge_tts", "voice": "zh-CN-XiaoxiaoNeural"}

        _drill_set(target_raw, ("tts", "edge_tts"), user_values)
        _drill_del(source_doc, ("tools", "output", "config", "edge_tts"))

        assert target_raw["tts"]["edge_tts"] == {
            "type": "edge_tts",
            "voice": "zh-CN-XiaoxiaoNeural",
        }
        assert target_raw["tts"]["enabled"] is True
        assert target_raw["tts"]["provider"] == "edge_tts"
        assert "edge_tts" not in source_doc["tools"]["output"]["config"]

    def test_missing_engine_section_is_noop(self):
        """源子段不存在时（用户未配置某引擎）→ _drill_get 返回 None → 跳过迁移。"""
        from src.modules.config.multi_file_loader import _drill_get

        source_doc = {
            "tools": {
                "output": {"config": {"subtitle": {"window_width": 800}}},
            },
        }
        assert _drill_get(source_doc, ("tools", "output", "config", "gptsovits")) is None

    def test_nested_path_target_creates_intermediate_dicts(self):
        """_drill_set 沿 path 自动创建缺失的中间 dict（target 没有 [tts.<engine>] 时仍可写入）。"""
        from src.modules.config.multi_file_loader import _drill_set

        target: dict = {}
        _drill_set(target, ("tts", "voicebox"), {"profile_id": "abc123", "host": "127.0.0.1"})
        assert target == {"tts": {"voicebox": {"profile_id": "abc123", "host": "127.0.0.1"}}}

        # 中间层类型非 dict 时重置为 dict（保证嵌套可写入）
        target2 = {"tts": "not-a-dict"}
        _drill_set(target2, ("tts", "omni_tts"), {"host": "127.0.0.1"})
        assert target2 == {"tts": {"omni_tts": {"host": "127.0.0.1"}}}


class TestEngineSubSectionMigrationEndToEnd:
    """端到端：通过 load_config_dir 走完整迁移闭环，验证：
    - tools.toml 含引擎子段 → core.toml [tts.<engine>] 自动承接用户值
    - tools.toml 源子段被剥离（[tools.output.config.<engine>] 不再出现）
    - 第二次加载不再触发额外迁移/备份（幂等）
    """

    def _write_user_tools_with_engines(self, config_dir) -> None:
        """在生成的 tools.toml 基础上追加 4 个引擎子段，模拟 v2.0.11 旧配置。

        自动生成的 tools.toml 不含 [tools.output]（默认 None 时模板不输出）；
        测试先确保 [tools.output.config] 段存在再追加引擎子段。
        """
        import tomlkit

        tools_path = config_dir / "tools.toml"
        doc = tomlkit.parse(tools_path.read_text(encoding="utf-8-sig")).unwrap()
        # 保证 [tools.output.config] 段存在
        tools = doc.setdefault("tools", {})
        output = tools.setdefault("output", {})
        cfg = output.setdefault("config", {})
        cfg.setdefault("enabled", ["subtitle", "vts"])
        cfg["edge_tts"] = {
            "type": "edge_tts",
            "voice": "zh-CN-XiaoxiaoNeural",
        }
        cfg["gptsovits"] = {
            "type": "gptsovits",
            "host": "127.0.0.1",
            "port": 9880,
            "ref_audio_path": "/data/ref.wav",
            "prompt_text": "示例文本",
            "text_language": "zh",
            "prompt_language": "zh",
            "top_k": 20,
            "top_p": 0.6,
            "temperature": 0.3,
            "speed_factor": 1.0,
            "streaming_mode": True,
            "media_type": "wav",
            "text_split_method": "cut5",
            "batch_size": 1,
            "batch_threshold": 0.7,
            "repetition_penalty": 1.0,
            "sample_steps": 10,
            "super_sampling": True,
            "sample_rate": 32000,
        }
        cfg["voicebox"] = {
            "type": "voicebox",
            "host": "127.0.0.1",
            "port": 17493,
            "profile_id": "user-cloned-profile-001",
            "language": "zh",
            "sample_rate": 24000,
            "generation_timeout_ms": 120000,
        }
        cfg["omni_tts"] = {
            "type": "omni_tts",
            "host": "127.0.0.1",
            "port": 9880,
            "ref_audio_path": "",
            "prompt_text": "",
            "text_language": "zh",
            "prompt_language": "zh",
            "top_k": 20,
            "top_p": 0.6,
            "temperature": 0.3,
            "speed_factor": 1.0,
            "streaming_mode": True,
            "sample_rate": 32000,
            "use_text_cleanup": True,
        }
        tools_path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")

    def test_engines_migrated_to_core_with_values_preserved(self, config_dir):
        """端到端：含引擎子段的旧 tools.toml → core.toml [tts.<engine>] 完整承接。"""
        import shutil

        shutil.rmtree(config_dir / "old", ignore_errors=True)
        self._write_user_tools_with_engines(config_dir)

        config, _ = load_config_dir(config_dir)

        tts = config["core"]["tts"]
        # 调度字段保留
        assert tts["enabled"] is True
        # 各引擎子段完整搬运
        assert tts["edge_tts"] == {"type": "edge_tts", "voice": "zh-CN-XiaoxiaoNeural"}
        assert tts["gptsovits"]["host"] == "127.0.0.1"
        assert tts["gptsovits"]["ref_audio_path"] == "/data/ref.wav"
        assert tts["gptsovits"]["streaming_mode"] is True
        assert tts["gptsovits"]["sample_rate"] == 32000
        assert tts["voicebox"]["profile_id"] == "user-cloned-profile-001"
        assert tts["voicebox"]["generation_timeout_ms"] == 120000
        assert tts["omni_tts"]["use_text_cleanup"] is True
        assert tts["omni_tts"]["sample_rate"] == 32000

    def test_engine_subsections_removed_from_tools_toml(self, config_dir):
        """端到端：迁移后 tools.toml 中 [tools.output.config.<engine>] 子段全部消失。"""
        self._write_user_tools_with_engines(config_dir)
        load_config_dir(config_dir)

        tools_text = (config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        for engine in ("edge_tts", "gptsovits", "voicebox", "omni_tts"):
            # 引擎子段头不应再出现
            assert f"[tools.output.config.{engine}]" not in tools_text, (
                f"{engine} 子段应已从 tools.toml 移除"
            )
        # enabled 列表保留（测试 helper 写入的 ["subtitle", "vts"]）
        assert '"subtitle"' in tools_text
        assert '"vts"' in tools_text

    def test_migration_idempotent(self, config_dir):
        """第二次加载不应再触发迁移/写回（迁移后的状态是稳定的）。"""
        import shutil

        shutil.rmtree(config_dir / "old", ignore_errors=True)
        self._write_user_tools_with_engines(config_dir)

        # 第一次加载完成迁移
        load_config_dir(config_dir)
        backup_count_first = (
            len(list((config_dir / "old").rglob("*.toml")))
            if (config_dir / "old").exists()
            else 0
        )
        core_text_first = (config_dir / "core.toml").read_text(encoding="utf-8-sig")
        tools_text_first = (config_dir / "tools.toml").read_text(encoding="utf-8-sig")

        # 第二次加载
        config2, _ = load_config_dir(config_dir)
        backup_count_second = (
            len(list((config_dir / "old").rglob("*.toml")))
            if (config_dir / "old").exists()
            else 0
        )

        # 备份数应一致（无新写入）
        assert backup_count_second == backup_count_first
        # 核心文件内容应不变（无漂移重写）
        assert (config_dir / "core.toml").read_text(encoding="utf-8-sig") == core_text_first
        assert (config_dir / "tools.toml").read_text(encoding="utf-8-sig") == tools_text_first
        # 内存中的引擎数据仍存在
        tts = config2["core"]["tts"]
        assert tts["gptsovits"]["host"] == "127.0.0.1"
        assert tts["voicebox"]["profile_id"] == "user-cloned-profile-001"

    def test_partial_engine_migration_when_user_has_only_some(self, config_dir):
        """用户只配了部分引擎（仅 gptsovits）→ 只迁移该引擎；其他引擎留空。"""
        import tomlkit

        tools_path = config_dir / "tools.toml"
        doc = tomlkit.parse(tools_path.read_text(encoding="utf-8-sig")).unwrap()
        # 自动生成模板不含 [tools.output]（默认 None 时跳过），手动确保段存在
        doc.setdefault("tools", {}).setdefault("output", {}).setdefault("config", {})
        doc["tools"]["output"]["config"]["gptsovits"] = {
            "host": "192.168.1.10",
            "port": 9880,
            "ref_audio_path": "/custom/path.wav",
        }
        tools_path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")

        config, _ = load_config_dir(config_dir)
        tts = config["core"]["tts"]

        # gptsovits 承接用户值
        assert tts["gptsovits"]["host"] == "192.168.1.10"
        assert tts["gptsovits"]["ref_audio_path"] == "/custom/path.wav"
        # 其他引擎子段按各自 ConfigSchema 补全为完整默认字段（子段补全机制，
        # 用户可切换 provider 后无需再跑补全）；默认值来自引擎 Schema 而非用户文件
        assert tts["edge_tts"]["type"] == "edge_tts"
        assert tts["voicebox"]["profile_id"] == ""
        assert isinstance(tts["omni_tts"], dict) and "host" in tts["omni_tts"]
        # 其他引擎子段未出现在 tools.toml（之前也没有）
        tools_text = tools_path.read_text(encoding="utf-8-sig")
        assert "[tools.output.config.edge_tts]" not in tools_text
        assert "[tools.output.config.voicebox]" not in tools_text
        assert "[tools.output.config.omni_tts]" not in tools_text

    def test_engine_subsections_coexist_with_non_tts_siblings(self, config_dir):
        """迁移不损害 [tools.output.config] 的非 TTS 子段（vts 等）。"""
        import tomlkit

        tools_path = config_dir / "tools.toml"
        doc = tomlkit.parse(tools_path.read_text(encoding="utf-8-sig")).unwrap()
        cfg = doc.setdefault("tools", {}).setdefault("output", {}).setdefault("config", {})
        cfg["gptsovits"] = {"host": "127.0.0.1", "port": 9880}
        cfg["vts"] = {"type": "vts", "lip_sync_enabled": True}
        tools_path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")

        load_config_dir(config_dir)

        tools_text = tools_path.read_text(encoding="utf-8-sig")
        # 非 TTS 子段保留（vts 在 helpers 中写入，迁移只移走 gptsovits）
        assert "[tools.output.config.vts]" in tools_text
        assert "lip_sync_enabled = true" in tools_text
        # TTS 子段已迁出
        assert "[tools.output.config.gptsovits]" not in tools_text


class TestCoreTTSSchema:
    """core_schemas.TTSConfig：v2.0.10 新增 [tts] 段；v2.0.12 新增四个引擎子段。"""

    def test_tts_defaults(self):
        from src.modules.config.core_schemas import TTSConfig

        cfg = TTSConfig()
        assert cfg.enabled is True
        assert cfg.provider == "gptsovits"
        assert cfg.max_queue == 3
        assert cfg.render_timeout_ms == 60000
        # 引擎子段默认空 dict（free-form，per-key 校验由 provider schema 补全机制负责）
        assert cfg.edge_tts == {}
        assert cfg.gptsovits == {}
        assert cfg.voicebox == {}
        assert cfg.omni_tts == {}

    def test_tts_engine_subsection_accepts_arbitrary_keys(self):
        """引擎子段是 free-form dict：任意键值对均接受（详细校验在引擎模块内）。"""
        from src.modules.config.core_schemas import TTSConfig

        cfg = TTSConfig(
            gptsovits={
                "host": "127.0.0.1",
                "port": 9880,
                "ref_audio_path": "/data/ref.wav",
                "top_k": 20,
                "streaming_mode": True,
                "unknown_future_field": "preserved",
            },
            voicebox={"profile_id": "profile-x", "generation_timeout_ms": 60000},
        )
        assert cfg.gptsovits["host"] == "127.0.0.1"
        assert cfg.gptsovits["unknown_future_field"] == "preserved"
        assert cfg.voicebox["profile_id"] == "profile-x"

    def test_tts_validation(self):
        """max_queue / render_timeout_ms 受约束（ge>=>= 0 / max=20）。"""
        import pytest

        from src.modules.config.core_schemas import TTSConfig

        cfg = TTSConfig(max_queue=10, render_timeout_ms=0)
        assert cfg.max_queue == 10
        assert cfg.render_timeout_ms == 0

        with pytest.raises(Exception):
            TTSConfig(max_queue=0)
        with pytest.raises(Exception):
            TTSConfig(max_queue=21)
        with pytest.raises(Exception):
            TTSConfig(render_timeout_ms=-1)

    def test_core_config_has_tts_section(self):
        """CoreConfig 已挂载 [tts] 字段。"""
        from src.modules.config.core_schemas import CoreConfig, TTSConfig

        core = CoreConfig()
        assert isinstance(core.tts, TTSConfig)
        assert core.tts.enabled is True

    def test_meta_config_version_default(self):
        """MetaConfig.version 默认值与 multi_file_loader.CONFIG_VERSION 同步（改一必改二）。"""
        from src.modules.config.core_schemas import MetaConfig
        from src.modules.config.multi_file_loader import CONFIG_VERSION

        assert MetaConfig().version == CONFIG_VERSION
