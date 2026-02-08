# VTuber 全流程 E2E 测试缺口分析

本文档分析：要**跑通并测试整个 VTuber 流程**（输入 → 标准化 → 决策 → 参数生成 → 渲染），还缺少哪些 Provider、主流程接线和测试代码。

---

## 一、当前数据流（事件链）

```
1. InputProvider 产生 RawData
   → InputProviderManager 发布 perception.raw_data.generated

2. InputLayer 订阅 perception.raw_data.generated
   → 转为 NormalizedMessage → 发布 normalization.message_ready

3. DecisionManager 订阅 normalization.message_ready
   → DecisionProvider.decide() → 发布 decision.intent_generated（带 Intent）

4. FlowCoordinator 订阅 decision.intent_generated
   → ExpressionGenerator.generate(intent) → 发布 expression.parameters_generated（ExpressionParameters）

5. 各 OutputProvider 订阅 expression.parameters_generated
   → render(parameters)
```

---

## 二、已有组件一览

| 层级 | 组件 | 状态 |
|------|------|------|
| **Layer 1 输入** | ConsoleInputProvider, MockDanmakuProvider, BiliDanmakuInputProvider | ✅ 已实现 |
| **Layer 1 管理** | InputProviderManager（start_all_providers / stop_all_providers） | ✅ 已实现 |
| **Layer 2** | InputLayer（RawData → NormalizedMessage） | ✅ 已实现 |
| **Layer 3 决策** | MaiCoreDecisionProvider, RuleEngineDecisionProvider, LocalLLMDecisionProvider | ✅ 已实现 |
| **Layer 3 管理** | DecisionManager（订阅 normalization.message_ready，发布 decision.intent_generated） | ✅ 已实现 |
| **Layer 4-5** | ExpressionGenerator, FlowCoordinator, OutputProviderManager | ✅ 已实现 |
| **Layer 7 渲染** | TTSProvider, SubtitleProvider, StickerOutputProvider, VTSProvider, OmniTTSProvider, AvatarOutputProvider | ✅ 已实现（含 Registry） |

---

## 三、缺口 1：主流程未接输入层（必须补）

**现象**：`main.py` 未创建 `InputProviderManager`，也未在任何地方调用 `start_all_providers()`。

- `InputLayer` 只订阅了 `perception.raw_data.generated`，但**没有任何东西在生产环境发布该事件**（除非插件内部自己发，且插件目录 `src/plugins` 当前不存在，只有 `plugins_backup`）。
- 因此**整条链路从第一步就断掉**：没有 RawData → 没有 NormalizedMessage → 决策和渲染都不会被触发。

**需要补的代码**：

1. **main.py**
   - 创建 `InputProviderManager(event_bus)`。
   - 从配置或插件得到 InputProvider 列表（见下）。
   - 将 `InputProviderManager` 传给 `InputLayer`（可选，便于统计）。
   - 在 `core.connect()` 之后（或合适位置）调用  
     `await input_provider_manager.start_all_providers(providers)`。
   - 在 `run_shutdown()` 中调用  
     `await input_provider_manager.stop_all_providers()`。

2. **InputProvider 来源二选一（或先做一种）**
   - **方案 A：从配置 [input] 创建**（类似 OutputProviderManager.load_from_config）
     - 在 config 中增加 `[input]`，例如 `inputs = ["console", "mock_danmaku"]`，以及 `[input.outputs.console]`、`[input.outputs.mock_danmaku]` 等。
     - 实现 `InputProviderRegistry` + `OutputProviderManager.load_from_config` 的等价物：根据 `inputs` 列表从配置创建 InputProvider 实例，交给 InputProviderManager。
   - **方案 B：从插件收集**
     - 恢复或迁移插件到 `src/plugins`，在 `PluginManager.load_plugins()` 里收集插件返回的 `InputProvider` 列表，交给 main 中创建的 `InputProviderManager`，再 `start_all_providers(providers)`。
   - 文档 [PLUGIN_MIGRATION_STATUS.md](PLUGIN_MIGRATION_STATUS.md) 中已说明：当前插件返回的 Provider 被丢弃，需要接好这条线。

---

## 四、缺口 2：配置缺失 [decision] / [rendering]（必须补）

**现象**：`create_app_components()` 使用 `config.get("decision", {})` 和 `config.get("rendering", {})`。若 `config.toml` 中没有这两段，则：

- `decision_manager = None` → 无人订阅 `normalization.message_ready`，也不会发布 `decision.intent_generated`。
- `flow_coordinator = None` → 无人订阅 `decision.intent_generated`，也不会发布 `expression.parameters_generated`。

**需要补的配置**（可参考 `config-template.toml`）：

- 在 **config.toml** 中增加：
  - **`[decision]`**：至少包含 `provider = "maicore"`（或测试用 `rule_engine`）以及对应 `[decision.maicore]` / `[decision.rule_engine]`。
  - **`[rendering]`**：至少包含 `enabled = true`、`outputs = ["subtitle", "vts", ...]` 以及 `[rendering.outputs.xxx]` 等（与现有 OutputProviderManager.load_from_config 一致）。

这样主流程才会创建 DecisionManager 和 FlowCoordinator，整条事件链才会从决策到渲染都接通。

---

## 五、缺口 3：E2E 测试用 Mock 与用例

要在**不依赖 MaiCore、不依赖真实 TTS/VTS** 的情况下测试整条 VTuber 流程，建议补充以下内容。

### 1. MockDecisionProvider（推荐新增）

- **作用**：不连 WebSocket，对任意 `NormalizedMessage` 返回固定或简单规则生成的 `Intent`，便于 E2E 只测“链路是否跑通”。
- **实现要点**：
  - 实现 `DecisionProvider` 接口：`decide(normalized_message) -> Intent`。
  - 可配置：例如固定回复文案、固定情感类型；或简单关键词 → Intent 映射。
- **注册**：在测试或 main 的 DecisionProviderFactory 中 `register("mock", MockDecisionProvider)`，测试时 `config["decision"]["provider"] = "mock"`。

若不想新增类，也可在 E2E 里直接使用已有的 **RuleEngineDecisionProvider**（配一个简单 rules 文件），只要不依赖外部服务即可。

### 2. MockOutputProvider（可选）

- **作用**：只订阅 `expression.parameters_generated`，把收到的 `ExpressionParameters` 追加到列表，测试里对列表做 assert（例如是否收到、条数、关键字段）。
- **实现要点**：
  - 继承 `OutputProvider`，在 `setup()` 里 `event_bus.on("expression.parameters_generated", self._on_params)`。
  - `_on_params` 里把 event_data 中的参数 append 到 `self.received_params`。
  - `render()` 可空实现或仅记录。

这样 E2E 不需要真实 TTS/VTS 即可验证“决策 → 参数生成 → 输出层收到参数”这一段。

### 3. E2E 测试用例（推荐新增）

- **目标**：一条用例跑通整条事件链。
- **步骤建议**：
  1. 搭建：EventBus、InputLayer、DecisionManager（使用 MockDecisionProvider 或 RuleEngineDecisionProvider）、FlowCoordinator（可挂 1 个 MockOutputProvider）。
  2. 启动：InputLayer.setup()、DecisionManager 订阅、FlowCoordinator.setup() + start()、MockOutputProvider.setup()。
  3. 触发输入（二选一）：
     - 直接 `await event_bus.emit("perception.raw_data.generated", {"data": raw_data, "source": "test"}, source="test")`；
     - 或启动 MockDanmakuProvider + InputProviderManager，等一条模拟弹幕。
  4. 等待/断言：
     - 可等待 `decision.intent_generated` 或 `expression.parameters_generated` 被调用（例如用 asyncio.Event 或 mock 回调计数）；
     - 断言 MockOutputProvider 的 `received_params` 非空且包含预期字段（如 `tts_text`、`subtitle_text`）。
- **位置建议**：例如 `tests/e2e/test_vtuber_flow_e2e.py` 或 `tests/layers/test_full_flow_integration.py`。

---

## 六、清单：要编写/修改的内容汇总

| 序号 | 类型 | 内容 | 优先级 |
|------|------|------|--------|
| 1 | 主流程 | main.py：创建 InputProviderManager，从配置或插件获取 InputProvider 列表，在 connect 时 start_all_providers，在 shutdown 时 stop_all_providers | **必须** |
| 2 | 配置 | 实现“从 [input] 配置创建 InputProvider”的逻辑（或先只接插件返回的 InputProvider） | **必须**（若采用配置方案） |
| 3 | 配置 | config.toml：增加 [decision] 与 [rendering]（若当前没有） | **必须** |
| 4 | 测试/决策 | MockDecisionProvider，或 E2E 中改用 RuleEngineDecisionProvider + 简单规则 | **E2E 必须** |
| 5 | 测试/渲染 | MockOutputProvider（只记录收到的 ExpressionParameters） | 可选 |
| 6 | 测试 | E2E 用例：EventBus + InputLayer + Decision(Mock) + FlowCoordinator + MockOutput，发 RawData，断言输出层收到参数 | **推荐** |

完成 1、3 后，主流程即可在“有输入源”的前提下跑通整条 VTuber 链路；再补 4、6（及可选 5）即可在不依赖 MaiCore 和真实设备的情况下做 E2E 测试。
