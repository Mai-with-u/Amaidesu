# 服务管理器使用指南

## 概述

服务管理器（ServiceManager）是 Amaidesu 项目中的统一服务注册和生命周期管理机制。它提供了一个集中的地方来管理所有非 Provider 的共享服务。

## 为什么需要服务管理器？

在重构之前，服务通过 `setattr(core, "dg_lab_service", dg_lab_service)` 这种临时方式注册，存在以下问题：

1. **缺乏统一管理**：服务注册方式不一致
2. **没有生命周期管理**：服务的初始化和清理需要手动处理
3. **难以依赖注入**：其他组件很难获取服务实例
4. **类型安全缺失**：无法确保服务已注册或已初始化

服务管理器解决了这些问题，提供了统一的服务注册、获取和生命周期管理。

## 核心概念

### 服务协议

所有注册的服务应该实现以下方法（可选，但推荐）：

```python
class ServiceProtocol(Protocol):
    async def setup(self) -> None:
        """初始化服务"""
        ...

    async def cleanup(self) -> None:
        """清理服务资源"""
        ...

    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        ...
```

### 服务命名规范

服务名称使用小写字母和下划线，例如：
- `llm` - LLM 服务
- `dg_lab` - DG-LAB 硬件控制服务
- `config` - 配置服务
- `context` - 上下文管理器

## 使用方法

### 1. 在 AmaidesuCore 中注册服务

在 `main.py` 的 `create_app_components` 函数中：

```python
# 创建服务实例
dg_lab_service = DGLabService(dg_lab_config_obj)
await dg_lab_service.setup()

# 注册到核心
core.register_service("dg_lab", dg_lab_service)
```

### 2. 通过 AmaidesuCore 获取服务

其他组件可以通过 AmaidesuCore 获取服务：

```python
# 检查服务是否存在
if core.has_service("dg_lab"):
    # 获取服务实例
    dg_lab = core.get_service("dg_lab")

    # 检查服务是否就绪
    if core.is_service_ready("dg_lab"):
        await dg_lab.trigger_shock(strength=10)
```

### 3. 使用全局服务函数（简单场景）

对于简单的使用场景，可以使用全局服务函数：

```python
from src.services import get_service, has_service

# 检查并获取服务
if has_service("dg_lab"):
    dg_lab = get_service("dg_lab")
    await dg_lab.trigger_shock(strength=10)
```

### 4. 直接使用 ServiceManager（高级场景）

对于需要更多控制的场景，可以直接使用 ServiceManager：

```python
from src.services.manager import ServiceManager

# 创建管理器实例
manager = ServiceManager()

# 注册服务
manager.register("my_service", service_instance, config={...})

# 获取服务
service = manager.get("my_service")

# 初始化/清理服务
await manager.setup_service("my_service")
await manager.cleanup_service("my_service")

# 批量操作
await manager.setup_all()
await manager.cleanup_all()
```

## 服务生命周期

### 1. 注册阶段

```python
core.register_service("service_name", service_instance)
```

### 2. 初始化阶段

在 `AmaidesuCore.disconnect()` 中自动调用：

```python
await self._service_manager.cleanup_all()
```

或者手动初始化：

```python
await core.service_manager.setup_service("service_name")
```

### 3. 使用阶段

```python
service = core.get_service("service_name")
await service.some_method()
```

### 4. 清理阶段

在应用关闭时自动清理（按注册相反顺序）：

```python
await core.service_manager.cleanup_all()
```

## 内置服务

项目中有以下内置服务：

| 服务名 | 类型 | 描述 |
|--------|------|------|
| `llm` | LLMManager | LLM 客户端管理器 |
| `dg_lab` | DGLabService | DG-LAB 硬件控制服务 |
| `config` | ConfigService | 配置服务 |
| `context` | ContextManager | 上下文管理器 |

## 最佳实践

### 1. 服务初始化

服务应该在 `main.py` 的 `create_app_components` 函数中初始化：

```python
# 创建并初始化服务
service = MyService(config)
await service.setup()

# 注册到核心
core.register_service("my_service", service)
```

### 2. 服务依赖

如果服务之间有依赖关系，按照依赖顺序注册：

```python
# 先注册基础服务
core.register_service("config", config_service)

# 再注册依赖配置的服务
core.register_service("llm", llm_service)
```

### 3. 错误处理

获取服务时始终检查是否存在：

```python
service = core.get_service("optional_service")
if service is None:
    logger.warning("可选服务不可用")
    return

# 使用服务
await service.do_something()
```

### 4. 可选服务

对于可选服务（如 DGLabService），使用条件注册：

```python
if dg_lab_config and DG_LAB_AVAILABLE:
    try:
        dg_lab_service = DGLabService(dg_lab_config_obj)
        await dg_lab_service.setup()
        core.register_service("dg_lab", dg_lab_service)
    except Exception as e:
        logger.error(f"初始化 DGLab 服务失败: {e}")
```

## 添加新服务

### 步骤 1: 创建服务类

```python
# src/services/my_service/service.py
class MyService:
    def __init__(self, config):
        self.config = config

    async def setup(self):
        """初始化服务"""
        pass

    async def cleanup(self):
        """清理服务"""
        pass

    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        return True

    async def do_work(self):
        """服务的主要功能"""
        pass
```

### 步骤 2: 在 main.py 中初始化

```python
# main.py
from src.services.my_service import MyService

async def create_app_components(...):
    # 创建服务实例
    my_service = MyService(config.get("my_service", {}))
    await my_service.setup()

    # 注册到核心
    core.register_service("my_service", my_service)
```

### 步骤 3: 在其他组件中使用

```python
# 在任何需要的地方
my_service = core.get_service("my_service")
if my_service:
    await my_service.do_work()
```

## API 参考

### ServiceManager

| 方法 | 描述 |
|------|------|
| `register(name, service, config=None)` | 注册服务 |
| `get(name)` | 获取服务实例 |
| `has(name)` | 检查服务是否已注册 |
| `is_ready(name)` | 检查服务是否就绪 |
| `list_services()` | 列出所有服务名称 |
| `get_service_info()` | 获取所有服务的信息 |
| `setup_service(name)` | 初始化单个服务 |
| `cleanup_service(name)` | 清理单个服务 |
| `setup_all()` | 初始化所有服务 |
| `cleanup_all()` | 清理所有服务 |
| `unregister(name)` | 注销服务 |

### AmaidesuCore 服务方法

| 方法 | 描述 |
|------|------|
| `service_manager` | 获取服务管理器实例 |
| `register_service(name, service)` | 注册服务到核心 |
| `get_service(name)` | 获取服务实例 |
| `has_service(name)` | 检查服务是否已注册 |
| `is_service_ready(name)` | 检查服务是否就绪 |
| `list_services()` | 列出所有服务名称 |

### 全局函数

| 函数 | 描述 |
|------|------|
| `get_service(name)` | 获取全局服务实例 |
| `has_service(name)` | 检查全局服务是否已注册 |
| `list_services()` | 列出所有全局服务名称 |

## 示例：DGLab 服务集成

完整的 DGLab 服务集成示例：

```python
# main.py
from src.services.dg_lab import DGLabService, DGLabConfig

async def create_app_components(...):
    # 1. 检查依赖
    dg_lab_service = None
    dg_lab_config = config.get("dg_lab", {})

    if dg_lab_config and DG_LAB_AVAILABLE:
        try:
            # 2. 创建配置对象
            dg_lab_config_obj = DGLabConfig(**dg_lab_config)

            # 3. 创建服务实例
            dg_lab_service = DGLabService(dg_lab_config_obj)

            # 4. 初始化服务
            await dg_lab_service.setup()
            logger.info("DGLab 服务已初始化")

            # 5. 注册到核心
            core.register_service("dg_lab", dg_lab_service)
            logger.info("DGLab 服务已注册到服务管理器")

        except Exception as e:
            logger.error(f"初始化 DGLab 服务失败: {e}")
            dg_lab_service = None

    # 6. 在其他组件中使用
    # 例如：在某个 Provider 中
    if core.has_service("dg_lab"):
        dg_lab = core.get_service("dg_lab")
        await dg_lab.trigger_shock(strength=10)
```

## 相关文档

- [Provider 开发指南](./provider-guide.md)
- [开发规范](./development-guide.md)
- [架构概览](../architecture/overview.md)
