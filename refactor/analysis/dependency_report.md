# Amaidesu 依赖关系报告

## 1. 外部依赖分析

### 1.1 Python 依赖（requirements.txt）

```
aiohttp                          # 异步 HTTP 客户端/服务器
fastapi                          # 现代 Web 框架
loguru                           # 结构化日志库
pydantic                         # 数据验证库
pyvts                            # VTube Studio API 客户端
setuptools                       # Python 打包工具
uvicorn                          # ASGI 服务器（配合 FastAPI）
ruff                             # 代码检查和格式化工具
requests                         # HTTP 请求库
websockets>=10.0                 # WebSocket 客户端/服务器
python-logging-loki              # Grafana Loki 日志集成
typer                            # CLI 工具库
edge-tts                         # Edge 浏览器 TTS
mss                              # 屏幕截图库
numpy                            # 数值计算库
dashscope                        # 阿里云通义千问 SDK
openai                           # OpenAI API 客户端
Pillow                           # 图像处理库
sounddevice                      # 音频播放库
obsws-python                     # OBS WebSocket 客户端
soundfile                        # 音频文件读写
pygame                           # 游戏库（可选，用于音频播放）
toml                             # TOML 解析库（Python < 3.11）
torch                            # PyTorch 深度学习框架
tomli                            # TOML 解析库（Python 3.11+）
torchaudio                       # PyTorch 音频处理
maim-message                     # MaiCore 消息库
customtkinter                    # 现代 Tkinter GUI 库
librosa                          # 音频分析库
scipy                            # 科学计算库
pyautogui                        # GUI 自动化库
python-osc                       # OSC 协议库
```

### 1.2 依赖分类

#### 1.2.1 核心依赖（必需）

| 依赖 | 用途 | 版本 | 重要性 |
|------|------|------|--------|
| aiohttp | 异步 HTTP 和 WebSocket | >=3.8.1 | 极高 |
| websockets | WebSocket 通信 | >=10.0 | 极高 |
| maim-message | MaiCore 消息协议 | - | 极高 |
| loguru | 日志系统 | - | 高 |
| asyncio | 异步编程 | Python 3.10+ | 极高 |
| typing | 类型提示 | Python 3.10+ | 高 |

#### 1.2.2 Web 服务器依赖（可选）

| 依赖 | 用途 | 版本 | 重要性 |
|------|------|------|--------|
| fastapi | Web 框架 | - | 中 |
| uvicorn | ASGI 服务器 | - | 中 |
| pydantic | 数据验证 | - | 中 |

#### 1.2.3 虚拟形象控制依赖

| 依赖 | 用途 | 版本 | 重要性 |
|------|------|------|--------|
| pyvts | VTube Studio 控制 | - | 高 |
| python-osc | OSC 协议（Warudo 控制） | - | 高 |
| obsws-python | OBS 控制 | - | 中 |

#### 1.2.4 音频/语音依赖

| 依赖 | 用途 | 版本 | 重要性 |
|------|------|------|--------|
| sounddevice | 音频播放 | - | 中 |
| soundfile | 音频文件读写 | - | 中 |
| edge-tts | Edge TTS | - | 中 |
| pygame | 音频播放（可选） | - | 低 |
| librosa | 音频分析 | - | 中 |
| scipy | 音频处理 | - | 中 |
| torchaudio | 音频处理（PyTorch） | - | 低 |

#### 1.2.5 AI/LLM 依赖

| 依赖 | 用途 | 版本 | 重要性 |
|------|------|------|--------|
| openai | OpenAI API | - | 高 |
| dashscope | 阿里云通义千问 | - | 中 |
| torch | PyTorch 深度学习 | - | 低 |
| torchaudio | 音频处理 | - | 低 |
| numpy | 数值计算 | - | 中 |

#### 1.2.6 其他工具依赖

| 依赖 | 用途 | 版本 | 重要性 |
|------|------|------|--------|
| requests | HTTP 请求 | - | 中 |
| Pillow | 图像处理 | - | 中 |
| mss | 屏幕截图 | - | 中 |
| pyautogui | GUI 自动化 | - | 中 |
| customtkinter | GUI | - | 低 |
| typer | CLI 工具 | - | 低 |
| toml/tomli | TOML 解析 | - | 中 |
| python-logging-loki | 日志聚合 | - | 低 |

### 1.3 依赖健康度分析

#### 1.3.1 依赖版本管理

**问题**:
- 大部分依赖没有指定版本号
- 缺少版本锁定机制
- 容易出现依赖冲突

**建议**:
- 使用 `requirements-lock.txt` 锁定版本
- 使用 `pip-tools` 管理依赖
- 定期更新依赖并测试

#### 1.3.2 依赖安全性

**问题**:
- 缺少依赖安全检查
- 没有使用依赖审计工具
- 没有定期更新依赖

**建议**:
- 使用 `pip-audit` 检查依赖漏洞
- 使用 `safety` 检查安全问题
- 定期更新依赖

#### 1.3.3 依赖冗余

**问题**:
- `aiohttp` 和 `fastapi` 可能存在功能重叠
- `pygame` 和 `sounddevice` 功能重叠
- `toml` 和 `tomli` 功能重叠

**建议**:
- 评估并移除冗余依赖
- 统一使用一个依赖

---

## 2. 内部模块依赖分析

### 2.1 核心模块依赖关系

```
┌─────────────────────────────────────────────────────────────┐
│                        AmaidesuCore                           │
│  (中央枢纽，协调所有模块)                                      │
└────┬──────────┬──────────┬──────────┬──────────┬───────────┘
     │          │          │          │          │
     ▼          ▼          ▼          ▼          ▼
┌─────────┐ ┌──────┐ ┌─────┐ ┌─────┐ ┌──────────┐
│Plugin   │ │Pipeline│ │Event│ │Context│ │Avatar    │
│Manager  │ │Manager│ │ Bus │ │Manager│ │Manager   │
└────┬────┘ └───┬──┘ └──┬──┘ └──┬──┘ └────┬─────┘
     │          │       │       │          │
     ▼          ▼       │       │          ▼
┌─────────┐ ┌────┐     │   ┌────┐    ┌──────────┐
│Plugins  │ │Pipe│     │   │LLM │    │VTS/Warudo│
│(23个)   │ │lines│     │   │Mgr │    │Adapters  │
└─────────┘ └────┘     │   └────┘    └──────────┘
                      │
                      ▼
                 ┌──────────┐
                 │MaiCore   │
                 │(外部)    │
                 └──────────┘
```

### 2.2 核心模块详细依赖

#### 2.2.1 AmaidesuCore

**依赖**:
- `PipelineManager`: 管道管理
- `ContextManager`: 上下文管理
- `EventBus`: 事件总线
- `AvatarControlManager`: 虚拟形象控制
- `LLMClientManager`: LLM 客户端管理

**被依赖**:
- 所有插件（通过 `self.core`）
- 所有管道（通过 `self.core`）

#### 2.2.2 PluginManager

**依赖**:
- `AmaidesuCore`: 核心实例
- `BasePlugin`: 插件基类

**被依赖**:
- `main.py`: 加载插件

#### 2.2.3 PipelineManager

**依赖**:
- `AmaidesuCore`: 核心实例
- `MessagePipeline`: 管道基类

**被依赖**:
- `AmaidesuCore`: 管道执行

#### 2.2.4 EventBus

**依赖**:
- 无（独立组件）

**被依赖**:
- `AmaidesuCore`: 事件总线
- `BasePlugin`: 事件发布和订阅

#### 2.2.5 ContextManager

**依赖**:
- 无（独立组件）

**被依赖**:
- `AmaidesuCore`: 上下文管理
- 多个插件：上下文提供者

#### 2.2.6 LLMClientManager

**依赖**:
- `LLMClient`: LLM 客户端
- `ModelConfig`: 模型配置

**被依赖**:
- `AmaidesuCore`: LLM 客户端管理
- `BasePlugin`: LLM 客户端获取

#### 2.2.7 AvatarControlManager

**依赖**:
- `AdapterBase`: 虚拟形象适配器基类
- `LLMExecutor`: LLM 执行器

**被依赖**:
- `AmaidesuCore`: 虚拟形象控制

---

## 3. 插件依赖分析

### 3.1 插件间依赖关系（基于服务注册）

#### 3.1.1 核心服务依赖

| 服务名 | 提供者 | 依赖者 |
|--------|--------|--------|
| prompt_context | ContextManager | bili_danmaku, read_pingmu, vtube_studio |
| text_cleanup | LLMTextProcessor | tts, stt |
| vts_control | VTubeStudio | sticker, emotion_judge |
| subtitle_service | Subtitle | tts |

#### 3.1.2 插件依赖图

```
ContextManager (核心服务)
    ├── bili_danmaku (使用)
    ├── read_pingmu (使用)
    └── vtube_studio (使用)

LLMTextProcessor (文本处理服务)
    ├── stt (使用 stt_correction)
    └── tts (使用 text_cleanup)

VTubeStudio (VTS 控制服务)
    ├── emotion_judge (使用)
    └── sticker (使用)

Subtitle (字幕服务)
    └── tts (使用)
```

### 3.2 插件依赖问题

#### 3.2.1 循环依赖风险

**潜在循环**:
- `stt` → `LLMTextProcessor` → 可能依赖 `tts` → `Subtitle`

**现状**: 未发现明显循环依赖

**建议**:
- 明确声明插件依赖
- 实现依赖检测机制
- 控制插件加载顺序

#### 3.2.2 软依赖（可选依赖）

**软依赖示例**:
- 大多数插件软依赖 `EventBus`
- 大多数插件软依赖 `LLMClientManager`
- 部分插件软依赖 `AvatarControlManager`

**处理方式**:
- 代码中检查服务是否存在
- 不存在时记录警告
- 降级或禁用相关功能

#### 3.2.3 缺少依赖声明

**问题**:
- 插件没有声明依赖关系
- 依赖关系隐式硬编码在代码中
- 难以管理插件加载顺序

**建议**:
- 在插件配置中声明依赖
- 实现依赖解析器
- 自动确定插件加载顺序

---

## 4. 管道依赖分析

### 4.1 管道间依赖

**当前状态**: 管道间无直接依赖

**管道执行顺序**:
- 按优先级排序
- 独立执行，不依赖其他管道

### 4.2 管道与插件依赖

**无直接依赖**:
- 管道独立于插件
- 通过 `AmaidesuCore` 间接通信
- 管道可以访问 `core` 实例

---

## 5. 事件依赖分析

### 5.1 事件流分析

#### 5.1.1 已知事件

| 事件名 | 发布者 | 订阅者 |
|--------|--------|--------|
| command_router.received | CommandRouter | 未知 |
| avatar.expression.changed | AvatarManager | 未知 |
| llm_response.received | 未知 | 未知 |

#### 5.1.2 事件流示例

```
ConsoleInput (插件)
    └── AmaidesuCore.send_to_maicore()
        └── MaiCore
            └── AmaidesuCore._handle_maicore_message()
                ├── CommandRouter (管道)
                │   └── event_bus.emit("command_router.received", ...)
                │
                ├── AvatarManager
                │   └── event_bus.emit("avatar.expression.changed", ...)
                │
                └── Plugins (通过消息处理器)
                    ├── TTS
                    │   └── event_bus.emit("tts.started", ...)
                    │
                    ├── Subtitle
                    │   └── event_bus.emit("subtitle.updated", ...)
                    │
                    └── Sticker
                        └── event_bus.emit("sticker.triggered", ...)
```

### 5.2 事件依赖问题

#### 5.2.1 事件命名不统一

**问题**:
- 没有统一的事件命名约定
- 容易造成事件名冲突
- 难以追踪事件流

**示例**:
- `command_router.received` (使用 `.` 分隔）
- `avatar_expression_changed` (使用 `_` 分隔）

**建议**:
- 定义事件命名规范（如 `module.action.detail`）
- 提供事件文档
- 实现事件命名检查

#### 5.2.2 事件顺序不保证

**问题**:
- 事件并发执行，不保证顺序
- 可能导致状态不一致

**建议**:
- 提供事件顺序保证选项
- 实现事件序列化执行
- 文档化事件顺序要求

#### 5.2.3 事件错误隔离

**现状**: 事件处理器错误不会影响其他处理器

**问题**:
- 难以调试
- 可能导致部分功能失效

**建议**:
- 提供错误处理策略选项
- 支持错误传播
- 改进错误日志

---

## 6. 配置依赖分析

### 6.1 配置层级

```
全局配置 (config.toml)
    ├── [plugins.{plugin}] → 覆盖插件配置
    ├── [pipelines.{pipeline}] → 覆盖管道配置
    └── [llm], [llm_fast], [vlm] → LLM 配置

组件配置
    ├── src/plugins/{plugin}/config.toml
    ├── src/pipelines/{pipeline}/config.toml
    └── 代码默认值
```

### 6.2 配置合并优先级

**插件配置**:
1. 全局配置 `[plugins.{plugin}]` (最高优先级)
2. 插件独立配置 `src/plugins/{plugin}/config.toml`
3. 代码默认值 (最低优先级)

**管道配置**:
1. 全局配置 `[pipelines.{pipeline}]` (最高优先级)
2. 管道独立配置 `src/pipelines/{pipeline}/config.toml`
3. 代码默认值 (最低优先级)

### 6.3 配置依赖问题

#### 6.3.1 配置验证不足

**问题**:
- 配置验证不完善
- 可能在运行时才发现配置错误
- 缺少配置提示

**建议**:
- 实现配置验证
- 提供配置错误提示
- 支持配置模板

#### 6.3.2 配置合并复杂

**问题**:
- 配置合并逻辑复杂
- 容易出现合并错误
- 难以调试配置问题

**建议**:
- 简化配置模型
- 提供配置可视化工具
- 改进配置日志

#### 6.3.3 配置热重载缺失

**问题**:
- 修改配置需要重启
- 用户体验差
- 无法动态调整

**建议**:
- 实现配置热重载
- 监控配置文件变化
- 优雅地重载配置

---

## 7. 依赖管理建议

### 7.1 外部依赖管理

1. **版本锁定**
   - 创建 `requirements-lock.txt`
   - 使用 `pip-tools` 管理依赖
   - 定期更新依赖

2. **安全检查**
   - 使用 `pip-audit` 检查漏洞
   - 使用 `safety` 检查安全问题
   - 定期更新依赖

3. **依赖清理**
   - 移除未使用的依赖
   - 统一功能重叠的依赖
   - 优化依赖数量

### 7.2 插件依赖管理

1. **依赖声明**
   - 在插件配置中声明依赖
   - 支持可选依赖
   - 支持版本约束

2. **依赖解析**
   - 实现依赖解析器
   - 检测循环依赖
   - 确定加载顺序

3. **依赖隔离**
   - 支持插件沙盒
   - 限制插件访问
   - 提供依赖注入

### 7.3 事件依赖管理

1. **事件规范**
   - 定义事件命名规范
   - 提供事件文档
   - 实现事件类型检查

2. **事件调试**
   - 提供事件追踪
   - 记录事件流
   - 支持事件监控

3. **事件优化**
   - 支持事件批处理
   - 优化事件分发
   - 减少事件开销

---

## 8. 总结

### 8.1 依赖健康度评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 外部依赖管理 | 5/10 | 缺少版本锁定和安全检查 |
| 内部模块依赖 | 7/10 | 结构清晰，但耦合度较高 |
| 插件依赖管理 | 4/10 | 缺少依赖声明和管理 |
| 管道依赖管理 | 8/10 | 独立性好，无依赖 |
| 事件依赖管理 | 5/10 | 缺少规范和调试工具 |
| 配置依赖管理 | 6/10 | 配置复杂，热重载缺失 |

**总体评分**: 5.8/10

### 8.2 优先改进项

1. **外部依赖版本锁定** (优先级: 高)
   - 创建 `requirements-lock.txt`
   - 使用 `pip-tools` 管理依赖
   - 定期更新依赖

2. **插件依赖声明** (优先级: 高)
   - 在插件配置中声明依赖
   - 实现依赖解析器
   - 确定加载顺序

3. **事件规范** (优先级: 中)
   - 定义事件命名规范
   - 提供事件文档
   - 实现事件追踪

4. **配置简化** (优先级: 中)
   - 简化配置模型
   - 提供配置验证
   - 改进配置日志

5. **安全检查** (优先级: 低)
   - 使用 `pip-audit` 检查漏洞
   - 定期更新依赖
   - 修复安全问题

### 8.3 下一步行动

1. 实现 `requirements-lock.txt`
2. 实现插件依赖声明机制
3. 定义事件命名规范
4. 简化配置模型
5. 增加依赖安全检查
