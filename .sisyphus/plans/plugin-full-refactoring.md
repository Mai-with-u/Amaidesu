# Plan: Complete Plugin Refactoring (All Plugins)

## Context

### Original Request
用户要求进行完全的重构，具体是：
- 重构所有插件到新的Plugin接口（src/core/plugin.py）
- 保留plugins的原有git历史（不删除重建）
- extensions/仅作参考，重构完成后删除
- 重构完成后测试

### Interview Summary
**User's Requirements**:
- 完全重构所有21个待迁移插件
- 保留原有git历史（使用in-place编辑而非删除重建）
- 使用src/extensions/仅作为参考材料（不要迁移extensions）
- 所有插件重构完成后进行测试（不是重构过程中）
- 测试使用现有的pytest基础设施

**Technical Decisions**:
- 使用Plugin Protocol接口（不继承任何基类）
- 使用EventBus进行通信，不再通过core
- 保持PluginManager的双系统兼容性（BasePlugin和Plugin共存）
- 测试基础设施已存在（pytest + pytest-asyncio）

### Research Findings

**1. Test Infrastructure Analysis (Completed)**
- pytest框架存在，支持pytest-asyncio
- tests/目录有12个核心测试文件
- src/plugins/*/test_*.py已有插件特定测试
- 推荐使用pytest-native模式（@pytest.mark.asyncio）
- 测试示例：test_decision_manager.py, test_extension_system.py

**2. Extensions Analysis (Completed)**
- src/extensions/包含22个扩展+1个示例
- Extensions使用Adapter Pattern包装旧BasePlugin
- 每个extension定义自己的CoreWrapper（非共享类）
- CoreWrapper映射：
  - send_to_maicore() → event_bus.emit("input.raw_data", msg, source)
  - register_websocket_handler() → event_bus.listen_event("websocket.{type}", handler)
  - register_service()/get_service() → 内部_services字典
- Extensions是临时状态，目标是Provider-first架构

**3. Refactored Plugins Pattern Study (Completed)**
- 迁移模式已从console_input和keyword_action建立
- **Class变更**：移除BasePlugin继承，__init__改为只接受config
- **setup()变更**：async def setup(self, event_bus, config) -> List[Any]
- **事件通信变更**：使用event_bus.on()和event_bus.emit()代替core方法
- **必需方法**：async def cleanup(self), def get_info(self) -> Dict[str, Any]
- **返回值**：返回Provider列表或空列表

**4. Plugins to Migrate (21 plugins)**
**Input/Data (6)**:
1. bili_danmaku - B站直播弹幕
2. bili_danmaku_official - B站官方API弹幕
3. bili_danmaku_official_maicraft - MaiCraft的B站弹幕
4. read_pingmu - 屏幕监控读取
5. remote_stream - 远程流
6. screen_monitor - 屏幕监控
7. mock_danmaku - 模拟弹幕

**Output/Rendering (6)**:
1. gptsovits_tts - GPTSoVITS TTS引擎
2. subtitle - 字幕显示
3. sticker - 贴图/表情显示
4. vtube_studio - VTS虚拟形象控制
5. vrchat - VRChat OSC控制
6. warudo - Warudo虚拟形象控制
7. obs_control - OBS Studio控制

**Processing/Logic (3)**:
1. emotion_judge - 情感识别+触发
2. stt - 语音转文字（VAD + 讯飞API）
3. dg_lab_service - DG Lab集成

**Game/Hardware (2)**:
1. maicraft - Minecraft集成
2. mainosaba - Mainosaba集成

**Other/Old (2)**:
1. tts - TTS插件
2. omni_tts - Omni TTS插件

**Migrated (2)**:
1. console_input ✅
2. keyword_action ✅

**Total**: 23 plugins (2 migrated + 21 pending)

---

## Work Objectives

### Core Objective
将所有21个待迁移插件从BasePlugin架构迁移到Plugin Protocol接口，同时保留原有git历史记录。

### Concrete Deliverables
- 21个迁移后的插件文件（in-place编辑）
- 每个插件都实现Plugin Protocol接口
- 保留原有配置文件和测试文件（如果有）
- 删除src/extensions/目录（所有迁移完成后）
- 迁移后验证所有插件可正常加载

### Definition of Done
- [ ] 所有21个插件已迁移到Plugin接口
- [ ] 每个插件都通过PluginManager正确识别和加载
- [ ] 项目启动无WARNING级别错误
- [ ] src/extensions/目录已删除
- [ ] 所有原有功能保持正常（通过手动测试验证）

### Must Have
- 迁移所有21个插件（无遗漏）
- 保留原有git历史（in-place编辑）
- 使用EventBus代替AmaidesuCore进行通信
- 实现setup(event_bus, config)方法并返回List[Any]
- 实现cleanup()方法正确清理资源
- 实现get_info()方法返回插件元数据
- 保持PluginManager双系统兼容性

### Must NOT Have (Guardrails)
- **不使用git mv**迁移文件（保留历史）
- **不删除重建**插件文件（in-place编辑）
- **不修改src/extensions/**（仅作参考）
- **不在迁移期间**创建测试文件（测试在所有迁移完成后进行）
- **不重构**已迁移的console_input和keyword_action（保持现有状态）
- **不创建Provider类**（插件可直接返回空列表，使用EventBus通信）
- **不添加新功能**（仅迁移现有功能）

---

## Verification Strategy (MANDATORY)

### Test Decision
- **Infrastructure exists**: YES
- **User wants tests**: YES (after refactoring is complete)
- **Framework**: pytest with pytest-asyncio
- **Timing**: After all 21 plugins are migrated (not during refactoring)

### Manual QA Strategy (After Refactoring Complete)

Since testing happens after all refactoring is complete, manual verification MUST be exhaustive:

**1. Startup Verification**:
- [ ] Run: `python main.py`
- [ ] Verify: No WARNING-level errors related to plugins
- [ ] Verify: All 23 plugins load successfully (2 migrated + 21 newly migrated)
- [ ] Verify: PluginManager correctly identifies all plugins as Plugin type

**2. Functionality Verification** (for each plugin category):

**Input Plugins** (bili_danmaku, bili_danmaku_official, etc.):
- [ ] Run app and trigger input events
- [ ] Verify event_bus emits events correctly
- [ ] Check logs for plugin-specific output

**Output Plugins** (tts, vtube_studio, vrchat, etc.):
- [ ] Run app with configuration
- [ ] Verify plugin receives events and processes them
- [ ] Check output device/external service works

**Processing Plugins** (emotion_judge, stt, keyword_action, etc.):
- [ ] Run app with test messages
- [ ] Verify processing logic works
- [ ] Check log output for processing steps

**Game/Hardware Plugins** (maicraft, mainosaba):
- [ ] Verify external service connections
- [ ] Check integration events work correctly

**3. Configuration Verification**:
- [ ] Verify config.toml files are still valid
- [ ] Check plugin-specific configs load correctly
- [ ] Test with disabled plugins

**Evidence Required**:
- Terminal output from startup
- Screenshots for UI changes
- Log excerpts showing plugin loading
- Test configuration files

---

## Task Flow

```
Phase 1: Prepare → Phase 2: Migrate → Phase 3: Verify → Phase 4: Cleanup
```

## Parallelization

| Group | Tasks | Reason |
|-------|-------|--------|
| A | bili_danmaku, bili_danmaku_official, bili_danmaku_official_maicraft (3) | Independent input plugins |
| B | read_pingmu, remote_stream, screen_monitor, mock_danmaku (4) | Independent input plugins |
| C | gptsovits_tts, subtitle, vtube_studio, vrchat, warudo (5) | Independent output plugins |
| D | emotion_judge, stt, dg_lab_service (3) | Independent processing plugins |
| E | maicraft, mainosaba (2) | Independent game/hardware plugins |
| F | obs_control, sticker (2) | Output plugins with dependencies |
| G | tts, omni_tts (2) | Old TTS plugins (special handling) |

All groups are independent - can run in parallel for Groups A-E, Group F requires Group C to be done first (sticker depends on vtube_studio).

---

## TODOs

### Phase 1: Preparation (5-10 minutes)

- [ ] 0. Verify PluginManager dual-system support
  - What to do: Check that plugin_manager.py correctly handles both BasePlugin and Plugin types
  - Must NOT do: Modify plugin_manager.py (already has dual-system support)
  - Verification: Read plugin_manager.py lines 366-442, confirm auto-detection logic exists

- [ ] 1. Backup current plugin directory (optional)
  - What to do: Create backup of src/plugins/ before migration
  - Command: `cp -r src/plugins/ src/plugins.backup.$(date +%Y%m%d_%H%M%S)`
  - Parallelizable: YES (independent task)
  - Acceptance Criteria: Backup directory exists, file count matches original

### Phase 2: Migrate Plugins (Group A - Input Plugins, 30-60 minutes)

- [ ] 2. Migrate bili_danmaku plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/bili_danmaku/plugin.py
  - Preserves git history: YES (in-place edit)
  - Parallelizable: YES
  - Must NOT do: Delete and recreate file, move file, change config paths

  - Steps:
    1. Remove `class BiliDanmakuPlugin(BasePlugin):`
    2. Change to `class BiliDanmakuPlugin:`
    3. Change `__init__(self, core, plugin_config)` to `__init__(self, config)`
    4. Update `async def setup(self)` to `async def setup(self, event_bus, config) -> List[Any]`
    5. Add `self.event_bus = event_bus`
    6. Replace `self.core.send_to_maicore()` with `await self.event_bus.emit("input.raw_data", msg, self.__class__.__name__)`
    7. Replace `self.core.register_websocket_handler()` with `event_bus.on()`
    8. Add `async def cleanup(self)` method
    9. Add `def get_info(self) -> Dict[str, Any]` method
    10. Return `[]` from setup() (no providers for now)

  - Acceptance Criteria:
    - [ ] Class no longer inherits BasePlugin
    - [ ] __init__ accepts only config parameter
    - [ ] setup() has signature async def setup(self, event_bus, config) -> List[Any]
    - [ ] Event communication uses event_bus.emit() and event_bus.on()
    - [ ] cleanup() method implemented
    - [ ] get_info() method implemented
    - [ ] setup() returns empty list []

  - References:
    - Pattern: src/plugins/console_input/plugin.py (already migrated)
    - Pattern: src/plugins/keyword_action/plugin.py (already migrated)
    - Interface: src/core/plugin.py (Plugin Protocol definition)
    - Migration Checklist: From refactored plugin pattern study

  - Commit: NO (group with multiple tasks)

- [ ] 3. Migrate bili_danmaku_official plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/bili_danmaku_official/plugin.py
  - Parallelizable: YES
  - Same migration steps as bili_danmaku

  - Acceptance Criteria:
    - [ ] All 10 criteria same as bili_danmaku
    - [ ] Official API danmaku functionality preserved

  - Commit: NO

- [ ] 4. Migrate bili_danmaku_official_maicraft plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/bili_danmaku_official_maicraft/plugin.py
  - Parallelizable: YES
  - Same migration steps as bili_danmaku

  - Acceptance Criteria:
    - [ ] All 10 criteria same as bili_danmaku
    - [ ] MaiCraft-specific functionality preserved

  - Commit: NO

### Phase 3: Migrate Plugins (Group B - More Input Plugins, 30-45 minutes)

- [ ] 5. Migrate read_pingmu plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/read_pingmu/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Screen reading functionality preserved

  - Commit: NO

- [ ] 6. Migrate remote_stream plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/remote_stream/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Remote streaming functionality preserved

  - Commit: NO

- [ ] 7. Migrate screen_monitor plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/screen_monitor/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Screen monitoring functionality preserved

  - Commit: NO

- [ ] 8. Migrate mock_danmaku plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/mock_danmaku/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Mock danmaku functionality preserved

  - Commit: NO

### Phase 4: Migrate Plugins (Group C - Output Plugins with Dependencies, 45-60 minutes)

- [ ] 9. Migrate vtube_studio plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/vtube_studio/plugin.py
  - Parallelizable: YES (in Group C, but can start after Group A/B)
  - Depends On: Group A,B complete (sticker depends on this)
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] VTS control functionality preserved
    - [ ] Avatar service registration preserved

  - Commit: NO

- [ ] 10. Migrate sticker plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/sticker/plugin.py
  - Parallelizable: YES (depends on vtube_studio)
  - Depends On: Task 9 (vtube_studio)
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Sticker/emotion display functionality preserved

  - Commit: NO

- [ ] 11. Migrate obs_control plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/obs_control/plugin.py
  - Parallelizable: YES (in Group C, independent of vtube_studio)
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] OBS Studio control functionality preserved

  - Commit: NO

### Phase 5: Migrate Plugins (Group D - Output Plugins, 30-60 minutes)

- [ ] 12. Migrate gptsovits_tts plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/gptsovits_tts/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] GPTSoVITS TTS functionality preserved

  - Commit: NO

- [ ] 13. Migrate subtitle plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/subtitle/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Subtitle display functionality preserved

  - Commit: NO

- [ ] 14. Migrate vrchat plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/vrchat/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] VRChat OSC control functionality preserved

  - Commit: NO

- [ ] 15. Migrate warudo plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/warudo/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Warudo control functionality preserved

  - Commit: NO

### Phase 6: Migrate Plugins (Group E - Processing Plugins, 30-45 minutes)

- [ ] 16. Migrate emotion_judge plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/emotion_judge/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Emotion detection functionality preserved

  - Commit: NO

- [ ] 17. Migrate stt plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/stt/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Speech-to-text functionality preserved

  - Commit: NO

- [ ] 18. Migrate dg_lab_service plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/dg_lab_service/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] DG Lab integration functionality preserved

  - Commit: NO

### Phase 7: Migrate Plugins (Group F - Game/Hardware Plugins, 20-30 minutes)

- [ ] 19. Migrate maicraft plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/maicraft/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Minecraft integration functionality preserved

  - Commit: NO

- [ ] 20. Migrate mainosaba plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/mainosaba/plugin.py
  - Parallelizable: YES
  - Same migration steps as Group A plugins

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Mainosaba integration functionality preserved

  - Commit: NO

### Phase 8: Migrate Old TTS Plugins (Group G, 15-30 minutes)

- [ ] 21. Migrate tts plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/tts/plugin.py
  - Parallelizable: YES
  - Note: May be complex - old TTS plugin that was superseded

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] TTS functionality preserved

  - Commit: NO

- [ ] 22. Migrate omni_tts plugin
  - What to do: In-place edit plugin.py to Plugin interface
  - File: src/plugins/omni_tts/plugin.py
  - Parallelizable: YES
  - Note: May be complex - old TTS plugin that was superseded

  - Acceptance Criteria:
    - [ ] All 10 criteria from migration checklist
    - [ ] Omni TTS functionality preserved

  - Commit: NO

### Phase 9: Integration Testing (10-20 minutes)

- [ ] 23. Startup verification
  - What to do: Run python main.py and verify all plugins load
  - Command: `python main.py`
  - Expected: No WARNING-level errors, all 23 plugins load

  - Acceptance Criteria:
    - [ ] Application starts successfully
    - [ ] All plugins load without WARNING or ERROR
    - [ ] PluginManager logs show correct plugin types (23 Plugin type)
    - [ ] No import errors or module not found errors

- [ ] 24. Plugin type verification
  - What to do: Check PluginManager correctly identifies migrated plugins
  - Verification: Run `grep -r "plugin_type.*new_plugin" logs/` or check startup logs

  - Acceptance Criteria:
    - [ ] All 21 newly migrated plugins show "new_plugin" type
    - [ ] No BasePlugin types loaded (except by design)

- [ ] 25. Basic functionality smoke test
  - What to do: Quick test of 2-3 representative plugins
  - Select plugins: bili_danmaku (input), vtube_studio (output), emotion_judge (processing)

  - Acceptance Criteria:
    - [ ] Input plugin receives and emits events
    - [ ] Output plugin receives events and processes
    - [ ] Processing plugin works correctly
    - [ ] No runtime errors during operation

### Phase 10: Cleanup (5-10 minutes)

- [ ] 26. Delete src/extensions/ directory
  - What to do: Remove extensions directory that was only for reference
  - Command: `rm -rf src/extensions/`
  - Acceptance Criteria:
    - [ ] src/extensions/ directory no longer exists
    - [ ] Git shows deletion of extensions files

  - Commit: YES
  - Message: `refactor(cleanup): remove src/extensions/ after plugin migration`
  - Files: All files in src/extensions/

- [ ] 27. Remove backup directory (optional, if created)
  - What to do: Clean up backup created in Phase 1
  - Command: `rm -rf src/plugins.backup.*`
  - Acceptance Criteria:
    - [ ] Backup directories removed
    - [ ] No backup directories remain in src/

  - Commit: NO (if no backup was created)

- [ ] 28. Update documentation
  - What to do: Update AGENTS.md to reflect Plugin interface as primary
  - File: AGENTS.md
  - Must NOT do: Remove BasePlugin documentation (keep for reference)

  - Acceptance Criteria:
    - [ ] Plugin interface documented in AGENTS.md
    - [ ] BasePlugin still mentioned as legacy
    - [ ] Migration guidelines documented

  - Commit: YES
  - Message: `docs(plugin): update AGENTS.md with Plugin interface documentation`
  - Files: AGENTS.md

---

## Commit Strategy

| After Tasks | Message | Files | Verification |
|------------|---------|-------|--------------|
| All Group A (2-4) | `refactor(plugin): migrate bili_danmaku and bili_danmaku_official plugins` | src/plugins/bili_danmaku/plugin.py, src/plugins/bili_danmaku_official/plugin.py | python main.py |
| All Group A (5-8) | `refactor(plugin): migrate bili_danmaku_official_maicraft, read_pingmu, remote_stream plugins` | src/plugins/bili_danmaku_official_maicraft/plugin.py, src/plugins/read_pingmu/plugin.py, src/plugins/remote_stream/plugin.py | python main.py |
| Group C (9-10) | `refactor(plugin): migrate vtube_studio and sticker plugins` | src/plugins/vtube_studio/plugin.py, src/plugins/sticker/plugin.py | python main.py |
| Group C (11-15) | `refactor(plugin): migrate obs_control plugin` | src/plugins/obs_control/plugin.py | python main.py |
| Group D (16-18) | `refactor(plugin): migrate gptsovits_tts and subtitle plugins` | src/plugins/gptsovits_tts/plugin.py, src/plugins/subtitle/plugin.py | python main.py |
| Group D (14-15) | `refactor(plugin): migrate vrchat and warudo plugins` | src/plugins/vrchat/plugin.py, src/plugins/warudo/plugin.py | python main.py |
| Group E (16-17) | `refactor(plugin): migrate emotion_judge and stt plugins` | src/plugins/emotion_judge/plugin.py, src/plugins/stt/plugin.py | python main.py |
| Group E (17-18) | `refactor(plugin): migrate dg_lab_service plugin` | src/plugins/dg_lab_service/plugin.py | python main.py |
| Group F (19-20) | `refactor(plugin): migrate maicraft and mainosaba plugins` | src/plugins/maicraft/plugin.py, src/plugins/mainosaba/plugin.py | python main.py |
| Group F (21-22) | `refactor(plugin): migrate old tts plugin` | src/plugins/tts/plugin.py | python main.py |
| Group F (22-22) | `refactor(plugin): migrate old omni_tts plugin` | src/plugins/omni_tts/plugin.py | python main.py |
| Task 23 | `test(startup): verify all plugins load correctly` | - | python main.py |
| All Groups Complete | `refactor(plugin): complete - all 21 plugins migrated` | - | python main.py |
| Task 26 | `cleanup: delete src/extensions/` | All files in src/extensions/ | - |
| Task 27 | `docs: update AGENTS.md with Plugin interface` | AGENTS.md | - |
| Task 28 | `final: plugin refactoring complete` | - | - |

---

## Success Criteria

### Verification Commands
```bash
# Startup verification
python main.py

# Plugin count verification
ls -1 src/plugins/*/plugin.py | wc -l

# Extensions deletion verification
ls src/extensions/
```

### Final Checklist
- [ ] All 21 plugins migrated to Plugin interface
- [ ] All plugins load without WARNING or ERROR
- [ ] src/extensions/ directory deleted
- [ ] No functionality regression (manual tests pass)
- [ ] Git history preserved for all plugins
- [ ] Documentation updated (AGENTS.md)
- [ ] PluginManager correctly identifies all plugins as Plugin type
