# -*- coding: utf-8 -*-
"""TTS 回放巡检：把某场直播的主播台词按当前配置重新合成，自动判读异常。

用途：直播中偶发的合成异常（token 复读导致的音频拉长与杂音）是低概率
事件，直播时难以逐句把关；本脚本把历史场次的真实台词语料按**当前生产
配置**完整重放，用客观判据（时长比离群）自动标记异常句并落盘音频，
供耳朵复核。

前置条件：
- GPT-SoVITS 服务已在运行（默认 127.0.0.1:9880，8GB 显存单实例纪律）
- Amaidesu 数据库存在（data/amaidesu.db，只读访问）

用法：
    uv run python scripts/tts_replay.py                 # 回放最新场次全部台词
    uv run python scripts/tts_replay.py --session 21    # 指定场次
    uv run python scripts/tts_replay.py --limit 10      # 只回放前 10 条（快速验证）
    uv run python scripts/tts_replay.py --save-all      # 保存全部音频（默认只存异常句）

数据源：live_chat 表（sender_role='assistant' AND message_type='speak'），
表结构权威定义见 src/modules/storage/schema.py。
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.modules.config.toml_utils import load_toml_with_comments
from src.modules.storage._default_path import DEFAULT_DB_PATH
from src.modules.tts.backends.gptsovits.provider import GPTSoVITSProvider
from src.modules.tts.common import normalize_tts_text


def wav_duration(data: bytes) -> float | None:
    """从 WAV 字节估算音频时长（秒）；形态不符返回 None。

    兼容整段 WAV 与服务端流式首块（44B 头 + 裸 PCM）两种形态。
    """
    if len(data) < 44 or data[:4] != b"RIFF":
        return None
    pos = data.find(b"data", 12)
    if pos < 0:
        return None
    sample_rate = struct.unpack("<I", data[24:28])[0]
    channels = struct.unpack("<H", data[22:24])[0] or 1
    width = struct.unpack("<H", data[34:36])[0] // 8 or 2
    pcm_bytes = len(data) - (pos + 8)
    if sample_rate <= 0:
        return None
    return pcm_bytes / (sample_rate * channels * width)


def fetch_utterances(session_id: int) -> list[tuple[int, int, str]]:
    """读取指定场次的主播台词（id, timestamp_ms, content），只读访问。"""
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT id, timestamp_ms, content FROM live_chat "
            "WHERE live_session_id = ? AND sender_role = 'assistant' "
            "AND message_type = 'speak' ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()


def latest_session_id() -> int:
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(id) FROM live_sessions").fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        raise SystemExit("数据库中没有任何场次记录（live_sessions 为空）")
    return int(row[0])


async def main() -> None:
    parser = argparse.ArgumentParser(description="TTS 回放巡检：重放历史台词并自动判读合成异常")
    parser.add_argument("--session", type=int, default=None, help="live_sessions.id（默认取最新场次）")
    parser.add_argument("--limit", type=int, default=None, help="只回放前 N 条")
    parser.add_argument("--save-all", action="store_true", help="保存全部音频（默认只存异常句）")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "tts_replay", help="音频落盘目录")
    args = parser.parse_args()

    session_id = args.session if args.session is not None else latest_session_id()
    utterances = fetch_utterances(session_id)
    if args.limit:
        utterances = utterances[: args.limit]
    if not utterances:
        raise SystemExit(f"场次 {session_id} 没有主播台词（live_chat 无 assistant/speak 记录）")

    tts_cfg = dict(dict(load_toml_with_comments(ROOT / "config" / "infra.toml")["tts"])["gptsovits"])
    engine = GPTSoVITSProvider(config=tts_cfg)
    await engine.setup()
    await engine._warmup_task  # noqa: SLF001 — 巡检脚本等待预热完成再开始计量

    est_sec = sum(len(normalize_tts_text(c)) for _, _, c in utterances) / 4.5
    print(f"场次 {session_id} | 台词 {len(utterances)} 条 | 预计合成 {est_sec / 60:.0f} 分钟左右")
    print(
        f"参数: temp={engine.temperature} rep={engine.repetition_penalty} steps={engine.sample_steps} "
        f"streaming_mode={engine.streaming_mode}（回放走整段返回，合成路径一致）"
    )
    print("-" * 72)

    out_dir = args.out / f"session_{session_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[tuple[int, str, float | None, float | None, str]] = []
    t_start = time.perf_counter()
    for idx, (utt_id, _ts_ms, content) in enumerate(utterances, 1):
        chars = len(normalize_tts_text(content))
        status, audio_sec, data = "ok", None, b""
        try:
            data = engine.synthesize_bytes(content)
            if len(data) < 1000:
                status = "空音频"
            else:
                audio_sec = wav_duration(data)
                if audio_sec is None:
                    status = "WAV解析失败"
        except Exception as e:  # noqa: BLE001 — 逐条隔离，单条失败不中断回放
            status = f"失败: {type(e).__name__}"

        records.append((utt_id, content, chars / 4.5 if chars else None, audio_sec, status))

        # 时长比离群判读在收尾统一做（需要全场中位数），此处只报进度
        preview = content[:24].replace("\n", " ")
        print(
            f"[{idx}/{len(utterances)}] #{utt_id} {chars}字 {f'{audio_sec:.1f}s' if audio_sec else status} | {preview}",
            flush=True,
        )

        if args.save_all and audio_sec is not None:
            (out_dir / f"utt_{utt_id}.wav").write_bytes(data)
        elif status != "ok" and data:
            (out_dir / f"utt_{utt_id}_ANOMALY_{status.split(':')[0]}.wav").write_bytes(data)

    elapsed = time.perf_counter() - t_start
    await engine.cleanup()

    # ---- 判读：时长比 = 实际秒 / 按语速折算的期望秒；中位数自校准 ----
    ratios = [a / e for _, _, e, a, s in records if e and a and s == "ok" and a > 0]
    if not ratios:
        print("\n没有可判读的成功样本（全部失败？检查 GSV 服务状态）")
        return
    med = sorted(ratios)[len(ratios) // 2]

    print("-" * 72)
    anomalies: list[str] = []
    for utt_id, content, expected, actual, status in records:
        if status != "ok":
            anomalies.append(f"  #{utt_id} [{status}] {content[:40]}")
            continue
        ratio = (actual / expected) if (expected and actual) else 0.0
        if ratio > med * 1.5:
            anomalies.append(
                f"  #{utt_id} 时长比 {ratio:.2f}（基准 {med:.2f}）"
                f"实际 {actual:.1f}s / 期望 {expected:.1f}s | {content[:40]}"
            )
        elif ratio < med * 0.5:
            anomalies.append(f"  #{utt_id} 时长比 {ratio:.2f} 偏短（疑似截断）| {content[:40]}")

    print(
        f"回放完成: {len(records)} 条 | 耗时 {elapsed / 60:.1f} 分钟 | "
        f"时长比基准(中位) {med:.2f} | 异常 {len(anomalies)} 条"
    )
    if anomalies:
        print("异常清单（音频已存 " + str(out_dir) + "）:")
        for line in anomalies:
            print(line)
    else:
        print("全部正常 ✓")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
