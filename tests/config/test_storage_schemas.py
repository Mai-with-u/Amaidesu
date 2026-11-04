"""storage.toml 的 Schema 校验：遗忘/撤回短语表必须是能命中的"词"。

判定命令时短语按 Unicode 意义切小句、标点与表情只作分隔，带标点、空格或表情的短语
永远无法与任何小句相等。按"未知即硬错"在加载期点名拦下，避免使用者以为配置生效、
实际从未触发；这批用例同时覆盖 Schema 层与走配置加载管线的加载期硬错。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.storage_schemas import DEFAULT_FORGET_PHRASES, DEFAULT_RETRACT_PHRASES, PrivacyConfig
from tests.config.helpers import read_doc, write_doc


def test_default_phrase_tables_pass_validation() -> None:
    """内置默认短语表本身必须全部合法（否则新生成配置加载就会硬错）。"""
    config = PrivacyConfig()
    assert list(config.forget_phrases) == list(DEFAULT_FORGET_PHRASES)
    assert list(config.retract_phrases) == list(DEFAULT_RETRACT_PHRASES)


def test_padded_phrase_passes_without_being_rewritten() -> None:
    """首尾空白在判定时会被去掉，不需要为它报错；Schema 也不改写使用者的原值。"""
    config = PrivacyConfig(forget_phrases=[" 忘了我 "])
    assert config.forget_phrases == [" 忘了我 "]


@pytest.mark.parametrize(
    ("phrases", "bad_text"),
    [
        (["忘了我!"], "忘了我!"),
        (["忘了我 吧"], "忘了我 吧"),
        (["[doge]忘了我"], "[doge]忘了我"),
        ([""], "空短语"),
        (["   "], "空短语"),
    ],
    ids=["punctuation", "space", "emoticon", "empty", "blank"],
)
def test_unmatchable_phrase_is_rejected(phrases: list[str], bad_text: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        PrivacyConfig(forget_phrases=phrases)
    assert bad_text in str(excinfo.value)


def test_unmatchable_retract_phrase_is_rejected() -> None:
    with pytest.raises(ValidationError, match="永远不会命中"):
        PrivacyConfig(retract_phrases=["算了吧。"])


def test_config_loading_fails_hard_with_bad_phrase(tmp_path: Path) -> None:
    """走配置加载管线：storage.toml 里写了永不命中的短语 → 加载期 ConfigValidationError。"""
    generate_default_configs(tmp_path)
    doc = read_doc(tmp_path, "storage.toml")
    doc["privacy"]["forget_phrases"] = ["忘了我", "忘了我!"]
    write_doc(tmp_path, "storage.toml", doc)

    with pytest.raises(ConfigValidationError, match="永远不会命中"):
        load_config_dir(tmp_path)
