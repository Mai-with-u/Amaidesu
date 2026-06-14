"""
Mock 输出 Handler（用于测试）

不再继承 OutputProvider，而是作为独立类实现 handler 协议。
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.modules.types import Intent


class MockOutputProvider:
    """Mock 输出 Handler（用于测试）

    不继承 OutputProvider 基类，直接实现 handler 协议。
    """

    def __init__(self, config: dict[str, Any] | None = None):
        # 不调用 super().__init__(config) - 不再有基类
        self._config = config or {}
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


# 导出别名（保持向后兼容）
MockOutputHandler = MockOutputProvider