r"""IndexTTS 本地常驻 HTTP 服务启动脚本

IndexTTS 官方只提供 Python API 与给人用的 WebUI，没有 HTTP 服务；本脚本
把官方推理包包装成 Amaidesu ``index_tts`` 引擎对接的本地常驻服务（FastAPI
薄包装，"本地进程 HTTP"形态同 GPT-SoVITS api_v2）。

用法（须用 IndexTTS 检出目录里 .venv 的 Python 解释器运行，依赖只装在那
个环境里；--index-tts-dir 指向该检出根目录）：

    D:\path\to\index-tts\.venv\Scripts\python.exe scripts/indextts_serve.py ^
        --index-tts-dir D:\path\to\index-tts ^
        --ref D:\path\to\reference_audio.wav ^
        --port 9888

端点：
- GET  /health      → {"status","device","ref_audio"}
- POST /tts         {"text", "seg_tokens"?, "emo_vector"?} → 完整 WAV（audio/wav）
- POST /tts_stream  同参数 → 流式 WAV（头部总长度占位，逐段追加 PCM）

注意：
- 模型常驻约 5.6GB 显存，8GB 级显卡要求桌面基线 <2.5GB，否则触发驱动
  显存回退、速度劣化数百倍
- 依赖 fastapi/uvicorn（随官方 webui extra 安装）；在 IndexTTS 目录里
  不要用 ``uv run`` 启动（会触发隐性依赖重装）
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import threading
import time

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
parser.add_argument("--index-tts-dir", required=True, help="IndexTTS 官方仓库检出根目录（含 indextts/ 与 checkpoints/）")
parser.add_argument("--port", type=int, default=9888)
parser.add_argument("--ref", required=True, help="说话人参考音频路径（音色由它决定，服务启动后固定）")
parser.add_argument("--lang", default="zh", help="合成语言（zh/zhen/en/ja/es/ar）")
args = parser.parse_args()

# 官方推理包在 import 时按相对路径定位缓存目录，必须先切工作目录再导入
sys.path.insert(0, args.index_tts_dir)
os.chdir(args.index_tts_dir)

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import Response, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
import torch  # noqa: E402
import uvicorn  # noqa: E402

from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

SR = 22050

print(">> 加载模型中（约 20-30s，期间端口未开）...")
t0 = time.perf_counter()
TTS = IndexTTS2(
    cfg_path=os.path.join(args.index_tts_dir, "checkpoints", "config.yaml"),
    model_dir=os.path.join(args.index_tts_dir, "checkpoints"),
    use_bf16=True,
)
print(f">> 模型加载完成 {time.perf_counter() - t0:.0f}s")

_LOCK = threading.Lock()  # 推理非线程安全，请求串行化

app = FastAPI(title="IndexTTS local server")


class TtsRequest(BaseModel):
    text: str
    seg_tokens: int | None = None
    emo_vector: list[float] | None = None


def _wav_stream_header() -> bytes:
    """流式 WAV 头：总长度用占位大值，后续 PCM 直接追加。"""
    block_align = 1 * 16 // 8
    byte_rate = SR * block_align
    riff_size = 0xFFFFFFFF - 8
    data_size = 0xFFFFFFFF - 44
    return (
        b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, byte_rate, block_align, 16)
        + b"data" + struct.pack("<I", data_size)
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "device": TTS.device, "ref_audio": args.ref}


@app.post("/tts")
def tts(req: TtsRequest) -> Response:
    out_path = os.path.join(args.index_tts_dir, "checkpoints", "_srv_tts_out.wav")
    with _LOCK:
        TTS.infer(
            spk_audio_prompt=args.ref, text=req.text, output_path=out_path,
            lang=args.lang, emo_vector=req.emo_vector,
        )
    with open(out_path, "rb") as f:
        data = f.read()
    os.remove(out_path)
    return Response(data, media_type="audio/wav")


@app.post("/tts_stream")
def tts_stream(req: TtsRequest) -> Response:
    def gen():
        with _LOCK:
            yield _wav_stream_header()
            for wav in TTS.infer(
                spk_audio_prompt=args.ref, text=req.text, output_path=None,
                lang=args.lang, emo_vector=req.emo_vector, stream_return=True,
                max_text_tokens_per_segment=req.seg_tokens or 120,
            ):
                pcm = wav.to(torch.int16).numpy().tobytes()
                yield pcm

    return StreamingResponse(gen(), media_type="audio/wav")


uvicorn.run(app, host="127.0.0.1", port=args.port)
