# DG-Lab Service 插件

## 概述

`DGLabServicePlugin` 是一个纯粹的**服务提供插件**。

它的唯一职责是封装与 DG-Lab 硬件（通过 `fucking-3.0` 中间件）的 HTTP API 通信的逻辑，并将这些功能作为一项名为 `dg_lab_control` 的服务暴露给核心系统。

这个插件自身**不监听**任何用户消息，也**不主动触发**任何操作。它是一个被动的能力提供者，等待其他插件或脚本来调用。

## 功能

- **提供服务**: 在 `setup` 阶段，将自身注册为 `dg_lab_control` 服务。
- **提供方法**: 暴露一个公开的 `async def trigger_shock(...)` 方法，允许其他插件触发一次完整的电击序列（设置强度 -> 等待 -> 强度归零）。
- **参数化控制**: `trigger_shock` 方法接受 `strength`, `waveform`, 和 `duration` 等可选参数，允许调用方覆盖配置文件中的默认值，实现灵活控制。
- **并发安全**: 使用 `asyncio.Lock` 确保同一时间只有一个电击序列在执行，防止命令冲突。

## 依赖

- **硬件/中间件**: 需要 [fucking-3.0](https://github.com/zzzzzyc/fucking-3.0) 项目正在运行，并已开启其 HTTP API 服务。
- **Python库**: `aiohttp`

## 配置

插件的配置选项位于其目录下的 `config-template.toml` 或 `config.toml` 文件中。

```toml
# DG-LAB HTTP API 的基础 URL
dg_lab_api_base_url = "http://127.0.0.1:8081" 

# 默认的触发参数
target_strength = 20
target_waveform = "big"
shock_duration_seconds = 2

# HTTP 请求超时（秒）
request_timeout = 5
```

## 使用方法

在其他插件或动作脚本中，可以通过核心实例来获取和使用此服务：

```python
# 获取服务实例
dg_lab_service = core.get_service("dg_lab_control")

if dg_lab_service:
    # 使用默认配置触发
    await dg_lab_service.trigger_shock()

    # 或者使用自定义参数触发
    await dg_lab_service.trigger_shock(strength=50, duration=0.5)
``` 