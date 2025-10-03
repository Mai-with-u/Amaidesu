from typing import Optional, Dict, Any
import os
import sys

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import toml as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore


def load_local_config() -> Dict[str, Any]:
    """加载位于当前包目录的 config.toml；若不存在则尝试从模板复制逻辑交由上层处理。
    若文件不存在，返回空字典。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "config.toml")
    if not tomllib:
        return {}
    if not os.path.exists(cfg_path):
        return {}
    try:
        with open(cfg_path, "rb") as f:
            return tomllib.load(f) or {}
    except Exception:
        return {}


def get_api_key_from_config(config: Dict[str, Any]) -> Optional[str]:
    dashscope = config.get("dashscope") if isinstance(config, dict) else None
    if isinstance(dashscope, dict):
        api_key = dashscope.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            return api_key.strip()
    return None


def get_device_index_from_config(config: Dict[str, Any]) -> Optional[int]:
    device = config.get("device") if isinstance(config, dict) else None
    if isinstance(device, dict):
        idx = device.get("index")
        try:
            return int(idx) if idx is not None and str(idx).strip() != "" else None
        except Exception:
            return None
    return None


def get_audio_params_from_config(config: Dict[str, Any]) -> tuple[int, int, int]:
    sample_rate = 16000
    channels = 1
    block_size = 3200
    audio = config.get("audio") if isinstance(config, dict) else None
    if isinstance(audio, dict):
        sample_rate = int(audio.get("sample_rate", sample_rate) or sample_rate)
        channels = int(audio.get("channels", channels) or channels)
        block_size = int(audio.get("block_size", block_size) or block_size)
    return sample_rate, channels, block_size


