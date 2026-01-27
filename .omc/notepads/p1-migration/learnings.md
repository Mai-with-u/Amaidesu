# P1简单插件迁移学习笔记

## 迁移的插件
1. **mock_danmaku** - 模拟弹幕插件（InputProvider）
2. **subtitle** - 字幕显示插件（OutputProvider）
3. **sticker** - 贴纸插件（OutputProvider）
4. **emotion_judge** - 情感判断插件（DecisionProvider）
5. **keyword_action** - 关键词动作插件（已在之前完成）

## 迁移模式总结

### InputProvider模式（mock_danmaku）
- 从外部数据源（文件、网络等）采集原始数据
- 实现`_collect_data()`方法，返回AsyncIterator[RawData]
- Plugin类启动后台任务持续采集数据
- 通过EventBus发送`input.raw`事件

### OutputProvider模式（subtitle, sticker）
- 渲染数据到目标设备（GUI、VTS等）
- 实现`_render_internal()`方法处理RenderParameters
- 监听`render.{type}`事件
- 处理配置中的显示参数

### DecisionProvider模式（emotion_judge）
- 处理CanonicalMessage并生成决策
- 实现`decide()`方法返回决策结果
- 监听`canonical.message`事件
- 可以使用外部服务（LLM、VTS等）

## 关键代码变更

### 移除的导入
```python
# 移除
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore

# 添加
from src.core.plugin import Plugin
from src.core.event_bus import EventBus
```

### Plugin类变更
```python
# 旧代码（BasePlugin）
class MyPlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.core = core
    
    async def setup(self):
        await super().setup()
        self.core.register_service("my_service", self)

# 新代码（Plugin协议）
class MyPlugin:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None
        self._providers = []
    
    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        self.event_bus = event_bus
        provider = MyProvider(config)
        await provider.setup(event_bus, config)
        self._providers.append(provider)
        return self._providers
```

### EventBus使用
```python
# 发布事件
await self.event_bus.emit("event.name", data, source="PluginName")

# 订阅事件
self.event_bus.on("event.name", self.handler, priority=100)

# 监听渲染事件（OutputProvider）
self.event_bus.on("render.subtitle", self._handle_render_request)

# 监听标准消息（DecisionProvider）
self.event_bus.on("canonical.message", self._handle_canonical_message)
```

## 遇到的问题和解决方案

### 1. GUI线程管理（subtitle）
**问题**: CustomTkinter需要在独立线程中运行GUI事件循环
**解决方案**: 使用`threading.Thread`启动GUI线程，通过`queue.Queue`进行线程间通信

### 2. 图片处理（sticker）
**问题**: 需要调整base64编码图片的大小
**解决方案**: 使用PIL.Image解码、调整大小、重新编码为base64

### 3. 冷却机制（emotion_judge, sticker）
**问题**: 防止频繁触发导致动作不自然
**解决方案**: 实现`cool_down_seconds`配置和`last_trigger_time`跟踪

### 4. 服务访问限制
**问题**: 新架构移除了self.core访问服务的方式
**解决方案**: 
- 暂时通过EventBus发送请求
- 未来需要完善服务注册/获取机制
- 当前实现中服务访问部分为占位符

## 测试要点

### Unit测试
- 测试Provider初始化和配置解析
- 测试Plugin设置和Provider创建
- 测试生命周期方法（setup, cleanup）
- 测试事件处理逻辑
- 使用MockEventBus避免真实EventBus依赖

### 集成测试
- 测试EventBus事件流
- 测试Provider间的协作
- 测试配置覆盖和合并
- GUI插件需要特殊处理（可能跳过GUI相关测试）

## 配置变更

### 配置节点保持不变
所有插件的配置节点保持不变，确保向后兼容：
- `mock_danmaku.*`
- `subtitle_display.*`
- `sticker.*`
- `emotion_judge.*`
- `keyword_action.*`

### 配置合并策略
```python
# 优先级：配置节点 > 顶级配置
subtitle_config = config.get("subtitle_display", {})
if not subtitle_config:
    subtitle_config = config  # 向后兼容
```

## Git历史保留

使用git mv保留文件历史：
```bash
# 备份旧文件
git mv plugin.py plugin_old.py

# 添加新文件
git add plugin.py provider.py

# 提交
git commit -m "migrate: plugin_name to new Plugin architecture"
```

## 后续优化

1. **服务注册/获取机制**: 完善从EventBus获取服务的机制
2. **测试覆盖**: 增加GUI插件的集成测试
3. **错误处理**: 完善Provider的错误恢复机制
4. **性能优化**: 优化事件处理和数据转换性能
5. **文档完善**: 为每个Provider添加详细的docstring和示例
