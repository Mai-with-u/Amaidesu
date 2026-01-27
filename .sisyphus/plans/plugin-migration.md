# 插件系统重构工作计划：从 BasePlugin 到 Protocol-Based Design

## Context

### Original Request
用户要求迁移 21 个插件从 BasePlugin 继承模式到新的 Plugin 接口（Protocol-based）模式。

### Interview Summary

**Key Discussions**:
- **迁移模式确认**：基于已迁移的 console_input 和 keyword_action 插件，确认了标准的迁移模式
- **Git 历史保留**：使用 in-place 编辑，不删除重建文件
- **extensions/ 处理**：仅作参考，不迁移其代码
- **测试策略**：不在迁移期间创建测试，所有迁移完成后统一测试

**Research Findings**:
- **Protocol-based 设计**：使用 PEP 544 Protocol 实现结构化子类型
- **双系统支持**：PluginManager 已支持 base_plugin 和 new_plugin 两种类型
- **事件驱动通信**：使用 event_bus.emit() 和 event_bus.on() 替代 core.send_to_maicore() 和 core.register_websocket_handler()
- **原子提交策略**：每个插件独立提交，便于代码审查和回溯
- **常见陷阱**：循环导入、错误隔离、内存泄漏（事件订阅未清理）等

### Metis Review

**Identified Gaps** (addressed):
- **依赖分析缺口**：未详细分析每个插件的具体服务依赖关系 → 计划中增加依赖复杂度分类
- **验证策略缺口**：未明确如何验证每个插件迁移成功 → 计划中增加详细的验证步骤
- **并行策略缺口**：未明确哪些插件可以并行迁移 → 计划中按依赖关系分组
- **清理步骤缺口**：未明确删除 extensions/ 的时机和验证 → 计划中增加清理验证步骤

---

## Work Objectives

### Core Objective
将 21 个插件从 BasePlugin 继承模式迁移到 Plugin Protocol 接口模式，实现结构化子类型和事件驱动通信。

### Concrete Deliverables
- 21 个迁移完成的 plugin.py 文件（in-place 编辑）
- 每个 setup() 方法返回空列表 `[]`
- 每个 cleanup() 方法完整实现
- 每个 get_info() 方法返回插件元数据
- 模块级别的 plugin_entrypoint 定义
- 使用 event_bus 进行通信的所有插件

### Definition of Done
- [ ] 所有 21 个插件的 plugin.py 已迁移
- [ ] 每个迁移的插件通过导入验证（无语法错误）
- [ ] 每个 setup() 方法签名符合新接口：`async def setup(self, event_bus, config) -> List[Any]`
- [ ] 每个 cleanup() 方法正确实现
- [ ] 每个 get_info() 方法返回正确的元数据
- [ ] 所有插件在 __init__.py 中正确导出 plugin_entrypoint
- [ ] src/extensions/ 目录已删除
- [ ] 运行 `python main.py --help` 无导入错误

### Must Have
- ✅ 保留所有插件的 git 历史（in-place 编辑）
- ✅ 迁移所有 21 个插件
- ✅ 每个 setup() 返回空列表 `[]`
- ✅ 实现 cleanup() 和 get_info() 方法
- ✅ 使用 event_bus 通信
- ✅ 迁移完成后删除 src/extensions/

### Must NOT Have (Guardrails)
- ❌ 不删除任何 plugin.py 文件并重新创建（必须 in-place 编辑）
- ❌ 不迁移 src/extensions/ 目录下的任何代码
- ❌ 不在迁移期间创建测试文件
- ❌ 不修改插件的业务逻辑（仅接口适配）
- ❌ 不修改 src/extensions/**（仅作参考）
- ❌ 不使用 core.send_to_maicore() 或 core.register_websocket_handler()（改用 event_bus）

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest 配置在 tests/ 目录)
- **User wants tests**: NO (测试在所有迁移完成后进行)
- **Framework**: pytest (但不在此阶段使用)
- **QA approach**: Manual verification only（手动验证每个插件的导入和基本结构）

### Manual QA Only

**CRITICAL**: 由于用户明确要求不在迁移期间创建测试，必须使用手动验证。

#### 验证类型

**For Each Plugin Migration**:

| Type | Verification Tool | Procedure |
|------|------------------|-----------|
| **Syntax Check** | Bash (python -m py_compile) | `python -m py_compile src/plugins/{plugin_name}/plugin.py` |
| **Import Test** | Bash (python -c) | `python -c "from src.plugins.{plugin_name}.plugin import plugin_entrypoint"` |
| **Interface Check** | Bash (python -c) | `python -c "p = plugin_entrypoint({}); assert hasattr(p, 'setup'); assert hasattr(p, 'cleanup'); assert hasattr(p, 'get_info')"` |
| **Signature Check** | Bash (python -c) | `import inspect; p = plugin_entrypoint({}); sig = inspect.signature(p.setup); assert 'event_bus' in sig.parameters; assert 'config' in sig.parameters"` |

#### Evidence Required

- [ ] 命令执行输出（无错误）
- [ ] 导入成功信息
- [ ] 接口验证通过信息

#### Final Verification (After All Migrations)

| Verification Step | Command | Expected Output |
|-----------------|----------|-----------------|
| Import all plugins | `python -c "import src.plugins"` | No ImportError |
| Check plugin count | `python -c "print(len([name for name in dir(src.plugins) if not name.startswith('_')]))"` | ≥ 23 (包括已迁移的 2 个) |
| Delete extensions/ | `rm -rf src/extensions/` | No error |
| Final test | `python main.py --help` | Help output, no errors |

---

## Task Flow

```
Phase 1: Input Plugins (7个)
  → bili_danmaku, bili_danmaku_official, bili_danmaku_official_maicraft,
     read_pingmu, remote_stream, screen_monitor, mock_danmaku

Phase 2: Output Plugins (7个)
  → gptsovits_tts, subtitle, vtube_studio, vrchat, warudo,
     obs_control, sticker

Phase 3: Processing Plugins (3个)
  → emotion_judge, stt, dg_lab_service

Phase 4: Game/Hardware Plugins (2个)
  → maicraft, mainosaba

Phase 5: Old TTS Plugins (2个)
  → tts, omni_tts

Phase 6: Cleanup
  → Delete src/extensions/
  → Final verification
```

## Parallelization

| Group | Tasks | Reason |
|-------|-------|--------|
| A | Phase 1 (all 7 input plugins) | 独立插件，无内部依赖 |
| B | Phase 2 (all 7 output plugins) | 独立插件，无内部依赖 |
| C | Phase 3 (all 3 processing plugins) | 独立插件，无内部依赖 |
| D | Phase 4 (all 2 game/hardware plugins) | 独立插件，无内部依赖 |
| E | Phase 5 (all 2 old tts plugins) | 独立插件，无内部依赖 |

| Task | Depends On | Reason |
|------|------------|--------|
| Phase 6 (Cleanup) | All phases 1-5 | 必须等所有插件迁移完成 |

---

## TODOs

> **Critical**: 每个 plugin.py 文件必须 in-place 编辑，绝不删除重建！

> Implementation Pattern: For each plugin migration:
> 1. Remove BasePlugin inheritance
> 2. Update __init__() signature
> 3. Update setup() signature and implementation
> 4. Convert core.send_to_maicore() to event_bus.emit()
> 5. Convert core.register_websocket_handler() to event_bus.on()
> 6. Add cleanup() method
> 7. Add get_info() method
> 8. Add plugin_entrypoint module variable
> 9. Verify with manual tests

- [ ] 1. **Phase 1: Input Plugins 迁移**

  **What to do**:
  - 迁移 7 个 input 插件到新的 Plugin 接口
  - 每个 in-place 编辑，保留 git 历史

  **Must NOT do**:
  - 不删除重建 plugin.py 文件
  - 不迁移 extensions/ 代码

  **Parallelizable**: YES (with 2-7 all input plugins)

  **Migration Pattern for Each Plugin**:

  ```python
  # Before (BasePlugin inheritance):
  class XPlugin(BasePlugin):
      def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
          super().__init__(core, plugin_config)
          self.config = plugin_config
          # ... 其他初始化代码

      async def setup(self):
          await self.core.register_websocket_handler("text", self.handle_message)
          # ... 其他设置代码

      async def cleanup(self):
          # 清理代码
          await super().cleanup()

  # After (Protocol-based):
  class XPlugin:
      def __init__(self, config: Dict[str, Any]):
          self.config = config
          self.event_bus: Optional["EventBus"] = None
          self.core: Optional["AmaidesuCore"] = None
          # ... 其他初始化代码

      async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
          self.event_bus = event_bus
          # 注册事件监听
          event_bus.on("websocket.text", self.handle_message)
          # ... 其他设置代码
          return []  # 返回空列表

      async def cleanup(self):
          # 清理代码
          # 不再需要 super().cleanup()

      def get_info(self) -> Dict[str, Any]:
          return {
              "name": "X",
              "version": "1.0.0",
              "author": "Amaidesu Team",
              "description": "...",
              "category": "input",
              "api_version": "1.0"
          }

  # Module entrypoint
  plugin_entrypoint = XPlugin
  ```

  **Communication Pattern Changes**:

  | Before | After |
  |--------|-------|
  | `await self.core.send_to_maicore(message)` | `await self.event_bus.emit("maicore.send", {"message": message}, "XPlugin")` |
  | `await self.core.register_websocket_handler("text", handler)` | `await self.event_bus.on("websocket.text", handler)` |
  | `service = self.core.get_service("service_name") | `service = self.core.get_service("service_name")` （不变） |

  **Subtasks for Input Plugins**:

  - [ ] 1.1 迁移 bili_danmaku
    - **References**: 已迁移的 console_input, keyword_action 插件
    - **Expected Changes**:
      - 移除 `BasePlugin` 继承
      - 修改 `__init__(self, core, plugin_config)` → `__init__(self, config)`
      - 修改 `async def setup(self)` → `async def setup(self, event_bus, config) -> List[Any]`
      - 替换 `core.send_to_maicore()` → `event_bus.emit()`
      - 替换 `core.register_websocket_handler()` → `event_bus.on()`
      - 添加 `async def cleanup(self)` 方法
      - 添加 `def get_info(self) -> Dict[str, Any]` 方法
      - 添加 `plugin_entrypoint = BiliDanmakuPlugin`
    - **Acceptance Criteria**:
      - [ ] `python -m py_compile src/plugins/bili_danmaku/plugin.py` → No errors
      - [ ] `python -c "from src.plugins.bili_danmaku.plugin import plugin_entrypoint"` → Success
      - [ ] `python -c "p = plugin_entrypoint({}); assert hasattr(p, 'setup'); assert hasattr(p, 'cleanup'); assert hasattr(p, 'get_info')"` → Success
      - [ ] `import inspect; p = plugin_entrypoint({}); sig = inspect.signature(p.setup); assert 'event_bus' in sig.parameters; assert 'config' in sig.parameters` → Success

  - [ ] 1.2 迁移 bili_danmaku_official
    - **Complexity**: Moderate（有 client/service 子模块）
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 1.3 迁移 bili_danmaku_official_maicraft
    - **Complexity**: Moderate（可能依赖 maicraft 插件）
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 1.4 迁移 read_pingmu
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 1.5 迁移 remote_stream
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 1.6 迁移 screen_monitor
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 1.7 迁移 mock_danmaku
    - **Acceptance Criteria**: Same as 1.1

- [ ] 2. **Phase 2: Output Plugins 迁移**

  **What to do**:
  - 迁移 7 个 output 插件到新的 Plugin 接口

  **Parallelizable**: YES (with 8-14 all output plugins)

  **Subtasks for Output Plugins**:

  - [ ] 2.1 迁移 gptsovits_tts
    - **Complexity**: High（复杂音频流处理，多个服务依赖）
    - **References**:
      - `src/plugins/gptsovits_tts/plugin.py` - 当前实现
      - `src/plugins/gptsovits_tts/README.md` - 功能说明
      - 已迁移的 console_input, keyword_action 插件 - 迁移模式
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 2.2 迁移 subtitle
    - **Complexity**: Moderate（GUI 线程，CustomTkinter）
    - **References**:
      - `src/plugins/subtitle/plugin.py` - 当前实现
      - `src/plugins/subtitle/README.md` - 功能说明
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 2.3 迁移 vtube_studio
    - **Complexity**: High（VTS API 集成，多个服务）
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 2.4 迁移 vrchat
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 2.5 迁移 warudo
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 2.6 迁移 obs_control
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 2.7 迁移 sticker
    - **Complexity**: Moderate（图片处理，VTS 集成）
    - **References**:
      - `src/plugins/sticker/plugin.py` - 当前实现
      - `src/plugins/sticker/README.md` - 功能说明
      - 已迁移的 console_input, keyword_action 插件 - 迁移模式
    - **Acceptance Criteria**: Same as 1.1

- [ ] 3. **Phase 3: Processing Plugins 迁移**

  **What to do**:
  - 迁移 3 个 processing 插件到新的 Plugin 接口

  **Parallelizable**: YES (with 15-17 all processing plugins)

  **Subtasks for Processing Plugins**:

  - [ ] 3.1 迁移 emotion_judge
    - **Complexity**: High（LLM 集成，热键触发）
    - **References**:
      - `src/plugins/emotion_judge/plugin.py` - 当前实现
      - `src/plugins/emotion_judge/README.md` - 功能说明
      - 已迁移的 console_input, keyword_action 插件 - 迁移模式
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 3.2 迁移 stt
    - **Complexity**: High（VAD，讯飞 API，音频流）
    - **References**:
      - `src/plugins/stt/plugin.py` - 当前实现
      - `src/plugins/stt/README.md` - 功能说明
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 3.3 迁移 dg_lab_service
    - **Acceptance Criteria**: Same as 1.1

- [ ] 4. **Phase 4: Game/Hardware Plugins 迁移**

  **What to do**:
  - 迁移 2 个 game/hardware 插件到新的 Plugin 接口

  **Parallelizable**: YES (with 18-19 both plugins)

  **Subtasks for Game/Hardware Plugins**:

  - [ ] 4.1 迁移 maicraft
    - **Complexity**: Unknown（MaiCraft 集成）
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 4.2 迁移 mainosaba
    - **Acceptance Criteria**: Same as 1.1

- [ ] 5. **Phase 5: Old TTS Plugins 迁移**

  **What to do**:
  - 迁移 2 个 old TTS 插件到新的 Plugin 接口

  **Parallelizable**: YES (with 20-21 both old tts plugins)

  **Subtasks for Old TTS Plugins**:

  - [ ] 5.1 迁移 tts
    - **Complexity**: High（Edge-TTS，音频播放）
    - **References**:
      - `src/plugins/tts/plugin.py` - 当前实现
      - `src/plugins/tts/README.md` - 功能说明
    - **Acceptance Criteria**: Same as 1.1

  - [ ] 5.2 迁移 omni_tts
    - **Acceptance Criteria**: Same as 1.1

- [ ] 6. **Phase 6: Cleanup**

  **What to do**:
  - 删除 src/extensions/ 目录
  - 进行最终验证

  **Must NOT do**:
  - 不执行任何迁移任务（仅在所有插件迁移完成后执行）

  **Parallelizable**: NO (depends on tasks 1-21 all complete)

  **References**:
  - User requirement: "重构完毕之后就可以删除 ulw" (即 extensions/)

  **Subtasks for Cleanup**:

  - [ ] 6.1 验证所有插件导入成功
    - **Command**: `python -c "import src.plugins"`
    - **Expected**: No ImportError

  - [ ] 6.2 验证插件数量正确
    - **Command**: `python -c "print(len([name for name in dir(src.plugins) if not name.startswith('_')]))"`
    - **Expected**: ≥ 23 (包括已迁移的 2 个 + 21 个新迁移的)

  - [ ] 6.3 删除 src/extensions/ 目录
    - **Command**: `rm -rf src/extensions/`
    - **Expected**: No error
    - **Evidence Required**: Directory listing showing extensions/ no longer exists

  - [ ] 6.4 最终验证：运行 main.py --help
    - **Command**: `python main.py --help`
    - **Expected**: Help output, no import errors
    - **Evidence Required**: Terminal output showing help text

  **Acceptance Criteria**:
  - [ ] 所有 21 个插件迁移完成
  - [ ] 所有插件通过导入验证
  - [ ] src/extensions/ 目录已删除
  - [ ] `python main.py --help` 运行成功，无错误

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1.1-1.7 | `refactor(input): migrate XPlugin to protocol-based interface` | `src/plugins/*/plugin.py` | Import test |
| 2.1-2.7 | `refactor(output): migrate XPlugin to protocol-based interface` | `src/plugins/*/plugin.py` | Import test |
| 3.1-3.3 | `refactor(processing): migrate XPlugin to protocol-based interface` | `src/plugins/*/plugin.py` | Import test |
| 4.1-4.2 | `refactor(game): migrate XPlugin to protocol-based interface` | `src/plugins/*/plugin.py` | Import test |
| 5.1-5.2 | `refactor(tts): migrate XPlugin to protocol-based interface` | `src/plugins/*/plugin.py` | Import test |
| 6.3 | `chore: remove obsolete src/extensions/ directory` | `src/extensions/` | Directory listing |
| 6.4 | `test: verify all plugins import successfully` | - | `python main.py --help` |

---

## Success Criteria

### Verification Commands
```bash
# 验证所有插件导入
python -c "import src.plugins"

# 验证插件数量
python -c "print(len([name for name in dir(src.plugins) if not name.startswith('_')]))"

# 最终验证
python main.py --help
```

### Final Checklist
- [ ] All 21 plugin.py files migrated (in-place edits, no deletions)
- [ ] All setup() methods return empty list `[]`
- [ ] All cleanup() methods implemented
- [ ] All get_info() methods return metadata
- [ ] All plugin_entrypoint variables defined
- [ ] All plugins use event_bus for communication
- [ ] No plugins use core.send_to_maicore() or core.register_websocket_handler()
- [ ] src/extensions/ directory deleted
- [ ] All import tests pass
- [ ] `python main.py --help` runs successfully

---

## Risk Mitigation

### Common Pitfalls (from librarian research)

1. **Breaking existing plugins during migration**
   - **Solution**: In-place editing preserves git history
   - **Guardrail**: Each plugin tested individually before moving to next

2. **Circular imports with event_bus**
   - **Solution**: Use TYPE_CHECKING for forward references
   - **Pattern**: `if TYPE_CHECKING: from src.core.event_bus import EventBus`

3. **Using isinstance() with protocols incorrectly**
   - **Solution**: Not needed for protocol-based design
   - **Guardrail**: Duck typing used, no isinstance checks needed

4. **Ignoring error isolation in event_bus**
   - **Solution**: PluginManager handles error isolation
   - **Note**: Plugin doesn't need to worry about this

5. **Large atomic refactoring commits**
   - **Solution**: Each plugin in separate commit
   - **Guardrail**: Commit after each plugin migration

6. **Forgetting to clean up event subscriptions**
   - **Solution**: Implement cleanup() method properly
   - **Pattern**: Store listener references, unsubscribe in cleanup()

7. **Not testing protocol compliance**
   - **Solution**: Manual verification for each plugin
   - **Checklist**: setup(), cleanup(), get_info(), plugin_entrypoint

### Dependency Risks

| Plugin | Complexity | Dependencies | Migration Risk | Mitigation |
|---------|-------------|--------------|-------------|
| gptsovits_tts | High | Multiple services | Verify service calls remain same |
| vtube_studio | High | VTS API | Test with VTS running |
| stt | High | VAD, API | Test audio pipeline |
| emotion_judge | High | LLM | Test with mock LLM |
| bili_danmaku_official | Moderate | WebSocket | Test connection |
| subtitle | Moderate | GUI thread | Test UI launch |
| Others | Low-Moderate | Minimal | Standard pattern |

---

## Notes

- **In-Place Editing**: All edits must be done in-place using Edit tool, never delete and recreate files
- **Git History**: Each commit preserves history, atomic per plugin
- **No Tests During Migration**: User explicitly requested no test files during migration
- **Extensions Reference**: src/extensions/ is reference-only, never migrated
- **Cleanup Timing**: extensions/ deleted only after all 21 plugins migrated and verified
- **Parallel Execution**: Phases 1-5 can be executed in parallel, Phase 6 depends on all others
