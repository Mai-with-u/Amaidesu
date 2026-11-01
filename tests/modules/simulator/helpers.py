"""simulator 测试共享替身：duck-type 假 LLM 服务（SimulatorService._find_llm_service 按 generate/setup 属性检测）与假配置服务。"""

from __future__ import annotations

from typing import Any, Dict

from src.modules.llm.payload import Response, Usage
from tests.modules.llm.fakes import FakeLLMManager


class FakeLLMService(FakeLLMManager):
    """SimulatorService 形状的假 LLM 服务（统一假件的薄封装，保留 setup/cleanup 探测属性与调用计数）。"""

    def __init__(self, reply: str = "模拟观众弹幕", tokens: int = 10) -> None:
        super().__init__(Response(success=True, content=reply, usage=Usage(total_tokens=tokens)))

    @property
    def chat_calls(self) -> int:
        """generate 被调用的次数。"""
        return len(self.calls)


class FakeConfigService:
    def __init__(self, simulator_cfg: Dict[str, Any]) -> None:
        self.main_config = {"simulator": simulator_cfg}


def enabled_config(**overrides: Any) -> FakeConfigService:
    return FakeConfigService({"enabled": True, **overrides})


class FakeSessionManager:
    """最小场次管理器替身：恒有进行中场次（resolve_pk 固定返回），供 start 的场次前置检查通过。"""

    def __init__(self, pk: int = 1) -> None:
        self.pk = pk

    async def resolve_pk(self) -> int:
        return self.pk
