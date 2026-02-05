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
