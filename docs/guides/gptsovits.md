# GPT-SoVITS 语音服务部署

Amaidesu 的 `gptsovits` TTS 引擎对接本机运行的 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 服务：Amaidesu 把待合成文本通过 HTTP 发给它，收回流式音频播出。本文说明如何下载、启动该服务，以及 Amaidesu 侧的对应配置。

> 阅读前提：项目已完成[快速开始](../getting-started.md)的安装步骤。TTS 基础设施的整体位置见 [ADR-007](../decisions/007-tts-infrastructure-pipeline.md)。

## 一、下载服务

1. 从 [GPT-SoVITS 官方仓库 Releases](https://github.com/RVC-Boss/GPT-SoVITS/releases) 下载 Windows 整合包（自带内嵌 Python 运行时与预训练底模，免装环境）。以 `GPT-SoVITS-v5-20261006` 版整合包为例，解压到任意目录（下文记作 `<GSV 根目录>`）。
2. 确认关键内容就位（v5 整合包默认自带）：
   - `api_v2.py`（HTTP 服务入口，本文使用 v2 接口而非旧版 `api.py`）
   - `GPT_SoVITS/pretrained_models/` 下的底模：v5 系 `gsv-v5-pretrained/s2Gv5dev.pth`、v2Pro 系 `v2Pro/s2Gv2ProPlus.pth`，两者共用 `s1v3.ckpt`

## 二、配置模型权重

`api_v2.py` 从 `GPT_SoVITS/configs/tts_infer.yaml` 读取权重，生效的是 `custom` 段。当前使用的 v5dev 底模配置：

```yaml
custom:
  device: cuda          # 推理设备
  is_half: true         # 半精度
  version: v5dev
  t2s_weights_path: GPT_SoVITS/pretrained_models/s1v3.ckpt
  vits_weights_path: GPT_SoVITS/pretrained_models/gsv-v5-pretrained/s2Gv5dev.pth
```

v5 整合包的 `custom` 段默认是 v2ProPlus + CUDA（零改动可用）；要使用 v5 底模（音色更贴合参考音频、48k 输出），把 `version` 改为 `v5dev` 并将 `vits_weights_path` 指向 `gsv-v5-pretrained/s2Gv5dev.pth`。两个取舍：

- **v5 系（v5dev/v5turbo）**：音色好、原生 48k，但**不支持 token 级流式**（外挂 vocoder 架构限制，服务端自动回退为按句返回）
- **v2ProPlus**：支持 token 级流式（streaming_mode=2/3），32k 输出
- 使用自己微调的权重时，把 `t2s_weights_path` / `vits_weights_path` 指向权重文件，并让 `version` 与权重版本一致

## 三、启动服务

在 `<GSV 根目录>` 下执行（`runtime\python.exe` 是整合包自带的 Python 解释器，无需安装任何环境；以下命令均假设当前目录为 `<GSV 根目录>`，可整段复制粘贴，把路径换成你的实际解压位置）：

```
cd /d D:\GPT-SoVITS-v5-20261006
runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880
```

- `-a` / `-p`：监听地址与端口（默认即 `127.0.0.1:9880`，与 Amaidesu 默认配置一致）
- 权重、设备均来自上面的 yaml，不需要命令行传模型路径
- 启动完成标志：日志出现 `Uvicorn running on http://127.0.0.1:9880`
- 服务需保持运行；每次冷启动后**第一次合成约需数秒预热**（Amaidesu 会在装配后自动预热，见下文启动顺序）

## 四、Amaidesu 侧配置

`config/infra.toml`：

```toml
[tts]
enabled = true
provider = "gptsovits"

[tts.gptsovits]
host = "127.0.0.1"
port = 9880
# 参考音频由 GPT-SoVITS 服务端读取，建议绝对路径（示例为参考音频放在整合包 referenceAudio 目录下的形态，换成你的实际位置）
ref_audio_path = 'D:\GPT-SoVITS-v5-20261006\referenceAudio\maimai_ref_generated.wav'
# 参考音频的文字转写，必须与音频内容逐字一致
prompt_text = "大家好，我是麦麦。很高兴认识大家，今天也请多多关照哦。"
# 声卡播放采样率，须与模型版本匹配（见下表）；v5dev 底模为 48000
sample_rate = 48000
# 流式档位：0=整段返回；1=按句流式；2/3=token级流式（仅 v2Pro 系底模支持，v5 系会被服务端自动回退为按句）
streaming_mode = 1
```

### 参考音频的要求

参考音频是声音克隆的"声音样本"，直接决定合成音色：

- 时长 **3~10 秒**（服务端强校验，超限直接报错），内容可以包含多个短句
- 录制干净、无背景音，情绪风格即合成风格
- `prompt_text` 必须是它的逐字转写——转写不准会导致发音风格漂移
- 路径由 GPT-SoVITS 服务端读取（不是 Amaidesu 解释），**建议写绝对路径**避免歧义

### 采样率与模型版本匹配

| 模型版本（`version`） | 输出采样率 | Amaidesu `sample_rate` |
|---|---|---|
| v1 / v2 / v2Pro / v2ProPlus | 32000 Hz | 32000 |
| v3 | 24000 Hz（可选超采样至 48000） | 24000 |
| v4 / v5 系 | 48000 Hz | 48000 |

不匹配时声音会变调变速（如按 32000 播 48000 流会低沉变慢）。

### 语气与流式参数

`top_k` / `top_p` / `temperature` / `speed_factor` / `repetition_penalty` 在 api_v2 下真实生效，可按直播风格调整。

流式能力与底模绑定：**v5 系底模（v5dev/v5turbo）因外挂 vocoder 架构不支持 token 级流式**，`streaming_mode` 传 2/3 会被服务端静默回退为按句返回（v2ProPlus 底模则支持 2/3 档，实测首包约 1.2s/0.7s vs 按句 5.8s）。v5dev 单句合成在正常 GPU 上不到 1 秒，按句返回的实际延迟已经很小。

## 五、启动顺序与验证

1. 先启动 GPT-SoVITS 服务（等日志出现 `Uvicorn running`）
2. 再启动 Amaidesu——装配完成后自动预热（调用参考音频预处理接口 + 合成一句预热文本，约 5~10 秒，日志出现"GPT-SoVITS 预热完成"）
3. 开场前预留十几秒让预热跑完

> 预热未完成就触发第一句话（Amaidesu 启动后约 10 秒内），预热请求与真实请求会在服务端争抢 GPU，可能导致首句流式卡死超时；第二句起恢复正常。按上述顺序启动即可规避。

验证方式二选一：

- WebUI 调试页：TTS 状态徽标显示 `GPTSoVITSProvider · api_v2` 即装配成功，输入文本点试说
- 命令行直连（服务自检）：

```
curl -G "http://127.0.0.1:9880/tts" --data-urlencode "text=测试" --data-urlencode "text_lang=zh" --data-urlencode "ref_audio_path=referenceAudio\maimai_ref_generated.wav" --data-urlencode "prompt_lang=zh" -o test.wav
```

`ref_audio_path` 换成你的实际参考音频路径；相对路径由服务端按 `<GSV 根目录>` 解释，与 curl 的执行目录无关。生成的 `test.wav` 能正常出声即服务就绪。

## 常见问题

- **报"GPT-SoVITS 服务不可达"**：服务未启动或端口不一致；Amaidesu 侧告警为简短一行，详细原因看 GSV 窗口日志。
- **报"ref_audio_path is required" / 400**：Amaidesu 配置里参考音频字段为空，按第四节补填。
- **报"参考音频在3~10秒范围外"**：更换参考音频。
- **报"参考音频未配置"**（Amaidesu 侧）：`[tts.gptsovits]` 的 `ref_audio_path` 与 `prompt_text` 必须同时非空。
- **声音变调**：`sample_rate` 与模型版本不匹配，见上表。
