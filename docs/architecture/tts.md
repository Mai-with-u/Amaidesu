# 语音合成（TTS）

一句话形状：**TTS 是基础设施不是工具——装配期按配置单选一个引擎实例直接注入主播 Agent，发言队列串行播出；ToolRegistry 里零 TTS 条目。**决策依据见 [ADR-007](../decisions/007-tts-infrastructure-pipeline.md)。

## 装配与接口

- 装配入口 `build_tts_infrastructure`（`src/modules/tts/assembly.py`）：读 `infra.toml` 的 `[tts]` 段，按 `provider` 单选构造引擎实例注入 StreamerAgent。未知的引擎名记错误并回退 `edge_tts`；构造失败降级为无 TTS（发言照常生成、只不上声，字幕择流随之切换）。
- 引擎契约 `TTSProvider`（`protocol.py`）：唯一业务入口是 `handle_speech(text, utterance_id)`——合成并播放，失败发布 `tts.utterance.failed`；首次调用前引擎自治完成 `setup()`（幂等），装配层不显式调它。
- 每引擎一个包（`backends/edge|gptsovits|voicebox|omni/`）。新增引擎 = 加包 + 工厂名单登记 + 配置 schema 引用，框架零改动。

## 四引擎

| 引擎 | 形态 | 连接目标 | 流式 | 预热 |
|------|------|---------|------|------|
| edge_tts | 微软云端免费 | 外网 | 否（整段合成后播放） | 无 |
| gptsovits | 本地 GPT-SoVITS api_v2 服务 | `host:port`（默认 127.0.0.1:9880） | 是（streaming_mode 0–3 档） | 有（合成一句"预热。"触发首次推理，日志「GPT-SoVITS 预热完成，首句合成已是热态」） |
| voicebox | 本地 HTTP 服务，音色 = `profile_id` 克隆 | 默认端口 17493 | SSE 轮询 | 无 |
| omni_tts | 全模态大模型（经 LLM profile 调音频端点） | model.toml 档位指向的端点 | 否（全量收齐后播放） | 无 |

> omni_tts 尚未经过实战调通验证；直播实战常用的是 edge_tts 与 gptsovits。

GSV 的服务部署、参考音频与多音字维护见 [GPT-SoVITS 语音服务部署](../guides/gptsovits.md)。

## 播放链

```
Replyer 产出 speech
  → SpeechDispatcher 生成 utterance_id（utt_<毫秒时间戳>_<序号>）
  → UtteranceQueue（FIFO 单 worker 串行；入队即返回，不阻塞决策循环）
  → 引擎合成 + 声卡播放
```

- 队列满丢最旧；单句有渲染超时；中途取消时引擎截断声卡流防止残留声音。
- `wait_playback` 开启时"发言等播放"：等当前句播完再推进思考，防止台词积压；带超时上限，超时放行不丢话。
- 生命周期三事件 `tts.utterance.started / finished / failed` 由引擎自己发布（`utterance_id` 全链关联），是**终点广播**——消费者不得据此触发新决策（防环）。

## 口型分接

播放器把合成音频复制递入 AudioSink（`src/modules/audio/sink.py`），虚拟形象域的共享口型分析器消费同一份流——协议中性，TTS 域不知道 avatar 的存在（ADR-024）。

## 配置面

`infra.toml` 的 `[tts]`：`enabled` 总开关、`provider` 单选、队列与超时参数、`wait_playback`，以及四个引擎同名子段（gptsovits 的合成调参、omni 的 `llm_profile` 与必填 `voice` 等）。键全集以 `core_schemas.py` 的 TTS schema 为准。

## 相关决策

- [ADR-007](../decisions/007-tts-infrastructure-pipeline.md)　TTS 作为配置驱动基础设施
- [ADR-024](../decisions/024-lipsync-frame-channel.md)　口型帧级通道（AudioSink 分接）
