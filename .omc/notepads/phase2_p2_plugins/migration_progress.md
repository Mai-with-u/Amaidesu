# P2中等插件迁移进度

## 迁移状态

### ✅ 已完成

1. **bili_danmaku** - B站弹幕插件
   - ✅ 创建 BiliDanmakuInputProvider
   - ✅ 重写 Plugin 类 (新协议)
   - ✅ 创建测试文件
   - ✅ 保留旧版本为 plugin_old_baseplugin.py
   - ⏳ git 历史保留 (后续处理)

### 🚧 进行中

2. **bili_danmaku_official** - B站官方弹幕插件
   - ⏳ 创建 BiliDanmakuOfficialInputProvider
   - ⏳ 重写 Plugin 类
   - ⏳ 创建测试文件

3. **bili_danmaku_official_maicraft** - B站官方弹幕+Minecraft插件
   - ⏳ 创建 BiliDanmakuOfficialMaiCraftPlugin
   - ⏳ 重写 Plugin 类
   - ⏳ 创建测试文件

4. **vtube_studio** - VTubeStudio虚拟形象控制插件
   - ⏳ 创建 VTSOutputProvider
   - ⏳ 重写 Plugin 类
   - ⏳ 创建测试文件

5. **tts** - TTS语音合成插件 (Edge TTS)
   - ⏳ 创建 TTSOutputProvider
   - ⏳ 重写 Plugin 类
   - ⏳ 创建测试文件

6. **gptsovits_tts** - GPT-SoVITS TTS插件
   - ⏳ 创建 GPTSoVITSOutputProvider
   - ⏳ 重写 Plugin 类
   - ⏳ 创建测试文件

## 迁移模式总结

### InputProvider模式 (用于输入型插件)

```python
class XXXInputProvider(InputProvider):
    def __init__(self, config: dict):
        super().__init__(config)
        # 配置加载
        # 状态初始化

    async def _collect_data(self) -> AsyncIterator[RawData]:
        # 采集数据
        yield RawData(...)

    async def _cleanup(self):
        # 清理资源
```

### OutputProvider模式 (用于输出型插件)

```python
class XXXOutputProvider(OutputProvider):
    async def _setup_internal(self):
        # 初始化设备/服务

    async def _render_internal(self, parameters: RenderParameters):
        # 渲染参数到设备

    async def _cleanup_internal(self):
        # 清理资源
```

### Plugin模式 (统一)

```python
class XXXPlugin:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        # 配置加载

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        self.event_bus = event_bus
        # 创建Provider
        providers = [XXXProvider(self.config)]
        return providers

    async def cleanup(self):
        # 清理所有Provider

    def get_info(self) -> Dict[str, Any]:
        return {...}
```

## 迁移要点

1. **导入更新**:
   - 移除 `from src.core.plugin_manager import BasePlugin`
   - 添加 `from src.core.plugin import Plugin`
   - 添加 Provider 导入

2. **类继承**:
   - 移除 `class XXXPlugin(BasePlugin)`
   - 改为 `class XXXPlugin:`
   - 实现Plugin协议的方法

3. **Provider创建**:
   - 在 `setup()` 中创建Provider实例
   - 返回Provider列表

4. **配置访问**:
   - 移除 `self.core` 访问
   - 使用 `self.config` 直接访问配置
   - 通过 `event_bus.emit()` 发送事件

5. **服务访问**:
   - 通过 `event_bus` 获取服务
   - 或使用注入的core实例(如果需要)

## 遇到的问题

### 问题1: Git历史保留

- **问题**: 新创建的文件没有git历史
- **解决方案**: 使用 `git mv` 移动现有文件(保留历史)
- **状态**: ⏳ 待执行git操作

### 问题2: LSP错误

- **问题**: 类型检查工具报告大量错误
- **原因**: 主要是其他未迁移插件的类型问题
- **解决方案**: 迁移完成后统一修复
- **状态**: ⏳ 暂时忽略,属于预期

### 问题3: 核心依赖

- **问题**: 部分插件依赖 `self.core`
- **解决方案**: 在Plugin类中保留core引用(通过依赖注入)
- **状态**: ⏳ 需要评估每个插件的需求

## 下一步行动

1. ⏳ 完成剩余5个插件的迁移
2. ⏳ 执行git操作保留历史
3. ⏳ 运行测试验证功能
4. ⏳ 修复LSP错误
5. ⏳ 更新文档
