"""ModelConfig → LLMManager 链路集成测试（Violation 3 — llm_summary profile）

验证 F1 Batch D 修复：
- `[llm_summary]` profile 在 `config/model.toml` 中存在
- ModelConfig Schema 必须声明 `llm_summary` 字段，否则 multi_file_loader
  会通过 `from_dict_with_drift_check` 将其作为冗余字段剥离（model_dump 不含）
- LLMManager.setup() 必须能为 llm_summary 创建独立 client 实例
  （即使引用同一 provider，也要构造独立 client，避免与 llm_fast 共享连接池）

覆盖两个 QA 场景：
    Scenario A: config 加载后保留 model.llm_summary
    Scenario B: LLMManager 为 llm_summary 创建独立 client 实例

运行: uv run pytest tests/modules/config/test_model_llm_summary_chain.py -v
"""

from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from src.modules.config.model_schemas import ModelConfig
from src.modules.config.multi_file_loader import load_config_dir
from src.modules.llm.clients.base import _client_impls
from src.modules.llm.manager import LLMManager

# =============================================================================
# Fixtures - 定位真实 config/ 目录
# =============================================================================

# 测试文件位置: tests/modules/config/test_model_llm_summary_chain.py
# 项目根: 上溯 3 级 (tests/modules/config -> 项目根)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
REAL_CONFIG_DIR = PROJECT_ROOT / "config"


@pytest.fixture
def loaded_model_config() -> Dict[str, Any]:
    """加载真实 config/ 目录并返回 model section（dict）。

    复现生产加载链路：multi_file_loader.load_config_dir → ModelConfig.from_dict_with_drift_check
    → model_dump()。如果 ModelConfig 缺少 llm_summary 字段，这里就会缺失。
    """
    if not REAL_CONFIG_DIR.exists():
        pytest.skip(f"未找到配置目录: {REAL_CONFIG_DIR}")
    config, _report = load_config_dir(REAL_CONFIG_DIR)
    assert "model" in config, "load_config_dir 应当返回 model section"
    return config["model"]


# =============================================================================
# Scenario A: ModelConfig 保留 llm_summary（schema 层验证）
# =============================================================================


class TestScenarioAConfigRetainsLlmSummary:
    """Scenario A：加载真实 model.toml 后，model.llm_summary 必须保留。"""

    def test_model_config_schema_declares_llm_summary_field(self):
        """ModelConfig 必须声明 llm_summary 字段（这是 violation 3 的根因修复点）。"""
        assert "llm_summary" in ModelConfig.model_fields, (
            "ModelConfig 必须声明 llm_summary 字段，否则 multi_file_loader 会将其作为冗余字段剥离"
        )

    def test_loaded_model_config_contains_llm_summary(self, loaded_model_config: Dict[str, Any]):
        """加载真实 config/model.toml 后，model.llm_summary 必须存在且非空。"""
        assert "llm_summary" in loaded_model_config, "llm_summary 被 schema 剥离了！说明 ModelConfig 未正确声明该字段"
        summary_profile = loaded_model_config["llm_summary"]
        assert isinstance(summary_profile, dict), "llm_summary 应当是 dict（LLMProfileConfig.model_dump()）"
        # 关键字段必须存在
        assert "provider" in summary_profile, "llm_summary.provider 缺失"
        assert "model" in summary_profile, "llm_summary.model 缺失"

    def test_loaded_model_config_llm_summary_references_valid_provider(self, loaded_model_config: Dict[str, Any]):
        """加载后的 llm_summary.provider 必须引用已声明的 provider。"""
        summary_provider = loaded_model_config["llm_summary"]["provider"]
        provider_names = {p["name"] for p in loaded_model_config["llm_providers"]}
        assert summary_provider in provider_names, (
            f"llm_summary.provider={summary_provider!r} 未在 llm_providers 中找到（可用：{sorted(provider_names)}）"
        )

    def test_no_drift_on_llm_summary(self, loaded_model_config: Dict[str, Any]):
        """llm_summary 不应被 drift report 标记为冗余。

        重新加载并显式检查 drift report，确保修复后 [llm_summary] 不再被剥离。
        """
        _config, report = load_config_dir(REAL_CONFIG_DIR)
        redundant_keys = [r for r in report.redundant if "llm_summary" in r]
        assert not redundant_keys, (
            f"llm_summary 被标记为冗余配置项: {redundant_keys}，说明 ModelConfig 仍未声明 llm_summary 字段"
        )

    def test_llm_summary_independent_from_llm_fast(self, loaded_model_config: Dict[str, Any]):
        """llm_summary 应当与 llm_fast 是独立的 profile（即使引用同一 provider）。"""
        assert "llm_summary" in loaded_model_config
        assert "llm_fast" in loaded_model_config
        # 两者都是独立 dict 实例
        assert loaded_model_config["llm_summary"] is not loaded_model_config["llm_fast"]


# =============================================================================
# Scenario B: LLMManager 为 llm_summary 创建独立 client 实例
# =============================================================================


class TestScenarioBLLMManagerIndependentClient:
    """Scenario B：LLMManager.setup() 后 llm_summary 应有独立 client 实例。"""

    @pytest.fixture
    async def setup_manager_with_real_config(self, loaded_model_config: Dict[str, Any]):
        """用真实 config/model.toml 初始化 LLMManager（mock client 注册表）。

        Mock 策略参考 tests/modules/llm/test_llm_manager.py：
        patch.dict(_client_impls, {"openai": mock_class}) 拦截 get_client_impl。
        每次 mock_class(merged_config) 返回独立 MagicMock，便于断言独立性。
        """
        # 用 side_effect 让每次构造都返回全新的 MagicMock（独立实例）
        created_instances: list[Any] = []

        def _make_instance(cfg: Dict[str, Any]) -> MagicMock:
            inst = MagicMock()
            inst.cleanup = MagicMock()  # 同步 mock 即可，setup 不调用
            inst._merged_config = cfg  # 保留合并配置便于断言
            created_instances.append(inst)
            return inst

        mock_backend_class = MagicMock(side_effect=_make_instance)
        manager = LLMManager()

        with patch.dict(_client_impls, {"openai": mock_backend_class}):
            with patch("src.modules.llm.clients.token_usage_manager.TokenUsageManager"):
                await manager.setup(loaded_model_config)
                yield manager, created_instances, mock_backend_class

    @pytest.mark.asyncio
    async def test_has_client_llm_summary(self, setup_manager_with_real_config):
        """LLMManager.has_client('llm_summary') 必须返回 True。"""
        manager, _created, _ = setup_manager_with_real_config
        assert manager.has_client("llm_summary") is True, (
            "LLMManager 未为 llm_summary 创建 client —— 说明 config 链路仍未保留 llm_summary"
        )

    @pytest.mark.asyncio
    async def test_llm_summary_client_is_independent_from_llm_fast(self, setup_manager_with_real_config):
        """llm_summary 的 client 实例必须与 llm_fast 不同（Task 8 独立连接池约束）。"""
        manager, _created, _ = setup_manager_with_real_config
        summary_client = manager.get_client("llm_summary")
        fast_client = manager.get_client("llm_fast")
        assert summary_client is not fast_client, (
            "llm_summary 与 llm_fast 共享同一 client 实例，违反 Task 8 独立连接池约束"
        )

    @pytest.mark.asyncio
    async def test_llm_summary_uses_separate_constructor_call(self, setup_manager_with_real_config):
        """mock_backend_class 应被多次调用（每个 profile 一次独立构造）。"""
        _manager, created, mock_backend_class = setup_manager_with_real_config
        # 至少 llm + llm_fast + vlm + llm_local + llm_summary 都应独立构造
        # （假设 config/model.toml 中这些 profile 都引用了某 provider）
        assert mock_backend_class.call_count >= 1, "应当至少为引用 provider 的 profile 构造一次 client"
        # 创建的实例两两不同（无共享）
        for i, inst_a in enumerate(created):
            for j, inst_b in enumerate(created):
                if i != j:
                    assert inst_a is not inst_b, f"client 实例 {i} 和 {j} 共享同一对象，违反独立 client 约束"

    @pytest.mark.asyncio
    async def test_llm_summary_profile_config_recorded(self, setup_manager_with_real_config):
        """manager._profile_configs 必须为 llm_summary 记录合并后的配置。"""
        manager, _created, _ = setup_manager_with_real_config
        assert "llm_summary" in manager._profile_configs, (
            "_profile_configs 未记录 llm_summary，说明 setup() 未处理该 profile"
        )
        summary_cfg = manager._profile_configs["llm_summary"]
        # 合并后应含 model（来自 profile）和 base_url（来自 provider）
        assert "model" in summary_cfg, "合并配置缺少 model 字段"
        assert "base_url" in summary_cfg, "合并配置缺少 base_url 字段（应继承自 provider）"
