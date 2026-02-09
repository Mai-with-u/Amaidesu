# 插件迁移完成清单

## 概述

本文档记录了从旧插件系统到新 Provider 系统的迁移完成状态。所有功能都已成功迁移到新架构，无需担心向后兼容性问题。

## 迁移状态：✅ 完成

### Input Provider 迁移状态

| 插件名 | 旧位置 | 新位置 | 迁移状态 | 说明 |
|--------|--------|--------|----------|------|
| bili_danmaku | `plugins/bili_danmaku/` | `src/domains/input/providers/bili_danmaku/` | ✅ 完成迁移 | 完全重构为 Provider |
| bili_danmaku_official | `plugins/bili_danmaku_official/` | `src/domains/input/providers/bili_danmaku_official/` | ✅ 完成迁移 | 完全重构为 Provider |
| console_input | `plugins/console_input/` | `src/domains/input/providers/console_input/` | ✅ 完成迁移 | 完全重构为 Provider |
| stt | `plugins/stt/` | `src/domains/input/providers/stt/` | ✅ 完成迁移 | 完全重构为 Provider |
| mainosaba | `plugins/mainosaba/` | `src/domains/input/providers/mainosaba/` | ✅ 完成迁移 | 完全重构为 Provider |
| read_pingmu | `plugins/read_pingmu/` | `src/domains/input/providers/read_pingmu/` | ✅ 完成迁移 | 完全重构为 Provider |
| mock_danmaku | `plugins/mock_danmaku/` | `src/domains/input/providers/mock_danmaku/` | ✅ 完成迁移 | 完全重构为 Provider |

### Decision Provider 迁移状态

| 插件名 | 旧位置 | 新位置 | 迁移状态 | 说明 |
|--------|--------|--------|----------|------|
| keyword_action | `plugins/keyword_action/` | `src/domains/decision/providers/keyword_action/` | ✅ 完成迁移 | 完全重构为 Provider |
| maicraft | `plugins/maicraft/` | `src/domains/decision/providers/maicraft/` | ✅ 完成迁移 | 完全重构为 Provider |
| emotion_judge | `plugins/emotion_judge/` | ❌ 不迁移 | 保留在备份 | 原因：被合并到 MaiCore 决策逻辑 |

### Output Provider 迁移状态

| 插件名 | 旧位置 | 新位置 | 迁移状态 | 说明 |
|--------|--------|--------|----------|------|
| tts | `plugins/tts/` | `src/domains/output/providers/tts/` | ✅ 完成迁移 | 完全重构为 Provider |
| subtitle | `plugins/subtitle/` | `src/domains/output/providers/subtitle/` | ✅ 完成迁移 | 完全重构为 Provider |
| vts | `plugins/vtube_studio/` | `src/domains/output/providers/vts/` | ✅ 完成迁移 | 重命名并重构 |
| gptsovits | `plugins/gptsovits_tts/` | `src/domains/output/providers/gptsovits/` | ✅ 完成迁移 | 完全重构为 Provider |
| omni_tts | `plugins/omni_tts/` | `src/domains/output/providers/omni_tts/` | ✅ 完成迁移 | 完全重构为 Provider |
| vrchat | `plugins/vrchat/` | `src/domains/output/providers/avatar/` | ✅ 完成迁移 | 合并到 avatar Provider |
| sticker | `plugins/sticker/` | `src/domains/output/providers/sticker/` | ✅ 完成迁移 | 完全重构为 Provider |
| obs_control | `plugins/obs_control/` | `src/domains/output/providers/obs_control/` | ✅ 完成迁移 | 完全重构为 Provider |
| warudo | `plugins/warudo/` | `src/domains/output/providers/warudo/` | ✅ 完成迁移 | 完全重构为 Provider |
| remote_stream | `plugins/remote_stream/` | `src/domains/output/providers/remote_stream/` | ✅ 完成迁移 | 完全重构为 Provider |
| avatar | ❌ 新增 | `src/domains/output/providers/avatar/` | ✅ 新增 | 合并了 vrchat 的功能 |

### Service Provider 迁移状态

| 插件名 | 旧位置 | 新位置 | 迁移状态 | 说明 |
|--------|--------|--------|----------|------|
| dg_lab | `plugins/dg_lab_service/` | `src/services/dg_lab/` | ✅ 完成迁移 | 完全重构为 Service |

## 架构改进

### 1. Provider 系统优势
- **类型安全**：所有 Provider 都继承基类，提供统一的接口
- **配置驱动**：通过 TOML 配置文件启用/禁用 Provider
- **生命周期管理**：由 ProviderManager 统一管理
- **错误隔离**：单个 Provider 不会影响其他 Provider

### 2. 数据流优化
- 严格遵守 **3域架构**：Input → Decision → Output
- 使用 **EventBus** 作为唯一的跨域通信机制
- 避免了循环依赖和数据混乱

### 3. 配置管理
- 所有配置统一在 `config-template.toml` 中管理
- 支持常量覆盖和自定义参数
- 支持运行时动态切换 Provider

## 注意事项

### 1. 配置文件更新
- 旧配置文件格式已废弃
- 必须使用新的 Provider 配置格式
- 参考 `config-template.toml` 进行配置

### 2. 事件系统
- 所有插件间的通信都通过 EventBus 完成
- 使用 `CoreEvents` 常量确保类型安全
- 不再允许直接的 Provider 间调用

### 3. 错误处理
- 单个 Provider 错误不会影响整体运行
- 提供了完善的错误处理机制
- 支持 graceful fallback

## 验证命令

运行以下命令验证迁移是否成功：

```bash
# 1. 运行架构测试
uv run pytest tests/architecture/test_event_flow_constraints.py -v

# 2. 运行所有 Provider 测试
uv run pytest tests/ -v

# 3. 检查代码格式和风格
uv run ruff check .
uv run ruff format .
```

## 总结

✅ **所有功能已成功迁移到新架构**
✅ **无功能缺失**
✅ **性能提升**
✅ **可维护性提高**
✅ **类型安全保证**

旧插件代码已归档到 `plugins_backup/` 目录中，仅供参考和对比使用。所有后续开发都应基于新的 Provider 架构进行。

---

*最后更新：2026-02-09*