# Voicebox TTS Handler

基于 [Voicebox](https://github.com/jamiepine/voicebox) 的文本转语音 Handler，通过 HTTP API 调用本地运行的 Voicebox 桌面应用进行语音合成。

Voicebox 是一个开源的本地 AI 语音工作室，支持 7 种 TTS 引擎、语音克隆、情感标签等特性。

## 前置准备

### 1. 下载安装 Voicebox

从 GitHub Releases 下载 Windows 版 .msi 安装包：

- GitHub: [https://github.com/jamiepine/voicebox/releases](https://github.com/jamiepine/voicebox/releases)

安装后打开 Voicebox 桌面应用，后台 API 服务器会自动运行在 `http://127.0.0.1:17493`。

### 2. 下载模型

Voicebox 首次使用时会自动下载模型。在 GUI 中选择引擎（推荐 Qwen TTS 或 Kokoro），按提示下载即可。

> 你的 RTX 4060 8GB 显存可以流畅运行 Qwen TTS 0.6B（~1.7GB）及 Kokoro（82M CPU 可跑）等轻量引擎。

### 3. 克隆音色（可选）

如果你需要使用特定的音色：
1. 在 Voicebox GUI 中创建 Voice Profile
2. 上传一段 10-30 秒的参考音频
3. 记下该 Profile 的 UUID（通过 `GET http://127.0.0.1:17493/profiles` 查看）

## 配置

### `config/output.toml`

```toml
[handlers]
enabled = ["voicebox"]

[handlers.voicebox]
# API 主机地址
host = "127.0.0.1"
# API 端口
port = 17493
# 语音 profile UUID（必填，在 Voicebox 中克隆的音色 ID）
profile_id = "030ac560-ea28-4e73-b8cb-d8ac179550a5"
# 语言代码
language = "zh"
# 输出采样率
sample_rate = 24000
# 生成超时（毫秒，首次需加载模型可能较慢）
generation_timeout_ms = 120000
```

| 字段 | 说明 |
|------|------|
| `profile_id` | **必填**。Voicebox Profile 的 UUID（不是名称），通过 `GET /profiles` 获取 |
| `language` | 语言代码（`zh` / `en` / `ja` 等） |
| `generation_timeout_ms` | 生成超时，首次需加载模型建议 120s，后续可改小 |

### 启动顺序

1. 打开 **Voicebox 桌面应用**（确保后台 API 运行）
2. 运行 Amaidesu: `uv run python main.py`

## 工作流程

Handler 执行一次 TTS 的完整流程：

```
Amaidesu → POST /generate → 返回 generation_id
                            ↓
              GET /generate/{id}/status （SSE 事件流）
                  ├─ data: {"status": "loading_model"}
                  ├─ data: {"status": "generating"}
                  └─ data: {"status": "completed"}
                            ↓
              GET /audio/{id} → 下载 WAV 音频
                            ↓
              AudioStreamChannel → 口型同步 + 本地播放
```

## 常见问题

**Q: 如何查看我的 Profile UUID？**
在浏览器打开 `http://127.0.0.1:17493/profiles`，或运行：
```python
import requests; print(requests.get("http://127.0.0.1:17493/profiles").json())
```

**Q: 第一次生成很慢？**
首次需要加载模型到 GPU（约 15-30 秒），之后模型常驻显存，生成仅需数秒。

**Q: 显存不够怎么办？**
Voicebox 支持多引擎。可以在 GUI 中切换到 Kokoro（82M，CPU 推理）或 Qwen TTS 0.6B（~1.7GB）。已下载的模型在 `GET /models/status` 中查看。

**Q: Streamed 模式的 /generate/stream 能用吗？**
当前 Handler 使用异步 POST /generate → SSE 轮询 → GET /audio 流程。`/generate/stream` 提供原始 PCM 流式输出，后续可考虑支持。

## 参考资料

- Voicebox 官方仓库: [https://github.com/jamiepine/voicebox](https://github.com/jamiepine/voicebox)
- Voicebox 官方文档: [https://voicebox.sh](https://voicebox.sh)
- Amaidesu 官方文档: 见 `docs/` 目录
