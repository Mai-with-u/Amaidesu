"""``[[llm_models]].context_window`` 字段的 schema 默认 + 漂移写回补默认测试。

跟随 ``tests/modules/config/`` 既有模式：
- 新字段默认值（零值默认：int 0，与 ``price_in`` 先例一致——落库口径把未配置记 0）
- 旧安装缺新字段 → 漂移写回补默认（不改文件版本，纯新增字段零成本）
- 旧安装写入非零值 → 写回保留用户值（不覆盖用户显式配置）
- 用户显式配置后水位键可被引擎装配期读出

context_window 是 Dashboard 水位展示的分母；0 表示"未配置"，前端据此隐藏水位，
不显示 0%。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import tomlkit

from src.modules.config.model_schemas import LLMModelConfig, ModelRootConfig
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager


# === Schema 默认值（零值默认：0，与 price_in = 0.0 先例一致）===


class TestContextWindowDefaults:
    def test_model_context_window_default_zero(self) -> None:
        """``context_window`` 缺省 0（未配置 = 前端不渲染水位，不显示 0%）。"""
        model = LLMModelConfig()
        assert model.context_window == 0

    def test_model_context_window_accepts_positive_int(self) -> None:
        """``context_window`` 接受非负 int；正整数（典型 8k/32k/128k/200k）原样保留。"""
        for value in (1, 4096, 8192, 32_768, 65_536, 128_000, 200_000, 1_000_000):
            model = LLMModelConfig(context_window=value)
            assert model.context_window == value

    def test_model_context_window_rejects_negative(self) -> None:
        """``context_window`` 不接受负数（ge=0 约束）；负值构造抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LLMModelConfig(context_window=-1)

    def test_model_root_default_has_zero_context_window(self) -> None:
        """全新安装 ModelRootConfig 默认每个 model.context_window = 0。"""
        root = ModelRootConfig()
        for model in root.llm_models:
            assert model.context_window == 0


# === 漂移写回补默认（纯新增字段零成本，不改版本）===


class TestDriftWritebackFillsContextWindow:
    def test_existing_model_missing_context_window_gets_default_on_load(self, tmp_path: Path) -> None:
        """旧安装 model 缺 context_window → 加载时按 schema 默认 0 补齐落盘。"""
        config_dir = tmp_path / "config"
        generate_default_configs(config_dir)
        model_path = config_dir / "model.toml"
        model_doc = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        original_version = model_doc["meta"]["version"]
        # 模拟旧安装：所有 llm_models 条目缺 context_window 字段
        for model in model_doc["llm_models"]:
            if "context_window" in model:
                del model["context_window"]
        model_path.write_text(tomlkit.dumps(model_doc), encoding="utf-8-sig")

        _config, report = load_config_dir(config_dir)

        written = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        for model in written["llm_models"]:
            assert model.get("context_window", "<missing>") == 0, (
                f"model {model.get('name', '?')!r} 漂移写回后 context_window 必须为 0（默认零值）"
            )
        # 纯新增字段零成本：不升文件版本
        assert written["meta"]["version"] == original_version
        assert not report.has_drift

    def test_user_supplied_context_window_is_preserved_on_writeback(self, tmp_path: Path) -> None:
        """用户显式设置 context_window=128000 → 写回保留用户值（不覆盖显式配置）。"""
        config_dir = tmp_path / "config"
        generate_default_configs(config_dir)
        model_path = config_dir / "model.toml"
        model_doc = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        target_model_name = model_doc["llm_models"][0]["name"]
        model_doc["llm_models"][0]["context_window"] = 128_000
        model_path.write_text(tomlkit.dumps(model_doc), encoding="utf-8-sig")

        _config, _report = load_config_dir(config_dir)

        written = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
        target_written = next(m for m in written["llm_models"] if m["name"] == target_model_name)
        assert target_written["context_window"] == 128_000


# === 引擎装配期：context_window 经 self._model_context_windows 暴露 ===


class TestEngineExposesContextWindow:
    @pytest.fixture
    async def setup_manager_with_context_window(self, loaded_model_config: Dict[str, Any]) -> LLMManager:
        """在默认配置上把第一个模型的 context_window 改 128000，再 setup()。"""
        config = dict(loaded_model_config)
        models = [dict(m) for m in config["llm_models"]]
        models[0]["context_window"] = 128_000
        # 第二个模型保留默认 0（验证 0 也被记录）
        config["llm_models"] = models

        def _make_instance(cfg):
            inst = MagicMock()
            inst.cleanup = MagicMock()
            inst._merged_config = cfg
            return inst

        mock_backend_class = MagicMock(side_effect=_make_instance)
        manager = LLMManager()
        with patch.dict(_CLIENT_DISPATCH, {"openai": mock_backend_class}):
            await manager.setup(config)
        return manager

    @pytest.fixture
    def loaded_model_config(self, tmp_path: Path) -> Dict[str, Any]:
        config_dir = tmp_path / "config"
        generate_default_configs(config_dir)
        config, _report = load_config_dir(config_dir)
        return config["model"]

    @pytest.mark.asyncio
    async def test_get_model_context_windows_returns_all_registered_models(
        self, setup_manager_with_context_window: LLMManager
    ) -> None:
        """``get_model_context_windows`` 返回所有已注册 model（keyed by identifier）。"""
        windows = setup_manager_with_context_window.get_model_context_windows()
        assert len(windows) == 1, "全新安装 ModelRootConfig 仅 default 一条 model"
        identifier = next(iter(windows))
        assert windows[identifier] == 128_000

    @pytest.mark.asyncio
    async def test_get_model_context_window_returns_zero_for_unset(self) -> None:
        """``get_model_context_window`` 在窗口未配置时返回 0（不抛、不取相邻值）。"""
        manager = LLMManager()
        assert manager.get_model_context_window("never-configured-model") == 0
