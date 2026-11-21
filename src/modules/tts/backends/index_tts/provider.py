"""IndexTTSProvider - IndexTTS 本地语音合成引擎（index_tts）

- 引擎：经自建本地 HTTP 服务流式合成（按文本切段逐段推送 PCM），逐块写声卡播放
- 调用方：``src.agents.streamer.utterance_queue``（编排层确定性调用）
- 自治生命周期：``handle_speech()`` 入口自动 ensure_setup，调用方无需预调
- 错误语义：合成失败主动发 ``tts.utterance.failed`` 并 raise，由队列兜底
- 接受可选 ``utterance_id`` 参数：传入时按 ``tts.utterance.*`` 生命周期发布
  事件；缺省/None 时静默跳过（手动 / 直调场景无 utterance 上下文）
- 说话人音色由服务端启动参数（参考音频）持有，引擎配置不涉及音色；
  情感向量（8 维）可静态注入合成参数

红线边界：引擎是被动驱动的工具，只做"文本→音频"纯转换——说什么永远由
主播 Agent 定稿；情感向量只静态注入合成参数，不承载台词内容。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import numpy as np

from src.modules.config.core_schemas import IndexTTSParams
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from ...common import (
    build_stats_dict,
    compute_duration_ms,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
    prepare_speech_text,
)
from .client import IndexTTSClient, IndexTTSServiceError

# IndexTTS 服务输出 = 裸 PCM int16 单声道（采样率随服务端，配置须与服务端一致）
CHANNELS = 1
DTYPE = np.int16

# 情感向量维度：喜/怒/哀/惧/厌恶/低落/惊喜/平静
EMO_VECTOR_DIMS = 8


class IndexTTSProvider:
    """IndexTTS 本地语音合成引擎。

    引擎通过 ``handle_speech(text, utterance_id)`` 暴露唯一入口；调用方
    （编排队列）无需关心 setup / cleanup 生命周期——``handle_speech`` 开头
    自动 ensure_setup。
    """

    PROVIDER_NAME = "index_tts"

    # 参数 Schema 单一事实源在 config 侧（core_schemas），此处仅别名引用
    ConfigSchema = IndexTTSParams

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        audio_sink: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        # 音频分接（AudioSink 协议形状；口型分析等消费者经装配链注入）
        self.audio_sink = audio_sink
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(config)
        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.seg_tokens = self.typed_config.seg_tokens
        self.sample_rate = self.typed_config.sample_rate
        # 情感向量：全 0 视作"不使用"，不携带该参数（服务端按中性情感合成）
        self.emo_vector: Optional[list[float]] = None
        if any(value != 0.0 for value in self.typed_config.emo_vector):
            if len(self.typed_config.emo_vector) != EMO_VECTOR_DIMS:
                raise ValueError(
                    f"emo_vector 须为 {EMO_VECTOR_DIMS} 维（喜/怒/哀/惧/厌恶/低落/惊喜/平静），"
                    f"实际 {len(self.typed_config.emo_vector)} 维"
                )
            self.emo_vector = list(self.typed_config.emo_vector)

        self.client: Optional[IndexTTSClient] = None
        self.audio_manager: Any = None
        self.render_count = 0
        self.error_count = 0
        self._is_connected = False
        self._has_started = False
        # 启动预热任务（服务端首请求含说话人条件冷提取，挪出直播首句）
        self._warmup_task: Optional[asyncio.Task] = None
        # 串行化锁：同一时间只合成一句话
        self.tts_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    # ===== 生命周期 =====

    def _build_audio_manager(self) -> Any:
        # 函数内 import：src.modules.audio 顶层即拉起 numpy（重型数值依赖）并初始化声卡库，仅在需要音频设备管理时加载
        from src.modules.audio import AudioDeviceManager

        return AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            sink=self.audio_sink,
        )

    async def setup(self) -> None:
        """初始化引擎（幂等）。"""
        if self._has_started:
            return
        self.client = IndexTTSClient(
            host=self.host,
            port=self.port,
            seg_tokens=self.seg_tokens,
            emo_vector=self.emo_vector,
        )

        # 首次导入声卡库会初始化 PortAudio（实测约半秒），setup 多在第一句话时懒触发，放工作线程，
        # 免得第一句开口时整个事件循环（形象逐帧驱动、弹幕、控制台）一起顿住
        self.audio_manager = await asyncio.to_thread(self._build_audio_manager)

        self._has_started = True
        self._is_connected = True
        self._warmup_task = asyncio.create_task(self._warmup())
        emo_suffix = "，情感向量已配置" if self.emo_vector is not None else ""
        self.logger.info(
            f"IndexTTSProvider 设置完成（{self.host}:{self.port}，seg_tokens={self.seg_tokens}{emo_suffix}）"
        )

    async def _warmup(self) -> None:
        """启动预热：把服务端首请求的说话人条件冷提取挪出直播首句。

        极短文本合成触发服务端的参考音频特征提取与首次推理缓存。全程
        fire-and-forget：服务未起/预热失败只记 warning，不影响装配，真实
        首句合成自带兜底（最多退化为承担一次冷启动延迟）。
        """
        try:
            async for _chunk in self.client.stream_synthesize("预热。"):  # type: ignore[union-attr]
                pass
            self.logger.info("IndexTTS 预热完成，首句合成已是热态")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.warning(f"IndexTTS 预热未完成（服务未启动或异常，首句合成将承担冷启动延迟）: {e}")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        self.logger.info("IndexTTSProvider 清理中...")
        if self._warmup_task is not None and not self._warmup_task.done():
            self._warmup_task.cancel()
        if self.audio_manager:
            self.audio_manager.stop_stream(discard_pending=True)
        if self.client:
            await self.client.close()
        self._is_connected = False
        self._has_started = False
        self.logger.info("IndexTTSProvider 清理完成")

    # ===== 业务方法 =====

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        """合成并播放语音（流式主干：合成与播放同时进行，首段到达即出声）。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件。
        """
        if not self._has_started:
            await self.setup()
        original_text = text.strip()
        final_text = prepare_speech_text(self.logger, original_text)
        if final_text is None:
            return

        # 流式引擎不走 common 编排骨架：started 须在合成前发布（duration=None）、
        # finished 时长按写往声卡的 PCM 样本数精确计算、截断须先等在途写线程收敛
        # 再关流——事件时序与截断语义同全量骨架本质不同
        # （见 common.synthesize_play_and_notify 设计约束）。
        # 事件 speech_text 传规范化前原文：字幕是展示面，不随合成清洗改写
        await emit_utterance_started(
            self.event_bus,
            utterance_id=utterance_id,
            speech_text=original_text,
            engine=self.PROVIDER_NAME,
            duration_ms=None,
        )

        lock_acquired = False
        in_flight_task: Optional[asyncio.Task[Any]] = None
        try:
            async with self.tts_lock:
                lock_acquired = True
                # 打开声卡输出流放工作线程（在事件循环里同步打开，每句开口形象都顿一两百毫秒）；
                # 开始这一句仍在事件循环上做：口型分析器要在循环里起后台任务
                stream = await asyncio.to_thread(self.audio_manager.open_output_stream)
                self.audio_manager.start_prepared_stream(stream, utterance_id=utterance_id)

                # 累计写往声卡的 PCM 样本数（单声道：ndarray.size 即样本数），
                # finished 事件的 duration_ms 由"样本数 ÷ 采样率"精确计算
                total_samples = 0
                async for pcm_bytes in self.client.stream_synthesize(final_text):  # type: ignore[union-attr]
                    chunk = np.frombuffer(pcm_bytes, dtype=DTYPE)
                    if chunk.size == 0:
                        continue
                    in_flight_task = asyncio.create_task(asyncio.to_thread(self.audio_manager.write_chunk, chunk))
                    await in_flight_task
                    total_samples += int(chunk.size)

                # 收尾放工作线程等待回调流播完余量，期间被取消则由取消分支立即 abort 唤醒
                in_flight_task = asyncio.create_task(asyncio.to_thread(self.audio_manager.stop_stream))
                await in_flight_task

            self.logger.debug(f"TTS 播放完成: '{final_text[:30]}...'")
            self.render_count += 1

            await emit_utterance_finished(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                duration_ms=compute_duration_ms(total_samples, self.sample_rate),
            )

        except asyncio.CancelledError:
            # 仅持锁方有权重置当前声卡会话：先同步 abort+丢弃缓冲唤醒在途 stop_stream，再等待工作线程收敛
            if lock_acquired:
                self.audio_manager.stop_stream(discard_pending=True)
                if in_flight_task is not None and not in_flight_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(in_flight_task), timeout=1.0)
                    except Exception as exc:  # noqa: BLE001 — 流已关闭的在途任务收尾异常属预期
                        self.logger.debug(f"取消路径在途写/收尾收敛等待失败（流已关闭，属预期）: {exc}", exc=True)
            self.logger.warning(f"TTS 渲染被取消（已截断播放）: utterance_id={utterance_id}")
            raise
        except IndexTTSServiceError as e:
            # 服务未启动等环境故障是预期内的：简短告警即可，不打全量 traceback。
            # 失败半句的声卡预缓冲直接丢弃，不冲播出半句话
            if lock_acquired:
                self.audio_manager.stop_stream(discard_pending=True)
            self.error_count += 1
            self.logger.warning(f"TTS 渲染失败: {e}（本条放弃，等待服务恢复）")
            # 先发 failed 让字幕出字再上抛；cause=e 给异常打"已报告"标记，队列兜底不再补发第二条
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                speech_text=text,
                engine=self.PROVIDER_NAME,
                error_message=str(e),
                cause=e,
            )
            raise
        except Exception as e:
            if lock_acquired:
                self.audio_manager.stop_stream(discard_pending=True)
            self.error_count += 1
            self.logger.exception(f"TTS 渲染失败: {e}")
            # 同上：事件发出后给异常打"已报告"标记，避免队列再补发一条重复的 failed
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                speech_text=text,
                engine=self.PROVIDER_NAME,
                error_message=f"{type(e).__name__}: {e}",
                cause=e,
            )
            raise

    def get_stats(self) -> Dict[str, Any]:
        return build_stats_dict(
            name=self.__class__.__name__,
            is_connected=self._is_connected,
            render_count=self.render_count,
            error_count=self.error_count,
            extra={"host": self.host, "port": self.port, "seg_tokens": self.seg_tokens},
        )


def create_index_tts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    audio_sink: Optional[Any] = None,
) -> IndexTTSProvider:
    """工厂：构造 ``IndexTTSProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return IndexTTSProvider(
        config=config,
        event_bus=event_bus,
        audio_sink=audio_sink,
    )


__all__ = ["IndexTTSProvider", "create_index_tts_provider"]
