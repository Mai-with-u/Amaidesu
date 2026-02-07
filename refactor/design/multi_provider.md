# 多Provider并发设计

## 核心目标

支持 Input Domain 和 Output Domain 的**多Provider并发处理**，提高系统吞吐量。

---

## 并发模型

### Input Domain 并发

多个输入源同时采集数据，各自独立运行：

```
弹幕Provider ──┐
              ├──→ 各自生成 RawData → EventBus → InputLayer
游戏Provider ──┤
              │
语音Provider ──┘
```

**关键点**：
- 每个 InputProvider 独立运行自己的采集循环
- 通过 EventBus 发送 RawData，互不干扰
- 一个 Provider 失败不影响其他 Provider

### Output Domain 并发

同一个 Intent 同时渲染到多个输出设备：

```
Intent ──┬──→ SubtitleProvider → 字幕窗口
         ├──→ TTSProvider → 音频播放
         └──→ VTSProvider → 虚拟形象
```

**关键点**：
- 使用 `asyncio.gather()` 并发执行所有 OutputProvider
- 支持配置 `error_handling: "continue"` 或 `"stop"`
- 可选择性等待所有完成或任一完成

---

## Manager 设计

### InputProviderManager

```python
class InputProviderManager:
    async def start_all_providers(self, providers: List[str]) -> None:
        """并发启动所有 Provider"""
        tasks = [self._start_provider(name) for name in providers]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def stop_all_providers(self) -> None:
        """优雅停止所有 Provider"""
        for provider in self._providers.values():
            provider.stop()
        # 等待所有 Provider 完成清理
```

### OutputProviderManager

```python
class OutputProviderManager:
    async def render_all(self, intent: Intent) -> None:
        """并发渲染到所有启用的 Provider"""
        tasks = [
            provider.render(intent)
            for provider in self._enabled_providers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # 根据 error_handling 配置处理异常
```

---

## 错误隔离

### 原则

单个 Provider 失败不应影响其他 Provider。

### 实现

```python
async def _safe_render(self, provider: OutputProvider, intent: Intent):
    try:
        await asyncio.wait_for(
            provider.render(intent),
            timeout=self.render_timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"{provider.name} 渲染超时")
    except Exception as e:
        logger.error(f"{provider.name} 渲染失败: {e}")
```

---

## 配置示例

```toml
[providers.input]
enabled = true
enabled_inputs = ["console_input", "bili_danmaku", "minecraft"]

[providers.output]
enabled = true
concurrent_rendering = true
error_handling = "continue"  # continue | stop
render_timeout = 10.0
enabled_outputs = ["subtitle", "tts", "vts"]
```

---

## Provider 注册机制（按需加载）

### 设计原则

**配置驱动，按需加载** - Provider的加载完全由配置文件驱动，只导入和注册配置中启用的Provider。

### 核心特性

1. **配置驱动**：Provider的加载由配置文件中的 `enabled_inputs`、`available_providers`、`enabled_outputs` 列表决定
2. **按需加载**：只导入配置中启用的Provider模块，不加载未使用的Provider
3. **依赖隔离**：未使用的Provider的依赖缺失不影响应用启动
4. **插件支持**：支持通过 `module_path` 配置自定义Provider

### 注册流程

```
1. 加载配置文件 (config.toml)
2. 读取启用的Provider列表
   ├── providers.input.enabled_inputs
   ├── providers.decision.available_providers
   └── providers.output.enabled_outputs
3. 动态导入对应的Provider模块
4. 自动注册到ProviderRegistry
5. 创建Provider实例
```

### 性能优势

| 指标 | 全量加载（旧） | 按需加载（新） | 提升 |
|------|---------------|---------------|------|
| 启动时间 | 导入所有Provider（~50个） | 只导入配置中的Provider（~6个） | 50-70% ↓ |
| 内存占用 | 所有Provider类常驻内存 | 只加载启用的Provider类 | 40-60% ↓ |
| 依赖要求 | 必须安装所有Provider依赖 | 只需安装配置中的Provider依赖 | 灵活 ↑ |

### 配置示例

```toml
# 输入Provider配置
[providers.input]
enabled = true
enabled_inputs = ["console_input", "bili_danmaku"]  # 只加载这2个

# 决策Provider配置
[providers.decision]
active_provider = "maicore"
available_providers = ["maicore", "local_llm", "rule_engine", "mock"]

# 输出Provider配置
[providers.output]
enabled = true
enabled_outputs = ["subtitle", "tts", "vts"]  # 只加载这3个
```

### 自定义插件支持

通过 `module_path` 配置可以加载自定义Provider：

```toml
[providers.output.outputs.my_custom_plugin]
type = "custom"
module_path = "plugins.my_custom_provider"  # 自定义模块路径
api_key = "your-api-key"
```

### 实现细节

**注册方法：** `ProviderRegistry.discover_and_register_providers(config_service, config)`

**关键逻辑：**
```python
providers_config = config.get("providers", {})
input_config = providers_config.get("input", {})
enabled_inputs = input_config.get("enabled_inputs", [])

for provider_name in enabled_inputs:
    module_path = f"src.domains.input.providers.{provider_name}"
    importlib.import_module(module_path)  # 动态导入触发注册
```

### 对比：全量加载 vs 按需加载

#### 全量加载（已废弃）
```python
# main.py
from src.domains.input.providers import *  # noqa: F401, F403
from src.domains.decision.providers import *  # noqa: F401, F403
from src.domains.output.providers import *  # noqa: F401, F403
```

**问题：**
- ❌ 导入所有Provider，即使配置中未启用
- ❌ 未使用的Provider依赖缺失导致启动失败
- ❌ 代码审查工具标记为"未使用导入"

#### 按需加载（当前）
```python
# main.py
stats = ProviderRegistry.discover_and_register_providers(config_service, config)
logger.info(f"Provider注册完成: {stats}")
```

**优势：**
- ✅ 只导入配置中启用的Provider
- ✅ 未使用的Provider依赖缺失不影响启动
- ✅ 代码清晰，无副作用导入
- ✅ 支持自定义插件路径
