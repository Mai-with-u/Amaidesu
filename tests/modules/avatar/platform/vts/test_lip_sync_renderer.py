"""VtsLipSyncRenderer：口型只写 MouthOpen；说话期间表情基线（如常驻微笑）推到目标值，静音/会话结束时收回"""

from src.modules.avatar.lipsync import MouthSignal
from src.modules.avatar.platform.vts.lip_sync_renderer import VtsLipSyncRenderer


def _renderer(base_expressions: dict) -> tuple[VtsLipSyncRenderer, list]:
    calls: list = []

    async def set_param(name, value, weight=1):
        calls.append((name, value))
        return True

    return VtsLipSyncRenderer(set_parameter=set_param, base_expressions=base_expressions), calls


async def test_mouth_follows_signal_baseline_holds_while_speaking_and_session_end_resets() -> None:
    renderer, calls = _renderer({"MouthSmile": 0.3})

    await renderer.on_mouth_signal(MouthSignal(mouth_open=0.5, volume=0.8))
    assert calls[0] == ("MouthOpen", 0.5) and ("MouthSmile", 0.3) in calls  # 先写嘴，同帧说话基线随后

    calls.clear()
    await renderer.on_mouth_signal(MouthSignal(mouth_open=0.5, volume=0.8))
    assert all(name != "MouthOpen" for name, _ in calls)  # 同值不重复写嘴；基线已活跃也不再写表情

    calls.clear()
    await renderer.on_mouth_signal(MouthSignal(mouth_open=0.0, volume=0.0))  # 静音：基线收回，交还 idle/情绪渲染
    assert ("MouthSmile", 0.0) in calls

    await renderer.on_mouth_signal(MouthSignal(mouth_open=0.6, volume=0.9))
    calls.clear()
    await renderer.on_session_end()
    assert calls[-1] == ("MouthOpen", 0.0) and renderer.current_mouth_open == 0.0


async def test_mouth_open_is_filtered_out_of_base_expressions() -> None:
    renderer, calls = _renderer({"MouthSmile": 0.3, "MouthOpen": 0.5})
    await renderer.on_mouth_signal(MouthSignal(mouth_open=0.2, volume=0.5))
    assert ("MouthOpen", 0.5) not in calls  # 张嘴归口型信号管，基线里的 MouthOpen 被忽略
