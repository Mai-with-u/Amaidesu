"""写回管线与自写抑制的基线测试

覆盖新机制中的缺口面：
- 写回短路：内容不变时不写盘、不产生备份
- 冗余物理删除：写回后磁盘上的冗余段真实消失
- 自写抑制：标记/消费/TTL 语义 + 管线写回自动压标（FileWatcher 跳过的前提）
- 校验硬错：类型违约 / 未注册采集器段 / 未注册 enabled 名单
- 注册表动态装配：伪组件注入后按其包内 Schema 校验子段
"""

from __future__ import annotations

import shutil
import time

import pytest
from pydantic import ConfigDict

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import (
    _validate_file,
    _write_back_schema_file,
    generate_default_configs,
    load_config_dir,
)
from src.modules.config.schemas.base import BaseConfig
from src.modules.config.self_write_guard import consume_self_write, mark_self_write


@pytest.fixture
def temp_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir, ignore_errors=True)


def _append(config_dir, file_name: str, text: str) -> None:
    path = config_dir / file_name
    path.write_text(path.read_text(encoding="utf-8-sig") + text, encoding="utf-8-sig")


class TestWritebackShortCircuit:
    def test_unchanged_content_no_rewrite(self, temp_config_dir):
        """无漂移的常规加载不写盘：mtime 不动、无备份目录。"""
        generate_default_configs(temp_config_dir)
        agents_path = temp_config_dir / "agents.toml"
        mtime_before = agents_path.stat().st_mtime_ns
        # 同目录内其他文件刚生成，mtime 分辨率内可能相同，先推开
        time.sleep(0.01)

        _config, report = load_config_dir(temp_config_dir)

        assert not report.has_drift
        assert agents_path.stat().st_mtime_ns == mtime_before
        assert not (temp_config_dir / "old").exists()

    def test_serialize_identical_returns_none(self, temp_config_dir):
        """内容不变短路：对已写回（序列化形态）的文件重复写回 → 不写盘返回 None。"""
        generate_default_configs(temp_config_dir)
        _append(temp_config_dir, "agents.toml", "\nstale_section = true\n")
        load_config_dir(temp_config_dir)  # 第一次写回：磁盘进入序列化形态

        import tomlkit

        with open(temp_config_dir / "agents.toml", "r", encoding="utf-8-sig") as f:
            raw = tomlkit.load(f).unwrap()
        instance, report = _validate_file("agents.toml", raw)
        assert not report.has_drift

        backup = _write_back_schema_file(temp_config_dir, "agents.toml", type(instance), instance)
        assert backup is None
        assert len(list((temp_config_dir / "old").rglob("agents.toml"))) == 1

    def test_drift_write_creates_backup_once(self, temp_config_dir):
        """有漂移才写盘：一次加载产生的备份在同一个批次目录。"""
        generate_default_configs(temp_config_dir)
        _append(temp_config_dir, "agents.toml", "\nstale_section = true\n")

        _config, _report = load_config_dir(temp_config_dir)

        old_dir = temp_config_dir / "old"
        assert old_dir.is_dir()
        backups = list(old_dir.rglob("agents.toml"))
        assert len(backups) == 1


class TestRedundantPhysicalRemoval:
    def test_redundant_section_removed_from_disk(self, temp_config_dir):
        """写回后冗余段在磁盘上真实消失（非仅报告层剥离）。"""
        generate_default_configs(temp_config_dir)
        _append(temp_config_dir, "tools.toml", '\n[dead_tool]\nkey = "stale"\n')

        config, report = load_config_dir(temp_config_dir)

        assert config["tools"].get("dead_tool") is None
        assert not report.has_drift  # 写回后残余漂移为净
        disk_text = (temp_config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        assert "dead_tool" not in disk_text


class TestSelfWriteGuard:
    def test_mark_then_consume_once(self, tmp_path):
        target = tmp_path / "agents.toml"
        mark_self_write(target)
        assert consume_self_write(target) is True
        assert consume_self_write(target) is False  # 一次性消费

    def test_ttl_expiry(self, tmp_path):
        target = tmp_path / "agents.toml"
        mark_self_write(target, ttl_s=0.01)
        time.sleep(0.05)
        assert consume_self_write(target) is False

    def test_unmarked_path_not_consumed(self, tmp_path):
        assert consume_self_write(tmp_path / "never_marked.toml") is False

    def test_string_path_matches_path_object(self, tmp_path):
        target = tmp_path / "agents.toml"
        mark_self_write(str(target))
        assert consume_self_write(target) is True


class TestPipelineMarksSelfWrite:
    def test_writeback_marks_touched_file_only(self, temp_config_dir):
        """管线写回自动压标：被写文件命中一次，未写文件不命中。"""
        generate_default_configs(temp_config_dir)
        _append(temp_config_dir, "agents.toml", "\nstale_section = true\n")

        load_config_dir(temp_config_dir)

        assert consume_self_write(temp_config_dir / "agents.toml") is True
        assert consume_self_write(temp_config_dir / "agents.toml") is False
        assert consume_self_write(temp_config_dir / "infra.toml") is False


def _replace_enabled(config_dir, file_name: str, old: str, new: str) -> None:
    """原地替换既有 enabled 行（文件尾追加会落入最后一张子表，位置不对）。"""
    path = config_dir / file_name
    content = path.read_text(encoding="utf-8-sig")
    assert content.count(old) == 1, f"锚点行不唯一: {old!r}"
    path.write_text(content.replace(old, new), encoding="utf-8-sig")


class TestValidationHardFail:
    def test_type_violation_raises(self, temp_config_dir):
        """字段类型违约 → 硬错，无 raw dict 降级。"""
        generate_default_configs(temp_config_dir)
        _replace_enabled(
            temp_config_dir,
            "agents.toml",
            'enabled = ["streamer"]',
            'enabled = "not-a-list"',
        )

        with pytest.raises(ConfigValidationError):
            load_config_dir(temp_config_dir)

    def test_unregistered_collector_section_raises(self, temp_config_dir):
        """未注册的采集器段 → ConfigValidationError（Typo 防护）。"""
        generate_default_configs(temp_config_dir)
        _append(temp_config_dir, "collectors.toml", "\n[ghost_collector]\nenabled = true\n")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        assert exc_info.value.file_name == "collectors.toml"
        assert "ghost_collector" in str(exc_info.value)

    def test_unregistered_enabled_name_raises(self, temp_config_dir):
        """enabled 名单出现未注册名 → ConfigValidationError。"""
        generate_default_configs(temp_config_dir)
        _replace_enabled(
            temp_config_dir,
            "collectors.toml",
            'enabled = ["console_input"]',
            'enabled = ["console_input", "no_such_collector"]',
        )

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        assert "no_such_collector" in str(exc_info.value)


class _PseudoSchema(BaseConfig):
    """伪采集器的包内 ConfigSchema（测试注入用）。"""

    model_config = ConfigDict(extra="forbid")

    interval_ms: int = 5
    label: str = ""


@pytest.fixture
def pseudo_registry(monkeypatch):
    """把伪组件注入注册表：包装 fill 而非 setitem——每次加载前
    ensure_component_registry 都会清空重灌全局 dict，直塞条目必被冲掉。"""
    from src.modules.config import registry as registry_module

    real_fill = registry_module.fill_component_schemas

    def _fill_with_pseudo():
        result = real_fill()
        result["pseudo"] = _PseudoSchema
        return result

    monkeypatch.setattr(registry_module, "fill_component_schemas", _fill_with_pseudo)


class TestRegistryDynamicAssembly:
    def test_registered_pseudo_section_validated_and_written(self, temp_config_dir, pseudo_registry):
        """伪组件注入注册表后：子段按其 Schema 校验，漂移写回落盘。"""
        generate_default_configs(temp_config_dir)
        _append(
            temp_config_dir,
            "collectors.toml",
            "\n[pseudo]\ninterval_ms = 42\nstale_key = 1\n",
        )

        config, report = load_config_dir(temp_config_dir)

        # 子段值可读 + 冗余键写回清理 + 默认值全量补齐
        assert config["collectors"]["pseudo"]["interval_ms"] == 42
        assert "stale_key" not in config["collectors"]["pseudo"]
        assert config["collectors"]["pseudo"]["label"] == ""
        assert not report.has_drift
        disk_text = (temp_config_dir / "collectors.toml").read_text(encoding="utf-8-sig")
        assert "stale_key" not in disk_text

    def test_pseudo_type_violation_raises_value_error(self, temp_config_dir, pseudo_registry):
        """伪组件子段类型违约 → ConfigValidationError 且消息含段名。"""
        generate_default_configs(temp_config_dir)
        _append(temp_config_dir, "collectors.toml", '\n[pseudo]\ninterval_ms = "abc"\n')

        with pytest.raises(ConfigValidationError, match="pseudo"):
            load_config_dir(temp_config_dir)
