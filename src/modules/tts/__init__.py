"""TTS 基础设施模块

配置驱动的语音合成基础设施：每个引擎一个 Provider（Edge TTS / GPT-SoVITS /
Voicebox / Omni TTS / MiniMax / OpenAI），引擎名单的唯一事实源是
``assembly.py`` 的装配表键集。其调用方为 ``src.agents.streamer.utterance_queue``
（位于 StreamerAgent 包内），由编排层在配置开启时确定性调用（不经 Agent
工具调用路径）；装配入口 ``build_tts_infrastructure`` 由本模块对外暴露。

设计要点
--------

- 引擎自身自治生命周期：``handle_speech()`` 入口内置 ``ensure_setup``
  检查（setup 幂等，重复调用早退），调用方无需在注入前显式 setup。
- 错误传播语义：引擎合成失败时**主动发** ``tts.utterance.failed`` 事件，
  在异常上打"已上报"标记后向上 raise，由编排队列兜底（不返回
  ``ToolExecutionResult``）。队列见到标记就不再发；没有标记说明引擎在发
  事件之前就出错了，由队列补发一条，保证每次失败恰好一条 failed 事件。
- 事件契约：``tts.utterance.*`` 生命周期事件由引擎直接发布，队列订阅
  用于对齐自己的 ``started`` / ``finished`` 时刻，只在上面那种引擎没发
  failed 的情况下替它补发。
- ``ConfigSchema`` 保留：``multi_file_loader`` 通过模块路径字符串延迟加载
  各 Provider 的 ``ConfigSchema`` 用于配置补全，不与工具注册耦合。
- 装配自治：``build_tts_infrastructure`` 在本包内（``src/modules/tts/``）
  构造选中引擎实例供组合根注入。
"""

from .assembly import build_tts_infrastructure
from .common import (
    build_stats_dict,
    compute_duration_ms,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
)
from .backends.edge.provider import EdgeTTSProvider, create_edge_tts_provider
from .backends.gptsovits.provider import GPTSoVITSProvider, create_gptsovits_provider
from .backends.minimax.provider import MiniMaxTTSProvider, create_minimax_tts_provider
from .backends.omni.provider import OmniTTSProvider, create_omni_tts_provider
from .backends.openai.provider import OpenAITTSProvider, create_openai_tts_provider
from .protocol import TTSProvider, TtsObservation
from .backends.voicebox.provider import VoiceboxProvider, create_voicebox_provider
from .wav_decoder import decode_wav_chunk, extract_pcm_from_wav

__all__ = [
    # Provider 类
    "EdgeTTSProvider",
    "GPTSoVITSProvider",
    "MiniMaxTTSProvider",
    "OmniTTSProvider",
    "OpenAITTSProvider",
    "VoiceboxProvider",
    "TTSProvider",
    "TtsObservation",
    # 工厂函数
    "create_edge_tts_provider",
    "create_gptsovits_provider",
    "create_minimax_tts_provider",
    "create_omni_tts_provider",
    "create_openai_tts_provider",
    "create_voicebox_provider",
    # 装配入口
    "build_tts_infrastructure",
    # 共享辅助
    "compute_duration_ms",
    "build_stats_dict",
    "emit_utterance_started",
    "emit_utterance_finished",
    "emit_utterance_failed",
    # WAV 解码工具
    "decode_wav_chunk",
    "extract_pcm_from_wav",
]
