"""ConfigBase 漂移检测和 TOML 生成测试"""

import pytest
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig, DriftReport


class _SampleConfig(BaseConfig):
    type: str = Field(default="sample", description="类型")
    name: str = Field(default="default", description="名称")
    port: int = Field(default=8080, description="端口")


class _NestedConfig(BaseConfig):
    type: str = Field(default="nested", description="嵌套类型")
    host: str = Field(default="localhost", description="主机")


class _ParentConfig(BaseConfig):
    type: str = Field(default="parent", description="父类型")
    child: _NestedConfig = Field(default_factory=_NestedConfig)


class TestDriftReport:
    def test_empty_report_has_no_drift(self):
        report = DriftReport()
        assert not report.has_drift

    def test_redundant_triggers_drift(self):
        report = DriftReport(redundant=["zombie"])
        assert report.has_drift

    def test_missing_triggers_drift(self):
        report = DriftReport(missing=["new_field"])
        assert report.has_drift

    def test_merge_prefixes_keys(self):
        base = DriftReport()
        other = DriftReport(redundant=["x"], missing=["y"])
        base.merge("section", other)
        assert "section.x" in base.redundant
        assert "section.y" in base.missing


class TestFromDictWithDriftCheck:
    def test_normal_data_no_drift(self):
        data = {"type": "sample", "name": "test", "port": 9090}
        instance, report = _SampleConfig.from_dict_with_drift_check(data)
        assert not report.has_drift
        assert instance.name == "test"
        assert instance.port == 9090

    def test_redundant_field_detected(self):
        data = {"type": "sample", "name": "test", "port": 8080, "zombie": "bad"}
        instance, report = _SampleConfig.from_dict_with_drift_check(data)
        assert "zombie" in report.redundant
        assert not hasattr(instance, "zombie")

    def test_missing_field_uses_default(self):
        data = {"type": "sample"}
        instance, report = _SampleConfig.from_dict_with_drift_check(data)
        assert "name" in report.missing
        assert "port" in report.missing
        assert instance.name == "default"
        assert instance.port == 8080

    def test_nested_drift_detection(self):
        data = {
            "type": "parent",
            "child": {"type": "nested", "host": "1.2.3.4", "extra": "bad"},
        }
        instance, report = _ParentConfig.from_dict_with_drift_check(data)
        assert "child.extra" in report.redundant

    def test_extra_forbidden_on_direct_init(self):
        with pytest.raises(Exception):
            _SampleConfig(type="sample", name="ok", unknown="ignored")

    def test_from_dict_strips_unknown_fields(self):
        instance = _SampleConfig.from_dict({"type": "sample", "name": "ok", "unknown": "ignored"})
        assert instance.name == "ok"
        assert not hasattr(instance, "unknown")


class TestGenerateToml:
    def test_generates_toml_with_comments(self):
        toml_str = _SampleConfig.generate_toml_string(name="sample_config")
        assert "[sample_config]" in toml_str
        assert "name" in toml_str
        assert "port" in toml_str

    def test_generates_toml_string_has_description_comments(self):
        toml_str = _SampleConfig.generate_toml_string(name="test")
        assert "#" in toml_str

    def test_get_default_dict(self):
        d = _SampleConfig.get_default_dict()
        assert d["name"] == "default"
        assert d["port"] == 8080
