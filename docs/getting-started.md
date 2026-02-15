# 快速开始

Amaidesu 是一个 VTuber 直播辅助工具，支持弹幕互动、语音合成、虚拟形象控制等功能。本指南将帮助你快速上手。

## 1. 环境要求

- Python 3.12+
- uv 包管理器

## 2. 安装步骤

### 2.1 安装 uv（Windows）

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2.2 克隆仓库

```bash
git clone https://github.com/ChangingSelf/Amaidesu.git
cd Amaidesu
```

### 2.3 同步依赖

```bash
uv sync
```

### 2.4 首次运行

```bash
uv run python main.py
```

首次运行会自动从模板生成 `config.toml` 文件。

### 2.5 编辑配置文件

打开生成的 `config.toml` 文件，填写必要的配置项：

#### LLM 配置（必需）

```toml
[llm]
api_key = "your-api-key"        # 你的 OpenAI API Key
base_url = "https://api.openai.com/v1"  # 或其他兼容 API 地址
model = "gpt-4"                 # 使用的模型
```

#### 输入 Provider 配置

```toml
[providers.input]
enabled_inputs = [
    "console_input",            # 控制台输入（测试用）
    # "bili_danmaku",          # B站弹幕
    # "stt",                   # 语音输入
]
```

#### 输出 Provider 配置

```toml
[providers.output]
enabled_outputs = [
    "subtitle",                 # 字幕输出
    "vts",                     # VTS 控制
    # "tts",                   # Edge TTS 语音
    # "avatar",                # 虚拟形象
]
```

#### 决策 Provider 配置

```toml
[providers.decision]
active_provider = "maicore"     # 使用 MaiCore 决策
```

### 2.6 再次运行

配置完成后，重新启动程序：

```bash
uv run python main.py
```

## 3. 配置说明

### 3.1 主要配置段

| 配置段 | 说明 |
|--------|------|
| `[llm]` | 标准 LLM 配置（用于高质量任务） |
| `[llm_fast]` | 快速 LLM 配置（用于低延迟任务） |
| `[vlm]` | 视觉语言模型配置（图像理解） |
| `[providers.input]` | 输入 Provider 列表 |
| `[providers.output]` | 输出 Provider 列表 |
| `[providers.decision]` | 决策 Provider 选择 |
| `[pipelines.*]` | 消息处理管道配置 |
| `[logging]` | 日志配置 |

### 3.2 Provider 类型

| 类型 | 说明 | 示例 |
|------|------|------|
| InputProvider | 数据采集 | 弹幕、语音、控制台 |
| DecisionProvider | 决策生成 | MaiCore、LLM |
| OutputProvider | 渲染输出 | TTS、字幕、虚拟形象 |

### 3.3 可用 Provider 列表

**输入 Provider：**
- `console_input` - 控制台输入（开发测试）
- `bili_danmaku` - B站弹幕
- `bili_danmaku_official` - B站官方弹幕
- `stt` - 语音转文字

**决策 Provider：**
- `maicore` - MaiCore 决策服务
- `llm` - 直接使用 LLM 生成回复
- `maicraft` - 弹幕游戏决策

**输出 Provider：**
- `subtitle` - 字幕显示
- `vts` - VTS 控制
- `tts` - Edge TTS 语音
- `omni_tts` - GPT-SoVITS 语音
- `avatar` - 虚拟形象
- `obs_control` - OBS 控制

## 4. 常用命令

### 4.1 运行应用

```bash
# 正常运行
uv run python main.py

# 调试模式（显示详细日志）
uv run python main.py --debug

# 过滤日志（只显示指定模块）
uv run python main.py --filter EdgeTTSProvider SubtitleProvider
```

### 4.2 代码质量

```bash
# 运行测试
uv run pytest tests/

# 代码检查
uv run ruff check .

# 代码格式化
uv run ruff format .

# 自动修复
uv run ruff check --fix .
```

### 4.3 包管理

```bash
# 添加依赖
uv add package-name

# 移除依赖
uv remove package-name
```

## 5. 快速验证

启动后，检查日志输出确保以下组件正常初始化：

```
[Info] Provider注册完成: Input=X, Decision=X, Output=X
[Info] 配置验证通过
[Info] 初始化输入Provider管理器（Input Domain）...
[Info] 初始化决策域组件（Decision Domain）...
[Info] 初始化输出Provider管理器...
[Info] 应用程序正在运行。
```

如果看到错误信息，请检查：
1. API 密钥是否正确配置
2. 网络连接是否正常
3. 配置文件格式是否正确

## 6. 下一步

- 了解架构设计：[3域架构](architecture/overview.md)
- 学习开发规范：[开发规范](development-guide.md)
- 查看 Provider 开发指南：[Provider 开发](development/provider-guide.md)
