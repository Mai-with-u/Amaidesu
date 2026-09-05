<br />
<div align="center">

# Amaidesu

Amadeus?

Amaidesu!

![示例截图](docs/images/demoScreenshot.png)


![Python Version](https://img.shields.io/badge/Python-3.12+-blue)
![Status](https://img.shields.io/badge/状态-前期开发中-red)
![forks](https://img.shields.io/github/forks/Mai-with-u/Amaidesu?style=flat)
![stars](https://img.shields.io/github/stars/Mai-with-u/Amaidesu?style=flat)
![issues](https://img.shields.io/github/issues/Mai-with-u/Amaidesu)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Mai-with-u/Amaidesu)


## 项目简介


[麦麦Bot](https://github.com/MaiM-with-u/MaiBot)的虚拟主播框架。

使用自带的 **Amaidesu 核心**（Agent + 工具 + 存储 + 编排架构），驱动虚拟主播完成弹幕互动、语音合成、虚拟形象控制与直播编排。

</div>

## 架构概述

**Amaidesu 2.0.0 = Agent（自主主体）+ 工具（能力契约）+ 存储（状态/记忆）+ 编排（Agenda 节目单）**

- **采集器（Collector）**：持续采集外部数据（B站弹幕、语音、屏幕变化、控制台），经 EventBus 以语义域事件（`room.message.*` 等）主动推送，事件拦截器做限流/相似过滤
- **业务 Agent**：主播 Agent 自主决策——MessageBuffer 聚合弹幕 → Planner 决策循环 → Replyer 表达引擎生成回复/情绪/动作；游戏代理（AI 玩家）为另一范式
- **工具（Tool）**：被动能力契约，经 ToolRegistry 统一调度——字幕、VTS/Warudo 皮套、OBS、屏幕感知等约 51 个工具（v2.0.12 起 TTS 已退役出工具池，成为基础模块）
- **TTS 基础设施**：`src/modules/tts/` 包内自治（4 引擎 Provider：`EdgeTTSProvider` / `GPTSoVITSProvider` / `VoiceboxProvider` / `OmniTTSProvider`），由 `core.toml [tts].provider` 装配期单选构造，注入 StreamerAgent 直接调用 `handle_speech`——不走 ToolRegistry
- **存储与记忆**：SQLite 11 表记录场次/消息/礼物/SC/Agenda；SimpleMemory 提供跨场关键词记忆召回——决策上下文自动注入相关记忆，LLM 亦可主动调用 `query_memory` 工具检索

**数据流**：
1. 外部输入 → 采集器 emit `room.message.danmaku/gift/super_chat/enter` → [事件拦截器]
2. 主播 Agent 订阅消费：聚合缓冲 → Planner 判断是否回复（置信度门槛）→ Replyer 生成表达
3. 通过工具调用渲染输出（`reply` 返回 speech/emotion/action，emotion/action 经工具调用驱动皮套/OBS；speech 经发言队列送入装配期注入的 TTS 引擎实例直接播出——v2.0.12 起 TTS 不再走 ToolRegistry）

架构图、完整组件清单与生命周期详见 [架构总览](docs/architecture/overview.md)。

## 安装与运行

### 快速开始

```bash
# 1. 安装 uv
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. 克隆仓库
git clone https://github.com/Mai-with-u/Amaidesu.git
cd Amaidesu

# 3. 同步依赖
uv sync

# 4. 首次运行（自动生成 config/ 目录及默认配置）
uv run python main.py

# 5. 编辑 config/ 目录下的 .toml 文件填入必要配置

# 6. 再次运行
uv run python main.py
```

### 命令行参数

```bash
# 调试模式
uv run python main.py --debug

# 过滤日志（只显示指定模块）
uv run python main.py --filter StreamerAgent ConsoleInputCollector

# 仅验证组合根装配后立即退出
uv run python main.py --dry
```

### Web Dashboard

项目内置 Web 管理界面，有**两种独立运行模式**：

| 端口 | 模式 | 前置条件 | 访问入口 | 适合场景 |
|------|------|---------|---------|---------|
| **60214** | 生产模式 | 需先 `pnpm run build` 生成 `dashboard/dist/` | http://127.0.0.1:60214 | 最终部署 / 给非开发者使用 |
| **60315** | 开发模式 | 需同时运行后端（端口 60214） | http://localhost:60315 | 前端开发 / 调样式 / HMR 热更新 |

#### 方式一：生产模式（单进程）

```bash
cd dashboard && pnpm run build   # 首次或前端改动后执行一次
uv run python main.py           # 一条命令搞定：后端 + 静态前端都在 60214
# 浏览器访问 http://127.0.0.1:60214
```

**注意**：未执行 `pnpm run build` 时，60214 仅提供 API（GET / 返回 JSON），不会显示 WebUI。

#### 方式二：开发模式（双进程）

```bash
# 终端 1：启动后端（提供 API + WebSocket）
uv run python main.py
# → Dashboard 后端运行在 http://127.0.0.1:60214

# 终端 2：启动 Vite 开发服务器（HMR 热更新）
cd dashboard
pnpm install      # 首次需要
pnpm run dev      # → Vite 启动在 http://localhost:60315
```

**浏览器访问 http://localhost:60315**（不是 5173、不是 60214）。

**开发模式说明：**
- Vite 自动代理 `/api` 和 `/ws` 请求到后端 60214
- 修改 `dashboard/src/**` 下的 .vue / .ts / .css 文件后浏览器自动热更新（无需刷新）
- 修改 `src/**/*.py`（后端）或 `config/*.toml` 需要重启主程序
- 只跑 `pnpm run dev` 而不跑 `uv run python main.py` 会因 WebSocket/API 无法连接而无法使用

详见 [快速开始](docs/getting-started.md)

## 文档导航

### 新手入门
- [快速开始](docs/getting-started.md) - 环境搭建和基本使用
- [开发规范](docs/development-guide.md) - 代码风格和约定

### 架构理解
- [v2.0.0 架构叙事](docs/architecture/v2-architecture.md) - 重构缘由与设计推导（先读这篇）
- [架构总览](docs/architecture/overview.md) - v2.0.0 组件清单与目录结构
- [数据流规则](docs/architecture/data-flow.md) - 数据流约束
- [事件系统](docs/architecture/event-system.md) - EventBus 使用
- [事件命名规范](docs/architecture/event-naming-convention.md) - 事件命名规则
- [架构决策记录](docs/architecture/adr/README.md) - ADR 决策清单

### 开发指南
- [组件开发指南](docs/development/component-guide.md) - 采集器/工具/Agent 三范式
- [事件系统](docs/architecture/event-system.md#事件拦截器interceptor) - 事件拦截器开发
- [提示词管理](docs/development/prompt-management.md)
- [依赖注入](docs/development/dependency-injection.md)
- [测试指南](docs/development/testing-guide.md)
- [文档维护规范](docs/development/documentation-guide.md)

## Git 工作流

- **主分支**：`main`
- **提交规范**：使用 Conventional Commits（feat/fix/docs/refactor 等）
