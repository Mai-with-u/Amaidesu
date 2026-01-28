# 配置迁移指南

本文档指导用户如何从旧配置格式迁移到新的Provider配置结构。

## 目录

- [概述](#概述)
- [配置结构对比](#配置结构对比)
- [迁移步骤](#迁移步骤)
- [详细配置说明](#详细配置说明)
- [常见问题](#常见问题)
- [配置验证](#配置验证)

---

## 概述

### 为什么迁移？

新的Provider配置结构具有以下优势：

1. **统一管理**：所有Provider（Input、Output、Decision）在统一的 `[providers.*]` 节点下管理
2. **清晰的层级**：按照系统架构层级组织配置（Layer 1: Perception, Layer 6: Rendering, Layer 3.5: Decision）
3. **更好的可扩展性**：新增Provider类型更容易，配置结构更清晰
4. **向后兼容**：旧配置格式仍然有效，无需立即迁移

### 系统对比

| 特性 | 旧配置格式 | 新配置格式 |
|------|-----------|-----------|
| **插件启用** | `enable_xxx = true` | `enabled = ["xxx", "yyy"]` |
| **输出Provider** | `[rendering.outputs.xxx]` | `[providers.output.outputs.xxx]` |
| **输入Provider** | 分散在各个插件配置中 | `[providers.input.inputs.xxx]` |
| **决策Provider** | 无独立配置 | `[providers.decision.providers.xxx]` |
| **配置层级** | 扁平化 | 按架构层级组织 |

---

## 配置结构对比

### 旧配置格式

```toml
# 插件启用（旧格式）
[plugins]
enable_console_input = true
enable_bili_danmaku = false
enable_vtube_studio = true

# 输出Provider（旧格式）
[rendering]
enabled = true
concurrent_rendering = true
outputs = ["subtitle", "vts"]

[rendering.outputs.tts]
type = "tts"
engine = "edge"
voice = "zh-CN-XiaoxiaoNeural"

[rendering.outputs.vts]
type = "vts"
vts_host = "localhost"
vts_port = 8001
```

### 新配置格式

```toml
# 插件启用（新格式）
[plugins]
enabled = [
    "console_input",
    "vtube_studio",
]

# 输入Provider（新格式）
[providers.input]
enabled = true
inputs = [
    "console_input",
    # "bili_danmaku",
]

[providers.input.inputs.console_input]
type = "console_input"

# 输出Provider（新格式）
[providers.output]
enabled = true
concurrent_rendering = true
outputs = ["subtitle", "vts"]

[providers.output.outputs.tts]
type = "tts"
engine = "edge"
voice = "zh-CN-XiaoxiaoNeural"

[providers.output.outputs.vts]
type = "vts"
vts_host = "localhost"
vts_port = 8001

# 决策Provider（新格式）
[providers.decision]
enabled = true
active_provider = "maicore"
providers = ["maicore", "rule_engine", "local_llm"]

[providers.decision.providers.maicore]
type = "maicore"
host = "127.0.0.1"
port = 8000
```

---

## 迁移步骤

### 步骤1：备份现有配置

在迁移之前，请备份现有的配置文件：

```bash
# 备份配置文件
cp config.toml config.toml.backup
cp config-template.toml config-template.toml.backup
```

### 步骤2：更新插件启用方式

**旧格式**：
```toml
[plugins]
enable_console_input = true
enable_bili_danmaku = false
enable_vtube_studio = true
enable_subtitle = true
```

**新格式**：
```toml
[plugins]
enabled = [
    "console_input",
    "vtube_studio",
    "subtitle",
]
```

**迁移规则**：
1. 将所有 `enable_xxx = true` 的插件名添加到 `enabled` 列表
2. `enable_xxx = false` 的插件不添加到列表
3. 删除旧的 `enable_xxx` 配置项

### 步骤3：迁移输出Provider配置

**旧格式**：
```toml
[rendering]
enabled = true
concurrent_rendering = true
error_handling = "continue"

outputs = ["tts", "subtitle", "vts"]

[rendering.outputs.tts]
type = "tts"
engine = "edge"
voice = "zh-CN-XiaoxiaoNeural"

[rendering.outputs.subtitle]
type = "subtitle"
window_width = 800
font_size = 24

[rendering.outputs.vts]
type = "vts"
vts_host = "localhost"
vts_port = 8001
```

**新格式**：
```toml
[providers.output]
enabled = true
concurrent_rendering = true
error_handling = "continue"

outputs = ["tts", "subtitle", "vts"]

[providers.output.outputs.tts]
type = "tts"
engine = "edge"
voice = "zh-CN-XiaoxiaoNeural"

[providers.output.outputs.subtitle]
type = "subtitle"
window_width = 800
font_size = 24

[providers.output.outputs.vts]
type = "vts"
vts_host = "localhost"
vts_port = 8001
```

**迁移规则**：
1. 将 `[rendering]` 重命名为 `[providers.output]`
2. 将 `[rendering.outputs.xxx]` 重命名为 `[providers.output.outputs.xxx]`
3. 保持所有子配置不变
4. 如果使用 `[rendering.expression_generator]`，重命名为 `[providers.output.expression_generator]`

### 步骤4：添加输入Provider配置（可选）

如果您的配置中有分散的输入Provider配置，可以统一迁移到新的 `[providers.input]` 节点：

**旧格式**（假设各个插件的config.toml中）：
```toml
# src/plugins/console_input/config.toml
enabled = true

# src/plugins/bili_danmaku/config.toml
enabled = true
room_id = "123456"

# src/plugins/mock_danmaku/config.toml
enabled = false
```

**新格式**（在根config.toml中统一配置）：
```toml
[providers.input]
enabled = true
inputs = [
    "console_input",
    "bili_danmaku",
]

[providers.input.inputs.console_input]
type = "console_input"

[providers.input.inputs.bili_danmaku]
type = "bili_danmaku"
room_id = "123456"
# ... 其他B站弹幕配置

[providers.input.inputs.mock_danmaku]
type = "mock_danmaku"
enabled = false
```

**注意**：输入Provider配置是可选的，您可以继续在各个插件的config.toml中配置。

### 步骤5：添加决策Provider配置（可选）

如果需要使用不同的决策Provider，可以添加新的 `[providers.decision]` 节点：

```toml
[providers.decision]
enabled = true
active_provider = "maicore"
providers = ["maicore", "rule_engine", "local_llm"]

# MaiCore决策Provider（默认）
[providers.decision.providers.maicore]
type = "maicore"
host = "127.0.0.1"
port = 8000
# token = "your_token_if_needed"

# 规则引擎决策Provider
[providers.decision.providers.rule_engine]
type = "rule_engine"
rules_file = "data/rules/decision_rules.toml"
default_response = "我不知道怎么回答这个问题"

# 本地LLM决策Provider
[providers.decision.providers.local_llm]
type = "local_llm"
llm_type = "llm"
system_prompt = "你是一个友好的AI助手，请简洁地回答用户的问题。"
```

### 步骤6：移除或标记旧配置（可选）

迁移完成后，您可以选择：
1. **完全删除旧配置**：删除 `[rendering]` 节点
2. **标记为过时**：保留但添加注释说明已过时（如config-template.toml所示）

建议在迁移测试通过后删除旧配置。

### 步骤7：验证配置

使用以下方法验证配置：

1. **启动程序检查**：
```bash
python main.py
```

2. **检查日志**：
```
✓ PluginManager 加载了 X 个插件
✓ OutputProviderManager 加载了 X 个Provider
✓ InputProviderManager 加载了 X 个Provider
✓ DecisionManager 使用了: maicore
```

3. **配置解析测试**（如果可用）：
```bash
python -m pytest tests/test_config.py -v
```

---

## 详细配置说明

### 插件配置（[plugins]）

#### 新格式（推荐）

```toml
[plugins]
enabled = [
    "console_input",
    "keyword_action",
    "llm_text_processor",

    # 输入功能
    # "bili_danmaku",
    # "stt",

    # 输出功能
    # "tts",
    # "subtitle",
    # "vtube_studio",
]
```

**说明**：
- `enabled`：插件名称列表，取消注释来启用插件
- 只有列表中的插件会被加载
- 空列表表示不加载任何插件

#### 旧格式（向后兼容）

```toml
[plugins]
enable_console_input = true
enable_bili_danmaku = false
enable_vtube_studio = true
```

**说明**：
- `enable_xxx`：布尔值，true表示启用
- 仍然有效，但新格式优先级更高
- 建议迁移到新格式

### 输入Provider配置（[providers.input]）

```toml
[providers.input]
enabled = true
inputs = [
    "console_input",
    "bili_danmaku",
    "mock_danmaku",
]

[providers.input.inputs.console_input]
type = "console_input"
# 控制台输入特定配置

[providers.input.inputs.bili_danmaku]
type = "bili_danmaku"
room_id = "123456"
# B站弹幕特定配置
```

**配置项**：
- `enabled`：是否启用输入层（true/false）
- `inputs`：启用的InputProvider名称列表
- `inputs.xxx.type`：Provider类型（与名称相同）
- `inputs.xxx.*`：Provider特定配置

**可用Provider**：
- `console_input`：控制台输入
- `bili_danmaku`：B站普通弹幕
- `bili_danmaku_official`：B站官方弹幕
- `bili_danmaku_selenium`：B站弹幕（Selenium版）
- `mock_danmaku`：模拟弹幕
- `read_pingmu`：读屏木
- `stt`：语音识别
- `funasr_stt`：FunASR语音识别

### 输出Provider配置（[providers.output]）

```toml
[providers.output]
enabled = true
concurrent_rendering = true
error_handling = "continue"

outputs = [
    "subtitle",
    "vts",
    "tts",
]

[providers.output.outputs.tts]
type = "tts"
engine = "edge"
voice = "zh-CN-XiaoxiaoNeural"
output_device_name = ""

[providers.output.outputs.subtitle]
type = "subtitle"
window_width = 800
font_size = 24

[providers.output.outputs.vts]
type = "vts"
vts_host = "localhost"
vts_port = 8001

[providers.output.expression_generator]
default_tts_enabled = true
default_subtitle_enabled = true
default_expressions_enabled = true
```

**配置项**：
- `enabled`：是否启用输出层（true/false）
- `concurrent_rendering`：是否并发渲染（true/false）
- `error_handling`：错误处理策略（continue/stop/drop）
- `outputs`：启用的OutputProvider名称列表
- `outputs.xxx.type`：Provider类型（与名称相同）
- `outputs.xxx.*`：Provider特定配置
- `expression_generator`：ExpressionGenerator配置

**错误处理策略**：
- `continue`：单个Provider失败，继续渲染其他Provider（推荐）
- `stop`：遇到错误立即停止渲染
- `drop`：丢弃当前渲染请求

**可用Provider**：
- `tts`：Edge TTS语音合成
- `subtitle`：字幕显示
- `vts`：VTube Studio控制
- `omni_tts`：Omni TTS (GPT-SoVITS)
- `sticker`：表情贴纸

### 决策Provider配置（[providers.decision]）

```toml
[providers.decision]
enabled = true
active_provider = "maicore"
providers = [
    "maicore",
    "rule_engine",
    "local_llm",
]

[providers.decision.providers.maicore]
type = "maicore"
host = "127.0.0.1"
port = 8000
connect_timeout = 10.0
reconnect_interval = 5.0

[providers.decision.providers.rule_engine]
type = "rule_engine"
rules_file = "data/rules/decision_rules.toml"
default_response = "我不知道怎么回答这个问题"

[providers.decision.providers.local_llm]
type = "local_llm"
llm_type = "llm"
system_prompt = "你是一个友好的AI助手，请简洁地回答用户的问题。"
```

**配置项**：
- `enabled`：是否启用决策层（true/false）
- `active_provider`：当前使用的Provider名称
- `providers`：所有可用的Provider名称列表
- `providers.xxx.type`：Provider类型
- `providers.xxx.*`：Provider特定配置

**可用Provider**：
- `maicore`：MaiCore决策（通过WebSocket连接）
- `rule_engine`：规则引擎（基于TOML规则文件）
- `local_llm`：本地LLM决策

---

## 常见问题

### Q1: 新旧配置格式可以混用吗？

**可以**。系统支持向后兼容，新旧配置可以混用。但建议完全迁移到新格式以获得更好的维护性。

**混用示例**：
```toml
[plugins]
enabled = ["console_input"]  # 新格式
enable_bili_danmaku = true  # 旧格式，仍然有效

[providers.output]
enabled = true  # 新格式
outputs = ["subtitle"]

[rendering]  # 旧格式，仍然有效
enabled = true
outputs = ["vts"]
```

### Q2: 迁移后旧配置需要立即删除吗？

**不需要**。系统会优先使用新配置，旧配置会被忽略。但建议在测试通过后删除旧配置，以避免混淆。

### Q3: 插件配置在插件目录还是根配置？

**两种方式都可以**：

1. **在插件目录的config.toml中**：
   - 优点：插件配置独立，便于管理
   - 适用于：插件特定配置

2. **在根config.toml的[plugins.xxx]中**：
   - 优点：全局配置覆盖
   - 适用于：需要在全局层面覆盖插件配置

**配置合并规则**：
- 根配置优先级更高，会覆盖插件目录配置
- 配置字段级合并，不会完全替换

### Q4: 如何添加自定义Provider？

**步骤**：

1. **实现Provider类**（继承对应的Provider基类）：
```python
from src.core.providers.output_provider import OutputProvider

class MyOutputProvider(OutputProvider):
    async def _render_internal(self, parameters):
        # 渲染逻辑
        pass
```

2. **在config.toml中配置**：
```toml
[providers.output]
outputs = ["my_provider"]

[providers.output.outputs.my_provider]
type = "my_provider"
custom_param = "value"
```

3. **在OutputProviderManager中注册**：
```python
provider_classes = {
    # ... 其他Provider
    "my_provider": "src.providers.my_provider.MyOutputProvider",
}
```

### Q5: 配置验证失败怎么办？

**常见问题及解决方案**：

1. **TOML语法错误**：
   - 检查引号、括号匹配
   - 使用TOML linter验证：`python -m toml-lint config.toml`

2. **配置项拼写错误**：
   - 检查配置项名称是否正确
   - 参考config-template.toml中的示例

3. **配置值类型错误**：
   - 检查布尔值是否为`true`/`false`
   - 检查数字是否为数字类型（不是字符串）
   - 检查列表是否使用正确的TOML语法

4. **Provider未注册**：
   - 检查Provider名称是否拼写正确
   - 检查Provider是否已注册到对应的Manager

### Q6: 如何回滚到旧配置？

**步骤**：

1. 恢复备份：
```bash
cp config.toml.backup config.toml
cp config-template.toml.backup config-template.toml
```

2. 删除新的 `[providers.*]` 配置节

3. 恢复旧的 `[rendering]` 和 `[plugins] enable_xxx` 配置

### Q7: 新配置会影响性能吗？

**不会**。新的配置结构只是组织方式的改进，不影响运行时性能。

### Q8: 旧的 `[rendering]` 配置什么时候会被移除？

**计划**：
- 当前版本（v0.x）：完全向后兼容
- 下一版本（v1.0）：标记为deprecated，但仍然支持
- 未来版本（v1.5+）：完全移除

建议在v1.0之前完成迁移。

---

## 配置验证

### 方法1：启动程序检查

```bash
python main.py
```

**预期输出**：
```
INFO     PluginManager 开始从目录加载插件: src/plugins
INFO     PluginManager 插件 'console_input' 在配置中被启用
INFO     PluginManager 插件 'keyword_action' 在配置中被启用
INFO     PluginManager 成功加载并设置插件: ConsoleInputPlugin
INFO     PluginManager 成功加载并设置插件: KeywordActionPlugin
INFO     PluginManager 插件加载完成，共加载 X 个插件

INFO     OutputProviderManager 开始从配置加载OutputProvider...
INFO     OutputProviderManager 配置了 2 个输出Provider: ['subtitle', 'vts']
INFO     OutputProviderManager OutputProvider加载完成: 成功=2/2, 失败=0/2

INFO     InputProviderManager 开始启动 X 个InputProvider...
INFO     InputProviderManager 所有 X 个Provider启动成功
```

### 方法2：检查配置加载日志

启用DEBUG日志查看详细配置加载信息：

```bash
python main.py --debug --filter PluginManager OutputProviderManager InputProviderManager DecisionManager
```

**关键日志**：
```
DEBUG    PluginManager 检查插件 'console_input' 是否启用: Enabled=True
DEBUG    OutputProviderManager Provider创建成功: tts -> TTSProvider
DEBUG    DecisionManager 创建DecisionProvider: maicore
```

### 方法3：使用配置测试工具（如果可用）

```bash
python -m pytest tests/test_config_parsing.py -v
python -m pytest tests/test_provider_config.py -v
```

### 配置验证清单

迁移完成后，请验证以下项目：

- [ ] 程序正常启动，无配置解析错误
- [ ] 所有需要的插件已加载
- [ ] 所有需要的Provider已启动
- [ ] 日志显示配置加载成功
- [ ] 功能测试正常（输入、输出、决策）
- [ ] 旧配置节已删除或标记为过时

---

## 附录

### 配置节映射表

| 旧配置节 | 新配置节 | 说明 |
|---------|---------|------|
| `[rendering]` | `[providers.output]` | 输出Provider配置 |
| `[rendering.outputs.xxx]` | `[providers.output.outputs.xxx]` | 单个OutputProvider配置 |
| `[rendering.expression_generator]` | `[providers.output.expression_generator]` | ExpressionGenerator配置 |
| `[plugins] enable_xxx = true` | `[plugins] enabled = ["xxx"]` | 插件启用方式 |
| 无 | `[providers.input]` | 输入Provider配置（新增） |
| 无 | `[providers.decision]` | 决策Provider配置（新增） |

### 配置优先级

```
全局配置 (根 config.toml [plugins.xxx])
  > 插件目录配置 (src/plugins/xxx/config.toml)
  > 代码默认值
```

### 相关文档

- [Plugin迁移指南](PLUGIN_MIGRATION_GUIDE.md)
- [AGENTS.md - 代码风格指南](../AGENTS.md)
- [系统架构设计文档](../refactor/design/plugin_system.md)
- [Provider接口文档](../refactor/design/provider_interfaces.md)

### 获取帮助

如果迁移过程中遇到问题：

1. 查看日志输出（使用 `--debug` 参数）
2. 参考config-template.toml中的示例
3. 查看本文档的"常见问题"部分
4. 在项目Issue中报告问题
