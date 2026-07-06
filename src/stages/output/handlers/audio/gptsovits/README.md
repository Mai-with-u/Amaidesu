# GPT-SoVITS TTS Handler

基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 的文本转语音 Handler,通过 HTTP API 调用本地部署的 GPT-SoVITS 模型进行语音合成。

## 前置准备

### 1. 下载整合包

从 GPT-SoVITS 官方仓库下载整合包:

- GitHub: [https://github.com/RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)
- 国内镜像: 建议从官方文档提供的百度网盘或 HuggingFace 链接下载

> 根据你的显卡型号选择对应版本(NVIDIA 40 系选 `nvidia40`,50 系选 `nvidia50`)。

### 2. 准备参考音频

在整合包目录下创建 `referenceAudio/` 文件夹,放入一段 5-10 秒的纯人声 wav 录音,并记下对应的文字内容。

参考音频要求:
- 时长 5-10 秒,纯人声,无背景噪音
- wav 格式
- 内容清晰

### 3. 选择模型

整合包自带预训练模型,开箱即用,无需训练:

| 模型 | GPT | SoVITS |
|------|-----|--------|
| v2ProPlus(推荐) | `pretrained_models/s1v3.ckpt` | `pretrained_models/v2Pro/s2Gv2ProPlus.pth` |

> 如果音色相似度不够,可以用 WebUI 进行微调训练,产出的模型放到 `GPT_weights_v2Pro/` 和 `SoVITS_weights_v2Pro/` 目录。

### 4. 启动脚本

在整合包根目录创建启动脚本 `启动GPT-SoVITS服务.bat`:

```bat
@echo off
cd /d "%~dp0"
runtime\python.exe api.py -s "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth" -g "GPT_SoVITS/pretrained_models/s1v3.ckpt" -a "127.0.0.1" -p 9880 -d cuda
pause
```

> 启动后需要等待 20-40 秒加载模型,看到 `Uvicorn running on http://127.0.0.1:9880` 表示就绪。

## 配置

### `config/output.toml`

```toml
[handlers]
enabled = ["gptsovits"]
render_timeout_ms = 0

[handlers.gptsovits]
host = "127.0.0.1"
port = 9880
ref_audio_path = "referenceAudio/maimai_hello.wav"
prompt_text = "我拒绝,这种话喊出来太羞耻了"
text_language = "zh"
prompt_language = "zh"
```

| 字段 | 说明 |
|------|------|
| `ref_audio_path` | 参考音频路径,相对于 GPT-SoVITS 整合包根目录 |
| `prompt_text` | 参考音频对应的文字 |
| `text_language` / `prompt_language` | `zh`(中文) / `en`(英文) / `ja`(日文) |

### 启动顺序

1. 双击整合包根目录的 `启动GPT-SoVITS服务.bat`
2. 运行 Amaidesu: `uv run python main.py`

## 常见问题

**Q: 合成全是静音?**
检查启动 bat 是否用了 `api.py`(旧版,正常)而非 `api_v2.py`(新版管线有 bug)。在 bat 里不要传 `-dr -dt -dl` 参数,参考音频由 Amaidesu 每次请求传入。

**Q: 长文本合成卡住?**
检查 `output.toml` 中 `render_timeout_ms = 0`,默认 10 秒超时会中断长文本合成。

**Q: 颜文字/emoji 导致合成崩溃?**
Handler 已集成文本白名单清洗,会自动过滤 GPT-SoVITS 不支持的字符。

**Q: 合成速度慢?**
- 关闭 `super_sampling`(默认已关)
- 调低 `sample_steps`(建议 8)
- GPU 推断 14 秒左右为正常速度(取决于显卡型号和文本长度)

## 参数说明

以下参数会被 Amaidesu **自动带上**,不需要用户配置(直接填在 `output.toml` 即可):

| HTTP 参数 | 值 | 说明 |
|-----------|----|------|
| `text` | (意图文本) | 要合成的文字 |
| `text_language` | `zh` | 文本语言 |
| `refer_wav_path` | (来自配置) | 参考音频文件路径 |
| `prompt_text` | (来自配置) | 参考音频对应文本 |
| `prompt_language` | `zh` | 参考音频语言 |

## 参考资料

- GPT-SoVITS 官方仓库: [https://github.com/RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)
- Amaidesu 官方文档: 见 `docs/` 目录
