"""配置加载：agents 启用名单取值、schemas 导出契约守卫、诊断保留默认值"""

import pytest

from src.modules.config import schemas
from src.modules.config.core_schemas import DiagnosticsConfig
from src.modules.config.service import ConfigService


def _service(base_dir) -> ConfigService:
    service = ConfigService(base_dir=str(base_dir))
    service.initialize()
    return service


def test_agents_enabled_list_keeps_the_users_choice(tmp_path) -> None:
    """取值必须是 AgentType 已知名，文件必须自带 [meta].version（每文件版本硬性要求）。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text(
        '[meta]\nversion = "2.0.31"\n\n[agents]\nenabled = ["streamer", "minecraft"]\n', encoding="utf-8"
    )
    assert _service(tmp_path).get_section("agents", {}).get("enabled") == ["streamer", "minecraft"]


async def test_reload_advances_per_file_view_together_with_flat_view(tmp_path) -> None:
    """热更新后按文件视图必须与拍平视图一起前进。

    回归：_file_sections 曾只在 initialize 填充、reload 不刷新，get_file_section
    的消费方读到开服旧值——开播守卫把刚关掉的 TTS 开关当成还开着（恒拦）。
    """
    service = _service(tmp_path)
    assert service.get_file_section("infra", {})["tts"]["enabled"] is True
    assert service.get_section("tts", {})["enabled"] is True

    # 外部改盘（真实路径：保存管线写盘后走 reload_config 重载）
    infra = tmp_path / "config" / "infra.toml"
    text = infra.read_text(encoding="utf-8-sig")
    infra.write_text(text.replace("enabled = true", "enabled = false", 1), encoding="utf-8")

    await service.reload_config(changed_scopes=["infra"])

    assert service.get_section("tts", {})["enabled"] is False
    assert service.get_file_section("infra", {})["tts"]["enabled"] is False


def test_schemas_all_exports_are_defined() -> None:
    assert "list_all_providers" not in schemas.__all__  # __all__ 里的名称必须实际定义
    assert all(hasattr(schemas, name) for name in schemas.__all__)


def test_diagnostics_keep_forever_by_default_and_reject_negative_retention() -> None:
    """诊断正文是场次回看的事件侧事实源，默认不滚动删除；显式正整数保留天数才启用清理。"""
    default = DiagnosticsConfig()
    assert (default.enabled, default.retention_days) == (True, 0)
    assert DiagnosticsConfig(retention_days=7).retention_days == 7
    with pytest.raises(ValueError):
        DiagnosticsConfig(retention_days=-1)
