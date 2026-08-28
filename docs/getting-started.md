# 快速开始

Amaidesu 是一个 VTuber 直播辅助工具，支持弹幕互动、语音合成、虚拟形象控制等功能。本指南会带你从零走到首次成功运行。

## 1. 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 包管理器
- Windows / macOS / Linux 均可（Windows PowerShell 示例见下文）

## 2. 安装步骤

### 2.1 安装 uv（Windows）

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS / Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2.2 克隆仓库

```bash
git clone https://github.com/Mai-with-u/Amaidesu.git
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

首次运行会检测 `config/` 目录。目录不存在时，程序按 Schema 自动生成 **七文件配置树**：

```
config/
├── core.toml        # 基础设施（meta/general/persona/context/events/dashboard/logging/interceptors）
├── model.toml       # LLM/VLM 模型（[[llm_providers]] + [llm]/[llm_fast]/[vlm]/[llm_local]/[llm_summary]/[llm_agenda]）
├── agents.toml      # 业务 Agent（[agents]）
├── tools.toml       # 工具包（[tools] + 感知/理解/输出/内容引擎/外部）
├── memory.toml      # 记忆系统（[memory] backend=simple|amemorix）
├── storage.toml     # 存储（[storage.sqlite]）
└── background.toml  # 后台维护（[background] + [background.compressor]）
```

生成完毕后程序会打印提示框并 **主动退出**，让你先编辑配置再回来：

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! 配置文件已在 config/ 目录下自动生成。                    !!
!! 请编辑 config/ 目录下的 .toml 文件，填写必要配置。       !!
!! 修改完成后，请重新运行程序。                           !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

> 这是 v2.0.0 引入的 `exit_if_config_created` 行为，避免用占位 API Key 直接跑 LLM、产生无意义的 token 消耗。补完配置后再次 `uv run python main.py` 即可。
>
> 如果是从旧版本（v1.x，五文件）升级，旧 `config/input.toml` / `config/decision.toml` / `config/output.toml` 会被自动迁移并备份到 `config/old/` 下，源段会被 `CrossFileMigration` 合并进新的 `tools.toml` / `agents.toml`。

### 2.5 编辑配置

七文件各自只承担一个域，本节只列首次成功运行所必需的最小集。其他字段保留默认值即可。

#### LLM 配置（必需）

`config/model.toml` 采用 **provider + profile** 两层结构：`[[llm_providers]]` 定义可复用的 API 连接，profile（`[llm]` / `[llm_fast]` 等）通过 `provider` 字段引用并按需覆盖模型参数。

```toml
# config/model.toml
[[llm_providers]]
name = "deepseek"            # provider 名称（profile 中引用）
client_type = "openai"       # 客户端类型
base_url = "https://api.deepseek.com"
api_key = "sk-your-key"      # 填入你的 API Key（留空会用 sk-dummy 并警告）

[llm]                        # 高质量对话（默认 Planner/Replyer 都用它）
provider = "deepseek"
model = "deepseek-chat"
temperature = 0.2

[llm_fast]                   # 快速对话（低延迟）
provider = "deepseek"
model = "deepseek-chat"
temperature = 0.7
```

> ⚠️ **注意** `config/` 目录已在 `.gitignore` 中忽略，`model.toml` 中的 API Key **不会提交到仓库**。当前实现不读取环境变量，请直接在 `config/model.toml` 中填写明文 Key。

#### 启用 Agent（必需）

`config/agents.toml` 控制哪些业务 Agent 启动。首次推荐只开 `streamer`（主播 Agent），等跑通后再加 `game`。

```toml
# config/agents.toml
[agents]
enabled = ["streamer"]

# 可选：自定义子段（全部字段均有默认值，留空走默认即可）
[agents.streamer]
planner_llm = "llm_fast"     # Planner 用的 LLM profile 名
replyer_llm = "llm"          # Replyer 用的 LLM profile 名
# proactive_enabled = false
# profanity_enabled = false
```

`game` 是游戏 Agent 总开关，`engine` 字段选择具体游戏实现（当前唯一可用的是 `text_adv` 文字冒险示例）：

```toml
[agents]
enabled = ["streamer", "game"]

[agents.game]
command_llm = "llm"
# engine = "text_adv"   # 当前仅支持 text_adv
```

#### 启用采集器

采集器归位 `config/tools.toml` 的 `[tools.perception]` 包下。先把"控制台输入"开起来，零依赖就能对话：

```toml
# config/tools.toml
[tools]
enabled = ["perception", "output"]   # 工具包启用列表

# 感知工具包（屏幕/音频/弹幕/遥测）
[tools.perception]
enabled = true
provider = "builtin"                 # builtin=框架内置 / game=游戏 Agent / mcp=外部源

# 工具包具体配置（采集器在此声明）
[tools.perception.config]
enabled = ["console_input"]          # 仅启用控制台输入
# enabled = ["bili_danmaku"]         # B 站 legacy 弹幕（需填 room_id）
# enabled = ["bili_danmaku_official"]# B 站官方弹幕（需填 id_code/app_id/access_key）
# enabled = ["read_pingmu"]          # 屏幕变化检测（需 VLM）
# enabled = ["stt"]                  # 语音转写（需 iflytek 配置）
# enabled = ["mock_danmaku"]         # 从 data/*.jsonl 回放（调试）

# 控制台输入子配置（可留空，使用默认）
[tools.perception.config.console_input]
user_id = "console_user"
user_nickname = "控制台"
```

跑通后再按需启用其他采集器，完整字段含义见配置 Schema（`src/modules/config/tools_schemas.py` 与各 Collector 模块的 `ConfigSchema`）。

#### 启用渲染输出（可选）

`[tools.output]` 包是 TTS / 字幕 / 皮套 / OBS 等渲染工具的统一入口，默认就启用，但**当前版本默认未在 `config.enabled` 里勾选任何具体渲染工具**，详见下方"已知限制"。

```toml
# config/tools.toml（接在上文 [tools.perception] 之后）
[tools.output]
enabled = true
provider = "builtin"

[tools.output.config]
enabled = ["subtitle", "vts"]        # 仅示例：勾上后会按同名子段加载配置
concurrent_rendering = true
render_timeout_ms = 10000
completion_timeout_ms = 30000

[tools.output.config.edge_tts]
type = "edge_tts"
voice = "zh-CN-XiaoxiaoNeural"
```

### 2.6 再次运行

```bash
uv run python main.py
```

也可以先用 `--dry` 仅验证装配（构造组件但不进入主循环）：

```bash
uv run python main.py --dry
```

成功启动后，控制台采集器会等待你输入文字；输入后由 Streamer Agent 决策并回复，日志里能看到事件流与 LLM 调用。

## 3. 配置说明

### 3.1 主要配置段（七文件 ↔ 顶层段权威表）

| 配置文件 | 顶层段 | 说明 |
|---------|--------|------|
| `core.toml` | `[meta]` | 配置元数据（`CONFIG_VERSION=2.0.4`，自动写回） |
| `core.toml` | `[general]` | 进程标识（Dashboard / 日志 / 模拟器区分用） |
| `core.toml` | `[persona]` | VTuber 人设（bot_name / personality / style_constraints / max_response_length） |
| `core.toml` | `[context]` | 上下文组装器（memory_recall_* / cache_ttl_ms） |
| `core.toml` | `[events]` | 事件历史环形缓冲（history_size / persist） |
| `core.toml` | `[dashboard]` | Web Dashboard 配置（端口 / CORS / 心跳） |
| `core.toml` | `[simulator]` | 模拟直播间（开关 + LLM 节奏 + token 预算） |
| `core.toml` | `[logging]` | 日志配置（级别 / 轮转 / 过滤） |
| `core.toml` | `[interceptors.rate_limit]` / `[interceptors.similar_filter]` | 事件拦截器（作用于弹幕流，见 §3.4） |
| `model.toml` | `[[llm_providers]]` | LLM provider 池（可复用 API 连接） |
| `model.toml` | `[llm]` / `[llm_fast]` / `[vlm]` / `[llm_local]` / `[llm_summary]` / `[llm_agenda]` | LLM profile（引用 provider + 覆盖参数） |
| `agents.toml` | `[agents]` + `[agents.streamer]` + `[agents.game]` | 业务 Agent 启用与子配置（替代旧 `[deciders]`） |
| `tools.toml` | `[tools]` + `[tools.perception]` / `[tools.understanding]` / `[tools.output]` / `[tools.content_engine]` / `[tools.external]` | 工具包启用与子配置（替代旧 `[collectors]` / `[handlers]`） |
| `memory.toml` | `[memory]` + 子表 | 记忆后端（`backend="simple"` 或 `"amemorix"`） |
| `storage.toml` | `[storage.sqlite]` | SQLite 存储（`db_path` / `wal` / `busy_timeout_ms`） |
| `background.toml` | `[background]` + `[background.compressor]` | 后台维护 tick 与压缩 worker |

> 字段权威定义在 `src/modules/config/*_schemas.py`；修改后启动会自动写回默认值与 `CONFIG_VERSION`，迁移测试见 `tests/config/`。

### 3.2 组件类型

| 类型 | 职责 | 代码位置 | 配置入口 |
|------|------|---------|---------|
| **采集器（Collector）** | 世界→系统的入口：把弹幕、语音、控制台、屏幕变化等外部数据标准化、推事件 | `src/modules/collectors/` | `[tools.perception.config]` |
| **业务 Agent（Agent）** | 拥有内部状态与工具的主循环体；订阅事件、决策、调用工具 | `src/agents/` | `[agents]` + `[agents.<name>]` |
| **工具（Tool）** | 单一能力函数（@tool 装饰器），由 Agent 在决策时按需调用 | `src/modules/tools/` | `[tools.<pack>.config]` |

> 渲染工具（TTS / 字幕 / VTS / OBS 等）在 v2 中以 **Tool Provider** 的形式注册：开启 `[tools.output]` 后，工具包内的组件会注册到 `ToolRegistry` 中，LLM 可以像调用普通工具一样触发渲染。详见 [组件开发指南](development/component-guide.md)。

### 3.3 可用组件清单

完整字段含义见 [3阶段架构总览](architecture/overview.md)；本节列出当前已落地的组件名。

#### 采集器（`SUPPORTED_COLLECTORS`）

源：`src/modules/collectors/factory.py`

| 名称 | 用途 | 关键子配置 |
|------|------|-----------|
| `console_input` | 控制台输入（开发测试，零依赖） | `user_id` / `user_nickname` |
| `bili_danmaku` | B 站 legacy 弹幕（轮询） | `room_id` / `poll_interval` |
| `bili_danmaku_official` | B 站官方长连弹幕 | `id_code` / `app_id` / `access_key(_secret)` / `api_host` |
| `read_pingmu` | 屏幕变化检测（VLM） | `api_key` / `model_name` / `screenshot_interval` / `diff_threshold` |
| `stt` | 语音转文字（讯飞 ASR + VAD） | `stt.iflytek_asr` / `stt.vad` / `stt.audio` / `stt.message_config` |
| `mock_danmaku` | 从 `data/*.jsonl` 回放（调试用） | `log_file_path` / `send_interval` / `loop_playback` |

> `text_adv_game` 仍在采集器 Schema 中保留，但实际已迁移到 `src/agents/game/text_adv/` 作为 Agent 实现，不再通过采集器配置。

#### 业务 Agent（`SUPPORTED_AGENTS`）

源：`src/modules/agents/factory.py`

| 名称 | 用途 | 关键子配置 |
|------|------|-----------|
| `streamer` | 主播 Agent（Planner + Replyer 两阶段决策） | `planner_llm` / `replyer_llm` / `proactive_*` / `agenda_*` / `profanity_*` / `command_*` |
| `game` | 游戏 Agent（当前唯一引擎 `text_adv`，文字冒险示例） | `engine = "text_adv"` / `command_llm` |

> `custom` 也在 AgentType 字面量里保留，但需用户自行注册，框架不内置实例化逻辑。

#### 工具（按族列举，代表工具名）

工具注册走 `src/modules/tools/registry.py` 的 `ToolRegistry`。StreamerAgent 默认自带 `reply` / `should_speak_proactively` / `parse_command` 三个工具，外加通用 `query_memory`（记忆）。其他族系需要启用对应 `[tools.<pack>]` 包才会注入。

| 工具包（`[tools.<pack>]`） | 代表工具 | 说明 |
|----------------------------|---------|------|
| `perception` | `look_at_screen` | 屏幕感知（VLM 调用） |
| `output` | `edge_tts_synthesize` / `push_subtitle` / `vts_trigger_hotkey` / `obs_switch_scene` | 渲染族：TTS / 字幕 / 皮套控制 / OBS 场景切换 |
| `builtin`（Streamer 自带） | `reply` / `query_memory` / `parse_command` / `should_speak_proactively` | 主播内置工具（开 `streamer` 即生效） |
| `game` | （由具体游戏 Agent 注入） | 游戏专属推进工具（如 text_adv 的截图+点击） |
| `external` | （MCP 来源） | 外部工具源，配置在 `core.toml [mcp]` |

### 3.4 事件拦截器

旧版 `input pipeline` 里的"防刷屏 / 相似文本合并"在 v2 中迁移为 **EventBus 全局事件拦截器**，默认开启，作用于弹幕流（`room.message.*` 事件）。`core.toml` 里对应两段：

```toml
# config/core.toml
[interceptors.rate_limit]
enabled = true                # 限流：单用户与全局窗口
global_rate_limit = 100       # 全局每秒上限
user_rate_limit = 10          # 单用户窗口内上限
window_size = 60              # 窗口大小（秒）

[interceptors.similar_filter]
enabled = true                # 相似文本合并（防复读机）
similarity_threshold = 0.85
time_window = 5.0             # 时间窗（秒）
min_text_length = 3
cross_user_filter = true
```

拦截器返回 `None` 即丢弃事件。开发新拦截器见 [事件系统 - 事件拦截器](architecture/event-system.md#事件拦截器interceptor)。

## 4. 常用命令

### 4.1 运行应用

```bash
# 正常运行
uv run python main.py

# 调试模式（显示 DEBUG 级别日志）
uv run python main.py --debug

# 过滤日志（只显示指定模块/类名）
uv run python main.py --filter EdgeTTSHandler SubtitleHandler

# 仅验证装配（构造组件但不进入主循环，适合冒烟测试）
uv run python main.py --dry
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

项目内置 Web 管理界面，**有两种独立运行模式**：

| 端口 | 模式 | 前置条件 | 访问入口 | 适合场景 |
|------|------|---------|---------|---------|
| **60214** | 生产模式 | 需先 `pnpm run build` 生成 `dashboard/dist/` | http://127.0.0.1:60214 | 最终部署 / 单进程启动 |
| **60315** | 开发模式 | 需同时运行后端（60214） | http://localhost:60315 | 前端开发 / HMR 热更新 |

#### 方式一：生产模式（单进程）

```bash
cd dashboard && pnpm run build   # 首次或前端改动后执行一次
uv run python main.py           # → 浏览器访问 http://127.0.0.1:60214
```

**注意** 未执行 `pnpm run build` 时，60214 仅提供 API（GET / 返回 JSON 提示），不会显示 WebUI。

#### 方式二：开发模式（双进程）

```bash
# 终端 1：启动后端
uv run python main.py           # → 后端运行在 http://127.0.0.1:60214

# 终端 2：启动 Vite 开发服务器
cd dashboard
pnpm install                     # 首次安装依赖
pnpm run dev                     # → Vite 启动在 http://localhost:60315
# 浏览器访问 http://localhost:60315（不是 5173、不是 60214）
```

**开发模式说明**

- Vite 自动代理 `/api` 和 `/ws` 请求到后端 60214
- 修改 `dashboard/src/**` 下文件后浏览器自动热更新（无需刷新）
- 修改后端 Python (`src/**/*.py`) 或配置文件 (`config/*.toml`) 需要重启主程序
- 只跑 `pnpm run dev` 而不跑主程序，WebSocket/API 会无法连接

#### 配置选项（`config/core.toml`）

```toml
[dashboard]
enabled = true                                      # 是否启用 Dashboard
host = "127.0.0.1"                                  # 监听地址
port = 60214                                        # 监听端口
cors_origins = ["http://localhost:60315", "http://127.0.0.1:60315"]  # 允许的跨域来源
max_history_messages = 1000                         # WebSocket 推送的最大历史消息数
websocket_heartbeat = 30                            # WebSocket 心跳间隔（秒）
auto_open_browser = false                           # 启动时自动打开浏览器（生产模式生效）
dev_mode = false                                    # 开发模式（通常由 CLI --dev-webui 启用）
vite_dev_port = 60315                               # Vite 开发服务器端口（与 dashboard/vite.config.ts 保持一致）
```

#### 功能特性

- **实时事件流**（`/ws`）：EventBus 上的事件实时推送，可按类型过滤
- **组件管理页**：采集器与 Agent 的动态启停 / 健康状态查看
- **配置在线编辑**：在线修改 `core.toml` / `tools.toml` 等并热加载
- **LLM 对话调试**：直接在 UI 里向指定 profile 发请求，看完整 prompt 与 token 消耗
- **会话历史**：按 session 维度查看观众消息、AI 回复、工具调用

## 5. 快速验证

启动后，你应该能在日志里依次看到这些关键行（措辞与代码完全一致；实际 N 因注册的事件数变化）：

```
[Info] 配置验证通过（v2 7-file tree 存在性 + 类型检查）
[Info] 所有必要的配置文件已存在。继续正常启动...
[Info] 初始化 LLM 服务...
[Info] 已创建 LLM 服务实例
[Info] 初始化上下文服务...
[Info] 已创建上下文服务实例
[Info] 初始化事件总线...
[Info] RateLimitInterceptor 已注册（[interceptors.rate_limit]）
[Info] SimilarFilterInterceptor 已注册（[interceptors.similar_filter]）
[Info] 事件总线已初始化，事件拦截器已挂载
[Info] 事件历史记录器已启动
[Info] 初始化 CollectorManager（src/modules/collectors/）...
[Info] CollectorManager 已启动（N 个 Collector）
[Info] 初始化 AgentManager（src/agents/）...
[Info] AgentManager 已启动（N 个 Agent）
[Info] ToolRegistry 已创建（AgentManager 构造时注入；9 个 output 包经 bind_core_tools 注册 M 个工具）
[Info] bind_pending_tools 完成（flush L1 @tool pending N 个）
[Info] AgentManager.audit_tools 完成；未实现声明：T（0 即通过）
[Info] 核心事件注册完成，共 N 个事件
[Info] 应用程序正在运行。按 Ctrl+C 退出。
```

控制台采集器处于待命状态：在终端里直接敲一条消息，应该能看到 Streamer Agent 经过 Planner → Replyer 处理后输出回复，并在日志里留下 `reply_tool` 调用与 LLM 调用记录。

如果看到错误信息，按顺序排查：

1. **API Key 没填**：`config/model.toml` 里 `[[llm_providers]].api_key` 是否仍是 `sk-dummy` 或占位符
2. **网络问题**：是否能直连 `base_url`；需要代理的话配环境变量 `HTTP_PROXY` / `HTTPS_PROXY`
3. **配置文件格式错误**：检查 `config/*.toml` 里是否有未配对的引号、缩进是否合法
4. **采集器缺失依赖**：启用 `stt` / `bili_danmaku_official` / `read_pingmu` 前请先填好对应子配置（id_code / appid / api_key 等）

## 6. 下一步

- 了解架构设计：[3阶段架构总览](architecture/overview.md)
- 学习开发规范：[开发规范](development-guide.md)
- 写一个自己的组件（Collector / Agent / Tool / 拦截器）：[组件开发指南](development/component-guide.md)

---

### 已知限制

- **渲染工具（TTS / 字幕 / VTS / OBS 等）的"工具注册接线"仍在完善中**。当前 `[tools.output]` 包已就位、采集器与 Agent 闭环可用，但 LLM 通过工具调用触发具体渲染工具的链路还在收尾，启用后未必能直接出声。建议先用 `console_input` + `streamer` Agent 把控制台交互跑通，再按需勾选渲染工具并实测。
- **控制台交互**已可用；弹幕采集、屏幕识别、语音转写需对应第三方凭据（id_code / appid / VLM API Key 等）。
- 完整字段定义在 `src/modules/config/*_schemas.py`；本指南只覆盖"首次跑通"的最小集。

*最后更新：2026-08-27（启动日志对齐 v2.0.5 工具注册路径：移除 `AgentManager.register_all_tools` 注入日志，改为 ToolRegistry 构造 + `bind_core_tools`（9 个 output 包）+ `bind_pending_tools`（flush L1 `@tool`）+ `audit_tools` 四步只读审计序列）*