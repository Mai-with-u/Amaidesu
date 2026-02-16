# 事件命名规范

本文档定义了 Amaidesu 项目中事件的命名规范，确保代码库中所有事件名称的一致性和可维护性。

## 命名格式

事件名称遵循以下格式：

```
{domain}.{component}.{action}
```

### 组成部分

| 部分 | 说明 | 示例 |
|------|------|------|
| `domain` | **必须** - 事件所属的域 | `input`, `decision`, `output`, `core` |
| `component` | **可选** - 产生事件的组件 | `obs`, `remote_stream`, `provider` |
| `action` | **必须** - 动作或状态描述 | `ready`, `generated`, `connected`, `send_text` |

### 分隔符

- **统一使用点号 (`.`)** 作为分隔符
- **不使用下划线 (`_`)** 作为分隔符
- 组件和动作内部可以使用下划线（如 `send_text`）

## 域前缀定义

| 域 | 前缀 | 说明 | 发布者 |
|----|------|------|--------|
| **Input Domain** | `input.` | 数据采集、消息就绪 | InputProvider |
| **Decision Domain** | `decision.` | 决策处理、意图生成 | DecisionProvider |
| **Output Domain** | `output.` | 渲染、输出控制 | OutputProviderManager, OutputProvider |
| **Core** | `core.` | 系统启动、关闭、错误 | 系统核心 |

## 常量命名规范

在 `CoreEvents` 类中定义事件常量时：

1. **使用全大写字母**
2. **使用下划线连接单词**
3. **前缀与域对应**

```python
class CoreEvents:
    # Input Domain
    INPUT_MESSAGE_READY = "input.message.ready"

    # Decision Domain
    DECISION_INTENT_GENERATED = "decision.intent.generated"
    DECISION_PROVIDER_CONNECTED = "decision.provider.connected"

    # Output Domain
    OUTPUT_INTENT_READY = "output.intent.ready"
    OUTPUT_OBS_SEND_TEXT = "output.obs.send_text"
```

## 当前事件列表

### Input Domain

| 常量 | 值 | 说明 |
|------|-----|------|
| `INPUT_MESSAGE_READY` | `input.message.ready` | 标准化消息就绪，由 InputProvider 发布 |

### Decision Domain

| 常量 | 值 | 说明 |
|------|-----|------|
| `DECISION_INTENT_GENERATED` | `decision.intent.generated` | 意图生成完成，由 DecisionProvider 发布 |
| `DECISION_PROVIDER_CONNECTED` | `decision.provider.connected` | DecisionProvider 连接成功 |
| `DECISION_PROVIDER_DISCONNECTED` | `decision.provider.disconnected` | DecisionProvider 断开连接 |

### Output Domain

| 常量 | 值 | 说明 |
|------|-----|------|
| `OUTPUT_INTENT_READY` | `output.intent.ready` | 过滤后意图就绪，由 OutputProviderManager 发布 |
| `OUTPUT_OBS_SEND_TEXT` | `output.obs.send_text` | OBS 发送文本 |
| `OUTPUT_OBS_SWITCH_SCENE` | `output.obs.switch_scene` | OBS 切换场景 |
| `OUTPUT_OBS_SET_SOURCE_VISIBILITY` | `output.obs.set_source_visibility` | OBS 设置源可见性 |
| `OUTPUT_REMOTE_STREAM_REQUEST_IMAGE` | `output.remote_stream.request_image` | 远程流请求图像 |

## 数据流

```
Input Domain          Decision Domain         Output Domain
    │                      │                       │
    │ INPUT_MESSAGE_READY │                       │
    ├──────────────────────►                       │
    │                      │                       │
    │                      │ DECISION_INTENT_     │
    │                      │ GENERATED            │
    │                      ├──────────────────────►
    │                      │                       │
    │                      │                       │ OUTPUT_INTENT_READY
    │                      │                       ├──► OutputProviders
```

## 添加新事件

添加新事件时，请遵循以下步骤：

1. **确定域前缀**：根据事件的发布者确定域前缀
2. **确定组件名**（可选）：如果事件来自特定组件，添加组件名
3. **确定动作名**：描述事件代表的动作或状态
4. **在 `names.py` 中添加常量**：
   ```python
   # 在 CoreEvents 类中添加
   OUTPUT_NEW_ACTION = "output.new_component.new_action"
   ```
5. **在 `registry.py` 中注册**：
   ```python
   EventRegistry.register_core_event(
       CoreEvents.OUTPUT_NEW_ACTION,
       NewActionPayload,
   )
   ```
6. **创建对应的 Payload 类**（如需要）

## 最佳实践

1. **使用常量**：始终使用 `CoreEvents` 中的常量，不要使用字符串字面量
2. **保持一致性**：新增事件时参考现有命名模式
3. **简洁明了**：名称应清晰表达事件的含义
4. **避免过度嵌套**：最多使用 3 层（domain.component.action）

## 迁移历史

### 2026-02-16：事件命名规范化重构

- `data.message` → `input.message.ready`
- `decision.intent` → `decision.intent.generated`
- `output.intent` → `output.intent.ready`
- `obs.*` → `output.obs.*`
- `remote_stream.*` → `output.remote_stream.*`
- 删除了 22 个未使用的事件

---

*最后更新：2026-02-16*
