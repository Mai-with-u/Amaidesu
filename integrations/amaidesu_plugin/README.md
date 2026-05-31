# Amaidesu 动作控制与提示词覆盖插件

MaiBot SDK v2 插件，用于控制 [Amaidesu](https://github.com/Mai-with-u/Amaidesu) 虚拟形象的动作和情绪，并提供系统提示词覆盖功能。

## 功能

| 组件 | 类型 | 说明 |
|------|------|------|
| 回复器提示词覆盖 | `@HookHandler` | 拦截 `maisaka.replyer.before_model_request`，替换回复器系统提示词 |
| 规划器提示词覆盖 | `@HookHandler` | 拦截 `maisaka.planner.before_request`，替换规划器系统提示词 |
| 动作控制 | `@Tool` | LLM 决策自动触发动作（热键、表情、情绪等） |
| `/amaidesu` | `@Command` | 手动控制动作和情绪（调试用） |
| `/amaidestatus` | `@Command` | 查询 Amaidesu 连接状态 |

## 前置条件

- MaiBot v1.0.0
- maibot-plugin-sdk >= 2.0.0
- Amaidesu 已启动并在 60214 端口提供 HTTP API

## 安装

### 方式一：符号链接（推荐，开发调试用）

插件源码位于 Amaidesu 项目的 `integrations/amaidesu_plugin/` 目录，通过符号链接映射到 MaiBot 的 `plugins/` 目录，修改源码后实时生效，无需重新安装。

```powershell
# 1. 开启 Windows 开发者模式（仅需一次）
#    设置 → 隐私和安全性 → 开发者选项 → 开启"开发人员模式"

# 2. 以管理员身份创建符号链接
New-Item -ItemType SymbolicLink -Path "<MaiBot路径>\plugins\amaidesu_plugin" -Target "<Amaidesu路径>\integrations\amaidesu_plugin"

# 示例
New-Item -ItemType SymbolicLink -Path "E:\MaiBot\plugins\amaidesu_plugin" -Target "E:\Amaidesu\integrations\amaidesu_plugin"
```

> **符号链接 vs 复制**：符号链接（软链接）是操作系统级别的目录映射，MaiBot 扫描 `plugins/` 时看到的就是 Amaidesu 项目中的源文件。修改 `plugin.py` 后 MaiBot 下次加载直接生效。Windows 需要开发者模式或管理员权限才能创建。

### 方式二：文件复制

```powershell
# 将插件目录复制到 MaiBot 的 plugins/ 下
Copy-Item -Recurse "<Amaidesu路径>\integrations\amaidesu_plugin" "<MaiBot路径>\plugins\amaidesu_plugin"
```

> 注意：复制方式修改源码后需要重新复制才能生效。

## 配置

`config.toml` 由 MaiBot 首次加载插件时**自动生成**（基于 Config 类的默认值）。如需自定义，编辑插件目录下的 `config.toml` 即可。

### `[plugin]` — 插件基本信息

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否启用插件 |
| `config_version` | `"2.0.0"` | 配置版本 |

### `[api]` — Amaidesu HTTP API

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `url` | `http://127.0.0.1:60214/api/v1/maibot/action` | Amaidesu 动作 API 地址 |
| `timeout` | `10` | 请求超时时间（秒） |

### `[replyer_override]` — 回复器提示词覆盖

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否启用 |
| `system_prompt_content` | `""` | 覆换的系统提示词（留空则不覆盖） |

### `[planner_override]` — 规划器提示词覆盖

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否启用 |
| `system_prompt_content` | `""` | 覆换的系统提示词（留空则不覆盖） |

**注意**：提示词内容会直接替换 MaiBot 发送给 LLM 的系统提示词。不要使用 `{bot_name}` 等模板变量——Hook 拦截的是已填充后的完整文本，这些变量不会被二次填充。

**回复器 vs 规划器**：MaiBot 的回复流程分两步，各自有独立的系统提示词：

| 阶段 | Hook | 作用 | 配置 |
|------|------|------|------|
| **规划器** | `maisaka.planner.before_request` | 决定是否回复、用什么策略 | `[planner_override]` |
| **回复器** | `maisaka.replyer.before_model_request` | 生成实际回复内容 | `[replyer_override]` |

## 命令

### `/amaidesu <类型> <值>`

手动控制 Amaidesu 动作和情绪。

| 命令 | 说明 |
|------|------|
| `/amaidesu hotkey smile` | 触发热键动作（smile、wave、nod、clap、dance、think、bow、point） |
| `/amaidesu emotion happy` | 设置情绪（happy、neutral、sad、angry、excited、shy、surprised、confused） |
| `/amaidesu list` | 显示可用命令列表 |

### `/amaidestatus`

查询 Amaidesu 连接状态。

## Tool 说明

插件注册了 `amaidesu_action` 工具，LLM 可在对话中自动判断并调用。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `action_type` | string | 否 | 动作类型（hotkey、expression） |
| `action_value` | string | 否 | 动作值（smile、wave 等） |
| `emotion` | string | 否 | 情绪类型（happy、sad 等） |
| `priority` | integer | 否 | 优先级 0-100，默认 50 |
| `text` | string | 否 | 附带文本内容 |

## 提示词覆盖原理

通过 `@HookHandler` 拦截两个钩子，在 MaiBot 向 LLM 发送请求前替换系统提示词：

**回复器**（`maisaka.replyer.before_model_request`）：
- 回复器负责生成实际回复文本
- 覆盖它可改变 LLM 的角色设定和回复风格

**规划器**（`maisaka.planner.before_request`）：
- 规划器负责决定是否回复、用什么策略
- 覆盖它可影响回复决策逻辑

两个 Hook 的修改逻辑相同：
1. 在消息列表中查找第一条 `role: system` 的消息
2. 如果找到 → 替换其 `content`
3. 如果未找到 → 在列表开头插入新的 system 消息
4. 如果对应的 `enabled = false` 或内容为空 → 不做任何修改

## 目录结构

```
integrations/amaidesu_plugin/
├── _manifest.json    # 插件元信息（SDK v2 manifest）
├── plugin.py         # 插件入口（含 Config 类定义）
└── README.md         # 本文件
```

> `config.toml` 由 MaiBot 首次加载时自动生成，不纳入版本控制。

## 故障排查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 插件注册失败 | manifest 字段不符合验证器要求 | 检查 `_manifest.json` 格式，对照 `hello_world_plugin` |
| 提示词未覆盖 | `enabled = false` 或内容为空 | 编辑 `config.toml`，设置对应 section 的 `enabled = true` 并填写提示词 |
| 动作执行失败 | Amaidesu 未启动或端口不对 | 先运行 `/amaidestatus` 检查连接，确认 Amaidesu 在 60214 端口运行 |
| LLM 不调用 Tool | Tool 在 deferred 池，需要被搜索发现 | 在对话中提到需要做动作或表达情绪 |
| 符号链接创建失败 | 权限不足 | 开启 Windows 开发者模式或使用管理员终端 |
| 找不到 config.toml | 首次加载未生成 | 确认 MaiBot 已加载插件（检查日志），会自动生成 |
