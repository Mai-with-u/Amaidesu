"""写回管线与自写抑制：无漂移不写盘、漂移只备份一次、校验硬错、自写标记，以及采集器段物化不变量"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest
import tomlkit
from pydantic import ConfigDict

from src.modules import component_catalog
from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import (
    _validate_file,
    _write_back_schema_file,
    generate_default_configs,
    load_config_dir,
)
from src.modules.config.registry import COLLECTOR_SCHEMAS
from src.modules.config.schemas.base import BaseConfig
from src.modules.config.self_write_guard import consume_self_write, mark_self_write
from tests.config.helpers import read_doc, write_doc


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / "config"


def _drop_game_2048(config_dir: Path) -> None:
    """删掉 agents.toml 里的 game_2048 段，造出"缺段"漂移（写回会补默认）。"""
    doc = read_doc(config_dir, "agents.toml")
    del doc["agents"]["game_2048"]
    write_doc(config_dir, "agents.toml", doc)


def _settle(config_dir: Path) -> None:
    """预热：生成基线之后才注册的钩子的版本推进先行落定（这一轮会写盘并备份），再清掉备份与自写标记。"""
    load_config_dir(config_dir)
    shutil.rmtree(config_dir / "old", ignore_errors=True)
    for path in config_dir.glob("*.toml"):
        consume_self_write(path)


def _replace_enabled(config_dir: Path, file_name: str, new: str) -> None:
    """原地替换 enabled 整行（文件尾追加会落入最后一张子表，位置不对）；文件里必须恰有一行 ``enabled = [...]``。"""
    path = config_dir / file_name
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    targets = [i for i, line in enumerate(lines) if line.strip().startswith("enabled = [")]
    assert len(targets) == 1, f"enabled 行不唯一: {targets!r}"
    lines[targets[0]] = f"{new}\n"
    path.write_text("".join(lines), encoding="utf-8-sig")


def _append(config_dir: Path, file_name: str, text: str) -> None:
    path = config_dir / file_name
    path.write_text(path.read_text(encoding="utf-8-sig") + text, encoding="utf-8-sig")


def test_unchanged_load_never_writes_and_drift_write_backs_up_once(config_dir) -> None:
    generate_default_configs(config_dir)
    _settle(config_dir)
    agents_path = config_dir / "agents.toml"
    # mtime 定格到远 past：后续任何写盘得到的时间必然不同，判定"未写盘"不再
    # 依赖文件系统 mtime 分辨率与真实等待
    _FROZEN_MTIME_S = 1_000_000_000.0
    os.utime(agents_path, (_FROZEN_MTIME_S, _FROZEN_MTIME_S))
    mtime = agents_path.stat().st_mtime_ns

    _, report = load_config_dir(config_dir)
    assert not report.has_drift and agents_path.stat().st_mtime_ns == mtime
    assert not (config_dir / "old").exists()  # 无漂移：不写盘、无备份

    _drop_game_2048(config_dir)
    load_config_dir(config_dir)
    assert len(list((config_dir / "old").rglob("agents.toml"))) == 1  # 有漂移：同一批次目录里只留一份备份

    doc = tomlkit.parse(agents_path.read_text(encoding="utf-8-sig")).unwrap()  # 写回后磁盘已是序列化形态
    instance, report = _validate_file("agents.toml", doc)
    assert not report.has_drift
    assert _write_back_schema_file(config_dir, "agents.toml", type(instance), instance) is None  # 内容不变短路
    assert len(list((config_dir / "old").rglob("agents.toml"))) == 1


def test_self_write_marks_are_one_shot_expire_and_only_cover_the_written_file(config_dir, tmp_path) -> None:
    target = tmp_path / "agents.toml"
    mark_self_write(str(target))  # str 与 Path 等价
    assert consume_self_write(target) is True
    assert consume_self_write(target) is False  # 一次性消费
    assert consume_self_write(tmp_path / "never_marked.toml") is False
    mark_self_write(target, ttl_s=10.0)
    assert consume_self_write(target, now_s=0.0) is True  # 时刻 0 远未到失效点：命中
    mark_self_write(target, ttl_s=10.0)
    # 1e12 秒（约三万年后）必然越过任何单调时钟读数 + TTL 的失效点
    assert consume_self_write(target, now_s=1e12) is False  # TTL 过期（now_s 注入免去真实等待）

    generate_default_configs(config_dir)
    _settle(config_dir)
    _drop_game_2048(config_dir)
    load_config_dir(config_dir)  # 管线写回自动压标：被写文件命中一次，未写文件不命中
    assert consume_self_write(config_dir / "agents.toml") is True
    assert consume_self_write(config_dir / "agents.toml") is False
    assert consume_self_write(config_dir / "infra.toml") is False


def _remove_section(config_dir: Path, file_name: str, section: str) -> None:
    path = config_dir / file_name
    content = re.sub(rf"\[{section}\].*?(?=\n\[|\Z)", "", path.read_text(encoding="utf-8-sig"), flags=re.S)
    path.write_text(content, encoding="utf-8-sig")


def test_missing_files_are_regenerated_and_a_missing_section_is_refilled_after_one_backup(config_dir) -> None:
    generate_default_configs(config_dir)
    names = {path.name for path in config_dir.glob("*.toml")}
    for name in names - {"agents.toml"}:
        (config_dir / name).unlink()
    load_config_dir(config_dir)
    assert {path.name for path in config_dir.glob("*.toml")} == names  # 缺失文件自动补齐

    _remove_section(config_dir, "infra.toml", "events")
    load_config_dir(config_dir)
    assert "[events]" in (config_dir / "infra.toml").read_text(encoding="utf-8-sig")
    latest = max(path for path in (config_dir / "old").iterdir() if (path / "infra.toml").exists())
    assert "[events]" not in (latest / "infra.toml").read_text(encoding="utf-8-sig")  # 漂移触发的写回先备份写前的原文件


def test_legacy_agenda_keys_without_a_migration_hook_hard_fail_naming_the_hook_gap(config_dir) -> None:
    """agenda_* → rundown_* 迁移若配了钩子会先行搬走旧键；这里锁定"忘配钩子"的兜底：当面拦下而非静默清理。"""
    generate_default_configs(config_dir)
    _remove_section(config_dir, "agents.toml", "agents.streamer")
    _append(
        config_dir,
        "agents.toml",
        '\n[agents.streamer]\nagenda_enabled = true\nagenda_path = "config/agenda/live.toml"\n',
    )

    with pytest.raises(ConfigValidationError) as exc_info:
        load_config_dir(config_dir)
    assert "agenda_enabled" in str(exc_info.value) and "迁移钩子" in str(exc_info.value)


@pytest.mark.parametrize(
    ("file_name", "edit", "needles"),
    [
        ("tools.toml", lambda d: _append(d, "tools.toml", '\n[dead_tool]\nkey = "stale"\n'), ("dead_tool",)),
        (
            "collectors.toml",
            lambda d: _append(d, "collectors.toml", "\n[ghost_collector]\nenabled = true\n"),
            ("ghost_collector", "console_input"),
        ),
        (
            "collectors.toml",
            lambda d: _replace_enabled(d, "collectors.toml", 'enabled = ["console_input", "no_such_collector"]'),
            ("no_such_collector", "console_input"),
        ),
        ("agents.toml", lambda d: _replace_enabled(d, "agents.toml", 'enabled = "not-a-list"'), ()),  # 类型违约
    ],
    ids=["redundant_section", "unregistered_collector_section", "unregistered_enabled_name", "type_violation"],
)
def test_validation_failures_are_hard_errors_naming_the_offender_and_the_valid_list(
    config_dir, file_name, edit, needles
) -> None:
    generate_default_configs(config_dir)
    edit(config_dir)

    with pytest.raises(ConfigValidationError) as exc_info:
        load_config_dir(config_dir)
    assert all(needle in str(exc_info.value) for needle in needles)


def test_collectors_toml_always_holds_every_registered_section_without_touching_enabled(config_dir) -> None:
    """物化不变量：首启生成与加载存量文件后，磁盘段全集 ⊇ 注册表采集器全集；物化只补段不扩 enabled 名单。"""
    generate_default_configs(config_dir)
    assert set(COLLECTOR_SCHEMAS) <= set(read_doc(config_dir, "collectors.toml").unwrap())  # 生成路径直接物化

    _replace_enabled(config_dir, "collectors.toml", 'enabled = ["console_input"]')
    load_config_dir(config_dir)

    disk = read_doc(config_dir, "collectors.toml").unwrap()
    assert set(COLLECTOR_SCHEMAS) <= set(disk)
    assert disk["enabled"] == ["console_input"]  # 可先配后启，不自动启用


class _PseudoSchema(BaseConfig):
    """伪采集器的包内 ConfigSchema（测试注入用）。"""

    model_config = ConfigDict(extra="forbid")

    interval_ms: int = 5
    label: str = ""


@pytest.fixture
def pseudo_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """把伪组件注入注册表：包装 fill 而非 setitem——每次加载前 ensure_component_registry 会清空重灌全局 dict。"""
    real_fill = component_catalog.fill_component_schemas

    def fill_with_pseudo():
        result = real_fill()
        component_catalog.registry.COLLECTOR_SCHEMAS["pseudo"] = (
            _PseudoSchema  # collectors.toml 校验只认 COLLECTOR_SCHEMAS
        )
        result["pseudo"] = _PseudoSchema
        return result

    monkeypatch.setattr(component_catalog, "fill_component_schemas", fill_with_pseudo)


def _rewrite_pseudo_section(config_dir: Path, body: str) -> None:
    """改写已物化生成的 [pseudo] 段（物化后骨架已在，追加会重复表头）。"""
    path = config_dir / "collectors.toml"
    content = path.read_text(encoding="utf-8-sig")
    start = content.index("[pseudo]")
    end = content.find("\n[", start + 1)
    path.write_text(content[:start] + body + (content[end + 1 :] if end != -1 else ""), encoding="utf-8-sig")


def test_registered_component_sections_are_validated_by_their_own_schema(config_dir, pseudo_registry) -> None:
    generate_default_configs(config_dir)
    _rewrite_pseudo_section(config_dir, "[pseudo]\ninterval_ms = 42\n")

    config, report = load_config_dir(config_dir)
    assert config["collectors"]["pseudo"] == {"interval_ms": 42, "label": ""}  # 用户值保留 + 默认值补齐
    assert not report.has_drift

    _rewrite_pseudo_section(config_dir, '[pseudo]\ninterval_ms = "abc"\n')
    with pytest.raises(ConfigValidationError, match="pseudo"):  # 类型违约：消息含段名
        load_config_dir(config_dir)
