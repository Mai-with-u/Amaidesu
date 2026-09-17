"""collectors.toml 的段可达性 —— 采集器装配契约。

回归点（线上症状：采集器一个都没起来）：多文件加载把六个文件**拍平**成一层
合并视图，而 ``collectors.toml`` 的根键就是 ``enabled`` 与各采集器同名表
（没有 ``[collectors]`` 包裹表）——拍平后顶层只剩 ``enabled`` 与各采集器名，
``config["collectors"]`` 恒为空。装配入口若按拍平视图取这一段，就会拿到空 dict，
采集器零装配（日志里连"初始化 CollectorManager"都不会出现）。

契约：装配入口按**文件名**取原始命名空间（``ConfigService.get_file_section``）。
"""

from __future__ import annotations

import pytest

from src.modules.config.multi_file_loader import generate_default_configs
from src.modules.config.service import ConfigService


@pytest.fixture
def config_dir(tmp_path):
    """按 Schema 生成默认配置的 config/ 目录。"""
    target = tmp_path / "config"
    target.mkdir()
    generate_default_configs(target)
    return target


def test_file_section_exposes_enabled_and_component_tables(config_dir, tmp_path) -> None:
    """按文件取段：enabled 名单可达，且名单里的名字都在组件注册表内。

    子段本身**可选**（缺省走包内 Schema 默认值，装配侧用 ``.get(name, {})``），
    所以只校验"名单合法 + 出现的子段是表"。
    """
    from src.modules.config.registry import COMPONENT_SCHEMAS

    service = ConfigService(base_dir=str(tmp_path))
    service.initialize()

    section = service.get_file_section("collectors")
    enabled = section.get("enabled")
    assert enabled, "enabled 名单必须可达（取不到 → 采集器零装配）"
    for name in enabled:
        assert name in COMPONENT_SCHEMAS, f"enabled 里的 {name} 必须在组件注册表内"
        if name in section:
            assert isinstance(section[name], dict), f"{name} 的子段必须是 TOML 表"


def test_flat_view_has_no_collectors_key(config_dir, tmp_path) -> None:
    """拍平视图里没有 collectors 键——这正是旧装配路径取空的原因。"""
    service = ConfigService(base_dir=str(tmp_path))
    service.initialize()

    assert service.get_section("collectors") == {}
    # 而 agents 有 [agents] 包裹表，拍平后仍可达（所以那条路径一直正常）
    assert service.get_section("agents").get("enabled")


def test_assembly_path_can_instantiate_every_enabled_collector(config_dir, tmp_path) -> None:
    """装配入口（main.py 用 get_file_section + 工厂）能实例化 enabled 名单里的每一个。"""
    from src.modules.collectors.factory import instantiate_collector

    service = ConfigService(base_dir=str(tmp_path))
    service.initialize()
    section = service.get_file_section("collectors")

    for name in section["enabled"]:
        instance = instantiate_collector(name, section.get(name, {}), event_bus=None)
        assert instance is not None, f"enabled 里的 {name} 必须能实例化（否则是配置与实现脱节）"
