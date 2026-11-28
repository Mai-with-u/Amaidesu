"""OpenAITTSProvider - OpenAI 语音合成引擎（openai_tts）

- 引擎：经 OpenAI ``POST /v1/audio/speech`` 流式合成（响应体按 ``stream_format``
  为事件流或连续音频流，两者都归结为裸 PCM 片段），逐块写声卡播放，首块
  到达即出声
- 调用方：``src.agents.streamer.utterance_queue``（编排层确定性调用）
- 自治生命周期：``handle_speech()`` 入口自动 ensure_setup，调用方无需预调
- 错误语义：合成失败主动发 ``tts.utterance.failed`` 并 raise，由队列兜底；
  失败半句的声卡预缓冲直接丢弃，不冲播出半句话
- 接受可选 ``utterance_id`` 参数：传入时按 ``tts.utterance.*`` 生命周期发布
  事件；缺省/None 时静默跳过（手动 / 直调场景无 utterance 上下文）
- 密钥段内自持：``api_key`` 在 ``[tts.openai_tts]`` 段，与 LLM 提供商密钥
  互不相干（先例：MiniMax TTS、讯飞 STT 采集器、web 搜索工具）

与同名的 ``omni_tts`` 的区别：omni_tts 走 LLM 的 chat 模态音频输出（
``modalities=audio`` 的 OpenAI 兼容线协议，经 model.toml 的 profile 取端点与
密钥）；本引擎走专用 speech 端点，模型、音色与风格提示都是该端点自己的词表。

红线边界：引擎是被动驱动的工具，只做"文本→音频"纯转换——说什么永远由
主播 Agent 定稿；风格提示 / 语速只静态注入合成参数，不承载台词内容。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Tuple

import numpy as np

from src.modules.config.core_schemas import OpenAITTSParams
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
from .client import OpenAITTSApiError, OpenAITTSClient

# speech 端点的 pcm 封装固定为 24kHz / int16 / 单声道（接口无采样率参数）
SAMPLE_RATE = 24000
CHANNELS = 1
DTYPE = np.int16
# 单个样本的字节数：传输块边界可能切在半个样本之间，拼接残字节时用它对齐
_SAMPLE_BYTES = np.dtype(DTYPE).itemsize
# 远端单并发推理机硬打断交接期返回 HTTP 503 时的默认阶梯退避重试间隔（毫秒）：
# 旧句剩余同步推理释放单并发槽位通常需 1.5–4s，5 步阶梯退避合计 5.25s 覆盖交接窗口
DEFAULT_BUSY_RETRY_DELAYS_MS: Tuple[int, ...] = (250, 500, 1000, 1500, 2000)
# 触发短退避重试的远端繁忙状态码
_HTTP_SERVICE_UNAVAILABLE = 503


class OpenAITTSProvider:
    """OpenAI 语音合成引擎。

    引擎通过 ``handle_speech(text, utterance_id)`` 暴露唯一入口；调用方
    （编排队列）无需关心 setup / cleanup 生命周期——``handle_speech`` 开头
    自动 ensure_setup。
    """

    PROVIDER_NAME = "openai_tts"

    # 参数 Schema 单一事实源在 config 侧（core_schemas），此处仅别名引用
    ConfigSchema = OpenAITTSParams

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        audio_sink: Optional[Any] = None,
        *,
        busy_retry_delays_ms: Sequence[int] = DEFAULT_BUSY_RETRY_DELAYS_MS,
        sleep_fn: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        # 音频分接（AudioSink 协议形状；口型分析等消费者经装配链注入）
        self.audio_sink = audio_sink
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(config)
        self.base_url = self.typed_config.base_url
        self.api_key = self.typed_config.api_key.strip()
        self.model = self.typed_config.model
        self.voice = self.typed_config.voice.strip()
        self.instructions = self.typed_config.instructions
        self.speed = self.typed_config.speed
        self.stream_format = self.typed_config.stream_format

        # HTTP 503 短退避重试参数与可注入异步等待函数（测试注入零耗时等待以确定性验证退避序列）
        self._busy_retry_delays_ms: Tuple[int, ...] = tuple(max(0, int(ms)) for ms in busy_retry_delays_ms)
        self._sleep_fn: Callable[[float], Awaitable[None]] = sleep_fn if sleep_fn is not None else asyncio.sleep

        self.client: Optional[OpenAITTSClient] = None
        self.audio_manager: Any = None
        self.render_count = 0
        self.error_count = 0
        self._is_connected = False
        self._has_started = False
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
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            sink=self.audio_sink,
        )

    async def setup(self) -> None:
        """初始化引擎（幂等）。配置缺陷（密钥/音色/模型未填）在此硬错。"""
        if self._has_started:
            return
        if not self.api_key:
            raise RuntimeError("未配置 api_key：请在 [tts.openai_tts].api_key 填写 OpenAI API 密钥")
        if not self.model:
            raise RuntimeError("未配置 model：请在 [tts.openai_tts].model 填写合成模型（如 gpt-4o-mini-tts）")
        if not self.voice:
            raise RuntimeError("未配置 voice：请在 [tts.openai_tts].voice 填写音色名（如 alloy）")

        self.client = OpenAITTSClient(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            voice=self.voice,
            instructions=self.instructions,
            speed=self.speed,
            stream_format=self.stream_format,
        )

        # 首次导入声卡库会初始化 PortAudio（实测约半秒），setup 多在第一句话时懒触发，放工作线程，
        # 免得第一句开口时整个事件循环（形象逐帧驱动、弹幕、控制台）一起顿住
        self.audio_manager = await asyncio.to_thread(self._build_audio_manager)

        self._has_started = True
        self._is_connected = True
        self.logger.info(f"OpenAITTSProvider 设置完成（model={self.model}, voice={self.voice}）")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        self.logger.info("OpenAITTSProvider 清理中...")
        if self.audio_manager:
            self.audio_manager.stop_stream(discard_pending=True)
        if self.client:
            await self.client.close()
        self._is_connected = False
        self._has_started = False
        self.logger.info("OpenAITTSProvider 清理完成")

    # ===== 业务方法 =====

    async def handle_speech(
        self,
        text: str,
        utterance_id: Optional[str] = None,
        emotion: Optional[str] = None,
        emotion_intensity: Optional[float] = None,
    ) -> None:
        """合成并播放语音（流式主干：合成与播放同时进行，首块到达即出声）。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件。
            emotion / emotion_intensity: 情绪提示；本引擎不支持动态情感，忽略。
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
                attempt = 0
                while True:
                    # 传输块边界不保证按样本对齐（一个 int16 样本占 2 字节，块可能切在
                    # 半个样本之间）：不足一个样本的尾字节留到下一块拼，不能直接丢——
                    # 丢了会让相邻样本错位，听感上是持续的杂音；每次重试尝试重置残字节
                    leftover = b""
                    retry_delay_ms: Optional[int] = None
                    synth_stream = self.client.stream_synthesize(final_text)  # type: ignore[union-attr]
                    try:
                        async for pcm_bytes in synth_stream:
                            data = leftover + pcm_bytes
                            usable = len(data) - len(data) % _SAMPLE_BYTES
                            leftover = data[usable:]
                            chunk = np.frombuffer(data[:usable], dtype=DTYPE)
                            if chunk.size == 0:
                                continue
                            in_flight_task = asyncio.create_task(
                                asyncio.to_thread(self.audio_manager.write_chunk, chunk)
                            )
                            await in_flight_task
                            total_samples += int(chunk.size)
                        if leftover:
                            # 流结束仍剩半个样本：接口实现异常，丢弃并留痕（不值得为半个
                            # 样本失败整句，但静默丢字节会让音频问题无从追查）
                            self.logger.warning(f"流结束时残留半个样本（{len(leftover)} 字节，已丢弃）")
                        break
                    except OpenAITTSApiError as exc:
                        # 仅当尚未写出任何音频样本且远端返回 HTTP 503（繁忙）时执行有界阶梯退避重试：
                        # 硬打断旧流后远端单并发推理机通常需数秒释放旧句剩余同步推理槽位；已出声或非 503 立即放弃
                        if (
                            total_samples == 0
                            and exc.status_code == _HTTP_SERVICE_UNAVAILABLE
                            and attempt < len(self._busy_retry_delays_ms)
                        ):
                            retry_delay_ms = self._busy_retry_delays_ms[attempt]
                            attempt += 1
                            self.logger.warning(
                                f"TTS 推理机繁忙（HTTP 503），{retry_delay_ms}ms 后进行第 "
                                f"{attempt}/{len(self._busy_retry_delays_ms)} 次重试: utterance_id={utterance_id}"
                            )
                        else:
                            raise
                    finally:
                        # 无论正常结束、异常失败还是写声卡期被硬打断取消，均立即关闭异步生成器释放 HTTP 连接；
                        # 关闭自身异常只记 debug，绝不覆盖正在传播的 CancelledError 或业务异常
                        aclose = getattr(synth_stream, "aclose", None)
                        if callable(aclose):
                            try:
                                await aclose()
                            except Exception as close_exc:  # noqa: BLE001
                                self.logger.debug(
                                    f"关闭 TTS 合成流异常（已忽略，保留原始异常）: {close_exc}",
                                    exc=True,
                                )

                    if retry_delay_ms is not None:
                        # 先释放旧流再进入短退避；期间收到 CancelledError 直接向外传播至取消分支，绝不吞取消也不再重试
                        await self._sleep_fn(retry_delay_ms / 1000.0)

                # 收尾放工作线程等待回调流播完余量，期间被取消则由取消分支立即 abort 唤醒
                in_flight_task = asyncio.create_task(asyncio.to_thread(self.audio_manager.stop_stream))
                await in_flight_task

            self.logger.debug(f"TTS 播放完成: '{final_text[:30]}...'")
            self.render_count += 1

            await emit_utterance_finished(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                duration_ms=compute_duration_ms(total_samples, SAMPLE_RATE),
            )

        except asyncio.CancelledError:
            # 仅持锁方有权重置当前声卡会话：先同步 abort+丢弃缓冲再等工作线程退出，避免等锁任务误杀正在播的句子
            if lock_acquired:
                self.audio_manager.stop_stream(discard_pending=True)
                if in_flight_task is not None and not in_flight_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(in_flight_task), timeout=1.0)
                    except Exception as exc:  # noqa: BLE001 — 流已关闭的在途任务收尾异常属预期
                        self.logger.debug(f"取消路径在途写/收尾收敛等待失败（流已关闭，属预期）: {exc}", exc=True)
            self.logger.warning(f"TTS 渲染被取消（已截断播放）: utterance_id={utterance_id}")
            raise
        except OpenAITTSApiError as e:
            # 接口不可达 / 请求被拒是预期内的环境故障：简短告警即可，不打全量 traceback。
            # 错误路径仅在持锁时关流并丢弃预缓冲（失败半句不冲播）
            if lock_acquired:
                self.audio_manager.stop_stream(discard_pending=True)
            self.error_count += 1
            self.logger.warning(f"TTS 渲染失败: {e}（本条放弃，等待服务恢复）")
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
            # 先发 failed 让字幕出字再上抛；cause=e 给异常打"已报告"标记，队列兜底不再补发第二条
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
            extra={"model": self.model, "voice": self.voice},
        )


def create_openai_tts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    audio_sink: Optional[Any] = None,
) -> OpenAITTSProvider:
    """工厂：构造 ``OpenAITTSProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return OpenAITTSProvider(
        config=config,
        event_bus=event_bus,
        audio_sink=audio_sink,
    )


__all__ = ["OpenAITTSProvider", "create_openai_tts_provider"]
