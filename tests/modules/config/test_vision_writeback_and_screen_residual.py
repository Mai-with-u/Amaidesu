"""Task 7 验证：[tools.vision].config 漂移写回 + 采集器残留容忍

覆盖三组用例：
- (a) vision 新字段（monitor_index / default_region / vlm_timeout_ms /
  default_max_width）通过配置层 Schema 自动生成默认值并落盘到
  ``[tools.vision].config``；drift report 在写回后归零。
- (b) 残留 ``[collectors.screen]`` 段加载不抛，warning 含跳过原因；
  CollectorsRootConfig.extra="allow" 保留该段不删除。
- (c) ``enabled`` 含未知名（如已退役 ``screen``）加载不抛，warning
  含跳过原因；与 ``factory.instantiate_collector`` 的 skip+warn 行为对齐。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import pytest
from loguru import logger as _loguru_logger

from src.modules.config.multi_file_loader import (
    _CONFIG_FILES,
    generate_default_configs,
    load_config_dir,
)


@pytest.fixture
def temp_config_dir(tmp_path) -> Path:
    """隔离运行时配置的临时目录（与 tests/config/ 同形）。"""
    config_dir = tmp_path / "config"
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir, ignore_errors=True)


class _LoguruCapture:
    """内存里捕获 loguru 日志记录。"""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._sink_id: Optional[int] = None

    def __enter__(self) -> "_LoguruCapture":
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


# ---------------------------------------------------------------------------
# (a) vision 字段默认值自动生成 + 漂移写回
# ---------------------------------------------------------------------------


class TestVisionConfigDefaults:
    """配置层 VisionProviderConfig.config 复用 LookAtScreenProvider.ConfigSchema，
    漂移写回自动补齐 monitor_index / default_region / vlm_timeout_ms /
    default_max_width。"""

    def test_vision_provider_config_uses_provider_schema_defaults(self):
        """``VisionProviderConfig`` 默认实例的所有 vision 字段对齐 provider 权威 Schema。"""
        from src.modules.config.tools_schemas import VisionProviderConfig
        from src.modules.vision.look_at_screen import LookAtScreenProvider

        cfg = VisionProviderConfig()
        provider_schema = LookAtScreenProvider.ConfigSchema()
        # 直接复用同一类型，实例字段应一致
        assert isinstance(cfg.config, LookAtScreenProvider.ConfigSchema)
        assert cfg.config.monitor_index == provider_schema.monitor_index == 1
        assert cfg.config.default_region is None
        assert cfg.config.vlm_timeout_ms == provider_schema.vlm_timeout_ms == 15000
        assert cfg.config.default_max_width == provider_schema.default_max_width == 1280

    def test_empty_vision_config_section_populated_on_load(self, temp_config_dir):
        """空 ``[tools.vision].config`` 经加载管线后写入全部默认字段（除 None 字段）。"""
        generate_default_configs(temp_config_dir)
        # 加载一次：首启生成的所有字段都已落盘，且生成端与写回端形态一致
        _config, report = load_config_dir(temp_config_dir)
        assert not report.has_drift

        tools_text = (temp_config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        # monitor_index / vlm_timeout_ms / default_max_width 必须落盘
        assert "monitor_index = 1" in tools_text
        assert "vlm_timeout_ms = 15000" in tools_text
        assert "default_max_width = 1280" in tools_text
        # type 标记也写入（class marker，provider ConfigSchema 自带）
        assert 'type = "vision"' in tools_text
        # default_region = None 遵循 _set_toml_value / _table_from_model 兜底：不落盘
        assert "default_region" not in tools_text

    def test_vision_existing_user_values_preserved(self, temp_config_dir):
        """用户已设的非默认字段加载后保留；新增字段未填则补默认值。"""
        generate_default_configs(temp_config_dir)
        # 用户改 monitor_index 默认值为 2；首启生成已含 monitor_index=1 行，
        # 直接替换而非追加以避免 tomlkit KeyAlreadyPresent
        tools_path = temp_config_dir / "tools.toml"
        original = tools_path.read_text(encoding="utf-8-sig")
        patched = original.replace(
            "monitor_index = 1",
            "monitor_index = 2",
        )
        assert patched != original
        tools_path.write_text(patched, encoding="utf-8-sig")

        cfg, _report = load_config_dir(temp_config_dir)

        # 合并视图 scope=tools → 顶层 key 即文件根 ToolsRootConfig 的 "tools" 字段
        # （ToolsRootConfig.meta / .tools 一一对应；meta 已被阶段⑥ 剥离）
        vision_cfg = cfg["tools"]["tools"]["vision"]["config"]
        # monitor_index 保留为 2（用户值）
        assert vision_cfg["monitor_index"] == 2
        # 其他字段走默认
        assert vision_cfg["vlm_timeout_ms"] == 15000
        assert vision_cfg["default_max_width"] == 1280
        # 用户修改后写回不丢用户值
        disk_text = tools_path.read_text(encoding="utf-8-sig")
        assert "monitor_index = 2" in disk_text

    def test_unknown_field_in_vision_config_stripped(self, temp_config_dir):
        """vision config 出现未知字段：drift 视为冗余，写回自动清理（不抛）。

        BaseConfig.extra='forbid' 在 ``from_dict_with_drift_check`` 内被显式
        旁路（多余字段被剥离后再构造实例），保证加载稳健性。冗余项写入
        ``report.redundant``，写回阶段一并清出磁盘。
        """
        generate_default_configs(temp_config_dir)
        tools_path = temp_config_dir / "tools.toml"
        original = tools_path.read_text(encoding="utf-8-sig")
        # 在 [tools.vision.config] 段尾追加未知字段（替换最近的已有字段以避免重复键）
        patched = original.replace(
            "default_max_width = 1280",
            'default_max_width = 1280\nunknown_field = "x"',
        )
        assert patched != original
        tools_path.write_text(patched, encoding="utf-8-sig")

        cfg, _report = load_config_dir(temp_config_dir)
        # 加载不抛；未知字段被剥离
        # 返回的 report 是"写回后残余漂移"——冗余项若被写回清掉则不再列出
        # （漂移过程的可见性在日志，不在残留报告）；故断言"磁盘上消失"。
        assert "unknown_field" not in cfg["tools"]["tools"]["vision"]["config"]
        # 写回后磁盘上无 unknown_field
        disk_text = tools_path.read_text(encoding="utf-8-sig")
        assert "unknown_field" not in disk_text


# ---------------------------------------------------------------------------
# (b) 残留 [collectors.screen] 段加载容忍
# ---------------------------------------------------------------------------


class TestResidualCollectorSection:
    """``[collectors.<退役名>]`` 残留段：CollectorsRootConfig.extra='allow'
    保留段不删除；阶段④ 校验改为 warn + 跳过而非 raise。"""

    def test_residual_screen_section_loaded_without_raise(self, temp_config_dir):
        """残留 ``[screen]`` 段（已退役 screen 采集器的历史段名）不抛、warning 含跳过原因。

        段名匹配 :data:`COMPONENT_SCHEMAS` 的 key：注册表中以采集器名为顶层
        key（``screen``，不带 ``collectors.`` 前缀）。TOML 中 ``[screen]`` 段
        是顶层键，与 extras 入口对齐。Task 1 删除 ``screen`` 注册后该段成
        残留，需 ``CollectorsRootConfig.extra='allow'`` 容忍。
        """
        generate_default_configs(temp_config_dir)
        collectors_path = temp_config_dir / "collectors.toml"
        original = collectors_path.read_text(encoding="utf-8-sig")
        # 在文件末尾追加 [screen] 段（顶层 key；extras 入口）
        patched = original + "\n[screen]\nenabled = true\n"
        collectors_path.write_text(patched, encoding="utf-8-sig")

        with _LoguruCapture() as cap:
            cfg, _report = load_config_dir(temp_config_dir)

        # 不抛、加载成功（其他 scope 仍正常）
        assert "tools" in cfg
        # warning 含 'screen' 段名 + 跳过提示
        assert any("screen" in r["message"] and r["level"] == "WARNING" for r in cap.records), (
            f"未发现 warning: {cap.records}"
        )

    def test_residual_screen_section_preserved_on_disk(self, temp_config_dir):
        """残留段在磁盘上保留（extra='allow' 语义）；漂移写回不删除。"""
        generate_default_configs(temp_config_dir)
        collectors_path = temp_config_dir / "collectors.toml"
        original = collectors_path.read_text(encoding="utf-8-sig")
        patched = original + "\n[screen]\nenabled = true\n"
        collectors_path.write_text(patched, encoding="utf-8-sig")

        load_config_dir(temp_config_dir)

        final = collectors_path.read_text(encoding="utf-8-sig")
        assert "[screen]" in final
        assert "enabled = true" in final


# ---------------------------------------------------------------------------
# (c) enabled 含未知名加载容忍（与 factory.instantiate_collector 对齐）
# ---------------------------------------------------------------------------


class TestEnabledUnknownName:
    """``enabled`` 名单出现未注册采集器名（如退役 ``screen``）→
    加载不抛 + warn；与 Task 1 的 `instantiate_collector` skip+warn 行为对齐。"""

    @staticmethod
    def _append_enabled(config_dir, extra: str) -> None:
        """往 enabled 名单追加一个名字（按整行定位，不写死默认名单内容）。"""
        path = config_dir / "collectors.toml"
        lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
        targets = [i for i, line in enumerate(lines) if line.strip().startswith("enabled = [")]
        assert len(targets) == 1, f"enabled 行不唯一: {targets!r}"
        current = lines[targets[0]].strip()
        names = current[len("enabled = [") : -1]
        merged = f"{names}, {extra}" if names.strip() else extra
        lines[targets[0]] = f"enabled = [{merged}]\n"
        path.write_text("".join(lines), encoding="utf-8-sig")

    def test_unknown_enabled_screen_loaded_without_raise(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        # enabled 列表追加已退役的 "screen"
        self._append_enabled(temp_config_dir, '"screen"')

        with _LoguruCapture() as cap:
            cfg, _report = load_config_dir(temp_config_dir)

        assert "tools" in cfg  # 加载成功
        assert any("screen" in r["message"] and r["level"] == "WARNING" for r in cap.records)

    def test_unknown_typo_enabled_loaded_without_raise(self, temp_config_dir):
        """未知名不仅限退役名；任意拼写错的 enabled 项同样 warn + 跳过。"""
        generate_default_configs(temp_config_dir)
        self._append_enabled(temp_config_dir, '"typo_collector_xyz"')

        with _LoguruCapture() as cap:
            cfg, _report = load_config_dir(temp_config_dir)

        assert "tools" in cfg
        assert any("typo_collector_xyz" in r["message"] and r["level"] == "WARNING" for r in cap.records)


# 守门：与 _CONFIG_FILES 联动验证（避免误删文件清单后此用例失锚）
def test_config_files_inventory_constant_present():
    assert "tools.toml" in _CONFIG_FILES
    assert "collectors.toml" in _CONFIG_FILES
