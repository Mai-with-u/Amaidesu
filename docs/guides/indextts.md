# IndexTTS 语音服务部署

Amaidesu 的 `index_tts` TTS 引擎对接本机运行的 [IndexTTS](https://github.com/index-tts/index-tts) 服务：Amaidesu 把待合成文本通过 HTTP 发给它，收回流式音频播出。IndexTTS 官方只提供 Python API 与给人用的 WebUI，没有 HTTP 服务，本仓库的 `scripts/indextts_serve.py` 把官方推理包包装成 Amaidesu 对接的常驻 HTTP 服务。本文说明如何部署 IndexTTS、启动该服务，以及 Amaidesu 侧的对应配置。

> 阅读前提：项目已完成[快速开始](../getting-started.md)的安装步骤。TTS 基础设施的整体位置见 [TTS 基础设施管线](../decisions/007-tts-infrastructure-pipeline.md)。
>
> 定位说明（先读再决定装不装）：IndexTTS-2.5 的强项是**细粒度情感控制**（8 维情感向量 / 情感参考音频）与零样本声音克隆；代价是推理比 GPT-SoVITS 重得多——消费级 8GB 显卡上吞吐跟不上实时，**直播生产链暂不建议切换**，适合做音色/情感评估与离线合成。另注意它使用 bilibili 模型许可（自用与中小规模商用免费，许可全文见其仓库 LICENSE）。

## 一、下载 IndexTTS 并安装依赖

1. 克隆官方仓库到任意目录（下文记作 `<IndexTTS 根目录>`），需要 Python 3.10–3.12 与 CUDA 12.8 及以上的 NVIDIA 驱动：

   ```
   git clone https://github.com/index-tts/index-tts.git
   cd index-tts
   uv sync --extra webui
   ```

   - `webui` extra 自带 fastapi/uvicorn——`scripts/indextts_serve.py` 依赖它们，**必须带**这个 extra
   - Windows 上 DeepSpeed 很难装，跳过它（去掉 `--all-extras`，只带 `--extra webui`）
2. 国内网络下载 torch（约 3.2GB）直连官方源可能极慢或卡死。兜底办法：手动下载与锁文件一致的轮子（`torch==2.8.0+cu128` / `torchaudio==2.8.0+cu128`，Python 3.11 对应 cp311，可从国内 pytorch 镜像目录取），先 `uv pip install` 两个轮子，再跑 `uv sync --extra webui --no-install-package torch --no-install-package torchaudio` 补齐其余依赖。
3. **陷阱**：在这个目录里不要用 `uv run` 启动任何东西——它会做隐性依赖校验，把手装的 torch 识别为"来源不符"而触发重新下载。之后一律直接用 `.venv\Scripts\python.exe`。

## 二、下载模型权重

主权重约 5.11GB，二选一：

```
modelscope download --model IndexTeam/IndexTTS-2.5 --local_dir <IndexTTS 根目录>\checkpoints
hf download IndexTeam/IndexTTS-2.5 --local-dir <IndexTTS 根目录>\checkpoints
```

首次加载时还会自动下载 4 个辅助模型到 `checkpoints\hf_cache\`（国内直连 HuggingFace 不稳，这一步最容易失败）：

| 模型 | 大小 | 用途 |
|---|---|---|
| w2v-bert-2.0 | ≈2.3GB | 说话人条件提取 |
| bigvgan_v2_22khz_80band_256x | ≈450MB | 声码器 |
| campplus_cn_common | 27MB | 音色嵌入 |
| semantic_codec | ≈170MB | 语义编解码 |

自动下载失败的兜底：

- **w2v-bert-2.0**（最大最容易断）：从 ModelScope 镜像手动补：

  ```
  modelscope download --model AI-ModelScope/w2v-bert-2.0 --include "model.safetensors" --local_dir <IndexTTS 根目录>\checkpoints\hf_cache\w2v-bert-2.0
  ```

- **其他小文件**：删掉 `hf_cache` 下对应的残缺目录（里面只剩 `.incomplete` 残片）后重试加载，网络可用时自动补齐。
- 报 `Error no file named pytorch_model.bin, model.safetensors ...` 即该目录缺权重文件，按上两条处理。

## 三、启动服务

用 IndexTTS 环境的解释器运行本仓库的服务脚本：

```
 Amaidesu\.venv\Scripts\python.exe D:\path\to\Amaidesu\scripts\indextts_serve.py ^
     --index-tts-dir <IndexTTS 根目录> ^
     --ref <参考音频绝对路径> ^
     --port 9888
```

（实际运行请用 `<IndexTTS 根目录>\.venv\Scripts\python.exe`——脚本 import 的 indextts 与 fastapi 只装在那个环境里；上面的 `Amaidesu\.venv` 仅示意脚本自身的位置。）

- `--ref`：**音色由它决定**，要求同 GSV——3~10 秒干净人声，服务启动后固定，换音色改此参数重启服务（Amaidesu 侧不用动）
- `--port`：默认 9888，与 Amaidesu 默认配置一致
- 启动完成标志：日志出现 `Uvicorn running on http://127.0.0.1:9888`
- 每次冷启动后第一笔请求含说话人条件提取（Amaidesu 装配后会自动预热消化掉）

## 四、Amaidesu 侧配置

`config/infra.toml`：

```toml
[tts]
enabled = true
provider = "index_tts"

[tts.index_tts]
host = "127.0.0.1"
port = 9888
# 长句切段的 token 上限：调小可提前首包（120=整段无流式收益），代价是总合成时长变长且段间插入 200ms 静音
seg_tokens = 25
# 8 维情感向量 [喜,怒,哀,惧,厌恶,低落,惊喜,平静]，各 0.0~1.0；全 0 = 不使用（情感底色即参考音频的情绪）
emo_vector = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
# 服务端输出采样率（自建服务固定 22050），改错会变调
sample_rate = 22050
```

字段说明：

- **seg_tokens**：IndexTTS 的流式按文本切段，每段合成完才推送。默认参数下整句只有一段，首包 = 整句合成完；调小可显著提前首包（实测见下表），代价是总时长变长 + 段间静音
- **emo_vector**：静态兜底向量——主播每句的**情绪标签已动态接线**（replyer 产出情绪 → 发言队列 → 引擎按下方"情绪映射"表转向量随句注入），本字段只是"该句情绪无映射可用"时的静态兜底；全 0 = 无兜底，无映射的句子用参考音频情绪锚
- 音色不在 Amaidesu 配置里：由服务端 `--ref` 持有

### 情绪映射（引擎内置，调优改表）

主播情绪词表（17 个）只有 9 个与 IndexTTS 的 8 维直接对应，按**官方单维用法**映射（官方文档示例即单维给值如纯悲伤 0.8）：开心/兴奋/喜欢→喜、生气→怒、难过/哭→哀、害怕→惧、讨厌→厌恶、惊讶→惊喜（各 0.7~0.8）。其余 8 个（害羞/得意/无语/疲惫/认真/困惑/尴尬/平静）无直接对应维度，**不携带向量**、回退参考音频情绪锚——实测自由发明的多维组合会产生"兴奋混出哭腔"这类伪影，宁缺毋滥。

注入强度由两层控制，都是音色权衡：

- **总量上限 0.6**：IndexTTS 的混合公式为 `向量合成情绪 + (1-向量总和)×参考音频情绪锚`——注入占比即向量总和。0.8 档（官方 WebUI 上限）实测音色漂移明显，0.6 = 六成注入四成锚，音色更稳；嫌情绪淡可改回 0.8（引擎侧 `_EMO_SUM_CAP` 一行）
- **情绪强度缩放**：replyer 每句带强度 0~1，缩放后再归一化；强度小则偏向锚点

校准途径：IndexTTS 自带 WebUI（`uv run webui.py`）拖 8 维滑杆找好听的组合，抄进引擎映射表。

### 情绪源实验：QwenEmotion（未采用）

服务脚本带 `--qwen-emo-cpu` 开关：把官方的 QwenEmotion（0.6B 文本情感分类模型）加载到 CPU（不占显存，每句现算 +9-12s），请求带 `emotion_auto: true` 时由台词现算情绪向量，响应头 `X-Detected-Emotion` 回传检出字典。

A/B 实测结论（三句情绪鲜明台词对比锚点 vs 现算）：**QwenEmotion 推断准确（开心 0.9/生气 0.85/悲伤 0.9），但不采用**——高唤醒情绪（开心/生气）注入后过度表演、不如锚点自然，低唤醒悲伤族虽有增益但不足以支撑换源的代价（每句 +9-12s）。该开关留作对照工具，生产链使用主播情绪标签映射。

## 五、启动顺序与验证

1. 先启动 IndexTTS 服务（等日志出现 `Uvicorn running`）
2. 再启动 Amaidesu——装配完成后自动预热（合成一句"预热。"，消化说话人条件冷提取）
3. 验证方式二选一：
   - WebUI 调试页：TTS 状态徽标显示 `IndexTTSProvider` 即装配成功；"试说一句（扬声器）"走完整生产链路（扬声器 + 口型 + 字幕），"生成试听音频"在页面内播放与下载
   - 命令行直连（cmd）：

   ```
   curl -X POST "http://127.0.0.1:9888/tts" -H "Content-Type: application/json" -d "{\"text\":\"测试一句话\"}" -o test.wav
   ```

   生成的 `test.wav` 能正常出声即服务就绪。

## 六、显存与速度预期（实测数据）

以 8GB 显存的笔记本级显卡为准（更低显存未验证，更高按算力比例类推）：

| 项目 | 实测值 |
|---|---|
| 模型常驻显存 | 约 5.6GB（加载后 7.6GB/8GB 总量，**余量极小**） |
| 桌面显存基线要求 | **<2.5GB**（浏览器/游戏/直播软件都会吃掉它） |
| 模型加载 | 约 20-30s |
| 单句合成（冷缓存） | RTF≈3（3 秒音频合成约 9 秒） |
| 单句合成（暖缓存） | 整段 RTF≈1.35 |
| 首包（49 字句，`seg_tokens` 25/15） | 4.8s / 2.8s（默认 120 = 11.2s，无流式收益） |

两条硬约束：

- **显存溢出是数量级事故**：显存不够时 NVIDIA 驱动默认把数据静默转进内存跑（系统内存回退），速度劣化数百倍——征兆是服务日志里扩散步速从每秒几十步跌到每秒一两步。出现即关掉占显存的程序重启服务。可在 NVIDIA 控制面板把"CUDA - 系统内存回退策略"改为"首选无系统内存回退"，让溢出直接报错而不是龟速跑完
- **吞吐跟不上实时**：整段 RTF≈1.35 意味着连续长句会越积越慢，直播生产链暂不建议切换；评估与离线合成不受影响

## 常见问题

- **报"连接 IndexTTS 服务失败…拒绝网络连接"**：服务没起来或还在加载模型（加载期间端口未开），等服务就绪再试；Amaidesu 侧告警为简短一行，详细原因看服务窗口日志。
- **报 `ValueError: buffer size must be a multiple of element size`**：旧版引擎的缺陷（网络分块切在半个样本上），`3f0d425c` 已修；出现即说明 Amaidesu 侧代码未更新到该笔之后。
- **合成极慢（分钟级/句）**：显存溢出，见第六节。
- **报 `Timeout on reading data from socket`**：服务端单段合成超过客户端读超时（300s）——同样是显存溢出的连带症状。
- **`uv run` 后开始重新下载 torch**：隐性依赖校验把手装的 torch 识别为来源不符，`Ctrl+C` 后按第一节第 3 条的说明改用 `.venv\Scripts\python.exe`。
- **声音变调**：`sample_rate` 与服务端输出不一致（自建服务固定 22050）。
- **想换音色**：改服务启动参数 `--ref` 重启服务即可，Amaidesu 侧零改动。
