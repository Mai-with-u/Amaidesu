"""infra.toml 删除 ``[simulator].llm_profile`` 的迁移测试

绑定规则改为代码显式常量声明后，配置字段随之移除；旧配置经升级钩子删键、
幂等重放、版本推进并写回落盘。
"""

from pathlib import Path

from src.modules.config.multi_file_loader import load_config_dir
from src.modules.config.upgrade import _drop_simulator_llm_profile, advance_file_versions


OLD_INFRA_TOML = """\
[meta]
version = "2.0.31"

[simulator]
enabled = true
mode = "generate"
llm_profile = "simulator"
llm_temperature = 0.9
"""


def test_hook_deletes_llm_profile():
    data = {"simulator": {"enabled": True, "llm_profile": "simulator", "llm_temperature": 0.9}}
    changed = _drop_simulator_llm_profile(data)
    assert changed == ["simulator.llm_profile"]
    assert "llm_profile" not in data["simulator"]
    # 其余字段不受影响
    assert data["simulator"]["llm_temperature"] == 0.9


def test_hook_idempotent():
    data = {"simulator": {"enabled": True}}
    assert _drop_simulator_llm_profile(data) == []
    assert _drop_simulator_llm_profile({"simulator": {}}) == []
    # 非 dict / 缺段的退化输入同样无副作用
    assert _drop_simulator_llm_profile({}) == []


def test_version_advance_via_scheduler():
    raw = {"meta": {"version": "2.0.31"}, "simulator": {"llm_profile": "simulator"}}
    changed = advance_file_versions({"infra.toml": raw})
    assert "simulator.llm_profile" in changed["infra.toml"]
    assert "meta.version" in changed["infra.toml"]
    # 版本推进到本文件钩子链最后一个钩子的 target，与全局基线种子无关
    # （infra.toml 链尾：llm_profile @2.0.32 + events.persist @2.0.33
    #   + avatar.lipsync 迁移 @2.0.38，本 raw 无 avatar 段故零变更）
    assert raw["meta"]["version"] == "2.0.40"
    assert "llm_profile" not in raw["simulator"]


def test_migration_writeback_on_load(tmp_path: Path):
    """加载含旧键的配置目录：迁移生效且写回落盘（删键 + 版本推进）"""
    infra = tmp_path / "infra.toml"
    infra.write_text(OLD_INFRA_TOML, encoding="utf-8")

    load_config_dir(tmp_path)

    content = infra.read_text(encoding="utf-8")
    assert "llm_profile" not in content
    assert 'version = "2.0.40"' in content
