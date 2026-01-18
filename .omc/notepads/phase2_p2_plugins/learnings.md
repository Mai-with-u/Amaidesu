# P2插件迁移学习记录

## 迁移日期
2026年1月27日

## 迁移插件列表

1. bili_danmaku - B站弹幕插件 (API轮询) - ✅ 完成
2. bili_danmaku_official - B站官方弹幕插件 (WebSocket) - ⏳ 待完成
3. bili_danmaku_official_maicraft - B站官方弹幕+Minecraft插件 - ⏳ 待完成
4. vtube_studio - VTubeStudio虚拟形象控制插件 - ⏳ 待完成
5. tts - TTS语音合成插件 (Edge TTS) - ⏳ 待完成
6. gptsovits_tts - GPT-SoVITS TTS插件 - ⏳ 待完成

## 关键学习点

### 1. Provider模式设计

#### InputProvider (输入型插件)

**适用场景**: 从外部数据源采集原始数据的插件
**示例插件**: bili_danmaku, bili_danmaku_official, console_input

**关键方法**:
- `_collect_data() -> AsyncIterator[RawData]`: 核心数据采集逻辑
- `_cleanup()`: 资源清理

**生命周期**:
```python
# 1. 实例化
provider = MyInputProvider(config)

# 2. 启动 (Plugin.setup()中调用)
async for raw_data in provider.start():
    # 处理原始数据
    yield raw_data

# 3. 停止 (Plugin.cleanup()中调用)
await provider.stop()
```

**RawData结构**:
```python
RawData(
    content=Any,  # 原始内容 (bytes, str, dict等)
    source=str,    # 数据源标识 ("bili_danmaku", "console")
    data_type=str, # 数据类型 ("text", "audio", "image")
    timestamp=float,# Unix时间戳
    metadata=dict, # 额外元数据
)
```

#### OutputProvider (输出型插件)

**适用场景**: 将渲染参数输出到目标设备的插件
**示例插件**: tts, gptsovits_tts, vtube_studio, sticker, subtitle

**关键方法**:
- `_setup_internal()`: 初始化设备连接
- `_render_internal(parameters: RenderParameters)`: 核心渲染逻辑
- `_cleanup_internal()`: 资源清理

**生命周期**:
```python
# 1. 实例化
provider = MyOutputProvider(config)

# 2. 设置 (Plugin.setup()中调用)
await provider.setup(event_bus)

# 3. 渲染
await provider.render(render_params)

# 4. 清理 (Plugin.cleanup()中调用)
await provider.cleanup()
```

### 2. Plugin协议设计

**核心原则**: Plugin是Provider的聚合器,不继承基类

**必须实现的方法**:
```python
class MyPlugin:
    def __init__(self, config: Dict[str, Any]):
        # 配置加载
        self.config = config

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        # 创建并返回Provider列表
        providers = [MyProvider(config)]
        return providers

    async def cleanup(self):
        # 清理所有Provider
        for provider in self._providers:
            await provider.stop()

    def get_info(self) -> Dict[str, Any]:
        # 返回插件信息
        return {
            "name": "MyPlugin",
            "version": "1.0.0",
            "author": "Author",
            "description": "Description",
            "category": "input",  # input/output/processing/game/hardware/software
            "api_version": "1.0",
        }
```

**Plugin入口点**:
```python
plugin_entrypoint = MyPlugin
```

### 3. 与BasePlugin的主要区别

| 特性 | BasePlugin (旧) | Plugin (新) |
|------|-----------------|-------------|
| 继承 | 继承BasePlugin | 不继承基类 |
| 核心访问 | self.core | event_bus (通过参数注入) |
| 服务注册 | self.core.register_service() | 通过event_bus.emit() |
| 服务获取 | self.core.get_service() | 通过依赖注入或event_bus |
| 消息发送 | self.core.send_to_maicore() | 通过event_bus.emit() |
| 返回值 | None | List[Provider] |

### 4. 配置访问模式

**旧模式 (BasePlugin)**:
```python
class MyPlugin(BasePlugin):
    def __init__(self, core, plugin_config):
        super().__init__(core, plugin_config)
        # self.plugin_config 已经合并了全局配置和插件配置
        self.room_id = self.plugin_config.get("room_id")
```

**新模式 (Plugin)**:
```python
class MyPlugin:
    def __init__(self, config: Dict[str, Any]):
        # config 是插件配置 (来自plugins.xxx)
        self.room_id = config.get("room_id")
```

### 5. 事件总线使用

**事件发布** (替代send_to_maicore):
```python
# 旧模式
await self.core.send_to_maicore(message)

# 新模式
await self.event_bus.emit("maicore.send", {"message": message}, "MyPlugin")
```

**事件订阅**:
```python
# 在Provider的setup中订阅
async def setup(self, event_bus):
    event_bus.on("some.event", self.handle_event, priority=100)

async def handle_event(self, event_name, data, source):
    # 处理事件
    pass
```

## 迁移模式总结

### 输入型插件迁移模式

1. **创建InputProvider类**
   - 继承`InputProvider`
   - 实现`_collect_data()`方法
   - 实现`_cleanup()`方法(可选)
   - 在`_collect_data()`中`yield RawData(...)`

2. **创建Plugin类**
   - 实现`Plugin`协议
   - 在`setup()`中创建Provider实例
   - 返回Provider列表

3. **保留配置**
   - 配置文件结构不变
   - Plugin直接访问config

### 输出型插件迁移模式

1. **创建OutputProvider类**
   - 继承`OutputProvider`
   - 实现`_render_internal()`方法
   - 实现`_setup_internal()`方法(可选)
   - 实现`_cleanup_internal()`方法(可选)

2. **创建Plugin类**
   - 实现`Plugin`协议
   - 在`setup()`中创建Provider实例
   - 返回Provider列表

3. **事件处理**
   - Provider通过event_bus订阅事件
   - 接收到事件后调用render()

## 遇到的问题及解决方案

### 问题1: 导入语句更新

**问题**: 旧的导入语句`from src.core.plugin_manager import BasePlugin`不再适用

**解决方案**:
```python
# 移除
# from src.core.plugin_manager import BasePlugin

# 添加
from src.core.plugin import Plugin
from src.core.providers.input_provider import InputProvider  # 或 OutputProvider
```

### 问题2: self.core访问

**问题**: 新Plugin协议不提供self.core访问

**解决方案**:
- 通过`event_bus`发送事件
- 或者通过依赖注入获取core (如果需要)
- 大多数情况可以通过event_bus替代

### 问题3: 服务注册/获取

**问题**: 旧模式使用`self.core.register_service()`和`self.core.get_service()`

**解决方案**:
- 服务注册: 通过event_bus.emit()发布服务可用事件
- 服务获取: 通过event_bus订阅服务事件
- 或者通过依赖注入在Plugin初始化时传递

### 问题4: Git历史保留

**问题**: 新创建的文件没有git历史

**解决方案**:
```bash
# 重命名旧文件保留历史
git mv plugin.py plugin_old_baseplugin.py

# 创建新的plugin.py (新内容)
# 新文件将作为新文件添加到git
```

## 最佳实践

### 1. 错误处理

**Provider中的错误处理**:
```python
async def _collect_data(self):
    try:
        # 数据采集逻辑
        pass
    except Exception as e:
        self.logger.error(f"采集数据时出错: {e}", exc_info=True)
        # 继续运行,不要中断循环
```

### 2. 资源清理

**确保资源正确释放**:
```python
async def _cleanup(self):
    try:
        # 关闭连接
        if self.session:
            await self.session.close()
    except Exception as e:
        self.logger.error(f"清理资源时出错: {e}")
```

### 3. 配置验证

**启动时验证配置**:
```python
def __init__(self, config: dict):
    super().__init__(config)
    
    # 验证必需配置
    required_keys = ["room_id", "poll_interval"]
    missing_keys = [key for key in required_keys if key not in config]
    if missing_keys:
        raise ValueError(f"缺少必需配置: {missing_keys}")
```

### 4. 测试覆盖

**测试Provider和Plugin**:
- 测试Plugin的setup和cleanup
- 测试Provider的生命周期
- 测试配置验证
- 测试错误处理

## 代码示例对比

### bili_danmaku迁移示例

**旧代码 (BasePlugin)**:
```python
class BiliDanmakuPlugin(BasePlugin):
    def __init__(self, core, plugin_config):
        super().__init__(core, plugin_config)
        self.config = self.plugin_config
        # 直接在Plugin中处理弹幕

    async def setup(self):
        await super().setup()
        # 创建后台任务
        self._task = asyncio.create_task(self._run_polling_loop())

    async def _run_polling_loop(self):
        # 轮询弹幕
        async for message in self._fetch_and_process():
            await self.core.send_to_maicore(message)
```

**新代码 (Plugin + InputProvider)**:
```python
# Plugin类
class BiliDanmakuPlugin:
    def __init__(self, config):
        self.config = config

    async def setup(self, event_bus, config):
        self.event_bus = event_bus
        # 创建Provider
        provider = BiliDanmakuInputProvider(config)
        return [provider]

# Provider类
class BiliDanmakuInputProvider(InputProvider):
    async def _collect_data(self):
        while not self._stop_event.is_set():
            # 采集弹幕
            data = await self._fetch_danmaku()
            yield RawData(
                content=data,
                source="bili_danmaku",
                data_type="text",
                timestamp=time.time(),
            )
```

## 后续工作

1. **完成剩余5个插件的迁移**
2. **运行测试验证功能**
3. **修复LSP错误**
4. **更新文档**
5. **Git提交**
