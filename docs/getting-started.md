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

#### 输入 Collector 配置

```toml
[collectors]
enabled = ["console_input"]     # 控制台输入（测试用）
# enabled = ["bili_danmaku"]   # B站弹幕
# enabled = ["stt"]            # 语音输入
```

#### 输出 Handler 配置

```toml
[handlers]
enabled = ["subtitle", "vts"]  # 字幕输出、VTS 控制
# enabled = ["edge_tts"]       # Edge TTS 语音
# enabled = ["avatar"]          # 虚拟形象
```

#### 决策 Decider 配置

```toml
[deciders]
active = "maibot"               # 使用 MaiBot 决策
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
| `[collectors]` | 输入 Collector 列表 |
| `[handlers]` | 输出 Handler 列表 |
| `[deciders]` | 决策 Decider 选择 |
| `[pipelines.*]` | 消息处理管道配置 |
| `[logging]` | 日志配置 |

### 3.2 阶段参与者类型

| 类型 | 说明 | 示例 |
|------|------|------|
| InputCollector | 数据采集 | 弹幕、语音、控制台 |
| Decider | 决策生成 | MaiBot、LLM |
| OutputHandler | 渲染输出 | TTS、字幕、虚拟形象 |

### 3.3 可用阶段参与者列表

**输入 Collector：**
- `console_input` - 控制台输入（开发测试）
- `bili_danmaku` - B站弹幕
- `bili_danmaku_official` - B站官方弹幕
- `stt` - 语音转文字

**决策 Decider：**
- `maibot` - MaiBot 决策服务
- `llm` - 直接使用 LLM 生成回复
- `command` - 通用命令意图路由（解析 /命令 形式的消息）

**输出 Handler：**
- `subtitle` - 字幕显示
- `vts` - VTS 控制
- `edge_tts` - Edge TTS 语音
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
uv run python main.py --filter EdgeTTSHandler SubtitleHandler
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

### 4.4 Web Dashboard

项目提供 Web 管理界面，可以实时查看会话历史、调试消息、监控系统状态。**有两种独立运行模式**：

| 端口 | 模式 | 前置条件 | 适合场景 |
|------|------|---------|---------|
| **60214** | 生产模式 | 需先 `npm run build` 生成 `dashboard/dist/` | 最终部署 / 单进程启动 |
| **60315** | 开发模式 | 需同时运行后端（60214） | 前端开发 / HMR 热更新 |

#### 方式一：生产模式（单进程）

```bash
cd dashboard && npm run build   # 首次或前端改动后执行一次
uv run python main.py           # → 浏览器访问 http://127.0.0.1:60214
```

**注意**：未执行 `npm run build` 时，60214 仅提供 API（GET / 返回 JSON 提示），不会显示 WebUI。

#### 方式二：开发模式（双进程）

```bash
# 终端 1：启动后端
uv run python main.py           # → 后端运行在 http://127.0.0.1:60214

# 终端 2：启动 Vite 开发服务器
cd dashboard
npm install                     # 首次安装依赖
npm run dev                     # → Vite 启动在 http://localhost:60315
# 浏览器访问 http://localhost:60315（不是 5173、不是 60214）
```

**开发模式说明**：
- Vite 自动代理 `/api` 和 `/ws` 请求到后端 60214
- 修改 `dashboard/src/**` 下文件后浏览器自动热更新（无需刷新）
- 修改后端 Python (`src/**/*.py`) 或配置文件 (`config/*.toml`) 需要重启主程序
- 只跑 `npm run dev` 而不跑主程序，WebSocket/API 会无法连接

**配置选项**（在 `config.toml` 中）：

```toml
[dashboard]
enabled = true                                      # 是否启用 Dashboard
host = "127.0.0.1"                                  # 监听地址
port = 60214                                        # 监听端口
cors_origins = ["http://localhost:60315", "http://127.0.0.1:60315"]  # 允许的跨域来源
max_history_messages = 1000                         # 最大历史消息数
websocket_heartbeat = 30                            # WebSocket 心跳间隔（秒）
```

**功能特性**：
- 实时会话历史查看
- 消息调试和重放
- 阶段参与者状态监控
- 配置在线修改
- LLM 对话调试

## 5. 快速验证

启动后，检查日志输出确保以下组件正常初始化：

```
[Info] 阶段参与者注册完成: Collector=X, Decider=X, Handler=X
[Info] 配置验证通过
[Info] 初始化输入Collector（Input 阶段）...
[Info] 初始化决策Decider（Decision 阶段）...
[Info] 初始化输出Handler管理器...
[Info] 应用程序正在运行。
```

如果看到错误信息，请检查：
1. API 密钥是否正确配置
2. 网络连接是否正常
3. 配置文件格式是否正确

## 6. 下一步

- 了解架构设计：[3阶段架构](architecture/overview.md)
- 学习开发规范：[开发规范](development-guide.md)
- 查看阶段参与者开发指南：[阶段参与者开发](development/provider-guide.md)
