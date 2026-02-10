# DGLab 服务

DG-LAB 硬件控制服务，作为共享基础设施供其他组件调用。

## 概述

这是一个**服务（Service）**，不是 Provider：
- 不产生数据流
- 不订阅事件
- 提供共享 API 供其他组件通过依赖注入调用

## 功能

- **硬件连接管理**：通过 HTTP API 连接到 DG-Lab 设备
- **参数编码/解码**：支持强度（0-200）、波形预设、双通道控制
- **安全限制**：可配置最大强度、超时保护
- **并发控制**：防止多个电击序列同时运行

## 配置

在 `config.toml` 中添加：

```toml
[dg_lab]
# DG-Lab API 地址
api_base_url = "http://127.0.0.1:8081"
# 默认强度 (0-200)
default_strength = 10
# 默认波形预设: small | medium | big | random
default_waveform = "big"
# 默认电击持续时间（秒）
shock_duration_seconds = 2.0
# HTTP 请求超时时间（秒）
request_timeout = 5.0
# 最大强度限制（安全保护）
max_strength = 50
# 是否启用安全限制（推荐启用）
enable_safety_limit = true
```

## 使用示例

### 在其他组件中通过核心访问

```python
class MyOutputProvider:
    def __init__(self, core: AmaidesuCore, config: dict):
        self.core = core
        self.config = config

    async def handle_intent(self, intent: Intent):
        # 获取 DGLab 服务
        dg_lab_service = getattr(self.core, "dg_lab_service", None)

        if dg_lab_service and dg_lab_service.is_ready():
            # 触发电击
            await dg_lab_service.trigger_shock(
                strength=15,
                waveform="big",
                duration=2.0
            )
```

### 直接使用服务

```python
from src.services.dg_lab import DGLabService, DGLabConfig

# 创建配置
config = DGLabConfig(
    api_base_url="http://127.0.0.1:8081",
    default_strength=10,
    default_waveform="big",
    max_strength=50,
)

# 创建服务
service = DGLabService(config)

# 初始化
await service.setup()

# 触发电击
await service.trigger_shock(strength=20, duration=3.0)

# 检查状态
status = service.get_status()
print(f"运行中: {status.is_running}")

# 清理
await service.cleanup()
```

## API 参考

### DGLabService

#### `trigger_shock(strength, waveform, duration)`

触发电击序列。

**参数：**
- `strength` (int, 可选): 强度 (0-200)，为 None 时使用配置默认值
- `waveform` (str, 可选): 波形预设 (small/medium/big/random)，为 None 时使用配置默认值
- `duration` (float, 可选): 持续时间（秒），为 None 时使用配置默认值

**返回：**
- `bool`: 是否成功执行

**安全限制：**
- 如果启用安全限制，强度不会超过 `max_strength`
- 如果已有电击序列在运行，本次调用会被忽略

**示例：**
```python
# 使用默认参数
await service.trigger_shock()

# 自定义参数
await service.trigger_shock(strength=20, waveform="small", duration=1.5)
```

#### `get_status()`

获取当前状态。

**返回：**
- `ShockStatus`: 状态对象，包含：
  - `is_running`: 是否正在运行
  - `current_strength`: 当前强度
  - `current_waveform`: 当前波形

#### `is_ready()`

检查服务是否就绪。

**返回：**
- `bool`: 是否已初始化且可用

## 依赖

- `aiohttp`: HTTP 客户端库

安装依赖：
```bash
uv add aiohttp
```

## 测试

运行单元测试：
```bash
uv run pytest tests/services/test_dg_lab_service.py -v
```

## 注意事项

1. **安全第一**：建议始终启用 `enable_safety_limit`
2. **设备连接**：确保 DG-Lab API 服务正在运行（默认 `http://127.0.0.1:8081`）
3. **并发控制**：同一时间只能有一个电击序列运行
4. **超时保护**：设置合理的 `request_timeout` 避免长时间阻塞

## 架构设计

### 为什么是 Service 而不是 Provider？

DGLabService 不符合 Provider 的定义：
- **不是数据生产者**：不采集或产生数据流
- **不是数据消费者**：不订阅事件处理数据
- **是共享基础设施**：提供命令式 API 供其他组件调用

类似的共享服务包括：
- `LLMManager`: 提供 LLM 调用接口
- `ConfigService`: 提供配置访问

### 集成方式

服务在 `main.py` 中初始化并注册到核心：
```python
# main.py
dg_lab_service = DGLabService(dg_lab_config)
await dg_lab_service.setup()
setattr(core, "dg_lab_service", dg_lab_service)
```

其他组件通过核心访问：
```python
dg_lab_service = getattr(core, "dg_lab_service", None)
if dg_lab_service and dg_lab_service.is_ready():
    await dg_lab_service.trigger_shock()
```

这种设计符合**依赖注入**原则，便于测试和维护。
