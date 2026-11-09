# 语音合成（TTS）

一句话形状：**TTS 是基础设施不是工具——装配期按配置单选一个引擎实例直接注入主播 Agent，发言队列串行播出；ToolRegistry 里零 TTS 条目。**决策依据见 [TTS 作为配置驱动基础设施](../decisions/007-tts-infrastructure-pipeline.md)。

## 装配与接口

- 装配入口 `build_tts_infrastructure`（`src/modules/tts/assembly.py`）：读 `infra.toml` 的 `[tts]` 段，按 `provider` 单选构造引擎实例注入 StreamerAgent。未知的引擎名记错误并回退 `edge_tts`；构造失败降级为无 TTS（发言照常生成、只不上声，字幕择流随之切换）。
- 引擎契约 `TTSProvider`（`protocol.py`）：唯一业务入口是 `handle_speech(text, utterance_id)`——合成并播放，失败发布 `tts.utterance.failed`；首次调用前引擎自治完成 `setup()`（幂等），装配层不显式调它。
- 每引擎一个包（`backends/edge|gptsovits|voicebox|omni|minimax|openai/`）。新增引擎 = 加包 + 工厂名单登记 + 配置 schema 引用，框架零改动。

## 六引擎

| 引擎 | 形态 | 连接目标 | 流式 | 预热 |
|------|------|---------|------|------|
| edge_tts | 微软云端免费 | 外网 | 否（整段合成后播放） | 无 |
| gptsovits | 本地 GPT-SoVITS api_v2 服务 | `host:port`（默认 127.0.0.1:9880） | 是（streaming_mode 0–3 档） | 有（合成一句"预热。"触发首次推理，日志「GPT-SoVITS 预热完成，首句合成已是热态」） |
| voicebox | 本地 HTTP 服务，音色 = `profile_id` 克隆 | 默认端口 17493 | SSE 轮询 | 无 |
| omni_tts | 全模态大模型（经 LLM profile 调音频端点） | model.toml 档位指向的端点 | 否（全量收齐后播放） | 无 |
| minimax_tts | MiniMax 云端合成（T2A 接口，密钥段内自持） | `base_url`（默认国内站，国际站账号改官方域名同路径） | 是（SSE 分块 hex 音频，`bytes.fromhex` 解码即裸 PCM） | 无（云服务无冷启动惩罚） |
| openai_tts | OpenAI 官方语音合成（`/v1/audio/speech`，密钥段内自持） | `base_url`（默认官方端点，可整体替换为兼容网关） | 是（响应体即裸 PCM 24kHz 字节流，`np.frombuffer` 直接得样本） | 无（云服务无冷启动惩罚） |

> omni_tts 尚未经过实战调通验证；直播实战常用的是 edge_tts 与 gptsovits。minimax_tts 已过真机冒烟（密钥真实合成：流式 5 块、首块延迟约 0.5s），直播实战待用。openai_tts 与 omni_tts 同属"云端语音模型"但走不同端点：omni_tts 经 LLM 的 chat 模态音频输出（复用 model.toml 的 profile 与密钥），openai_tts 走专用 speech 端点，模型 / 音色 / 风格提示都是该端点自己的词表。openai_tts 的验证到"链路级"为止：用本地 aiohttp 服务冒充 speech 端点，跑通了真实 HTTP 请求、chunked 流式读取、样本拼接、事件派发全链路（`tests/modules/tts/test_openai_client_http.py`），但仍未拿真实密钥对 api.openai.com 冒烟，音色听感与真实首块延迟未验证。
>
> 该端点自身有生命周期风险：OpenAI 已把 tts-1 / tts-1-hd / gpt-4o-mini-tts 系列标记为弃用并于 2027-01-06 关停，官方引导新集成改走 Realtime API（不支持 `/v1/audio/speech`）。关停前本引擎照常可用，届时需要换端点或换引擎。

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

播放器把合成音频复制递入 AudioSink（`src/modules/audio/sink.py`），虚拟形象域的共享口型分析器消费同一份流——协议中性，TTS 域不知道 avatar 的存在（见 [口型帧级通道](../decisions/024-lipsync-frame-channel.md)）。

## 配置面

`infra.toml` 的 `[tts]`：`enabled` 总开关、`provider` 单选、队列与超时参数、`wait_playback`，以及六个引擎同名子段（gptsovits 的合成调参、omni 的 `llm_profile` 与必填 `voice`、minimax 的 `api_key`/`group_id`/`voice_id`、openai 的 `api_key`/`model`/`voice`/`instructions` 等）。键全集以 `core_schemas.py` 的 TTS schema 为准。

## 相关决策

- [TTS 作为配置驱动基础设施](../decisions/007-tts-infrastructure-pipeline.md)
- [口型帧级通道（AudioSink 分接）](../decisions/024-lipsync-frame-channel.md)
