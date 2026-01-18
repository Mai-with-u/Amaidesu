# P4 服务/工具插件迁移任务

## 待迁移插件

### 1. omni_tts
- [ ] 创建 Plugin 类包装现有 Provider
- [ ] 更新 `__init__.py` 导出
- [ ] 创建单元测试
- [ ] 运行 LSP 检查

### 2. dg_lab_service
- [ ] 创建 DGLabServiceOutputProvider (继承 OutputProvider)
- [ ] 重写 Plugin 类实现新协议
- [ ] 备份旧 plugin.py 为 plugin_old_baseplugin.py
- [ ] 创建单元测试
- [ ] 运行 LSP 检查

### 3. mainosaba
- [ ] 创建 MainosabaInputProvider (继承 InputProvider)
- [ ] 重写 Plugin 类实现新协议
- [ ] 备份旧 plugin.py 为 plugin_old_baseplugin.py
- [ ] 创建单元测试
- [ ] 运行 LSP 检查

### 跳过项
- command_processor - 是管道，不是插件
- message_replayer - 只有配置文件，没有代码
- arknights - 只有配置文件，没有代码
