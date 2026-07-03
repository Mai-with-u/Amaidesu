"""decision.toml 配置 Schema 测试 (Task 10)

覆盖目标：
1. 各 Decider（llm / maibot / amaidesu / command / replay）的 ConfigSchema 字段类型、默认值、约束
2. DecisionConfig 聚合模型对 `config/decision.toml` 全文件验证通过
3. `enabled` 列表验证
4. json_schema_extra (UI 元数据) 保留
5. BaseConfig 的 from_dict_with_drift_check 行为
6. TOML 生成与回环

约束：
- 不修改任何 decider 实现代码
- 不修改 multi_file_loader
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import tomlkit
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION_TOML_PATH = REPO_ROOT / "config" / "decision.toml"


def _import_base_config():
    from src.modules.config.schemas.base import BaseConfig

    return BaseConfig


def _import_under_test():
    from src.modules.config.schemas.decision_schemas import (
        AmaidesuDeciderConfigSchema,
        CommandDeciderConfigSchema,
        DecisionConfig,
        DecisionDecidersConfig,
        DecisionPipelinesConfig,
        LLMDeciderConfigSchema,
        MaiBotDeciderConfigSchema,
        ReplayDeciderConfigSchema,
    )

    return {
        "LLMDeciderConfigSchema": LLMDeciderConfigSchema,
        "MaiBotDeciderConfigSchema": MaiBotDeciderConfigSchema,
        "AmaidesuDeciderConfigSchema": AmaidesuDeciderConfigSchema,
        "CommandDeciderConfigSchema": CommandDeciderConfigSchema,
        "ReplayDeciderConfigSchema": ReplayDeciderConfigSchema,
        "DecisionDecidersConfig": DecisionDecidersConfig,
        "DecisionPipelinesConfig": DecisionPipelinesConfig,
        "DecisionConfig": DecisionConfig}


@pytest.fixture
def schemas():
    """懒加载被测 schema 模块"""
    return _import_under_test()


@pytest.fixture
def real_decision_toml_data() -> dict[str, Any]:
    """加载真实 config/decision.toml 的原始数据"""
    with open(DECISION_TOML_PATH, "rb") as f:
        return tomllib.load(f)


# =====================================================================
# 1. LLMDeciderConfigSchema
# =====================================================================


class TestLLMDeciderConfigSchema:
    """LLM Decider 配置 Schema"""

    def test_subclass_base_config(self, schemas):
        assert issubclass(schemas["LLMDeciderConfigSchema"], _import_base_config())

    def test_default_values(self, schemas):
        cls = schemas["LLMDeciderConfigSchema"]
        cfg = cls()

        assert cfg.client == "llm"
        assert cfg.fallback_mode == "simple"

    def test_parses_minimal_dict(self, schemas):
        cls = schemas["LLMDeciderConfigSchema"]
        cfg = cls.model_validate({})
        assert cfg.client == "llm"
        assert cfg.fallback_mode == "simple"

    def test_parses_explicit_dict(self, schemas):
        cls = schemas["LLMDeciderConfigSchema"]
        cfg = cls.model_validate({ "client": "llm_fast", "fallback_mode": "echo"})
        assert cfg.client == "llm_fast"
        assert cfg.fallback_mode == "echo"

    def test_invalid_client_rejected(self, schemas):
        cls = schemas["LLMDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "client": "nonsense"})

    def test_invalid_fallback_mode_rejected(self, schemas):
        cls = schemas["LLMDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "fallback_mode": "shout"})


# =====================================================================
# 2. MaiBotDeciderConfigSchema
# =====================================================================


class TestMaiBotDeciderConfigSchema:
    """MaiBot Decider 配置 Schema"""

    def test_subclass_base_config(self, schemas):
        assert issubclass(schemas["MaiBotDeciderConfigSchema"], _import_base_config())

    def test_default_values(self, schemas):
        cls = schemas["MaiBotDeciderConfigSchema"]
        cfg = cls()

        assert cfg.host == "localhost"
        assert cfg.port == 8000
        assert cfg.platform == "amaidesu"
        assert cfg.token is None
        assert cfg.connect_timeout == 10.0
        assert cfg.reconnect_interval == 5.0

    def test_parses_full_dict(self, schemas):
        cls = schemas["MaiBotDeciderConfigSchema"]
        cfg = cls.model_validate(
            {
                "host": "192.168.1.10",
                "port": 9000,
                "platform": "myplatform",
                "token": "secret-token",
                "connect_timeout": 3.5,
                "reconnect_interval": 7.0}
        )
        assert cfg.host == "192.168.1.10"
        assert cfg.port == 9000
        assert cfg.platform == "myplatform"
        assert cfg.token == "secret-token"
        assert cfg.connect_timeout == 3.5
        assert cfg.reconnect_interval == 7.0

    def test_port_below_1_rejected(self, schemas):
        cls = schemas["MaiBotDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "port": 0})

    def test_port_above_65535_rejected(self, schemas):
        cls = schemas["MaiBotDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "port": 70000})

    def test_connect_timeout_must_be_positive(self, schemas):
        cls = schemas["MaiBotDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "connect_timeout": 0.0})

    def test_reconnect_interval_must_be_positive(self, schemas):
        cls = schemas["MaiBotDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "reconnect_interval": -1.0})

    def test_token_optional(self, schemas):
        cls = schemas["MaiBotDeciderConfigSchema"]
        cfg = cls.model_validate({})
        assert cfg.token is None


# =====================================================================
# 3. AmaidesuDeciderConfigSchema  (stream-heavy: stage1 + stage2 fields)
# =====================================================================


class TestAmaidesuDeciderConfigSchema:
    """Amaidesu Decider 配置 Schema — 直播专用"""

    def test_subclass_base_config(self, schemas):
        assert issubclass(schemas["AmaidesuDeciderConfigSchema"], _import_base_config())

    def test_default_values(self, schemas):
        cls = schemas["AmaidesuDeciderConfigSchema"]
        cfg = cls()

        # Stage 2 LLM
        assert cfg.client == "llm_fast"
        assert cfg.fallback_mode == "silent"
        assert cfg.history_limit == 10
        # Stage 1 弹幕聚合
        assert cfg.batch_window_ms == 1500
        assert cfg.batch_max_size == 8
        assert cfg.tick_interval_ms == 300
        # Stage 1 节奏门控
        assert cfg.participation_rate == 0.3
        assert cfg.force_data_types == ["super_chat", "guard"]
        assert cfg.force_importance == 0.8
        assert cfg.no_action_backoff_base_ms == 8000
        assert cfg.no_action_backoff_cap_ms == 60000
        # 可选 LLM 节奏门控
        assert cfg.use_llm_timing_gate is False
        # 动作选择
        assert cfg.enable_action_selection is True
        # 人设默认值
        assert cfg.bot_name == "爱德丝"

    def test_parses_real_decision_toml_block(self, schemas):
        """config/decision.toml 中 [deciders.amaidesu] 段必须能解析"""
        cls = schemas["AmaidesuDeciderConfigSchema"]
        cfg = cls.model_validate(
            {
                "participation_rate": 1.0,
                "force_data_types": ["super_chat", "guard"],
                "force_importance": 0.8,
                "batch_window_ms": 1500,
                "batch_max_size": 8,
                "client": "llm_fast",
                "fallback_mode": "silent",
                "use_llm_timing_gate": False}
        )
        assert cfg.participation_rate == 1.0
        assert cfg.fallback_mode == "silent"
        assert cfg.use_llm_timing_gate is False
        # 未提供字段应用默认值
        assert cfg.batch_window_ms == 1500

    def test_participation_rate_range(self, schemas):
        cls = schemas["AmaidesuDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "participation_rate": -0.1})
        with pytest.raises(ValidationError):
            cls.model_validate({ "participation_rate": 1.5})

    def test_force_importance_range(self, schemas):
        cls = schemas["AmaidesuDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "force_importance": 1.5})

    def test_tick_interval_min_50ms(self, schemas):
        cls = schemas["AmaidesuDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "tick_interval_ms": 10})

    def test_batch_max_size_min_1(self, schemas):
        cls = schemas["AmaidesuDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "batch_max_size": 0})

    def test_invalid_client_rejected(self, schemas):
        cls = schemas["AmaidesuDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "client": "llm_huge"})

    def test_invalid_fallback_mode_rejected(self, schemas):
        """Amaidesu 的 fallback_mode 不包含 'error'（与 llm decider 不同）"""
        cls = schemas["AmaidesuDeciderConfigSchema"]
        with pytest.raises(ValidationError):
            cls.model_validate({ "fallback_mode": "error"})


# =====================================================================
# 4. CommandDeciderConfigSchema
# =====================================================================


class TestCommandDeciderConfigSchema:
    """Command Decider 配置 Schema"""

    def test_subclass_base_config(self, schemas):
        assert issubclass(schemas["CommandDeciderConfigSchema"], _import_base_config())

    def test_default_values(self, schemas):
        cls = schemas["CommandDeciderConfigSchema"]
        cfg = cls()

        assert cfg.command_prefix == "/"
        # command_mappings 有 default_factory，提供非空默认
        assert isinstance(cfg.command_mappings, dict)
        assert cfg.command_mappings.get("chat") == "chat"
        assert cfg.command_mappings.get("attack") == "attack"

    def test_parses_custom_mappings(self, schemas):
        cls = schemas["CommandDeciderConfigSchema"]
        cfg = cls.model_validate(
            {
                "command_prefix": "!",
                "command_mappings": {"hello": "say_hello"}}
        )
        assert cfg.command_prefix == "!"
        assert cfg.command_mappings == {"hello": "say_hello"}

    def test_empty_mappings_allowed(self, schemas):
        cls = schemas["CommandDeciderConfigSchema"]
        cfg = cls.model_validate({ "command_mappings": {}})
        assert cfg.command_mappings == {}


# =====================================================================
# 5. ReplayDeciderConfigSchema
# =====================================================================


class TestReplayDeciderConfigSchema:
    """Replay Decider 配置 Schema"""

    def test_subclass_base_config(self, schemas):
        assert issubclass(schemas["ReplayDeciderConfigSchema"], _import_base_config())

    def test_default_values(self, schemas):
        cls = schemas["ReplayDeciderConfigSchema"]
        cfg = cls()

        assert cfg.add_default_action is True

    def test_parses_dict_with_false(self, schemas):
        cls = schemas["ReplayDeciderConfigSchema"]
        cfg = cls.model_validate({ "add_default_action": False})
        assert cfg.add_default_action is False


# =====================================================================
# 6. DecisionPipelinesConfig
# =====================================================================


class TestDecisionPipelinesConfig:
    """Decision 阶段管道配置（预留扩展，当前为空）"""

    def test_defaults_to_valid_instance(self, schemas):
        cls = schemas["DecisionPipelinesConfig"]
        cfg = cls()
        dump = cfg.model_dump()
        assert isinstance(dump, dict)

    def test_parses_empty_dict(self, schemas):
        cls = schemas["DecisionPipelinesConfig"]
        cfg = cls.model_validate({})
        assert isinstance(cfg.model_dump(), dict)


# =====================================================================
# 7. DecisionDecidersConfig — 聚合
# =====================================================================


class TestDecisionDecidersConfig:
    """DecisionDecidersConfig 聚合所有 Decider 子配置"""

    def test_subclass_base_config(self, schemas):
        assert issubclass(schemas["DecisionDecidersConfig"], _import_base_config())

    def test_default_enabled_empty_list(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        cfg = cls()
        assert cfg.enabled == []

    def test_all_deciders_default_none(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        cfg = cls()
        for name in ("llm", "maibot", "amaidesu", "command", "replay"):
            assert getattr(cfg, name) is None, f"{name} 默认应 None"

    def test_deciders_type_field(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        cfg = cls()
        assert cfg is not None

    def test_enabled_accepts_known_decider(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        cfg = cls.model_validate({"enabled": ["amaidesu", "llm"]})
        assert cfg.enabled == ["amaidesu", "llm"]

    def test_enabled_accepts_all_known(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        cfg = cls.model_validate({"enabled": ["llm", "maibot", "amaidesu", "command", "replay"]})
        assert len(cfg.enabled) == 5

    def test_enabled_accepts_empty_list(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        cfg = cls.model_validate({"enabled": []})
        assert cfg.enabled == []

    def test_enabled_rejects_unknown_decider(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        with pytest.raises(ValidationError):
            cls.model_validate({"enabled": ["unknown_decider"]})

    def test_includes_amaidesu_when_provided(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        cfg = cls.model_validate({"amaidesu": {"participation_rate": 0.5}})
        assert cfg.amaidesu is not None
        assert cfg.amaidesu.participation_rate == 0.5

    def test_unknown_decider_subconfig_rejected(self, schemas):
        cls = schemas["DecisionDecidersConfig"]
        with pytest.raises(ValidationError):
            cls.model_validate({"made_up_decider": {"made_up_key": "value"}})


# =====================================================================
# 8. DecisionConfig — 顶层聚合
# =====================================================================


class TestDecisionConfigDefaults:
    """DecisionConfig 默认值"""

    def test_subclass_base_config(self, schemas):
        assert issubclass(schemas["DecisionConfig"], _import_base_config())

    def test_deciders_block_present(self, schemas):
        cls = schemas["DecisionConfig"]
        cfg = cls()
        assert cfg.deciders is not None
        assert isinstance(cfg.deciders, schemas["DecisionDecidersConfig"])

    def test_pipelines_block_present(self, schemas):
        cls = schemas["DecisionConfig"]
        cfg = cls()
        assert cfg.pipelines is not None


class TestDecisionConfigParseRealToml:
    """对真实 config/decision.toml 做完整 validation"""

    def test_full_file_validates(self, schemas, real_decision_toml_data):
        cls = schemas["DecisionConfig"]
        cfg = cls.model_validate(real_decision_toml_data)
        assert cfg.deciders is not None

    def test_no_redundant_on_real_file(self, schemas, real_decision_toml_data):
        """真实 config/decision.toml 不应有冗余字段（多余字段=真正的bug，安全网）

        注：missing 字段（Schema 有、Config 无）会被默认值填充，不视为错误，
        drift 报告仅用于提示用户。运行期行为由 model_validate 保证。
        """
        cls = schemas["DecisionConfig"]
        cfg, report = cls.from_dict_with_drift_check(real_decision_toml_data)
        assert not report.redundant, f"real decision.toml 出现冗余字段: {report.redundant}"

    def test_enabled_parses_to_list(self, schemas, real_decision_toml_data):
        cls = schemas["DecisionConfig"]
        cfg = cls.model_validate(real_decision_toml_data)
        assert cfg.deciders.enabled == ["amaidesu"]

    def test_amaidesu_block_parses(self, schemas, real_decision_toml_data):
        cls = schemas["DecisionConfig"]
        cfg = cls.model_validate(real_decision_toml_data)
        amaidesu = cfg.deciders.amaidesu
        assert amaidesu is not None
        assert amaidesu.participation_rate == 1.0

    def test_llm_block_parses(self, schemas, real_decision_toml_data):
        cls = schemas["DecisionConfig"]
        cfg = cls.model_validate(real_decision_toml_data)
        llm = cfg.deciders.llm
        assert llm is not None
        assert llm.client == "llm"

    def test_maibot_block_parses(self, schemas, real_decision_toml_data):
        cls = schemas["DecisionConfig"]
        cfg = cls.model_validate(real_decision_toml_data)
        maibot = cfg.deciders.maibot
        assert maibot is not None
        assert maibot.host == "localhost"

    def test_replay_block_parses(self, schemas, real_decision_toml_data):
        cls = schemas["DecisionConfig"]
        cfg = cls.model_validate(real_decision_toml_data)
        replay = cfg.deciders.replay
        assert replay is not None
        assert replay.add_default_action is True


class TestDecisionConfigDriftDetection:
    """from_dict_with_drift_check 行为"""

    def test_redundant_top_level_field(self, schemas):
        cls = schemas["DecisionConfig"]
        cfg, report = cls.from_dict_with_drift_check({"deciders": {"enabled": ["amaidesu"]}, "zombie_top": "bad"})
        assert "zombie_top" in report.redundant


# =====================================================================
# 9. json_schema_extra (UI 元数据)
# =====================================================================


class TestUIMetadata:
    """json_schema_extra 必须保留给 Dashboard 前端"""

    def test_deciders_enabled_has_x_ui_metadata(self, schemas):
        """enabled 列表字段应有 UI 元数据"""
        cls = schemas["DecisionDecidersConfig"]
        field_info = cls.model_fields["enabled"]
        extra = field_info.json_schema_extra or {}
        # 至少应包含一个 UI 提示
        assert extra, "enabled 字段应携带 json_schema_extra"

    def test_amaidesu_fallback_mode_options_present(self, schemas):
        """fallback_mode 字段应有 x-options 给前端 select"""
        cls = schemas["AmaidesuDeciderConfigSchema"]
        field_info = cls.model_fields["fallback_mode"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-options") == ["silent", "simple", "echo"]
        assert extra.get("x-ui-type") == "select"

    def test_amaidesu_client_options_present(self, schemas):
        cls = schemas["AmaidesuDeciderConfigSchema"]
        field_info = cls.model_fields["client"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-options") == ["llm", "llm_fast", "vlm"]
        assert extra.get("x-ui-type") == "select"

    def test_llm_fallback_mode_options_present(self, schemas):
        cls = schemas["LLMDeciderConfigSchema"]
        field_info = cls.model_fields["fallback_mode"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-options") == ["simple", "echo", "error"]
        assert extra.get("x-ui-type") == "select"

    def test_maibot_port_has_range_hint(self, schemas):
        """port 字段应有 x-min/x-max 给前端做范围校验提示"""
        cls = schemas["MaiBotDeciderConfigSchema"]
        field_info = cls.model_fields["port"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-min") == 1
        assert extra.get("x-max") == 65535

    def test_decision_deciders_subconfig_is_optional(self, schemas):
        """每个 Decider 子配置字段应有 None 默认（未启用时不要求）"""
        cls = schemas["DecisionDecidersConfig"]
        for name in ("llm", "maibot", "amaidesu", "command", "replay"):
            field_info = cls.model_fields[name]
            assert field_info.default is None, f"{name} 应默认 None"


# =====================================================================
# 10. TOML 生成与回环
# =====================================================================


class TestTomlRoundTrip:
    """TOML 生成与回环"""

    def test_generate_deciders_toml_template(self, schemas):
        """generate_toml_string 产出 [deciders] 子表（默认值模板）"""
        cls = schemas["DecisionDecidersConfig"]

        toml_str = cls.generate_toml_string(name="deciders", include_comments=False)
        doc = tomlkit.loads(toml_str)
        raw = doc["deciders"].unwrap()

        cfg2 = cls.model_validate(raw)
        # enabled 默认空 list；其余子配置默认值均为 None
        assert cfg2.enabled == []
        assert cfg2.amaidesu is None
        assert cfg2.llm is None
        assert cfg2.maibot is None
        assert cfg2.command is None
        assert cfg2.replay is None

    def test_dump_and_reload_preserves_values(self, schemas):
        """顶层 DecisionConfig：model_dump() ↔ model_validate() 来回不丢值"""
        cls = schemas["DecisionConfig"]
        cfg = cls.model_validate(
            {
                "deciders": {
                    "enabled": ["amaidesu"],
                    "amaidesu": { "participation_rate": 0.5}}
            }
        )

        cfg2 = cls.model_validate(cfg.model_dump())
        assert cfg2.deciders.enabled == cfg.deciders.enabled
        assert cfg2.deciders.amaidesu.participation_rate == 0.5
