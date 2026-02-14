"""
Mock 输出 Provider（用于测试）
"""

from typing import TYPE_CHECKING, Any

from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.types import Intent


class MockOutputProvider(OutputProvider):
    """Mock 输出 Provider（用于测试）"""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config or {})
        self.received_intents: list["Intent"] = []  # 记录收到的 Intent

    @property
    def received_parameters(self) -> list["Intent"]:
        """向后兼容属性：返回 received_intents"""
        return self.received_intents

    async def execute(self, intent: "Intent") -> bool:
        """执行意图（记录 Intent）"""
        self.received_intents.append(intent)
        return True

    def get_last_intent(self) -> "Intent | None":
        """获取最后一次收到的 Intent"""
        return self.received_intents[-1] if self.received_intents else None

    def get_all_intents(self) -> list["Intent"]:
        """获取所有收到的 Intent"""
        return self.received_intents.copy()

    def clear(self):
        """清空记录"""
        self.received_intents.clear()
