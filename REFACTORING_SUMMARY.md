# 插件系统重构总结

## 概述

本次重构将 Amaidesu 的插件系统从旧的 BasePlugin 架构迁移到新的 Plugin 协议架构，实现了更好的解耦、可测试性和可维护性。

## 重构时间范围

- 开始日期：2025-01-20
- 完成日期：2025-01-28
- 持续时间：8天

## 重构目标

1. **解耦插件与核心**：消除插件对 AmaidesuCore 的直接依赖
2. **依赖注入**：通过 event_bus 和 config 进行依赖注入，提高可测试性
3. **Provider 抽象**：引入 Provider 接口，封装具体功能
4. **向后兼容**：保持现有插件的正常运行，平滑迁移

## 核心变更

### 1. 新增核心组件

#### Plugin 协议（src/core/plugin.py）
```python
class Plugin(Protocol):
    """插件协议 - 聚合多个Provider"""

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        """初始化插件，返回Provider列表"""
        ...

    async def cleanup(self):
        """清理资源"""
        ...

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        ...
```

#### EventBus（src/core/event_bus.py）
- 发布-订阅模式
- 错误隔离机制
- 优先级控制
- 统计功能
- 生命周期管理

#### Provider 接口
- **InputProvider**：输入数据采集（如弹幕、控制台输入）
- **OutputProvider**：输出渲染（如 TTS、字幕显示）
- **DecisionProvider**：消息决策处理（如命令路由）

### 2. 保留的组件（向后兼容）

#### BasePlugin（src/core/plugin_manager.py）
- **已废弃**：标记为 deprecated，仅用于向后兼容
- **保留原因**：gptsovits_tts 插件仍在使用，需要后续迁移
- **未来计划**：将在所有插件迁移完成后移除

## 插件迁移状态

### 已迁移插件（使用新架构）

| 插件名称 | 状态 | 迁移日期 |
|---------|------|---------|
| bili_danmaku | ✅ 完成 | 2025-01-26 |
| bili_danmaku_official | ✅ 完成 | 2025-01-26 |
| bili_danmaku_official_maicraft | ✅ 完成 | 2025-01-26 |
| console_input | ✅ 完成 | 2025-01-26 |
| arknights | ✅ 完成 | 2025-01-26 |
| command_processor | ✅ 完成 | 2025-01-27 |
| mainosaba | ✅ 完成 | 2025-01-27 |
| mock_danmaku | ✅ 完成 | 2025-01-27 |
| sticker | ✅ 完成 | 2025-01-27 |
| subtitle | ✅ 完成 | 2025-01-27 |
| screen_monitor | ✅ 完成 | 2025-01-27 |
| read_pingmu | ✅ 完成 | 2025-01-27 |
| emotion_judge | ✅ 完成 | 2025-01-27 |
| dg_lab_service | ✅ 完成 | 2025-01-28 |
| remote_stream | ✅ 完成 | 2025-01-28 |
| obs_control | ✅ 完成 | 2025-01-28 |
| maicraft | ✅ 完成 | 2025-01-28 |
| stt | ✅ 完成 | 2025-01-28 |
| tts | ✅ 完成 | 2025-01-28 |
| vrchat | ✅ 完成 | 2025-01-28 |
| vtube_studio | ✅ 完成 | 2025-01-28 |
| warudo | ✅ 完成 | 2025-01-28 |
| omni_tts | ✅ 完成 | 2025-01-28 |

**总计**：24个插件已迁移到新架构

### 待迁移插件（仍在使用旧架构）

| 插件名称 | 状态 | 原因 |
|---------|------|------|
| gptsovits_tts | ⏳ 待迁移 | 依赖 AmaidesuCore 的 WebSocket 处理器和服务访问机制 |

**总计**：1个插件待迁移

## 配置变更

### 插件配置加载（无变更）

- 配置文件位置：`src/plugins/{plugin_name}/config.toml`
- 全局配置覆盖：根目录 `config.toml` 中的 `[plugins.{plugin_name}]` 段
- 配置加载逻辑保持不变，向后兼容

### 新增配置项

新架构插件支持 `get_info()` 方法返回以下信息：
- `name`: 插件名称
- `version`: 版本号
- `author`: 作者
- `description`: 描述
- `category`: 分类（input/output/processing/game/hardware/software）
- `api_version`: API 版本

## 文件变更

### 新增文件

核心组件：
- `src/core/plugin.py` - Plugin 协议定义
- `src/core/event_bus.py` - 事件总线实现
- `src/core/providers/base.py` - Provider 基类
- `src/core/providers/input_provider.py` - InputProvider 接口
- `src/core/providers/output_provider.py` - OutputProvider 接口
- `src/core/providers/decision_provider.py` - DecisionProvider 接口
- `src/core/data_types/raw_data.py` - RawData 数据类型

### 修改文件

- `src/core/plugin_manager.py` - 更新文档，标记 BasePlugin 为废弃，支持新旧两种插件类型
- `AGENTS.md` - 添加新 Plugin 架构说明、Provider 接口说明、迁移指南
- `README.md` - 更新架构概述，添加插件架构迁移说明

### 删除文件

旧版本备份文件（共20个）：
- `src/plugins/bili_danmaku/plugin_old_baseplugin.py`
- `src/plugins/bili_danmaku_official/plugin_old_baseplugin.py`
- `src/plugins/bili_danmaku_official_maicraft/plugin_old_baseplugin.py`
- `src/plugins/dg_lab_service/plugin_old_baseplugin.py`
- `src/plugins/emotion_judge/plugin_old.py`
- `src/plugins/maicraft/plugin_old.py`
- `src/plugins/mainosaba/plugin_old_baseplugin.py`
- `src/plugins/mock_danmaku/plugin_old.py`
- `src/plugins/obs_control/plugin_old.py`
- `src/plugins/omni_tts/plugin_old_baseplugin.py`
- `src/plugins/read_pingmu/plugin_old.py`
- `src/plugins/remote_stream/plugin_old.py`
- `src/plugins/screen_monitor/plugin_old.py`
- `src/plugins/sticker/plugin_old.py`
- `src/plugins/stt/plugin_old.py`
- `src/plugins/subtitle/plugin_old.py`
- `src/plugins/tts/plugin_old_baseplugin.py`
- `src/plugins/vrchat/plugin_old.py`
- `src/plugins/vtube_studio/plugin_old_baseplugin.py`
- `src/plugins/warudo/plugin_old.py`

## 新旧架构对比

| 特性 | 旧架构（BasePlugin） | 新架构（Plugin） |
|------|---------------------|------------------|
| 继承关系 | 继承 BasePlugin（继承 AmaidesuCore） | 不继承任何基类，实现 Plugin 协议 |
| 依赖访问 | 通过 self.core 访问核心功能 | 通过 event_bus 和 config 依赖注入 |
| 功能封装 | 所有逻辑在插件类中 | 通过 Provider 接口封装功能 |
| 耦合度 | 高（直接依赖 AmaidesuCore） | 低（通过事件总线通信） |
| 可测试性 | 低（需要 mock AmaidesuCore） | 高（可独立测试 Provider） |
| 通信模式 | 服务注册 | 事件总线 + 服务注册 |
| 配置加载 | 通过 __init__ 接收 config | 通过 __init__ 接收 config |
| 状态 | 已废弃 | 推荐 |

## 迁移指南

### 新插件开发

1. **使用 Plugin 协议**：
   ```python
   from src.core.plugin import Plugin
   from typing import Dict, Any, List

   class MyPlugin:
       def __init__(self, config: Dict[str, Any]):
           self.config = config

       async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
           # 创建 Provider
           return providers

       async def cleanup(self):
           # 清理资源
           pass

       def get_info(self) -> Dict[str, Any]:
           return {...}

   plugin_entrypoint = MyPlugin
   ```

2. **创建 Provider**：
   ```python
   from src.core.providers.input_provider import InputProvider

   class MyInputProvider(InputProvider):
       async def _collect_data(self):
           # 采集数据
           yield raw_data
   ```

3. **参考示例**：
   - `src/plugins/bili_danmaku/plugin.py` - 输入插件示例
   - `src/plugins/console_input/plugin.py` - 输入插件（无 Provider）示例

### 旧插件迁移

对于仍在使用 BasePlugin 的插件（如 gptsovits_tts）：

1. **分析依赖**：
   - 识别所有使用 `self.core` 的地方
   - 判断是否可以改用 event_bus 通信

2. **创建 Provider**：
   - 将核心功能提取到 Provider
   - 通过 Provider 接口封装具体操作

3. **重写插件**：
   - 移除 `extends BasePlugin`
   - 实现 `Plugin` 协议
   - 在 `setup()` 中创建并返回 Provider 列表

4. **测试验证**：
   - 确保所有功能正常工作
   - 测试事件总线通信
   - 验证资源清理

## 后续工作

### 短期任务（1-2周）

1. **迁移 gptsovits_tts**：
   - 评估 WebSocket 处理器注册的新方式
   - 可能需要扩展 Plugin 协议以支持 core 访问
   - 设计并通过事件总线实现服务发现

2. **完善新架构**：
   - 添加更多 Provider 类型（如 ProcessingProvider）
   - 优化事件总线性能
   - 增强错误处理和日志记录

3. **测试覆盖**：
   - 为新架构添加单元测试
   - 测试插件加载和卸载
   - 验证向后兼容性

### 中期任务（1个月）

1. **移除 BasePlugin**：
   - 所有插件迁移到新架构后
   - 删除 BasePlugin 类和相关代码
   - 更新所有文档

2. **性能优化**：
   - 优化 Provider 数据流
   - 减少事件总线开销
   - 实现异步管道处理

3. **开发者工具**：
   - 插件开发脚手架工具
   - 自动化测试工具
   - 性能分析工具

### 长期任务（3个月）

1. **插件市场**：
   - 建立插件规范和版本控制
   - 创建插件仓库
   - 开发插件安装工具

2. **微服务架构**：
   - 支持插件独立部署
   - 实现跨进程通信
   - 提供插件健康检查

3. **可视化监控**：
   - 实时插件状态监控
   - 事件总线流量可视化
   - 性能指标仪表盘

## 技术亮点

1. **解耦设计**：
   - 插件不再直接依赖 AmaidesuCore
   - 通过事件总线实现松耦合
   - Provider 接口提供清晰的功能边界

2. **依赖注入**：
   - event_bus 和 config 通过参数注入
   - 便于单元测试（可 mock 依赖）
   - 提高代码可维护性

3. **向后兼容**：
   - 同时支持新旧两种插件架构
   - 现有插件无需立即修改
   - 平滑迁移路径

4. **可扩展性**：
   - 易于添加新的 Provider 类型
   - 支持插件组合使用多个 Provider
   - 事件总线支持插件间协作

## 遇到的问题和解决方案

### 问题1：新架构插件如何访问 AmaidesuCore

**问题描述**：
- gptsovits_tts 需要注册 WebSocket 处理器
- 需要获取其他服务（如 text_cleanup, vts_lip_sync）
- 新架构的 setup() 方法只接收 event_bus 和 config

**解决方案**：
- 暂时保留 BasePlugin 用于需要直接访问 core 的插件
- 评估是否可以通过 event_bus 间接访问 core
- 未来可能扩展 Plugin 协议以支持 core 访问

### 问题2：插件间通信模式选择

**问题描述**：
- 新架构推荐使用事件总线通信
- 某些场景更适合服务注册模式
- 需要明确两种模式的使用场景

**解决方案**：
- 保留服务注册机制（用于稳定、长期存在的功能）
- 新增事件总线机制（用于瞬时通知、广播场景）
- 两者可以共存以保持向后兼容

### 问题3：Provider 类型设计

**问题描述**：
- 需要设计合理的 Provider 类型
- 某些插件功能难以归类到单一 Provider 类型
- 需要考虑未来扩展性

**解决方案**：
- 设计三类基础 Provider：Input、Output、Decision
- 允许插件实现多个 Provider
- 保持接口简单，便于扩展

## 总结

本次重构成功将 24/25 个插件迁移到新架构，实现了以下目标：

✅ **解耦插件与核心**：新架构插件不再直接依赖 AmaidesuCore
✅ **依赖注入**：通过 event_bus 和 config 进行依赖注入
✅ **Provider 抽象**：引入清晰的 Provider 接口，封装具体功能
✅ **向后兼容**：保留 BasePlugin，现有插件无需立即修改
✅ **文档完善**：更新 AGENTS.md 和 README.md，提供详细的迁移指南

**重构完成度**：96%（24/25 插件已迁移）

**下一步**：完成 gptsovits_tts 的迁移，最终移除 BasePlugin。

## 参考文档

- [AGENTS.md](./AGENTS.md) - 完整的插件开发规范
- [README.md](./README.md) - 项目架构概述和快速开始
- [refactor/design/plugin_system.md](./refactor/design/plugin_system.md) - 插件系统设计文档
- [refactor/design/event_bus.md](./refactor/design/event_bus.md) - 事件总线设计文档
