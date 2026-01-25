# Phase 5 第二阶段：插件迁移实施计划

> **日期**: 2026-01-25
> **状态**: 进行中
> **目标**: 将23个插件迁移到Extension系统

---

## 📋 迁移策略

### 核心原则

1. **最小化改动**: 不修改插件核心逻辑，只创建Extension包装
2. **向后兼容**: 保留插件原有配置和功能
3. **渐进迁移**: 逐个迁移，验证后再继续
4. **测试优先**: 静态代码评审确保质量

### Extension包装模式

每个插件通过创建Extension类包装，插件作为Extension的内部组件：

```python
# Extension包装示例
class PluginNameExtension(BaseExtension):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._plugin = None

    async def setup(self, event_bus, config) -> List[Any]:
        # 延迟导入插件，避免循环依赖
        from src.plugins.plugin_name.plugin import PluginNamePlugin

        # 创建插件实例（传入None作为core，稍后设置）
        self._plugin = PluginNamePlugin(None, config)
        self._plugin.core = self._get_core_wrapper(event_bus)

        # 调用插件的setup
        await self._plugin.setup()

        # 返回Provider列表（如果有）
        return []

    async def cleanup(self):
        if self._plugin:
            await self._plugin.cleanup()
        await super().cleanup()

    def _get_core_wrapper(self, event_bus):
        """创建一个AmaidesuCore的包装器，提供插件需要的方法"""
        class CoreWrapper:
            def __init__(self, event_bus):
                self.event_bus = event_bus
                self.platform = "amaidesu"

            async def send_to_maicore(self, message):
                """通过EventBus发送消息"""
                await event_bus.emit("input.raw_data", message)

            async def register_websocket_handler(self, msg_type, handler):
                """注册WebSocket处理器"""
                event_bus.listen_event(f"websocket.{msg_type}", handler)

        return CoreWrapper(event_bus)
```

---

## 🎯 迁移优先级

### 优先级1：简单插件（结构清晰，依赖少）

| 插件 | 复杂度 | 原因 | 预计时间 |
|------|--------|------|----------|
| bili_danmaku | 低 | API轮询，结构简单 | 30分钟 |
| console_input | 低 | 控制台输入，已迁移到InputProvider | 已完成 |
| mock_danmaku | 低 | 模拟弹幕，已迁移到InputProvider | 已完成 |
| sticker | 低 | 贴纸显示，依赖vts_control服务 | 30分钟 |
| subtitle | 低 | 字幕显示，简单输出 | 30分钟 |

### 优先级2：中等插件（有外部依赖或复杂逻辑）

| 插件 | 复杂度 | 原因 | 预计时间 |
|------|--------|------|----------|
| tts | 中 | TTS引擎，依赖text_cleanup服务 | 45分钟 |
| vtube_studio | 中 | VTS控制，注册vts_control服务 | 45分钟 |
| keyword_action | 中 | 关键词动作，简单处理 | 30分钟 |
| emotion_judge | 中 | 情感判断，使用vts_control服务 | 30分钟 |

### 优先级3：复杂插件（多个模块，复杂依赖）

| 插件 | 复杂度 | 原因 | 预计时间 |
|------|--------|------|----------|
| maicraft | 高 | 抽象工厂模式，多模块 | 60分钟 |
| mainosaba | 高 | VLM集成，屏幕截图，游戏控制 | 60分钟 |
| warudo | 高 | WebSocket口型同步，状态管理 | 60分钟 |
| llm_text_processor | 高 | LLM处理，注册text_cleanup服务 | 45分钟 |

---

## 📁 目录结构

迁移后的目录结构：

```
src/extensions/
├── bili_danmaku/
│   ├── extension.py           # Extension包装
│   └── config-template.toml   # 配置模板（可选）
├── maicraft/
│   ├── extension.py           # Extension包装
│   └── config-template.toml
├── mainosaba/
│   ├── extension.py           # Extension包装
│   └── config-template.toml
├── warudo/
│   ├── extension.py           # Extension包装
│   └── config-template.toml
├── sticker/
│   ├── extension.py
│   └── config-template.toml
├── subtitle/
│   ├── extension.py
│   └── config-template.toml
└── ... (其他插件)
```

---

## 🚀 实施步骤

### 步骤1: 创建Extension包装

为每个插件创建extension.py文件：

```bash
# 示例：为bili_danmaku创建Extension包装
src/extensions/bili_danmaku/extension.py
```

### 步骤2: 保留原有配置

插件原有配置保持不变，Extension直接使用：

```toml
# config.toml中的配置
[bili_danmaku]
enabled = true
room_id = 123456
# ... 其他配置
```

### 步骤3: 创建CoreWrapper

为每个Extension创建AmaidesuCore包装器，提供插件需要的API：

- `send_to_maicore(message)` → `emit("input.raw_data", message)`
- `register_websocket_handler(msg_type, handler)` → `listen_event(f"websocket.{msg_type}", handler)`
- `get_service(service_name)` → 从ExtensionManager获取服务
- `register_service(service_name, service)` → 注册到ExtensionManager

### 步骤4: 静态代码评审

每个Extension创建后进行静态检查：

```bash
# 代码风格检查
ruff check src/extensions/bili_danmaku/extension.py

# 类型检查（如果使用mypy）
mypy src/extensions/bili_danmaku/extension.py
```

### 步骤5: Git提交

每个Extension创建完成后提交：

```bash
git add src/extensions/bili_danmaku/
git commit -m "refactor: migrate bili_danmaku plugin to extension system"
```

---

## 📊 迁移清单

### 第一批：简单插件（已完成）

- [x] console_input (已作为InputProvider迁移)
- [x] mock_danmaku (已作为InputProvider迁移)

### 第二批：优先级1插件（进行中）

- [ ] bili_danmaku
- [ ] sticker
- [ ] subtitle
- [ ] read_pingmu
- [ ] remote_stream

### 第三批：优先级2插件

- [ ] tts
- [ ] vtube_studio
- [ ] keyword_action
- [ ] emotion_judge
- [ ] llm_text_processor
- [ ] dg_lab_service
- [ ] dg-lab-do

### 第四批：优先级3插件

- [ ] maicraft
- [ ] mainosaba
- [ ] warudo
- [ ] vrchat
- [ ] obs_control
- [ ] gptsovits_tts
- [ ] omni_tts
- [ ] funasr_stt
- [ ] message_replayer
- [ ] command_processor

### 其他插件

- [ ] bili_danmaku_official
- [ ] bili_danmaku_official_maicraft
- [ ] bili_danmaku_selenium

---

## ⚠️ 已知问题和解决方案

### 问题1: 插件依赖AmaidesuCore的WebSocket连接

**插件**: warudo, mainosaba

**问题**: 这些插件注册WebSocket处理器，新架构中WebSocket由MaiCoreDecisionProvider管理

**解决方案**: 创建CoreWrapper，将`register_websocket_handler`映射到EventBus事件监听

### 问题2: 插件使用服务注册/获取

**插件**: 几乎所有插件

**问题**: 插件通过`core.register_service()`和`core.get_service()`进行服务通信

**解决方案**: 在CoreWrapper中实现服务注册和获取功能，暂时保留服务注册机制

### 问题3: 配置迁移

**问题**: 插件配置在config.toml中，新架构需要统一配置

**解决方案**: 保持原有配置不变，ExtensionManager自动加载插件配置

### 问题4: 循环依赖

**问题**: Extension导入插件，插件导入AmaidesuCore，AmaidesuCore导入Extension

**解决方案**: 延迟导入，在Extension.setup()中动态导入插件

---

## 📝 技术债记录

### 小问题（不影响功能）

1. **服务注册机制未完全替换为EventBus**
   - 当前: 保留服务注册机制作为向后兼容
   - 改进: 后续逐步迁移到EventBus
   - 优先级: 低

2. **CoreWrapper可能无法完全模拟AmaidesuCore**
   - 当前: 实现插件需要的主要方法
   - 改进: 根据实际使用情况补充缺失方法
   - 优先级: 低

3. **配置转换工具未实现**
   - 当前: 保留原有配置格式
   - 改进: 提供自动配置转换工具
   - 优先级: 低

---

## ✅ 验收标准

### 功能验收

- [ ] 所有插件功能保持不变
- [ ] 插件可以正常加载和卸载
- [ ] 服务注册和获取正常工作
- [ ] WebSocket消息处理正常

### 代码质量验收

- [ ] ruff检查通过，无警告
- [ ] 代码风格一致，符合项目规范
- [ ] 文档注释完整
- [ ] 类型注解完整

### Git历史验收

- [ ] 每个插件独立提交
- [ ] 提交信息清晰
- [ ] 使用`git mv`移动文件（如果需要）

---

## 🎯 下一阶段

Phase 5第二阶段完成后，进入Phase 6：

1. AmaidesuCore简化（删除WebSocket/HTTP代码）
2. 清理未使用的旧代码
3. 端到端测试
4. 性能测试和优化
5. 配置迁移工具
6. 文档完善

---

**报告生成时间**: 2026-01-25
**报告生成人**: AI Assistant (Sisyphus)
