# Draft: Plugin Migration Plan

## Requirements (confirmed)

**User's Goal**: 迁移 21 个插件从 BasePlugin 继承模式到新的 Plugin 接口模式

**Key Requirements**:
1. 保留 git 历史（不删除重建，使用 in-place 编辑）
2. extensions/ 仅作参考，不要迁移
3. 所有 21 个插件必须迁移
4. 迁移完成后删除 src/extensions/ 目录
5. 不在迁移期间创建测试文件（测试在所有迁移完成后进行）

## Technical Decisions

### Migration Pattern (from console_input, keyword_action)
已确认的迁移模式：
1. **Class 变更**：移除 BasePlugin 继承
   - 旧：`class XPlugin(BasePlugin):`
   - 新：`class XPlugin:`

2. **构造函数变更**：
   - 旧：`__init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any])`
   - 新：`__init__(self, config: Dict[str, Any])`

3. **setup() 方法变更**：
   - 旧：`async def setup(self)`
   - 新：`async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]`

4. **通信变更**：
   - 旧：`core.send_to_maicore()` 和 `core.register_websocket_handler()`
   - 新：`event_bus.emit()` 和 `event_bus.on()`

5. **新增方法**：
   - `async def cleanup(self)` - 清理资源
   - `def get_info(self) -> Dict[str, Any]` - 返回插件元数据

6. **入口点**：
   - 模块级别：`plugin_entrypoint = XPlugin`

7. **返回值**：
   - setup() 返回空列表 `[]`（暂时不创建 Provider）

## Research Findings

### Plugin Dependency Analysis (in progress)
- explore agent 正在分析插件依赖关系
- librarian agent 正在研究 Python 插件架构迁移最佳实践

### 21 待迁移插件列表

**Input Plugins (7)**:
1. bili_danmaku - B站弹幕（旧API）
2. bili_danmaku_official - B站弹幕（官方API）
3. bili_danmaku_official_maicraft - B站弹幕（MaiCraft集成）
4. read_pingmu - 屏幕监控
5. remote_stream - 远程流
6. screen_monitor - 屏幕监控
7. mock_danmaku - 模拟弹幕

**Output Plugins (7)**:
8. gptsovits_tts - GPT-SoVITS TTS（新TTS）
9. subtitle - 字幕显示
10. vtube_studio - VTube Studio 控制
11. vrchat - VRChat 集成
12. warudo - Warudo 集成
13. obs_control - OBS 控制
14. sticker - 表情贴纸

**Processing Plugins (3)**:
15. emotion_judge - 情感判断
16. stt - 语音识别
17. dg_lab_service - DG-Lab 服务

**Game/Hardware Plugins (2)**:
18. maicraft - MaiCraft
19. mainosaba - MainOSaba

**Old TTS Plugins (2)**:
20. tts - 旧TTS
21. omni_tts - Omni TTS

## Open Questions

- 每个插件的具体复杂度和依赖关系（探索任务进行中）
- 并行迁移的最佳分组策略（探索任务进行中）
- 验证每个插件迁移成功的具体步骤

## Scope Boundaries

### INCLUDE
- 迁移所有 21 个插件的 plugin.py 文件
- 保留 git 历史（in-place 编辑）
- 适配 event_bus 通信模式

### EXCLUDE
- 迁移 src/extensions/ 目录下的代码
- 在迁移期间创建测试文件
- 修改插件的业务逻辑（仅接口适配）
