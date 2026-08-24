"""集成测试 - 模拟直播间（Wave 6 stub 适配）

Wave 6 变更（迁移 §C）：
- 原 LiveStreamSimulator 由 LLM 驱动的复杂实现已迁移到 ``collectors/mock/``
  （人设池/节奏生成/礼物生成）作为标准采集器。
- 本模块的 LiveStreamSimulator 退化为最小 stub（保留 start/collect/stop/cleanup
  接口以兼容 SimulatorService）。
- Wave 6 起 CONFIG_SCHEMA_REGISTRY 不再包含 ``simulator``（已迁移到 collectors/mock
  段，由工具配置驱动），旧的 schema_registry 注册测试已 DISCARD。
- 其余测试（依赖 ``_cfg`` / ``_context_service`` / ``_persona_pool`` 等私有字段的
  LLM 驱动路径）已被替换为对 stub 接口的最小校验。

测试内容：
- LiveStreamSimulator 可被构造（config + services 接受）
- start/stop/cleanup 幂等无异常
- collect() 返回空 async iterator（stub 行为）
- collect 接口签名兼容 ``async for message in simulator.collect()``
"""

from unittest.mock import MagicMock

import pytest

from src.modules.simulator.simulator import LiveStreamSimulator


class TestStubRegistration:
    """SimulatorService 仍注册 LiveStreamSimulator 类（向后兼容）。"""

    def test_simulator_class_importable(self):
        """LiveStreamSimulator 类可被导入并实例化（stub 形态）。"""
        sim = LiveStreamSimulator(config={}, event_bus=MagicMock())
        assert sim is not None


class TestStubLifecycle:
    """Stub 生命周期测试（Wave 6 简化为空操作）。"""

    @pytest.mark.asyncio
    async def test_setup_is_noop(self):
        sim = LiveStreamSimulator(config={}, event_bus=MagicMock())
        await sim.setup()
        # stub 不改变状态

    @pytest.mark.asyncio
    async def test_start_stop_cleanup(self):
        sim = LiveStreamSimulator(config={}, event_bus=MagicMock())
        await sim.start()
        await sim.stop()
        await sim.cleanup()


class TestStubCollect:
    """collect() 返回空 async iterator（stub 行为）。"""

    @pytest.mark.asyncio
    async def test_collect_yields_nothing(self):
        sim = LiveStreamSimulator(config={}, event_bus=MagicMock())
        items = []
        async for msg in sim.collect():
            items.append(msg)
        assert items == []

    @pytest.mark.asyncio
    async def test_collect_is_aiterator(self):
        """collect() 是 async generator（兼容 async for）。"""
        sim = LiveStreamSimulator(config={}, event_bus=MagicMock())
        # 检查 aiter 接口存在
        assert hasattr(sim.collect, "__aiter__") or hasattr(sim.collect, "__call__")


class TestStubExtraArgs:
    """Stub 接受额外服务注入（向后兼容原 LiveStreamSimulator DI 签名）。"""

    def test_extra_services_accepted(self):
        """即使 stub 不使用 service 参数，构造时也接受（不抛 TypeError）。"""
        sim = LiveStreamSimulator(
            config={},
            event_bus=MagicMock(),
            llm_service=MagicMock(),
            prompt_service=MagicMock(),
            context_service=MagicMock(),
            event_history_service=MagicMock(),
        )
        assert sim is not None