r"""IndexTTS 本地常驻 HTTP 服务启动脚本

IndexTTS 官方只提供 Python API 与给人用的 WebUI，没有 HTTP 服务；本脚本
把官方推理包包装成 Amaidesu ``index_tts`` 引擎对接的本地常驻服务（FastAPI
薄包装，"本地进程 HTTP"形态同 GPT-SoVITS api_v2）。

用法（须用 IndexTTS 检出目录里 .venv 的 Python 解释器运行，依赖只装在那
个环境里；--index-tts-dir 指向该检出根目录）：

    D:\path\to\index-tts\.venv\Scripts\python.exe scripts/indextts_serve.py ^
        --index-tts-dir D:\path\to\index-tts ^
        --ref D:\path\to\reference_audio.wav ^
        --port 9888 ^
        [--qwen-emo-cpu]

端点：
- GET  /health      → {"status","device","ref_audio","qwen_emo"}
- POST /tts         {"text", "seg_tokens"?, "emo_vector"?, "emotion_auto"?}
                    → 完整 WAV（audio/wav）
- POST /tts_stream  同参数 → 流式 WAV（头部总长度占位，逐段追加 PCM）

情绪向量解析顺序：请求显式 ``emo_vector`` > ``emotion_auto=true`` 时由
QwenEmotion 从台词现算（需 --qwen-emo-cpu，CPU 推理每句约 +2-4s，不占
显存）> 构造时无静态向量则不带（用参考音频情绪锚）。现算结果经维度
偏置 + 总和 0.6 上限归一（与 Amaidesu 引擎侧同规则）。

注意：
- 模型常驻约 5.6GB 显存，8GB 级显卡要求桌面基线 <2.5GB，否则触发驱动
  显存回退、速度劣化数百倍
- 依赖 fastapi/uvicorn（随官方 webui extra 安装）；在 IndexTTS 目录里
  不要用 ``uv run`` 启动（会触发隐性依赖重装）
"""

from __future__ import annotations

import argparse
import json
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
parser.add_argument("--qwen-emo-cpu", action="store_true", help="加载 QwenEmotion 到 CPU（实验：emotion_auto 请求由台词现算情绪向量）")
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

# ---------------------------------------------------------------------------
# QwenEmotion CPU 卸载版（实验）：官方类硬编码 device_map="auto"（占 GPU 显存），
# 本副本强制 CPU——0.6B fp32 约占 2.4GB 内存、每句推理约 +2-4s，不占显存。
# 属性与方法与官方类一致，仅设备与精度不同。
# ---------------------------------------------------------------------------

QWEN: "object | None" = None
if args.qwen_emo_cpu:
    from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

    from indextts.infer_v2_5 import QwenEmotion  # noqa: E402

    class QwenEmotionCPU(QwenEmotion):
        def __init__(self, model_dir):
            self.model_dir = model_dir
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_dir,
                torch_dtype=torch.float32,
                device_map="cpu",
            )
            self.prompt = "文本情感分类"
            self.cn_key_to_en = {
                "高兴": "happy",
                "愤怒": "angry",
                "悲伤": "sad",
                "恐惧": "afraid",
                "反感": "disgusted",
                "低落": "melancholic",
                "惊讶": "surprised",
                "自然": "calm",
            }
            self.desired_vector_order = ["高兴", "愤怒", "悲伤", "恐惧", "反感", "低落", "惊讶", "自然"]
            self.melancholic_words = {
                "低落",
                "melancholy",
                "melancholic",
                "depression",
                "depressed",
                "gloomy",
            }
            self.max_score = 1.2
            self.min_score = 0.0

    print(">> 加载 QwenEmotion 到 CPU（实验）...")
    t0 = time.perf_counter()
    QWEN = QwenEmotionCPU(os.path.join(args.index_tts_dir, "checkpoints", "qwen0.6bemo4-merge"))
    print(f">> QwenEmotion 加载完成 {time.perf_counter() - t0:.0f}s")

# 情绪向量归一（与 Amaidesu 引擎侧同规则）：维度偏置压低易出怪声的惊喜/平静，
# 总和压到 0.6 上限——混合公式 vec+(1-sum)×锚 里总和即注入占比，超 1 会负锚
_EMO_BIAS = (0.9375, 0.875, 1.0, 1.0, 0.9375, 0.9375, 0.6875, 0.5625)
_EMO_SUM_CAP = 0.6
_VECTOR_ORDER = ("happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm")


def _normalize_emo_vector(vec: list[float]) -> list[float]:
    biased = [v * b for v, b in zip(vec, _EMO_BIAS, strict=False)]
    total = sum(biased)
    if total > _EMO_SUM_CAP:
        biased = [v * (_EMO_SUM_CAP / total) for v in biased]
    return [round(v, 4) for v in biased]


def _resolve_emo_vector(req: "TtsRequest") -> tuple[list[float] | None, dict | None]:
    """情绪向量解析：显式 emo_vector > emotion_auto 现算 > None（用锚）。

    返回 (向量, QwenEmotion 检出字典)；检出字典供响应头/日志展示。
    """
    if req.emotion_auto:
        if QWEN is None:
            raise ValueError("emotion_auto 需要 --qwen-emo-cpu 启动服务")
        emotion_dict = QWEN.inference(req.text)
        vec = _normalize_emo_vector([float(emotion_dict.get(k, 0.0)) for k in _VECTOR_ORDER])
        print(f">> QwenEmotion 现算: {dict(emotion_dict)} → 归一化 {vec}")
        return vec, dict(emotion_dict)
    return req.emo_vector, None


app = FastAPI(title="IndexTTS local server")


class TtsRequest(BaseModel):
    text: str
    seg_tokens: int | None = None
    emo_vector: list[float] | None = None
    emotion_auto: bool = False


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
    return {"status": "ok", "device": TTS.device, "ref_audio": args.ref, "qwen_emo": QWEN is not None}


@app.post("/tts")
def tts(req: TtsRequest) -> Response:
    out_path = os.path.join(args.index_tts_dir, "checkpoints", "_srv_tts_out.wav")
    emo_vector, detected = _resolve_emo_vector(req)
    with _LOCK:
        TTS.infer(
            spk_audio_prompt=args.ref, text=req.text, output_path=out_path,
            lang=args.lang, emo_vector=emo_vector,
        )
    with open(out_path, "rb") as f:
        data = f.read()
    os.remove(out_path)
    headers = {"X-Detected-Emotion": json.dumps(detected, ensure_ascii=False)} if detected else {}
    return Response(data, media_type="audio/wav", headers=headers)


@app.post("/tts_stream")
def tts_stream(req: TtsRequest) -> Response:
    emo_vector, detected = _resolve_emo_vector(req)
    headers = {"X-Detected-Emotion": json.dumps(detected, ensure_ascii=False)} if detected else {}

    def gen():
        with _LOCK:
            yield _wav_stream_header()
            for wav in TTS.infer(
                spk_audio_prompt=args.ref, text=req.text, output_path=None,
                lang=args.lang, emo_vector=emo_vector, stream_return=True,
                max_text_tokens_per_segment=req.seg_tokens or 120,
            ):
                pcm = wav.to(torch.int16).numpy().tobytes()
                yield pcm

    return StreamingResponse(gen(), media_type="audio/wav", headers=headers)


uvicorn.run(app, host="127.0.0.1", port=args.port)
