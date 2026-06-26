# MCP 模块（Model Context Protocol 服务端）

Amaidesu 的 MCP（Model Context Protocol）服务端模块。它把 Amaidesu 的核心控制能力暴露为标准 MCP tools，让外部 AI 客户端（如 Claude Desktop、Cursor、其他支持 MCP 的 LLM 应用）可以在对话过程中调用 Amaidesu 触发虚拟形象动作、发送台词、或查询系统状态。这是 Amaidesu 的首个模块级 README。

## 简介

MCP 模块在 Amaidesu 3 阶段架构中扮演"协议桥"的角色：它自身不属于 Input / Decision / Output 三个阶段中的任何一阶段，而是将外部 AI 客户端的调用转换为对 Dashboard HTTP API（运行在 60214 端口）的请求，从而让上游 LLM 调度 Amaidesu 的 Output 阶段行为。

MCP 服务端基于 [FastMCP](https://github.com/jlowin/fastmcp) 实现，使用 `stateless_http=True` 模式运行（每个 tool call 是独立的 HTTP 请求，无会话状态），默认监听 `127.0.0.1:30214`。

## 架构位置

MCP 模块处于外部 AI 客户端与 Amaidesu 内核之间的边界层：

```
外部 MCP 客户端（Claude Desktop / Cursor / 其他）
        |
        |  MCP over HTTP（stateless）
        |  URL: http://127.0.0.1:30214/mcp/
        v
Amaidesu MCP Server (FastMCP, port 30214)
        |
        |  HTTP POST / GET（httpx 异步客户端）
        v
Dashboard HTTP API (port 60214)
        |
        |  /api/v1/maibot/action  -> Intent -> EventBus -> Output Handlers
        |  /api/v1/system/status  -> SystemStatusResponse
        v
Output 阶段 Handler（VTS / TTS / Subtitle / Sticker ...）
```

MCP 模块不直接构造 Intent 也不订阅 EventBus，它仅作为 HTTP 客户端调用 Dashboard API，由 Dashboard API 负责把请求转换为 Intent 并通过 EventBus 分发到 Output 阶段。

## 工具参考

模块注册了 3 个 MCP tool。Tool 名称即为客户端调用时的 identifier。

### `send_action`

对齐 MaiBot Plugin 的 `amaidesu_action` Tool，用于触发 Amaidesu 的结构化动作（热键 / 表情）。

**参数表**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `action_type` | string | 否 | `None` | 动作类型，例如 `hotkey` 或 `expression` |
| `action_value` | string | 否 | `None` | 动作值，例如 `smile`、`wave`、`nod`、`clap`、`dance`、`think`、`bow`、`point` |
| `emotion` | string | 否 | `None` | 情绪字符串，支持枚举值（`happy`、`neutral`、`sad`、`angry`、`excited`、`shy`、`surprised`、`confused`）或自然语言描述（如"开心"、"害羞"） |
| `priority` | integer | 否 | `50` | 优先级，范围 0-100，越高越优先 |
| `text` | string | 否 | `None` | 附带回复文本 |

**请求 Payload（POST `http://127.0.0.1:60214/api/v1/maibot/action`）**

```json
{
  "priority": 50,
  "action": "hotkey",
  "action_params": { "hotkey": "smile" },
  "emotion": "happy",
  "text": "你好呀"
}
```

**返回值示例**

```json
{
  "success": true,
  "intent_id": "int_abc123",
  "message": "Intent dispatched",
  "error": null
}
```

Tool 调用成功后，MCP 服务端会向客户端返回简短确认，例如 `Intent sent successfully: int_abc123`。

### `send_react`

对齐 MaiBot Plugin 的 `amaidesu_react` Tool，用于让外部 LLM 以自然语言描述一次性表达台词 + 情绪 + 动作。

**参数表**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `speech` | string | 是 | — | 台词文本，不能为空 |
| `emotion` | string | 否 | `None` | 自然语言情绪描述，如"害羞"、"开心" |
| `action` | string | 否 | `None` | 自然语言动作描述，如"脸红并挥手"、"比心" |

**请求 Payload（POST `http://127.0.0.1:60214/api/v1/maibot/action`）**

```json
{
  "priority": 50,
  "text": "谢谢大家的支持！",
  "emotion": "害羞",
  "action": "脸红并挥手"
}
```

**校验行为**

当 `speech` 为空字符串时，Tool 抛出 `ValueError`，与 MaiBot Plugin 中 `amaidesu_react` 的硬错误行为保持一致。

### `get_status`

替代 MaiBot Plugin 中 broken 的 `/amaidestatus` 命令，用于查询 Amaidesu 当前系统运行状态。

**参数**

无参数。

**请求（GET `http://127.0.0.1:60214/api/v1/system/status`）**

**返回值示例（`SystemStatusResponse`）**

```json
{
  "running": true,
  "uptime_seconds": 123.45,
  "version": "0.1.0",
  "python_version": "3.12.4",
  "input_phase": {
    "enabled": true,
    "active_components": 2,
    "total_components": 3
  },
  "decision_phase": {
    "enabled": true,
    "active_components": 1,
    "total_components": 4
  },
  "output_phase": {
    "enabled": true,
    "active_components": 5,
    "total_components": 12
  }
}
```

注意：该 endpoint 是系统级状态（`/api/v1/system/status`），**不是** MaiBot-specific 状态。Amaidesu 当前未实现 `/api/v1/maibot/status` 端点。

## 与 Plugin 能力对照表

下表对照 MCP 模块与 MaiBot Plugin（`integrations/amaidesu_plugin/`）的能力差异。

| Plugin 能力 | Plugin 端实现 | MCP 端状态 | 说明 |
|------------|--------------|-----------|------|
| `amaidesu_action` Tool | `@Tool` 装饰器，LLM 自动触发 | 支持，对应 `send_action` | 参数与 payload 形状对齐 |
| `amaidesu_react` Tool | `@Tool` 装饰器，LLM 自动触发 | 支持，对应 `send_react` | `speech` 必填，payload 仅用现有 schema 字段 |
| `/amaidesu` Command | `@Command` 装饰器，用户手动触发 | 部分支持 | MCP 协议无原生命令概念，LLM 可通过调用 `send_action` 间接实现用户手动触发的语义 |
| `/amaidestatus` Command | `@Command` 装饰器，查询连接状态 | 支持，对应 `get_status` | 实现中实际调用 `/api/v1/system/status`，**不**调用 `/api/v1/maibot/status`（后者不存在） |
| HookHandler 提示词钩子 | `@HookHandler` 拦截 `maisaka.replyer.before_model_request` / `maisaka.planner.before_request` | **未提供** | MCP 协议是 client 主动调用 server，server 无法反向干预外部 LLM 的 system prompt |

## 配置

MCP 服务端的配置位于 `config/core.toml`（多文件配置模式）的 `[mcp]` 段。

### 示例配置

```toml
[mcp]
enabled = true
host = "127.0.0.1"
port = 30214
```

### 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | boolean | `false` | 是否启用 MCP 服务。设为 `true` 后 `main.py` 会在 setup 阶段创建 `MCPServerService` 并启动监听 |
| `host` | string | `"127.0.0.1"` | MCP 服务监听地址。建议保持 `127.0.0.1` 避免对外暴露（外部 AI 客户端与 Amaidesu 同机时通过 loopback 连接） |
| `port` | integer | `30214` | MCP 服务监听端口（有效范围 1024-65535）。若与 Dashboard（60214）或其他服务冲突需调整 |

## 启动与生命周期

MCP 服务在 `main.py` 中由 Amaidesu 主流程自动管理，**用户无需手动启动或停止**。

启动流程（`main.py:create_app_components()`）：

1. 读取 `config.get("mcp", {})` 拿到 `[mcp]` 段配置
2. 若 `enabled == false`，记录日志后跳过（`mcp_service` 保持 `None`）
3. 若 `enabled == true`，构造 `MCPServerService(MCPServerConfig(**mcp_config))` 并调用 `await mcp_service.setup(mcp_config)`
4. `setup()` 内部创建 `FastMCP("amaidesu-mcp")` 实例、注册 3 个 tool、并以 `asyncio.create_task` 方式启动 HTTP 监听
5. 若初始化抛异常，记录错误日志并把 `mcp_service` 置为 `None`（降级模式，MCP 功能被禁用但主流程不受影响）

关闭流程（`main.py:run_shutdown()`）：

1. 主流程结束时调用 `await mcp_service.cleanup()`
2. `cleanup()` 取消后台 server task，并在 5 秒超时后强制清理
3. 清理完成记录日志

生命周期与 Dashboard server / LLMManager 等基础设施服务对齐，遵循 `__init__` → `setup` → `[使用]` → `cleanup` 模式。

## 客户端接入示例

### Claude Desktop

编辑 Claude Desktop 的 `claude_desktop_config.json`（Windows 路径：`%APPDATA%\Claude\claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "amaidesu": {
      "url": "http://127.0.0.1:30214/mcp/"
    }
  }
}
```

重启 Claude Desktop 后，工具面板会出现 `send_action`、`send_react`、`get_status` 三个工具，可直接在对话中调用。

### Cursor

编辑 Cursor 的 MCP 配置（`~/.cursor/mcp.json` 或 Settings → MCP）：

```json
{
  "mcpServers": {
    "amaidesu": {
      "url": "http://127.0.0.1:30214/mcp/"
    }
  }
}
```

### 通用 MCP 客户端

任何支持 MCP over HTTP（streamable / stateless）协议的客户端都可以通过 `http://127.0.0.1:30214/mcp/` 接入。

## 已知限制

### 1. HookHandler 提示词钩子功能不可用

MCP 协议的设计方向是**客户端主动调用服务端**：LLM 客户端发起 tool call，服务端响应结果。协议层面不允许服务端反向修改客户端 LLM 的 system prompt 或拦截模型请求。

因此，MCP 模块**无法**实现 MaiBot Plugin 中 `@HookHandler` 提供的"替换回复器/规划器系统提示词"能力。需要提示词覆盖能力时，请继续使用 `integrations/amaidesu_plugin/` 中的插件形式。

### 2. `/api/v1/maibot/status` 端点不存在

`get_status` Tool 调用的是 `GET http://127.0.0.1:60214/api/v1/system/status`，**不是** `/api/v1/maibot/status`。后者在 Amaidesu 当前实现中并不存在。

返回的数据是**系统级运行状态**（启动时间、各阶段组件数量、运行中标志位），不是 MaiBot-specific 的连接状态。如需更详细的事件统计，可访问 `/api/v1/system/stats`。

### 3. Dashboard URL 当前硬编码为 `127.0.0.1:60214`

MCP 服务端在调用 Dashboard API 时硬编码 `http://127.0.0.1:60214` 作为 base URL，并未从 `[dashboard]` 配置段的 `host` / `port` 动态读取。这意味着：

- 若用户修改了 Dashboard 端口（如改成 `60314`），MCP tool 调用会因为连接 60214 失败而报错
- 若 Amaidesu 部署在远程机器，MCP 服务端无法指向远程 Dashboard

未来版本会改为从 `[dashboard]` 配置动态拼接 URL，本期暂不修复。

### 4. 无原生命令概念

MCP 协议中**没有** Command 概念（Command 是 MaiBot Plugin 的装饰器抽象）。Plugin 中的 `/amaidesu list` 这种"用户手动触发"语义在 MCP 端只能通过"LLM 主动调用 tool"间接模拟，无法做到用户输入斜杠命令触发。

## 故障排查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| MCP 服务未启动 | `[mcp]` 配置段缺失或 `enabled = false` | 在 `config/core.toml` 中添加 `[mcp]` 段并设置 `enabled = true` |
| MCP 服务启动失败（main 日志报错） | 配置字段类型错误（如 `port = "abc"`） | 检查 `[mcp]` 段三个字段类型：`enabled` (bool) / `host` (string) / `port` (int) |
| 客户端连接 `30214` 端口失败 | 防火墙阻挡或 `enabled = false` | 确认 `uv run python main.py` 启动日志包含"MCP 服务初始化完成"字样 |
| Tool call 返回 404 | Dashboard 未启动 | 先确认 `127.0.0.1:60214` 上 Dashboard API 在响应（`curl http://127.0.0.1:60214/api/v1/system/status`） |
| Tool call 返回超时 | Dashboard 卡顿或后端事件循环阻塞 | 检查 Output 阶段是否有 handler 死循环；可临时减小 `[mcp] port` 范围排除端口冲突 |
| `send_react` 抛 `ValueError: speech required` | 调用方未传 `speech` 参数 | 该 Tool 的 `speech` 参数为必填项，与 Plugin 端行为一致；调用时必须提供台词 |
| `send_action` 返回 `priority must be between 0 and 100` | `priority` 超出范围 | 将 `priority` 控制在 0-100 区间内 |
| Claude Desktop 工具列表中不显示 Amaidesu tools | 配置格式错误或未重启 | 验证 `claude_desktop_config.json` 是合法 JSON；完全退出并重启 Claude Desktop |
| `get_status` 返回结果不含 maibot-specific 字段 | 预期行为 | `/api/v1/maibot/status` 不存在；当前仅返回系统级状态（`running` / `uptime_seconds` / 各阶段组件数） |
