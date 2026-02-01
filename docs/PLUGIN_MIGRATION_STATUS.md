# 插件迁移状态分析

本文档分析当前插件在新重构架构下的迁移状态：**插件协议层** 与 **主流程接线** 两部分。

---

## 一、插件协议层：已全部迁移

所有插件的 **入口** 均已采用新 Plugin 协议：

| 要求           | 状态 |
|----------------|------|
| 不继承 BasePlugin | ✅ 无插件继承 BasePlugin |
| `__init__(config)` | ✅ 全部为 `__init__(self, config)` |
| `setup(event_bus, config)` 返回列表 | ✅ 全部符合，返回 `List[Any]` 或空列表 |
| `cleanup()`     | ✅ 全部实现 |
| `get_info()`    | ✅ 全部实现 |
| `plugin_entrypoint` | ✅ 各插件在 plugin.py 中定义 |

因此 **从“是否还用旧插件接口”的角度，没有未迁移的插件**。

---

## 二、主流程接线：未完全接好

新架构期望：插件在 `setup()` 中 **创建并返回** Provider 列表，由主流程交给对应的 Manager 统一启动/停止。

当前主流程的实际情况：

### 2.1 PluginManager 丢弃了插件返回的 Provider

- **位置**：`src/core/plugin_manager.py` 约 171–175 行  
- **行为**：`providers = await plugin_instance.setup(...)` 后只打日志，**没有**把 `providers` 交给 InputProviderManager 或 OutputProviderManager。
- **结果**：插件返回的 InputProvider / OutputProvider 列表被丢弃，主流程从未使用这些实例。

### 2.2 InputProvider 未被统一启动

- **main.py**：未创建 `InputProviderManager`，未调用 `start_all_providers()`。
- **InputLayer**：可接受可选的 `input_provider_manager`，但当前未使用，且 main 也未传入。
- **结果**：  
  - **能工作的输入**：只有像 `console_input` 这种 **不返回 Provider**、在插件内部自己起任务并直接发事件的插件。  
  - **不能工作的输入**：凡 **只返回 InputProvider 列表**、依赖 Manager 启动的插件，其 Provider 从未被 `start()`，例如：
    - `bili_danmaku`
    - `bili_danmaku_official` / `bili_danmaku_official_maicraft`
    - `mock_danmaku`
    - `mainosaba`
    - `screen_monitor`（若返回 InputProvider）
    - 其他仅“返回 InputProvider”的插件  

这些插件从“协议”上已迁移，但从“主流程是否真的用上”的角度是 **未接好线**。

### 2.3 OutputProvider 双轨：用配置创建，不用插件返回

- **FlowCoordinator** → **OutputProviderManager** 通过 `load_from_config(config)` 从配置创建 OutputProvider（`ProviderRegistry.create_output(...)`）。
- 插件在 `setup()` 里创建的 OutputProvider 实例被 PluginManager 丢弃，**从未**交给 OutputProviderManager。
- **结果**：输出层能工作，是因为 **配置 + ProviderRegistry** 在创建实例，而不是因为插件返回的 Provider 被使用；插件返回的 OutputProvider 属于“未接好线”。

### 2.4 仍依赖 core 的插件子模块（局部未完全解耦）

部分插件或其子模块仍使用 `core`（如 `core.platform`、`core.get_context_manager()`、`core.event_bus` 等），属于对旧“通过 core 拿服务”的残留依赖，需在后续迁移中改为事件或显式注入：

| 位置 | 依赖 |
|------|------|
| `bili_danmaku_official/message/base.py` | `core.platform`, `core.get_context_manager()` |
| `bili_danmaku_official_maicraft/message/base.py` | `core.platform` |
| `keyword_action/actions/dg_lab_shock.py` | `core.event_bus`, `core.logger`（动作脚本被调用时传入 core） |

---

## 三、按插件类型的“未迁移”总结

| 类型 | 协议层 | 主流程接线 | 说明 |
|------|--------|------------|------|
| **不返回 Provider、自起任务发事件**（如 console_input） | ✅ 已迁移 | ✅ 有效 | 不依赖 Manager 启动，当前即可用。 |
| **仅返回 InputProvider**（bili_danmaku、bili_danmaku_official、mock_danmaku、mainosaba 等） | ✅ 已迁移 | ❌ 未接线 | 返回的 Provider 未被交给 InputProviderManager，也未被启动，**功能上未迁移完成**。 |
| **返回 OutputProvider**（tts、subtitle、sticker、vtube_studio、gptsovits_tts、omni_tts 等） | ✅ 已迁移 | ⚠️ 双轨 | 实际用的是 config + ProviderRegistry 创建的实例；插件返回的实例被丢弃，但输出层整体可用。 |
| **返回 DecisionProvider**（如 emotion_judge） | ✅ 已迁移 | ⚠️ 待确认 | 需确认 DecisionManager 是否从插件拿 Provider 还是仅从配置创建。 |

---

## 四、建议的后续改动（概要）

1. **主流程接好 InputProvider**  
   - 在 main 中创建 `InputProviderManager`，在插件加载完成后，从 PluginManager 收集所有插件返回的 **InputProvider**，交给 InputProviderManager，并调用 `start_all_providers()`；关闭时调用 `stop_all_providers()`。  
   - 可选：将 InputProviderManager 传入 InputLayer，便于统计或按 source 查 Provider。

2. **统一 OutputProvider 来源（二选一或过渡）**  
   - **方案 A**：主流程改为使用插件返回的 OutputProvider（PluginManager 把 OutputProvider 列表交给 OutputProviderManager，不再仅靠 load_from_config 创建）。  
   - **方案 B**：保持当前“仅由 config + ProviderRegistry 创建”，则明确文档说明“插件返回的 OutputProvider 仅用于兼容/扩展，实际渲染实例以配置为准”。

3. **去除对 core 的残留依赖**  
   - 在 bili_danmaku_official、keyword_action 等处，用 event_bus / 显式注入的 context_manager、platform 等替代 `core.xxx`，使插件与 core 解耦。

完成以上步骤后，“未迁移”将仅剩与设计选择相关的清理（例如是否完全采用“插件返回的 Provider”作为唯一来源），而不会再有“协议已新、但主流程不用”的未接线插件。
