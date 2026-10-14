"""profile 的对话历史预算退役的迁移测试（model.toml v2.0.44）。

历史预算改由各 Agent 的上下文段按模型窗口比例换算，``[llm_profiles.*]`` 上的
``history_token_budget`` 不再有消费者：加载时删除并落盘，其余 profile 字段不动。
"""

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


def test_history_budget_dropped_from_every_profile(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    path = tmp_path / "model.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = "2.0.43"
    for name, budget in (("planner", 8000), ("replyer", 128000), ("minecraft", 0)):
        doc["llm_profiles"][name]["history_token_budget"] = budget
    doc["llm_profiles"]["planner"]["temperature"] = 0.9
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    load_config_dir(tmp_path)

    migrated = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert migrated["meta"]["version"] == "2.0.44"
    for profile in migrated["llm_profiles"].values():
        assert "history_token_budget" not in profile
    assert migrated["llm_profiles"]["planner"]["temperature"] == 0.9

    first = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first
