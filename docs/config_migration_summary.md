# 配置系统迁移总结

## 迁移日期
2026-02-01

## 迁移原因
`src/config/config.py` 中的配置实现与 `src/utils/config.py` 功能重复，且前者几乎无人使用。

## 迁移内容

### ✅ 已迁移到 `src/utils/config.py`

| 功能 | 原位置 | 新位置 | 状态 |
|------|--------|--------|------|
| 版本号解析 | src/config/config.py | src/utils/config.py | ✅ 完成 |
| 版本比较 | src/config/config.py | src/utils/config.py | ✅ 完成 |
| 配置文件更新（保留注释） | src/config/config.py | src/utils/config.py | ✅ 完成 |
| 配置合并（智能合并） | src/config/config.py | src/utils/config.py | ✅ 完成 |

### ✅ 新增功能

- `check_and_update_config_with_version()` - 高级接口，自动检查并更新配置文件版本

### ❌ 已删除（未使用的遗留代码）

- `src/config/config.py` - 整个文件
- `src/config/config-template.toml` - 旧配置模板
- `src/config/__init__.py` - 空文件
- `global_config` 全局变量 - 无人使用
- `LLMConfig`, `VLMConfig` 等 Pydantic 模型 - 未被引用

## 新架构

```
main.py
  ↓
ConfigService (src/services/config_service.py)
  ├─ 统一配置管理接口（Facade模式）
  └─ 提供插件/管道/Provider配置合并
  ↓
utils/config.py (src/utils/config.py)
  ├─ load_config() - 加载 TOML 配置
  ├─ check_and_setup_main_config() - 主配置初始化
  ├─ check_and_setup_plugin_configs() - 插件配置初始化
  ├─ check_and_setup_pipeline_configs() - 管道配置初始化
  ├─ load_component_specific_config() - 组件配置加载
  ├─ merge_component_configs() - 配置合并
  └─ check_and_update_config_with_version() - 版本检查和自动更新 ⭐ NEW
  ↓
config.toml (根目录)
```

## 新增 API

### `check_and_update_config_with_version()`

```python
from src.utils.config import check_and_update_config_with_version

# 自动检查配置文件版本，如果需要则从模板更新
updated = check_and_update_config_with_version(
    config_path="config.toml",
    template_path="config-template.toml"
)

if updated:
    print("配置文件已更新")
```

**功能特点：**
- 自动检测版本号（从 `[inner]` section 读取）
- 智能合并：保留用户自定义设置
- 保留注释：从模板提取注释并保留
- 创建备份：更新前自动创建 `.backup` 文件
- 日志完整：详细记录更新过程

## 测试验证

运行迁移验证测试：
```bash
uv run python tests/test_config_migration.py
```

**测试结果：** ✅ 4/4 通过

1. ✅ 函数导入测试
2. ✅ 版本解析功能测试
3. ✅ 目录删除验证
4. ✅ 无遗留引用检查

## 使用示例

### 在 main.py 中启用自动版本检查

```python
from src.utils.config import check_and_update_config_with_version

# 在加载配置前检查版本
config_updated = check_and_update_config_with_version(
    config_path="config.toml",
    template_path="config-template.toml"
)

# 然后正常加载配置
config = load_config()
```

## 影响范围

### 无需修改的代码
- ✅ `src/services/config_service.py` - 继续使用 `utils/config`
- ✅ 所有插件和管道 - 配置加载逻辑不变
- ✅ 测试代码 - 无测试使用旧的 `src.config`

### 需要注意的事项
- 如果未来需要 Pydantic 类型验证，可以重新添加 `LLMConfig` 等模型
- 建议在 `config-template.toml` 中添加 `[inner]` section 来启用版本跟踪

## 文件变更

### 新增文件
- `tests/test_config_migration.py` - 迁移验证测试

### 修改文件
- `src/utils/config.py` - 新增版本检查和自动更新功能

### 删除文件
- `src/config/config.py` - 旧配置实现
- `src/config/config-template.toml` - 旧配置模板
- `src/config/__init__.py` - 空文件
- `src/config/` - 整个目录

## 后续建议

### 可选优化
1. **在 main.py 中集成版本检查**：启动时自动检查配置版本
2. **添加版本号到 config-template.toml**：确保版本跟踪功能正常工作
3. **监控配置更新日志**：观察用户反馈，优化合并策略

### 未来增强
- 如果需要强类型验证，可以重新添加 Pydantic 模型到 `src/core/` 或 `src/utils/`
- 考虑添加配置迁移脚本（处理版本间的结构变化）
- 添加配置验证功能（检查必填字段、格式等）

## 回滚方案

如果需要回滚，可以：
1. 从 git 历史恢复 `src/config/` 目录
2. 删除 `src/utils/config.py` 中新增的版本检查函数
3. 更新所有引用 `src.utils.config` 的代码回 `src.config.config`

**但当前迁移已验证成功，不建议回滚。**

---

迁移完成时间：2026-02-01
迁移人员：Claude Code
验证状态：✅ 通过
