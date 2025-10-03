"""
最小可运行客户端：
- 列出音频输入设备，支持通过环境变量 MIC_DEVICE_INDEX 选择
- 启动 Fun-ASR 实时识别，将麦克风音频推送并打印识别文本

运行前准备：
- pip install dashscope sounddevice numpy
- 设置环境变量 DASHSCOPE_API_KEY

参考: https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk
"""

import os
import signal
import sys
import time

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import dashscope

from src.sound_translator import (
    list_input_devices,
    resolve_device_index,
    FunASRRecognizer,
    MicStreamer,
    load_local_config,
    get_api_key_from_config,
    get_device_index_from_config,
    get_audio_params_from_config,
)


def main() -> None:
    cfg = load_local_config()
    api_key = get_api_key_from_config(cfg)
    if api_key:
        dashscope.api_key = api_key

    # 音频参数
    sample_rate, channels, block_size = get_audio_params_from_config(cfg)
    if channels != 1:
        print(f"[WARN] 仅支持单声道，已忽略配置 channels={channels}，强制使用 1。")
        channels = 1

    devices = list_input_devices()
    print("可用输入设备:")
    for d in devices:
        print(f"  [{d['index']}] {d['name']} (channels={d['max_input_channels']}, default_sr={d['default_samplerate']})")

    preferred_index = get_device_index_from_config(cfg)
    device_index = resolve_device_index(preferred_index)
    print(f"使用设备索引: {device_index}")

    # 回调：打印识别结果
    def on_sentence(text: str, is_final: bool) -> None:
        if text:
            tag = "[FINAL]" if is_final else "[PART]"
            print(f"{tag} {text}")

    def on_error(msg: str) -> None:
        print(f"[ERROR] {msg}")

    recognizer = FunASRRecognizer(
        model="fun-asr-realtime",
        sample_rate=sample_rate,
        audio_format="pcm",
        on_sentence=on_sentence,
        on_error=on_error,
    )

    streamer = MicStreamer(
        device_index=device_index,
        sample_rate=sample_rate,
        block_size=block_size,  # ~100ms @ 16kHz
        on_chunk=recognizer.send_audio_frame,
    )

    stop_flag = {"stop": False}

    def handle_sigint(signum, frame):  # type: ignore[override]
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    try:
        recognizer.start()
        streamer.start()
        print("正在识别，按 Ctrl+C 结束...")
        while not stop_flag["stop"]:
            time.sleep(0.1)
    finally:
        try:
            streamer.stop()
        except Exception:
            pass
        try:
            recognizer.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()


