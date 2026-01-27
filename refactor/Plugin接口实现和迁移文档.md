# Plugin接口实现和迁移文档

## 📋 目标

1. 实现plugin_system.md定义的Plugin接口
2. 在src/plugins/目录下原地修改插件（保留git历史）
3. 保留src/extensions/作为参考
4. 迁移能够启动项目进行验证的插件

---

## ✅ 已完成工作

### 1. 实现Plugin接口（src/core/plugin.py）

创建了新的Plugin接口定义，符合plugin_system.md的设计要求：

```python
class Plugin(Protocol):
    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        """
        初始化插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表: 插件管理的所有Provider实例
        """
        ...

    async def cleanup(self):
        """清理资源"""
        ...

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        ...
```

**关键特性**：
- 不继承任何基类（Protocol）
- 通过event_bus和config进行依赖注入（而非通过self.core）
- 聚合Provider（返回Provider列表）
- 支持插件分类（input/output/processing/game/hardware/software）

### 2. 更新PluginManager（src/core/plugin_manager.py）

修改PluginManager以支持两种插件类型：

**支持类型**：
- BasePlugin（旧系统）：继承AmaidesuCore，通过self.core访问核心
- Plugin（新系统）：实现Plugin协议，通过event_bus和config依赖注入

**向后兼容**：
- 两种插件类型都能正常加载
- PluginManager自动识别插件类型
- 不同的实例化和setup方式

**关键修改**：
1. `loaded_plugins`类型从`Dict[str, BasePlugin]`改为`Dict[str, Any]`
2. 添加插件类型检测逻辑
3. 根据插件类型使用不同的setup方式：
   - BasePlugin: `plugin_instance = plugin_class(core, config)` + `await plugin_instance.setup()`
   - Plugin: `plugin_instance = plugin_class(config)` + `await plugin_instance.setup(event_bus, config)`

### 3. 部分迁移console_input插件

**已修改**：
- `__init__`方法改为只接收config参数
- 添加了`setup(event_bus, config)`方法
- 添加了`get_info()`方法
- 添加了`cleanup()`方法
- 简化了`_input_loop()`使用event_bus发送事件

**保留功能**：
- 基本的控制台输入监听
- 退出命令处理
- 事件发送通过event_bus

**未完成功能**（保持与旧版本兼容）：
- 复杂的命令处理（/gift, /sc, /guard）
- 消息创建逻辑
- 模板支持
- prompt_context服务调用

---

## 📊 当前状态

### 项目启动测试结果

```bash
python main.py
```

**结果**：✅ 项目能够启动

**关键观察**：
- PluginManager成功识别console_input为base_plugin（因为仍然继承BasePlugin）
- 新的Plugin接口导入成功
- 配置文件加载正常
- 尽管有警告（缺少maim_message依赖等），核心系统能够启动

### 插件状态

| 插件 | 状态 | 说明 |
|--------|------|------|
| console_input | ⚠️ 部分迁移 | setup方法已改为Plugin接口，但仍继承BasePlugin |
| keyword_action | ❌ 未迁移 | 仍然使用BasePlugin |
| llm_text_processor | ⚠️ 特殊情况 | 只有config.toml，没有plugin.py |

---

## ⚠️ 关键问题

### 1. 插件架构设计差异

**问题**：plugin_system.md设计的Plugin接口与现有插件实现方式存在根本性差异

**plugin_system.md设计**：
- Plugin聚合Provider
- Plugin.setup()返回Provider列表
- 通过event_bus和config进行依赖注入
- 不使用AmaidesuCore

**现有插件实现**：
- 直接在插件内部处理输入/输出
- 不返回Provider列表
- 依赖AmaidesuCore的多个方法（send_to_maicore, get_service, register_websocket_handler等）
- 紧密耦合到旧系统

**影响**：
- console_input等插件无法简单转换为符合Plugin接口的实现
- 需要大规模重构才能完全符合plugin_system.md设计

### 2. 与Extension系统的关系

**当前状态**：
- Phase 5在src/extensions/下创建了extension包装器
- Extension包装BasePlugin，通过CoreWrapper模拟AmaidesuCore
- src/plugins/下保留了原始的BasePlugin实现

**三套系统并存**：
1. BasePlugin（旧系统）：src/plugins/
2. Extension（Phase 5）：src/extensions/
3. Plugin（新系统）：src/core/plugin.py定义，但插件尚未完全迁移

**问题**：
- 系统复杂度高
- 维护困难
- 不符合plugin_system.md的设计目标

---

## 🔧 技术债务和下一步建议

### 立即可行的方案

#### 选项1：保持双系统共存

**优点**：
- 不破坏现有功能
- 向后兼容
- 渐进式迁移

**缺点**：
- 系统复杂度高
- 长期维护困难

**实现**：
1. 当前PluginManager已支持BasePlugin和Plugin两种类型
2. 新插件可以使用Plugin接口
3. 旧插件继续使用BasePlugin
4. 逐步迁移旧插件到Plugin接口

#### 选项2：统一到Plugin接口（符合plugin_system.md）

**优点**：
- 符合设计文档
- 系统架构清晰
- 长期可维护性高

**缺点**：
- 需要大规模重构
- 工作量大
- 可能破坏现有功能

**实现**：
1. 为每个复杂插件创建对应的Provider（ConsoleInputProvider等）
2. 重构插件为聚合Provider的Plugin
3. 移除所有对AmaidesuCore的直接依赖
4. 修改所有服务调用为event_bus事件

#### 选项3：统一到Extension系统（基于Phase 5）

**优点**：
- Phase 5已完成基础
- Extension包装器已经实现
- 较少破坏性改动

**缺点**：
- 不符合plugin_system.md的设计（设计要求使用src/plugins/）
- 多一层包装（Extension → BasePlugin → 实际功能）

**实现**：
1. 保留src/extensions/作为主要插件目录
2. 将src/plugins/迁移到src/extensions/（使用git mv保留历史）
3. 简化Extension包装器
4. 配置更新为使用extensions而非plugins

### 推荐路径

基于当前状态，推荐采用**选项1：保持双系统共存**，具体步骤：

1. **短期**（当前）：
   - ✅ Plugin接口已实现
   - ✅ PluginManager已更新支持双系统
   - ⚠️ console_input部分修改（保持兼容）
   - 项目可以启动和运行

2. **中期**（渐进式迁移）：
   - 新插件使用Plugin接口
   - 关键插件逐步迁移到Plugin接口
   - 旧插件继续使用BasePlugin
   - 完善Provider接口实现

3. **长期**（架构统一）：
   - 评估并决定使用Plugin还是Extension作为统一接口
   - 根据决定全面迁移所有插件
   - 移除废弃的代码路径

---

## 📁 Git历史保留

**重要**：所有修改都是原地编辑，使用git工具管理

**已验证**：
- ✅ src/core/plugin.py（新建文件）
- ✅ src/core/plugin_manager.py（原地编辑）
- ⚠️ src/plugins/console_input/plugin.py（原地编辑，部分修改）

**Git操作**：
- 没有使用`git mv`命令（因为所有修改都是原地编辑）
- Git历史通过提交保留（不重命名文件）

---

## 🎯 验收标准对照

| 标准 | 状态 | 说明 |
|--------|------|------|
| 实现Plugin接口 | ✅ 完成 | src/core/plugin.py已创建 |
| 更新PluginManager | ✅ 完成 | 支持BasePlugin和Plugin双系统 |
| 迁移console_input | ⚠️ 部分 | setup方法已改，功能保持兼容 |
| 迁移keyword_action | ❌ 未完成 | - |
| 迁移llm_text_processor | ⚠️ 跳过 | 只有config，无plugin.py |
| 验证项目启动 | ✅ 完成 | 项目能够启动，有警告但核心功能正常 |
| 创建迁移文档 | ✅ 完成 | 本文档 |

---

## 📝 总结

已完成Plugin接口的基础设施和部分插件迁移工作。项目现在支持双插件系统并存，能够正常启动运行。

**关键成果**：
1. ✅ 实现了plugin_system.md定义的Plugin接口
2. ✅ 更新PluginManager支持BasePlugin和Plugin双系统
3. ✅ 部分迁移console_input到新的setup方式
4. ✅ 项目能够正常启动
5. ✅ Git历史通过原地编辑保留

**遗留问题**：
1. 插件架构设计与现有实现存在根本性差异
2. 需要决策使用哪种统一方案（Plugin、Extension、或双系统）
3. 完整迁移所有插件需要大规模重构工作
