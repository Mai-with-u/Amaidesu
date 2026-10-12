"""写回管线与自写抑制的基线测试

覆盖新机制中的缺口面：
- 写回短路：内容不变时不写盘、不产生备份
- 冗余物理删除：写回后磁盘上的冗余段真实消失
- 自写抑制：标记/消费/TTL 语义 + 管线写回自动压标（FileWatcher 跳过的前提）
- 校验硬错：类型违约 / 未注册采集器段与 enabled 名 → 硬错（点名 + 合法名单）
- 注册表动态装配：伪组件注入后按其包内 Schema 校验子段
- 物化不变量：加载后 collectors.toml 段全集 ⊇ 注册表采集器全集
"""

from __future__ import annotations

import shutil
import time

import pytest
from loguru import logger as _loguru_logger
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
    content = path.read_text(encoding="utf-8-sig")
    # 文件尾可能是叶子子表（如 [agents.text_adv.keys]），直接尾追会掉进子表
    # 内部而非顶级段；改为插在首个 Agent 子段前，保证落在 [agents] 顶级段
    marker = "\n[agents.streamer]"
    if file_name == "agents.toml" and marker in content:
        content = content.replace(marker, "\n" + text.strip("\n") + marker, 1)
    else:
        content = content + text
    path.write_text(content, encoding="utf-8-sig")


def _remove_key(config_dir, file_name: str, *path: str) -> None:
    """从 TOML 文件删除指定键（造 missing 漂移：缺键由写回补默认）。"""
    import tomlkit

    target = config_dir / file_name
    with open(target, encoding="utf-8-sig") as f:
        doc = tomlkit.load(f)
    node = doc
    for p in path[:-1]:
        node = node[p]
    del node[path[-1]]
    with open(target, "w", encoding="utf-8-sig") as f:
        tomlkit.dump(doc, f)


class TestWritebackShortCircuit:
    def test_unchanged_content_no_rewrite(self, temp_config_dir):
        """无漂移的常规加载不写盘：mtime 不动、无备份目录。"""
        generate_default_configs(temp_config_dir)
        # 预热：让生成基线之后新注册钩子的版本推进先行落定（此轮会写盘+备份）
        load_config_dir(temp_config_dir)
        shutil.rmtree(temp_config_dir / "old", ignore_errors=True)
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
        _remove_key(temp_config_dir, "agents.toml", "agents", "game_2048")
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
        _remove_key(temp_config_dir, "agents.toml", "agents", "game_2048")

        _config, _report = load_config_dir(temp_config_dir)

        old_dir = temp_config_dir / "old"
        assert old_dir.is_dir()
        backups = list(old_dir.rglob("agents.toml"))
        assert len(backups) == 1


class TestRedundantHardFail:
    def test_redundant_section_hard_fails(self, temp_config_dir):
        """冗余段全域硬错：文件里程序不认识的段在加载期当面拦下（不再静默清理）。"""
        generate_default_configs(temp_config_dir)
        _append(temp_config_dir, "tools.toml", '\n[dead_tool]\nkey = "stale"\n')

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        assert "dead_tool" in str(exc_info.value)


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
        # 预热：生成基线之后新钩子的版本推进先行落定，并清掉预热轮的自写标记
        load_config_dir(temp_config_dir)
        consume_self_write(temp_config_dir / "infra.toml")
        _remove_key(temp_config_dir, "agents.toml", "agents", "game_2048")

        load_config_dir(temp_config_dir)

        assert consume_self_write(temp_config_dir / "agents.toml") is True
        assert consume_self_write(temp_config_dir / "agents.toml") is False
        assert consume_self_write(temp_config_dir / "infra.toml") is False


def _replace_enabled(config_dir, file_name: str, new: str) -> None:
    """原地替换 enabled 整行（文件尾追加会落入最后一张子表，位置不对）。

    默认名单会随组件增减变化，所以不按旧值匹配、只按"整行"定位：
    文件里必须恰好有一行 ``enabled = [...]``。
    """
    path = config_dir / file_name
    content = path.read_text(encoding="utf-8-sig")
    lines = content.splitlines(keepends=True)
    targets = [i for i, line in enumerate(lines) if line.strip().startswith("enabled = [")]
    assert len(targets) == 1, f"enabled 行不唯一: {targets!r}"
    lines[targets[0]] = f"{new}\n"
    path.write_text("".join(lines), encoding="utf-8-sig")


class _LoguruCapture:
    """内存里捕获 loguru 日志（项目用 loguru，caplog 不适用）。"""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._sink_id: int | None = None

    def __enter__(self):
        def _sink(message) -> None:
            record = message.record
            self.records.append(
                {
                    "level": record["level"].name,
                    "message": record["message"],
                    "module": record["name"],
                }
            )

        self._sink_id = _loguru_logger.add(_sink, level="DEBUG")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._sink_id is not None:
            _loguru_logger.remove(self._sink_id)
            self._sink_id = None


class TestValidationHardFail:
    def test_type_violation_raises(self, temp_config_dir):
        """字段类型违约 → 硬错，无 raw dict 降级。"""
        generate_default_configs(temp_config_dir)
        _replace_enabled(
            temp_config_dir,
            "agents.toml",
            'enabled = "not-a-list"',
        )

        with pytest.raises(ConfigValidationError):
            load_config_dir(temp_config_dir)

    def test_unregistered_collector_section_hard_fails(self, temp_config_dir):
        """未注册的采集器段 → 硬错，消息含段名与合法名单。"""
        generate_default_configs(temp_config_dir)
        _append(temp_config_dir, "collectors.toml", "\n[ghost_collector]\nenabled = true\n")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        message = str(exc_info.value)
        assert "ghost_collector" in message
        assert "console_input" in message

    def test_unregistered_enabled_name_hard_fails(self, temp_config_dir):
        """enabled 名单出现未注册名 → 硬错，消息含名字与合法名单。"""
        generate_default_configs(temp_config_dir)
        _replace_enabled(
            temp_config_dir,
            "collectors.toml",
            'enabled = ["console_input", "maicraft_attention", "no_such_collector"]',
        )

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        message = str(exc_info.value)
        assert "no_such_collector" in message
        assert "console_input" in message


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
        # 采集器名单与合并视图都注入：collectors.toml 校验只认 COLLECTOR_SCHEMAS
        registry_module.COLLECTOR_SCHEMAS["pseudo"] = _PseudoSchema
        result["pseudo"] = _PseudoSchema
        return result

    monkeypatch.setattr(registry_module, "fill_component_schemas", _fill_with_pseudo)


class TestMaterializationInvariant:
    def test_disk_contains_full_collector_set_after_load(self, temp_config_dir):
        """物化不变量：加载任意存量 collectors.toml 后，磁盘段全集 ⊇ 注册表采集器全集。

        这是"9-12 补全机制被删、全库无红"事故的直接免疫护栏——
        任何让"文件缺在册段"重新静默发生的改动都会在这里红。
        按解析后的表名断言（段全是子表时 tomlkit 合法省略超表头，
        文本匹配 [name] 会误报）。
        """
        import tomlkit

        from src.modules.config.registry import COLLECTOR_SCHEMAS

        generate_default_configs(temp_config_dir)
        load_config_dir(temp_config_dir)

        with open(temp_config_dir / "collectors.toml", encoding="utf-8-sig") as f:
            disk_data = tomlkit.load(f).unwrap()
        for name in COLLECTOR_SCHEMAS:
            assert name in disk_data, f"物化不变量破坏：磁盘 collectors.toml 缺在册段 [{name}]"

    def test_generated_file_materialized_without_load(self, temp_config_dir):
        """生成路径物化：generate_default_configs 产出的文件直接含段全集
        （不经加载管线补齐）——新用户首启看到的文件就是可配置全集。"""
        import tomlkit

        from src.modules.config.registry import COLLECTOR_SCHEMAS

        generate_default_configs(temp_config_dir)

        with open(temp_config_dir / "collectors.toml", encoding="utf-8-sig") as f:
            disk_data = tomlkit.load(f).unwrap()
        for name in COLLECTOR_SCHEMAS:
            assert name in disk_data, f"首启生成文件缺在册段 [{name}]（生成路径物化失效）"

    def test_materialization_does_not_touch_enabled(self, temp_config_dir):
        """物化只补段不扩名单：enabled 保持用户控制（可先配后启，不自动启用）。"""
        import tomlkit

        generate_default_configs(temp_config_dir)
        _replace_enabled(temp_config_dir, "collectors.toml", 'enabled = ["console_input"]')

        load_config_dir(temp_config_dir)

        with open(temp_config_dir / "collectors.toml", encoding="utf-8-sig") as f:
            disk_data = tomlkit.load(f).unwrap()
        assert disk_data["enabled"] == ["console_input"]
        for name in ("bili_danmaku", "stt"):
            assert name in disk_data


class TestRegistryDynamicAssembly:
    @staticmethod
    def _rewrite_pseudo_section(temp_config_dir, section_body: str) -> None:
        """改写已物化生成的 [pseudo] 段（生成路径接物化后骨架不再存在，追加会重复表头）。"""
        path = temp_config_dir / "collectors.toml"
        content = path.read_text(encoding="utf-8-sig")
        start = content.index("[pseudo]")
        end = content.find("\n[", start + 1)
        replaced = content[:start] + section_body + (content[end + 1 :] if end != -1 else "")
        path.write_text(replaced, encoding="utf-8-sig")

    def test_registered_pseudo_section_validated_and_written(self, temp_config_dir, pseudo_registry):
        """伪组件注入注册表后：子段按其 Schema 校验，漂移写回落盘。"""
        generate_default_configs(temp_config_dir)
        self._rewrite_pseudo_section(temp_config_dir, "[pseudo]\ninterval_ms = 42\n")

        config, report = load_config_dir(temp_config_dir)

        # 子段值可读 + 用户值保留 + 默认值全量补齐
        assert config["collectors"]["pseudo"]["interval_ms"] == 42
        assert config["collectors"]["pseudo"]["label"] == ""
        assert not report.has_drift

    def test_pseudo_type_violation_raises_value_error(self, temp_config_dir, pseudo_registry):
        """伪组件子段类型违约 → ConfigValidationError 且消息含段名。"""
        generate_default_configs(temp_config_dir)
        self._rewrite_pseudo_section(temp_config_dir, '[pseudo]\ninterval_ms = "abc"\n')

        with pytest.raises(ConfigValidationError, match="pseudo"):
            load_config_dir(temp_config_dir)
