"""simulator 测试共享替身：duck-type 假 LLM 服务（SimulatorService._find_llm_service 按 generate/setup 属性检测）与假配置服务。"""

from __future__ import annotations

from typing import Any, Dict

from src.modules.llm.payload import Response, Usage


class FakeLLMService:
    def __init__(self, reply: str = "模拟观众弹幕", tokens: int = 10) -> None:
        self._reply = reply
        self._tokens = tokens
        self.chat_calls = 0

    async def generate(self, prompt: str, **kwargs: Any) -> Any:
        self.chat_calls += 1
        return Response(success=True, content=self._reply, usage=Usage(total_tokens=self._tokens))

    async def setup(self, config: Any) -> None:
        pass

    async def cleanup(self) -> None:
        pass


class FakeConfigService:
    def __init__(self, simulator_cfg: Dict[str, Any]) -> None:
        self.main_config = {"simulator": simulator_cfg}


def enabled_config(**overrides: Any) -> FakeConfigService:
    return FakeConfigService({"enabled": True, **overrides})
