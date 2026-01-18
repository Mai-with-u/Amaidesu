# Phase 2: P2中等插件迁移 - TODO列表

## 迁移的5个插件
1. bili_danmaku_official - B站官方弹幕插件 ✓ (已完成)
2. bili_danmaku_official_maicraft - B站官方弹幕+Minecraft插件 ✓ (已完成)
3. vtube_studio - VTubeStudio虚拟形象控制插件 ✓ (已完成)
4. tts - TTS语音合成插件 ⏳ (进行中)
5. gptsovits_tts - GPT-SoVITS TTS插件

## vtube_studio迁移进度
- [x] 创建VTSOutputProvider类
- [x] 重写VTubeStudioPlugin类，移除BasePlugin继承
- [x] 实现新的Plugin协议
- [x] 更新导入
- [x] 创建单元测试
- [x] 运行ruff检查通过
- [x] git mv保留历史

## tts迁移步骤
- [ ] 创建TTSOutputProvider类
- [ ] 重写TTSPlugin类，移除BasePlugin继承
- [ ] 实现新的Plugin协议
- [ ] 更新导入
- [ ] 创建单元测试
- [ ] 运行lsp_diagnostics验证

## gptsovits_tts迁移步骤
- [ ] 创建GPTSoVITSOutputProvider类
- [ ] 重写TTSPlugin类，移除BasePlugin继承
- [ ] 实现新的Plugin协议
- [ ] 更新导入
- [ ] 创建单元测试
- [ ] 运行lsp_diagnostics验证

## 验证要求
- 每个插件的迁移必须保留git历史（如需要移动文件）
- 所有代码必须通过lsp_diagnostics检查
- 每个插件必须有对应的单元测试
- 迁移后的插件必须能正常工作（至少代码层面）
