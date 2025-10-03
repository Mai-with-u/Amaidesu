"""
设备枚举与选择工具。

依赖: sounddevice
"""

from typing import List, Optional, Dict, Any

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore


def list_input_devices() -> List[Dict[str, Any]]:
    """列出可用输入设备的基本信息。"""
    if sd is None:
        return []

    devices = sd.query_devices()
    results: List[Dict[str, Any]] = []
    for index, info in enumerate(devices):
        if info.get("max_input_channels", 0) > 0:
            results.append(
                {
                    "index": index,
                    "name": info.get("name", str(index)),
                    "max_input_channels": info.get("max_input_channels", 0),
                    "default_samplerate": int(info.get("default_samplerate", 16000) or 16000),
                }
            )
    return results


def resolve_device_index(preferred_index: Optional[int]) -> Optional[int]:
    """返回可用的设备索引。若给定索引不可用则回退到系统默认或 None。"""
    if sd is None:
        return None

    try:
        if preferred_index is not None:
            info = sd.query_devices(preferred_index)
            if info.get("max_input_channels", 0) > 0:
                return preferred_index

        default_index = sd.default.device[0] if sd.default.device else None
        if default_index is not None:
            info = sd.query_devices(default_index)
            if info.get("max_input_channels", 0) > 0:
                return default_index
    except Exception:
        pass

    # 最终回退: 选择第一个可用输入设备
    devices = list_input_devices()
    if devices:
        return int(devices[0]["index"])
    return None


