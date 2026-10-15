"""BaseConfig 漂移检测（冗余/缺失/嵌套路径）、from_dict 剥离未知字段、TOML 生成"""

import pytest
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig, DriftReport


class _Sample(BaseConfig):
    type: str = Field(default="sample", description="类型")
    name: str = Field(default="default", description="名称")
    port: int = Field(default=8080, description="端口")


class _Nested(BaseConfig):
    type: str = Field(default="nested", description="嵌套类型")
    host: str = Field(default="localhost", description="主机")


class _Parent(BaseConfig):
    type: str = Field(default="parent", description="父类型")
    child: _Nested = Field(default_factory=_Nested)


def test_drift_report_flags_and_prefixes_merged_keys() -> None:
    assert not DriftReport().has_drift
    assert DriftReport(redundant=["zombie"]).has_drift and DriftReport(missing=["new_field"]).has_drift
    base = DriftReport()
    base.merge("section", DriftReport(redundant=["x"], missing=["y"]))
    assert "section.x" in base.redundant and "section.y" in base.missing


def test_drift_check_reports_redundant_missing_and_nested_paths() -> None:
    instance, report = _Sample.from_dict_with_drift_check({"type": "sample", "name": "test", "port": 9090})
    assert not report.has_drift and (instance.name, instance.port) == ("test", 9090)

    instance, report = _Sample.from_dict_with_drift_check({"type": "sample", "name": "t", "port": 1, "zombie": "bad"})
    assert "zombie" in report.redundant and not hasattr(instance, "zombie")

    instance, report = _Sample.from_dict_with_drift_check({"type": "sample"})
    assert {"name", "port"} <= set(report.missing) and (instance.name, instance.port) == (
        "default",
        8080,
    )  # 缺失键用默认值

    _, report = _Parent.from_dict_with_drift_check(
        {"type": "parent", "child": {"type": "nested", "host": "1.2.3.4", "extra": "bad"}}
    )
    assert "child.extra" in report.redundant


def test_unknown_fields_are_forbidden_on_init_but_stripped_by_from_dict() -> None:
    with pytest.raises(Exception):  # noqa: B017 - extra=forbid 的具体异常类型是 pydantic 内部细节
        _Sample(type="sample", name="ok", unknown="ignored")
    instance = _Sample.from_dict({"type": "sample", "name": "ok", "unknown": "ignored"})
    assert instance.name == "ok" and not hasattr(instance, "unknown")


def test_toml_generation_includes_fields_comments_and_default_dict() -> None:
    text = _Sample.generate_toml_string(name="sample_config")
    assert "[sample_config]" in text and "name" in text and "port" in text and "#" in text  # 描述渲染为注释
    defaults = _Sample.get_default_dict()
    assert (defaults["name"], defaults["port"]) == ("default", 8080)
