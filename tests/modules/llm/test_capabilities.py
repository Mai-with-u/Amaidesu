"""能力查询协议与装配期校验：依赖缺方法时构造即报错，而不是运行期静默退化。"""

from types import SimpleNamespace
from typing import Optional

import pytest

from src.agents.minecraft.context import MinecraftHistoryCompactor
from src.agents.minecraft.config import MinecraftContextConfig
from src.modules.llm.capabilities import (
    ProfileContextWindowLookup,
    ProfileVisualCapabilityLookup,
    validate_llm_capability,
)
from src.modules.llm.engine import LLMManager


def test_engine_satisfies_protocols() -> None:
    """引擎契约钉住：LLMManager 实现两个查询协议；改名/删方法时本测试先红。"""
    assert issubclass(LLMManager, ProfileContextWindowLookup)
    assert issubclass(LLMManager, ProfileVisualCapabilityLookup)


def test_validate_passes_none_and_satisfied_dependency() -> None:
    """未注入（None）放行；实现了协议方法的依赖放行。"""
    validate_llm_capability(None, ProfileVisualCapabilityLookup, parameter="llm")
    ok = SimpleNamespace(is_profile_visual=lambda profile: False)
    validate_llm_capability(ok, ProfileVisualCapabilityLookup, parameter="llm")


def test_validate_rejects_dependency_missing_method() -> None:
    """注入了缺方法的对象时 TypeError 点名缺失方法。"""
    bad = SimpleNamespace(generate=lambda: None)
    with pytest.raises(TypeError, match="is_profile_visual"):
        validate_llm_capability(bad, ProfileVisualCapabilityLookup, parameter="llm")


def test_compactor_rejects_llm_without_window_query() -> None:
    """装配校验钉在注入处：历史整理器拿到缺窗口查询的依赖，构造即报错。"""
    with pytest.raises(TypeError, match="get_profile_context_window"):
        MinecraftHistoryCompactor(SimpleNamespace(), MinecraftContextConfig())


async def test_compactor_accepts_fake_with_methods() -> None:
    """带齐方法的替身可正常构造，水位线按替身窗口换算。"""

    class FakeLlm:
        def get_profile_context_window(self, profile: Optional[str]) -> int:
            return 8000

    compactor = MinecraftHistoryCompactor(FakeLlm(), MinecraftContextConfig(max_tokens=4000))
    marks = compactor.watermarks()
    assert marks.budget == 4000
