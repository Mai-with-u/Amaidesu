"""视觉配置由提供者 Schema 生成和写回；历史配置残留按全域硬错语义在加载期拦下。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import pytest
from loguru import logger as _loguru_logger

from src.modules.config.errors import ConfigValidationError
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
        assert "vlm_timeout_ms" not in cfg.config.model_dump()
        assert cfg.config.default_max_width == 1280

    def test_empty_vision_config_section_populated_on_load(self, temp_config_dir):
        """空 ``[tools.vision].config`` 经加载管线后写入全部默认字段（除 None 字段）。"""
        generate_default_configs(temp_config_dir)
        # 加载一次：首启生成的所有字段都已落盘，且生成端与写回端形态一致
        _config, report = load_config_dir(temp_config_dir)
        assert not report.has_drift

        tools_text = (temp_config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        # monitor_index / default_max_width 必须落盘；vlm_timeout_ms 为 None 默认不落盘
        assert "monitor_index = 1" in tools_text
        assert "vlm_timeout_ms" not in tools_text
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
        # 其他字段走默认（None 字段不进合并视图，非 None 补默认值）
        assert "vlm_timeout_ms" not in vision_cfg
        assert vision_cfg["default_max_width"] == 1280
        # 用户修改后写回不丢用户值
        disk_text = tools_path.read_text(encoding="utf-8-sig")
        assert "monitor_index = 2" in disk_text

    def test_unknown_field_in_vision_config_hard_fails(self, temp_config_dir):
        """vision config 出现未知字段：冗余全域硬错（不再静默剥离清理）。"""
        generate_default_configs(temp_config_dir)
        tools_path = temp_config_dir / "tools.toml"
        original = tools_path.read_text(encoding="utf-8-sig")
        # 在 [tools.vision.config] 段尾追加未知字段（替换最近的已有字段以避免重复键）
        patched = original.replace(
            "monitor_index = 1",
            'monitor_index = 1\nunknown_field = "x"',
        )
        assert patched != original
        tools_path.write_text(patched, encoding="utf-8-sig")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        assert "unknown_field" in str(exc_info.value)


# ---------------------------------------------------------------------------
# (b) 残留 [collectors.screen] 段全域硬错
# ---------------------------------------------------------------------------


class TestResidualCollectorSection:
    """``[collectors.<退役名>]`` 残留段：加载期硬错（collectors.toml 是
    用户契约面，未知段留任即欺骗读者）。"""

    def test_residual_screen_section_hard_fails(self, temp_config_dir):
        """残留 ``[screen]`` 段（已退役 screen 采集器的历史段名）→ ConfigValidationError。"""
        generate_default_configs(temp_config_dir)
        collectors_path = temp_config_dir / "collectors.toml"
        original = collectors_path.read_text(encoding="utf-8-sig")
        # 在文件末尾追加 [screen] 段（顶层 key；extras 入口）
        patched = original + "\n[screen]\nenabled = true\n"
        collectors_path.write_text(patched, encoding="utf-8-sig")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        message = str(exc_info.value)
        assert "screen" in message
        assert "console_input" in message  # 合法名单随错误给出


# ---------------------------------------------------------------------------
# (c) enabled 含未知名全域硬错
# ---------------------------------------------------------------------------


class TestEnabledUnknownName:
    """``enabled`` 名单出现未注册采集器名 → 加载期硬错（点名 + 合法名单）。"""

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

    def test_unknown_enabled_screen_hard_fails(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        # enabled 列表追加已退役的 "screen"
        self._append_enabled(temp_config_dir, '"screen"')

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        message = str(exc_info.value)
        assert "screen" in message
        assert "collectors.enabled" in message

    def test_unknown_typo_enabled_hard_fails(self, temp_config_dir):
        """未知名不仅限退役名；任意拼写错的 enabled 项同样硬错。"""
        generate_default_configs(temp_config_dir)
        self._append_enabled(temp_config_dir, '"typo_collector_xyz"')

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(temp_config_dir)
        assert "typo_collector_xyz" in str(exc_info.value)


# 守门：与 _CONFIG_FILES 联动验证（避免误删文件清单后此用例失锚）
def test_config_files_inventory_constant_present():
    assert "tools.toml" in _CONFIG_FILES
    assert "collectors.toml" in _CONFIG_FILES
