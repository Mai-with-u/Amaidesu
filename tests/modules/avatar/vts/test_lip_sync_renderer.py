"""VtsLipSyncRenderer 表情维护测试

分工边界：口型只写"嘴"（``MouthOpen``），说话期间的表情基线保持与结束
收回归渲染器——说话时推到基线（如常驻微笑），静音/会话结束时收回
（0 = 交回 idle/情绪渲染的基线管理），不再有渲染器私有的静止值概念。
"""

import pytest

from src.modules.avatar.lipsync import MouthSignal
from src.modules.avatar.vts.lip_sync_renderer import VtsLipSyncRenderer


@pytest.fixture
def renderer():
    """创建带模拟写入回调与微笑基线的渲染器"""
    calls = []

    async def set_param(name, value, weight=1):
        calls.append((name, value))
        return True

    r = VtsLipSyncRenderer(
        set_parameter=set_param,
        base_expressions={"MouthSmile": 0.3},
    )
    return r, calls


def _signal(mouth_open: float, volume: float) -> MouthSignal:
    return MouthSignal(mouth_open=mouth_open, volume=volume)


@pytest.mark.asyncio
async def test_mouth_open_follows_signal(renderer):
    """张嘴信号直写 MouthOpen；同值不重复写。"""
    r, calls = renderer
    await r.on_mouth_signal(_signal(0.5, 0.8))
    # 口型先写嘴；同帧说话基线（MouthSmile）随后，顺序断言只看首笔
    assert calls[0] == ("MouthOpen", 0.5)

    await r.on_mouth_signal(_signal(0.5, 0.8))
    # 同值不重复写嘴；基线已活跃也不再写表情
    assert all(name != "MouthOpen" for name, _ in calls[1:])


@pytest.mark.asyncio
async def test_base_expression_maintained_while_speaking(renderer):
    """说话（音量足）时微笑基线推到目标值；静音时收回。"""
    r, calls = renderer
    await r.on_mouth_signal(_signal(0.2, 0.5))
    assert ("MouthSmile", 0.3) in calls

    calls.clear()
    await r.on_mouth_signal(_signal(0.0, 0.0))  # 静音
    assert ("MouthSmile", 0.0) in calls


@pytest.mark.asyncio
async def test_session_end_zeros_mouth(renderer):
    """会话结束：MouthOpen 归零、表情收回。"""
    r, calls = renderer
    await r.on_mouth_signal(_signal(0.6, 0.9))
    calls.clear()

    await r.on_session_end()
    assert calls[-1] == ("MouthOpen", 0.0)
    assert r.current_mouth_open == 0.0


@pytest.mark.asyncio
async def test_mouth_open_param_not_in_base_expressions():
    """基线表情里的 MouthOpen 被过滤（张嘴归口型信号管）。"""
    calls = []

    async def set_param(name, value, weight=1):
        calls.append((name, value))
        return True

    r = VtsLipSyncRenderer(set_parameter=set_param, base_expressions={"MouthSmile": 0.3, "MouthOpen": 0.5})
    await r.on_mouth_signal(_signal(0.2, 0.5))
    assert ("MouthOpen", 0.5) not in calls
