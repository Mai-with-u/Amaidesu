"""Input 阶段配置 Schema 测试 (Task 8)

覆盖 src/modules/config/schemas/input_schemas.py:

1. **InputConfig 根模型**: 顶层类型标识 + collectors 聚合
2. **InputCollectorsConfig**: enabled 列表 + 各 collector 子段
3. **各 Collector ConfigSchema 字段覆盖**: 与原 Collector.ConfigSchema 一致
4. **config/input.toml 实际验证**: 现有配置文件必须能通过验证
5. **UI 提示**: json_schema_extra 包含 x-label/x-icon/x-widget 等
6. **健壮性**: 缺失字段用默认值、未知字段被剥离、必填字段缺失抛 ValidationError
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import tomlkit
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_INPUT_TOML = PROJECT_ROOT / "config" / "input.toml"


@pytest.fixture(scope="module")
def real_input_toml_data() -> Dict[str, Any]:
    """读取真实 config/input.toml 的原始字典（如果存在）

    注意：input.toml 是模板文件，部分 Collector 的必填字段是占位符
    （如 bili_danmaku.room_id=0），无法通过 strict validation。
    因此该 fixture 只用于结构验证，strict validation 请使用 ``valid_input_dict``。
    """
    if not REAL_INPUT_TOML.exists():
        pytest.skip(f"config/input.toml 不存在: {REAL_INPUT_TOML}")
    with open(REAL_INPUT_TOML, "r", encoding="utf-8") as f:
        doc = tomlkit.load(f)
    return doc.unwrap()


@pytest.fixture(scope="module")
def valid_input_dict() -> Dict[str, Any]:
    """构造一个所有必填字段都填了合法值的 input.toml 字典

    用于 strict validation 测试。
    """
    return {
        "collectors": {
            "enabled": ["console_input", "stt"],
            "console_input": {
                "user_id": "console_user",
                "user_nickname": "控制台",
            },
            "stt": {
                "iflytek_asr": {
                    "appid": "test_appid",
                    "api_key": "test_key",
                    "api_secret": "test_secret",
                },
                "vad": {
                    "enable": True,
                    "vad_threshold": 0.5,
                    "silence_seconds": 1.0,
                },
                "audio": {
                    "sample_rate": 16000,
                    "channels": 1,
                    "dtype": "int16",
                },
                "message_config": {
                    "user_id": "stt_user",
                    "user_nickname": "语音",
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# 1. InputConfig 根模型
# ---------------------------------------------------------------------------


class TestInputConfigRoot:
    """InputConfig 是 input.toml 的根模型，聚合 collectors"""

    def test_input_config_importable(self):
        from src.modules.config.schemas.input_schemas import InputConfig

        assert InputConfig is not None

    def test_input_config_inherits_base_config(self):
        from src.modules.config.schemas.base import BaseConfig
        from src.modules.config.schemas.input_schemas import InputConfig

        assert issubclass(InputConfig, BaseConfig)

def test_input_config_default_instance():
    """InputConfig() 必须可用 (使用默认工厂)"""
    from src.modules.config.schemas.input_schemas import InputConfig

    cfg = InputConfig()
    assert cfg.collectors is not None
    assert cfg.collectors.enabled == []

    def test_input_config_with_collectors(self):
        """InputConfig 可通过 collectors 字段配置各 collector"""
        from src.modules.config.schemas.input_schemas import InputConfig

        cfg = InputConfig(
            collectors={
                "enabled": ["console_input"],
                "console_input": {"type": "console_input"},
            }
        )
        assert cfg.collectors.enabled == ["console_input"]
        assert cfg.collectors.console_input is not None
    def test_input_config_extras_forbidden(self):
        """未在 Schema 中定义的字段必须被 extra='forbid' 拒绝"""
        from src.modules.config.schemas.input_schemas import InputConfig

        with pytest.raises(ValidationError):
            InputConfig(collectors={"unknown_field": "bad"})


# ---------------------------------------------------------------------------
# 2. InputCollectorsConfig
# ---------------------------------------------------------------------------


class TestInputCollectorsConfig:
    """InputCollectorsConfig 包含 enabled 列表和每个 collector 的可选子配置"""

    def test_default_enabled_is_empty_list(self):
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        cfg = InputCollectorsConfig()
        assert cfg.enabled == []

    def test_enabled_accepts_list_of_strings(self):
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        cfg = InputCollectorsConfig(enabled=["console_input", "stt"])
        assert cfg.enabled == ["console_input", "stt"]

    def test_all_known_collectors_are_optional(self):
        """所有已知 collector 在 InputCollectorsConfig 中都应该是 Optional"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        # 默认所有 collector 子字段都是 None
        cfg = InputCollectorsConfig(enabled=["console_input"])
        for name in (
            "bili_danmaku",
            "bili_danmaku_official",
            "console_input",
            "mainosaba",
            "mock_danmaku",
            "read_pingmu",
            "stt",
        ):
            assert getattr(cfg, name) is None, f"{name} should be None by default"


# ---------------------------------------------------------------------------
# 3. 各 Collector ConfigSchema 字段覆盖
# ---------------------------------------------------------------------------


class TestCollectorSchemaFieldCoverage:
    """确保 input_schemas 中每个 collector ConfigSchema 覆盖了原 Collector 的字段"""

    def test_console_input_schema_fields(self):
        from src.stages.input.collectors.console_input.console_input_collector import (
            ConsoleInputCollector,
        )
        from src.modules.config.schemas.input_schemas import (
            InputCollectorsConfig,
        )

        original = ConsoleInputCollector.ConfigSchema.model_fields.keys()
        assert "user_id" in original
        assert "user_nickname" in original

        # InputCollectorsConfig.console_input 必须接受这些字段
        cfg = InputCollectorsConfig(
            console_input={
                "type": "console_input",
                "user_id": "alice",
                "user_nickname": "爱丽丝",
            }
        )
        assert cfg.console_input.user_id == "alice"
        assert cfg.console_input.user_nickname == "爱丽丝"

    def test_mock_danmaku_schema_fields(self):
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        cfg = InputCollectorsConfig(
            mock_danmaku={
                "type": "mock_danmaku",
                "log_file_path": "test.jsonl",
                "send_interval": 2.0,
                "loop_playback": False,
                "start_immediately": True,
            }
        )
        assert cfg.mock_danmaku.log_file_path == "test.jsonl"
        assert cfg.mock_danmaku.send_interval == 2.0
        assert cfg.mock_danmaku.loop_playback is False
        assert cfg.mock_danmaku.start_immediately is True

    def test_mainosaba_schema_fields(self):
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        cfg = InputCollectorsConfig(
            mainosaba={
                "type": "mainosaba",
                "full_screen": True,
                "check_interval": 2,
                "screenshot_min_interval": 0.5,
                "response_timeout": 15,
                "control_method": "enter_key",
                "click_position": [100, 200],
            }
        )
        assert cfg.mainosaba.control_method == "enter_key"
        assert cfg.mainosaba.click_position == [100, 200]

    def test_read_pingmu_schema_fields(self):
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        cfg = InputCollectorsConfig(
            read_pingmu={
                "type": "read_pingmu",
                "api_key": "sk-test",
                "base_url": "https://api.test.com/v1",
                "model_name": "gpt-4-vision",
                "screenshot_interval": 1.0,
                "diff_threshold": 30.0,
                "check_window": 5,
                "max_cache_size": 10,
                "max_cached_images": 8,
            }
        )
        assert cfg.read_pingmu.model_name == "gpt-4-vision"
        assert cfg.read_pingmu.max_cached_images == 8

    def test_bili_danmaku_required_field(self):
        """bili_danmaku 的 room_id 是必填字段，缺失必须抛 ValidationError"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        with pytest.raises(ValidationError):
            InputCollectorsConfig(
                bili_danmaku={
                    "type": "bili_danmaku",
                    # room_id 缺失
                }
            )

    def test_bili_danmaku_room_id_must_be_positive(self):
        """bili_danmaku 的 room_id 必须 > 0 (gt=0)"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        with pytest.raises(ValidationError):
            InputCollectorsConfig(
                bili_danmaku={
                    "type": "bili_danmaku",
                    "room_id": 0,  # gt=0 校验
                }
            )

    def test_bili_danmaku_official_required_fields(self):
        """bili_danmaku_official 的 4 个必填字段缺失任何一个都应抛 ValidationError"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        # 缺失 access_key_secret
        with pytest.raises(ValidationError):
            InputCollectorsConfig(
                bili_danmaku_official={
                    "type": "bili_danmaku_official",
                    "id_code": "x",
                    "app_id": "y",
                    "access_key": "z",
                    # access_key_secret 缺失
                }
            )

    def test_stt_iflytek_required_fields(self):
        """stt.iflytek_asr 的 3 个必填字段缺失任何一个都应抛 ValidationError"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        with pytest.raises(ValidationError):
            InputCollectorsConfig(
                stt={
                    "type": "stt",
                    "iflytek_asr": {
                        "appid": "test",
                        # api_key 缺失
                        # api_secret 缺失
                    },
                }
            )

    def test_stt_full_config(self):
        """stt 完整配置可正确解析"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        cfg = InputCollectorsConfig(
            stt={
                "type": "stt",
                "iflytek_asr": {
                    "appid": "abc",
                    "api_key": "k1",
                    "api_secret": "s1",
                },
                "vad": {
                    "enable": True,
                    "vad_threshold": 0.6,
                    "silence_seconds": 0.5,
                },
                "audio": {
                    "sample_rate": 8000,
                    "channels": 2,
                },
                "message_config": {
                    "user_id": "u1",
                    "user_nickname": "麦麦",
                },
            }
        )
        assert cfg.stt.iflytek_asr.appid == "abc"
        assert cfg.stt.vad.vad_threshold == 0.6
        assert cfg.stt.audio.sample_rate == 8000
        assert cfg.stt.message_config.user_nickname == "麦麦"


# ---------------------------------------------------------------------------
# 4. config/input.toml 实际验证
# ---------------------------------------------------------------------------


class TestRealConfigValidation:
    """config/input.toml 必须能通过新 schema 验证"""

    def test_valid_input_dict_validates(self, valid_input_dict):
        """合法填充的 input dict 必须能完整验证通过（无 ValidationError）"""
        from src.modules.config.schemas.input_schemas import InputConfig

        cfg, report = InputConfig.from_dict_with_drift_check(valid_input_dict)

        # 验证基本结构
        assert cfg.collectors.enabled == ["console_input", "stt"]
        assert cfg.collectors.console_input is not None
        assert cfg.collectors.console_input.user_id == "console_user"
        assert cfg.collectors.console_input.user_nickname == "控制台"

        assert cfg.collectors.stt is not None
        assert cfg.collectors.stt.iflytek_asr.appid == "test_appid"

        # 合法 dict 不应有 drift
        assert not report.redundant

    def test_real_input_toml_structural_sections(self, real_input_toml_data):
        """config/input.toml 应包含 schema 期望的所有 collector 子段

        由于 toml 是模板（含占位符如 room_id=0），不能 strict validation。
        此测试仅验证 section 结构的存在性，详细字段验证由 ``valid_input_dict`` fixture 完成。
        """

        collectors_section = real_input_toml_data.get("collectors", {})

        # enabled 列表必须存在
        assert "enabled" in collectors_section
        assert collectors_section["enabled"] == ["console_input"]

        # 至少有一个 collector 子段（console_input 在 enabled 中）
        assert "console_input" in collectors_section

    def test_real_input_toml_drift_unknown_sections(self, real_input_toml_data):
        """config/input.toml 中未在 schema 定义的 collector 子段应被报告为 redundant

        漂移检测应能识别 toml 中的未知 collector 段（如拼写错误）。
        由于 toml 包含占位符值（room_id=0 等），无法 strict validation。
        此测试通过直接对比 keys 来检测 drift，避开 strict validation。
        """
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        schema_known_keys = set(InputCollectorsConfig.model_fields.keys())
        # 保留的元数据字段
        metadata_keys = {"type", "enabled"}
        # schema 已知的 collector keys（去掉元数据字段）
        valid_keys = schema_known_keys - metadata_keys

        toml_keys = set(real_input_toml_data.get("collectors", {}).keys()) - metadata_keys
        unknown_keys = toml_keys - valid_keys

        assert not unknown_keys, f"input.toml 中存在 schema 未识别的 collector 段: {sorted(unknown_keys)}"

    def test_real_input_toml_section_keys_match_schema(self, real_input_toml_data):
        """config/input.toml 中定义的所有 collector 子段键必须被 InputCollectorsConfig 识别"""

        known_collectors = {
            "bili_danmaku",
            "bili_danmaku_official",
            "console_input",
            "mainosaba",
            "mock_danmaku",
            "read_pingmu",
            "stt",
        }

        collectors_section = real_input_toml_data.get("collectors", {})
        # 每个在 toml 中出现的 collector 必须在 schema 已知列表中（防止拼写错误）
        for key in collectors_section:
            if key in ("enabled", "type"):
                continue
            assert key in known_collectors, f"input.toml 中存在 schema 未识别的 collector 段: {key!r}"


# ---------------------------------------------------------------------------
# 5. UI 提示 (json_schema_extra)
# ---------------------------------------------------------------------------


class TestUISchemaHints:
    """Pydantic Schema 必须能被 ConfigSchemaGenerator 正确转换为 UI schema"""

    def test_control_method_is_select_in_schema(self):
        """mainosaba.control_method 是 Literal，schema generator 必须输出 select + options"""
        from src.stages.input.collectors.mainosaba.mainosaba_collector import (
            MainosabaCollector,
        )
        from src.modules.config.schema_generator import ConfigSchemaGenerator

        schema = ConfigSchemaGenerator.generate_config_schema(MainosabaCollector.ConfigSchema)
        cm_field = next(f for f in schema["fields"] if f["name"] == "control_method")
        # Literal 自动映射为 select + options
        assert cm_field["type"] == "select"
        assert set(cm_field["options"]) == {"mouse_click", "enter_key", "space_key"}

    def test_stt_iflytek_schema_generates_correctly(self):
        """STT 的 iflytek_asr 嵌套子配置应能被 schema generator 正确处理"""
        from src.stages.input.collectors.stt.config import IflytekAsrConfig
        from src.modules.config.schema_generator import ConfigSchemaGenerator

        schema = ConfigSchemaGenerator.generate_config_schema(IflytekAsrConfig)
        # 至少要包含必填的 appid / api_key / api_secret 字段
        field_names = {f["name"] for f in schema["fields"]}
        assert "appid" in field_names
        assert "api_key" in field_names
        assert "api_secret" in field_names
        # appid / api_key / api_secret 是必填字段
        for required_name in ("appid", "api_key", "api_secret"):
            f = next(f for f in schema["fields"] if f["name"] == required_name)
            assert f["required"] is True, f"{required_name} should be required"

    def test_console_input_schema_basic_fields(self):
        """console_input 应暴露 user_id 和 user_nickname 字段"""
        from src.stages.input.collectors.console_input.console_input_collector import (
            ConsoleInputCollector,
        )
        from src.modules.config.schema_generator import ConfigSchemaGenerator

        schema = ConfigSchemaGenerator.generate_config_schema(ConsoleInputCollector.ConfigSchema)
        field_names = {f["name"] for f in schema["fields"]}
        assert "user_id" in field_names
        assert "user_nickname" in field_names


# ---------------------------------------------------------------------------
# 6. 健壮性
# ---------------------------------------------------------------------------


class TestRobustness:
    """边界条件：缺失/无效/类型错误的处理"""

    def test_unknown_collector_field_is_stripped(self):
        """InputCollectorsConfig 中未定义的 collector 子段必须被剥离（不抛错）"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        # 包含一个未知的 collector
        _, report = InputCollectorsConfig.from_dict_with_drift_check(
            {
                "enabled": ["console_input"],
                "console_input": {"type": "console_input", "user_id": "x"},
                "totally_unknown_collector": {"foo": "bar"},
            }
        )
        assert "totally_unknown_collector" in report.redundant

    def test_extra_field_in_collector_is_stripped(self):
        """collector 子段中的未知字段必须被剥离（不抛错）"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        _, report = InputCollectorsConfig.from_dict_with_drift_check(
            {
                "console_input": {
                    "type": "console_input",
                    "user_id": "x",
                    "totally_unknown_field": "bad",
                },
            }
        )
        assert "console_input.totally_unknown_field" in report.redundant

    def test_unknown_collector_rejected(self):
        """未知 collector 名称必须被拒绝"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        with pytest.raises(ValidationError):
            InputCollectorsConfig(
                unknown_collector={
                    "user_id": "x",
                }
            )

    def test_wrong_int_for_bili_danmaku_poll_interval(self):
        """bili_danmaku.poll_interval 是 int 类型，传 str 必须失败"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        with pytest.raises(ValidationError):
            InputCollectorsConfig(
                bili_danmaku={
                    "room_id": 1,
                    "poll_interval": "three",  # 应该是 int
                }
            )

    def test_missing_required_room_id(self):
        """bili_danmaku 缺失必填 room_id 必须失败"""
        from src.modules.config.schemas.input_schemas import InputCollectorsConfig

        with pytest.raises(ValidationError):
            InputCollectorsConfig(
                bili_danmaku={
                    "poll_interval": 3,
                }
            )

    def test_default_factory_creates_empty_config(self):
        """InputConfig() 默认值应该没有任何 collector 配置"""
        from src.modules.config.schemas.input_schemas import InputConfig

        cfg = InputConfig()
        assert cfg.collectors.enabled == []
        for name in (
            "bili_danmaku",
            "bili_danmaku_official",
            "console_input",
            "mainosaba",
            "mock_danmaku",
            "read_pingmu",
            "stt",
        ):
            assert getattr(cfg.collectors, name) is None


# ---------------------------------------------------------------------------
# 7. helper 函数
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """get_input_collector_config 工厂函数"""

    def test_get_input_collector_config_returns_schema_instance(self):
        from src.modules.config.schemas.input_schemas import get_input_collector_config

        cfg = get_input_collector_config(
            "console_input",
            {"user_id": "x", "user_nickname": "麦麦"},
        )
        assert cfg.user_id == "x"
        assert cfg.user_nickname == "麦麦"

    def test_get_input_collector_config_raises_for_unknown(self):
        from src.modules.config.schemas.input_schemas import get_input_collector_config

        with pytest.raises(ValueError):
            get_input_collector_config("nonexistent", {})

    def test_input_pipelines_config_default(self):
        """InputPipelinesConfig 默认 pipelines 是空 dict"""
        from src.modules.config.schemas.input_schemas import InputPipelinesConfig

        cfg = InputPipelinesConfig()
        assert cfg.pipelines == {}
