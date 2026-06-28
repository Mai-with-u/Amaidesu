# Amaidesu 动作控制与提示词覆盖插件

MaiBot SDK v2 插件,用于控制 [Amaidesu](https://github.com/Mai-with-u/Amaidesu) 虚拟形象的结构化 action + emotion + text,以及系统提示词覆盖。

## 功能

| 组件 | 类型 | 说明 |
|------|------|------|
| 回复器提示词覆盖 | `@HookHandler` | 拦截 `maisaka.replyer.before_model_request`,替换回复器系统提示词 |
| 规划器提示词覆盖 | `@HookHandler` | 拦截 `maisaka.planner.before_request`,替换规划器系统提示词 |
| `amaidesu_action` | `@Tool` | LLM 决策自动触发结构化 action / emotion / text |
| `amaidesu_query_capabilities` | `@Tool` | 查询全限定 action 列表(替代旧的 `/amaidesu list` 硬编码) |
| `amaidesu_query_handlers` | `@Tool` | 查询合法 handler 名列表(供 LLM 构造 `action_name` 前缀) |
| `/amaidesu` | `@Command` | 手动控制,新结构化 payload(需要 handler-qualified action) |
| `/amaidestatus` | `@Command` | 查询 Amaidesu 连接状态 |

> ❌ `amaidesu_react` Tool 已删除(新结构化 action + emotion + text 不再需要独立 "react" 工具)

## 前置条件

- MaiBot v1.0.0
- maibot-plugin-sdk >= 2.0.0
- Amaidesu 已启动并在 60214 端口提供 HTTP API

## 安装

### 方式一:符号链接(推荐,开发调试用)

```powershell
# 1. 开启 Windows 开发者模式(仅需一次)
# 2. 创建符号链接
New-Item -ItemType SymbolicLink -Path "<MaiBot路径>\plugins\amaidesu_plugin" -Target "<Amaidesu路径>\integrations\amaidesu_plugin"
```

### 方式二:文件复制

```powershell
Copy-Item -Recurse "<Amaidesu路径>\integrations\amaidesu_plugin" "<MaiBot路径>\plugins\amaidesu_plugin"
```

## 配置

`config.toml` 由 MaiBot 首次加载插件时**自动生成**(基于 Config 类的默认值)。

### `[plugin]` — 插件基本信息

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否启用插件 |
| `config_version` | `"2.0.0"` | 配置版本 |

### `[api]` — Amaidesu HTTP API

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `base_url` | `http://127.0.0.1:60214` | Amaidesu HTTP API 基地址(无尾部 `/`) |
| `action_path` | `/api/v1/maibot/action` | 触发 Intent 端点 |
| `handlers_path` | `/api/v1/handlers` | 查询合法 handler 名端点 |
| `capabilities_path` | `/api/v1/capabilities` | 查询全限定 action 列表端点 |
| `timeout` | `10` | 请求超时时间(秒) |
| `startup_fetch_retries` | `3` | 启动 fetch `/api/v1/handlers` 重试次数 |
| `startup_fetch_backoff` | `1.0` | 启动 fetch 重试退避基础(秒,指数) |

### `[replyer_override]` / `[planner_override]` — 提示词覆盖

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否启用 |
| `system_prompt_content` | `""` | 覆换的系统提示词内容(留空则不覆盖) |

## 命令

### `/amaidesu <handler>.<action>`

手动触发结构化动作(需要 handler 前缀)。

```
/amaidesu warudo.wave     # 触发 warudo handler 的 wave 动作
/amaidesu vts.Wave         # 触发 vts handler 的 Wave 动作
/amaidesu vrchat.wave      # 触发 vrchat handler 的 wave 动作
```

### `/amaidesu emotion <name>`

设置情绪(枚举值,小写)。例如:

```
/amaidesu emotion happy
/amaidesu emotion sad
```

### `/amaidesu list`

**已废弃**。返回消息提示改用 `amaidesu_query_capabilities` / `amaidesu_query_handlers` Tool 查询动态能力(避免硬编码列表与实际能力漂移)。

### `/amaidestatus`

查询 Amaidesu 连接状态。

## Tool 说明

### `amaidesu_action`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `action_name` | string | 否 | 全限定 action 名,格式 `<handler>.<local_action>`(如 `warudo.wave`) |
| `action_parameters` | string(JSON) | 否 | 动作参数,如 `{"duration_ms": 1500}` |
| `emotion_name` | string | 否 | 情绪枚举名,如 `happy` / `neutral` / `sad` |
| `emotion_intensity` | float | 否 | 情绪强度 [0.0, 1.0],默认 0.5 |
| `text` | string | 否 | 可选附带文本(对 TTS 朗读) |

至少需要提供 `action_name` / `emotion_name` / `text` 之一。`action_name` 必须含 `.`(handler-qualified),否则预验证拒绝(若启动 fetch 成功)。

### `amaidesu_query_capabilities`

无参数。返回全限定 action 列表(带 parameters 描述):

```json
{
  "actions": [
    {"name": "warudo.wave", "description": "Warudo wave action", "parameters": {"duration_ms": {...}}},
    {"name": "warudo.nod", ...}
  ]
}
```

### `amaidesu_query_handlers`

无参数。返回合法 handler 名列表:

```json
{"handlers": ["warudo", "vts", "vrchat"]}
```

## 启动预验证

`on_load()` 时插件会拉取 `/api/v1/handlers` 缓存合法 handler 名:

- 成功 → `self._valid_handlers` 填充,后续 `amaidesu_action` 拒绝未知前缀(返回错误消息)
- 失败 → `try/except + 重试` 后 `_valid_handlers` 保持 `None`,`amaidesu_action` 关闭前缀预验证(LLM 自行控制)

## 提示词覆盖原理

通过 `@HookHandler` 拦截两个钩子,在 MaiBot 向 LLM 发送请求前替换系统提示词:

- **回复器**(`maisaka.replyer.before_model_request`): 覆盖实际回复风格
- **规划器**(`maisaka.planner.before_request`): 覆盖决策策略

## 目录结构

```
integrations/amaidesu_plugin/
├── _manifest.json    # 插件元信息(SDK v2 manifest)
├── plugin.py         # 插件入口
└── README.md         # 本文件
```

## 故障排查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 插件注册失败 | manifest 字段不符合验证器要求 | 检查 `_manifest.json` 格式 |
| 提示词未覆盖 | `enabled = false` 或内容为空 | 编辑 `config.toml`,设置对应 section 的 `enabled = true` |
| 动作执行失败 | Amaidesu 未启动 | 先运行 `/amaidestatus` 检查连接 |
| `amaidesu_action` 报"未知 handler 标识符" | 启动 fetch 成功但 LLM 用了未注册的 handler 前缀 | LLM 应先调用 `amaidesu_query_handlers` 获取合法前缀 |
| LLM 不调用 Tool | Tool 在 deferred 池,需要被搜索发现 | 在对话中提到需要做动作或表达情绪 |
| 符号链接创建失败 | 权限不足 | 开启 Windows 开发者模式或使用管理员终端 |
| 找不到 config.toml | 首次加载未生成 | 确认 MaiBot 已加载插件,会自动生成 |
