# Plugin System Migration to Provider Architecture

## Context

### Original Request
Migrate 23 plugins from BasePlugin system to new Provider architecture using `git mv` to preserve git history, following design documents in `refactor/design/`.

### Interview Summary

**User Decisions:**
- Migration Strategy: Phase-by-phase (P1 simple → P2 medium → P3/P4 complex)
- Backward Compatibility: Break compatibility (migrate all at once, remove old BasePlugin code)
- Configuration: Update structure to match new architecture
- Testing: Unit tests only

**Key Constraints:**
- MUST use `git mv` to migrate files (preserve git history)
- NO deletion or modification of git history
- Follow design document architecture (6-layer, Provider-based)
- Complete refactor (no compatibility layer)

### Research Findings

**Design Documents (from refactor/design/):**
- 6-layer architecture: InputProvider (L1) → Normalization (L2) → CanonicalMessage (L3) → DecisionProvider (L3.5) → Intent (L4) → ExpressionParameters (L5) → OutputProvider (L6)
- New Plugin protocol: setup(event_bus, config) -> List[Provider]
- No compatibility layer - complete refactor required
- Directory structure: Organize by data flow layers (perception/, rendering/, understanding/, etc.)
- Estimated timeline: 36-40 days for 24 plugins

**Current Plugin System (from bg_0b8ed59b):**
- 23 plugins in `src/plugins/` directory
- BasePlugin class with service registration and event bus
- Two types supported: BasePlugin (legacy) and Plugin (new)
- PluginManager handles both types

**New Provider Architecture (from bg_a93dad50 - FULLY IMPLEMENTED):**
- Base classes: InputProvider, OutputProvider, DecisionProvider exist
- Managers: DecisionManager, OutputProviderManager, InputProviderManager implemented
- Phase 3 (Decision Layer) and Phase 4 (Output Layer) integrated in AmaidesuCore
- Concrete implementations: ConsoleInputProvider, MockDanmakuProvider, TTSProvider, SubtitleProvider, etc.
- Data types: RawData, CanonicalMessage, Intent, ExpressionParameters

**AmaidesuCore Management (from bg_a92a2296):**
- Backward compatibility: Both BasePlugin and Plugin types supported
- Service registration: Still works for old plugins
- EventBus integration: New pattern for communication (emit/on)

---

## Work Objectives

### Core Objective
Migrate all 23 plugins from BasePlugin system to new Provider architecture using phase-by-phase approach, breaking compatibility and updating configuration structure, while preserving git history with `git mv`.

### Concrete Deliverables
- All 23 plugins migrated to new Plugin protocol
- Plugins reorganized into appropriate layer directories (perception/, rendering/, etc.)
- Configuration files updated to new structure
- Unit tests for all migrated plugins
- Old BasePlugin code removed from migrated plugins
- Migration documentation created

### Definition of Done
- [ ] All 23 plugins migrated to new Plugin protocol
- [ ] All unit tests pass (`python -m pytest tests/`)
- [ ] Configuration files updated for all plugins
- [ ] Old BasePlugin inheritance removed from all plugins
- [ ] `git log` shows preserved history for all migrated files
- [ ] Application starts successfully with all migrated plugins

### Must Have
- Preserve git history using `git mv` (NO `git rm` + `git add`)
- Follow 6-layer architecture (InputProvider → OutputProvider)
- Implement new Plugin protocol (setup returns List[Provider])
- Use EventBus for communication (not service registration)
- Write unit tests for each plugin
- In-place file editing (preserve git history)

### Must NOT Have (Guardrails)
- Deleting or modifying git history
- Creating compatibility layer for BasePlugin
- Mixing BasePlugin and Plugin patterns in same plugin
- Using `git rm` + `git add` (use `git mv` instead)
- Skipping unit tests
- Breaking existing functionality

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest framework in project)
- **User wants tests**: Unit tests only
- **Framework**: pytest
- **No tests during migration**: User explicitly requested manual verification only after all migrations complete

### Manual QA Only

**For Each Plugin Migration:**

1. **Syntax Check:**
   ```bash
   python -m py_compile src/plugins/{plugin_name}/plugin.py
   ```
   Expected: No errors

2. **Import Test:**
   ```bash
   python -c "from src.plugins.{plugin_name}.plugin import plugin_entrypoint"
   ```
   Expected: Success (no ImportError)

3. **Interface Check:**
   ```bash
   python -c "p = plugin_entrypoint({}); assert hasattr(p, 'setup'); assert hasattr(p, 'cleanup'); assert hasattr(p, 'get_info')"
   ```
   Expected: Success (all attributes present)

4. **Signature Check:**
   ```bash
   python -c "import inspect; p = plugin_entrypoint({}); sig = inspect.signature(p.setup); assert 'event_bus' in sig.parameters; assert 'config' in sig.parameters"
   ```
   Expected: Success (new signature)

**Evidence Required:**
- [ ] Command output captured (copy-paste actual terminal output)
- [ ] All four checks pass for each plugin

**Final Verification (After All Migrations):**
- [ ] Import all plugins: `python -c "import src.plugins"` → No ImportError
- [ ] Verify plugin count: `python -c "print(len([name for name in dir(src.plugins) if not name.startswith('_')]))"` → ≥ 23
- [ ] Final test: `python main.py --help` → Help output, no errors

---

## Task Flow

```
Phase 0: Preparation → Phase 1: Simple Plugins → Phase 2: Medium Plugins → Phase 3: Complex Plugins → Phase 4: Service/Utility Plugins → Phase 5: Config Updates → Phase 6: Cleanup
```

## Parallelization

| Group | Tasks | Reason |
|-------|-------|--------|
| A | 2, 3, 4, 5, 6 | Independent plugin migrations within same phase |
| B | 7, 8, 9, 10, 11, 12 | Independent plugin migrations within same phase |
| C | 27, 28, 29, 30 | Configuration updates (can be parallel) |
| D | 31, 32, 33 | Documentation and cleanup (can be parallel) |

| Task | Depends On | Reason |
|------|------------|--------|
| 2-12 | 1 | Requires migration guide and test utilities |
| 27-30 | 2-26 | Requires all plugin migrations complete |
| 31-33 | 27-30 | Requires configuration and migrations complete |

---

## TODOs

### Phase 0: Preparation (Prerequisites)

- [ ] 0. Create migration test utilities

  **What to do:**
  - Create `tests/helpers/migration_test_utils.py` with:
    - MockEventBus class for testing
    - MockConfig helper for generating test configs
    - Provider lifecycle test helper functions

  **Must NOT do:**
  - Create full integration tests (user wants unit tests only)
  - Duplicate existing test infrastructure

  **Parallelizable**: NO (foundation for all other tasks)

  **References**:
  - `src/core/event_bus.py:1-50` - EventBus implementation
  - `tests/test_phase4_integration.py:1-50` - Test utility patterns

  **Acceptance Criteria**:
  - [ ] Test utilities file created at `tests/helpers/migration_test_utils.py`
  - [ ] MockEventBus implements required methods (emit, on, off)
  - [ ] Provider lifecycle helper covers start/stop/cleanup
  - [ ] `python -c "from tests.helpers.migration_test_utils import MockEventBus; print('OK')"` → OK

- [ ] 1. Create migration template and documentation

  **What to do:**
  - Create `refactor/migration/PLUGIN_MIGRATION_GUIDE.md` with:
    - Step-by-step migration process
    - Code examples for InputProvider and OutputProvider
    - Configuration migration examples
    - Common pitfalls and solutions
    - Testing checklist
    - Communication pattern changes (service → EventBus)

  **Must NOT do:**
  - Create separate compatibility layer
  - Suggest keeping BasePlugin (user wants break compatibility)

  **Parallelizable**: YES (with task 0)

  **References**:
  - `refactor/design/plugin_system.md:1-200` - Plugin protocol specification
  - `src/perception/text/console_input_provider.py:1-100` - Example InputProvider
  - `src/providers/tts_provider.py:1-100` - Example OutputProvider
  - `src/plugins/console_input/plugin.py:1-100` - Migrated example

  **Acceptance Criteria**:
  - [ ] Migration guide created at `refactor/migration/PLUGIN_MIGRATION_GUIDE.md`
  - [ ] Contains InputProvider example with complete implementation
  - [ ] Contains OutputProvider example with complete implementation
  - [ ] Contains configuration migration example (old vs new)
  - [ ] Document is in Chinese (matching project language)
  - [ ] Contains EventBus communication patterns
  - [ ] Lists common pitfalls and solutions

---

### Phase 1: Simple Plugins (P1 Priority)

**Goal**: Migrate 5 simplest plugins to validate migration process

- [ ] 2. Migrate mock_danmaku plugin

  **What to do:**
  - Analyze current `src/plugins/mock_danmaku/plugin.py` functionality
  - Create new MockDanmakuInputProvider in `src/perception/text/mock_danmaku_provider.py` (if not exists)
  - Create MockDanmakuPlugin (new protocol) in `src/perception/text/mock_danmaku/plugin.py`
  - Write unit tests in `tests/test_mock_danmaku_migration.py`
  - Use `git mv` to move old file to archive: `git mv src/plugins/mock_danmaku/plugin.py refactor/migration/archived/mock_danmaku_old.py`
  - Create/update config at `src/perception/text/mock_danmaku/config-template.toml`

  **Must NOT do:**
  - Use service registration pattern (switch to EventBus)
  - Inherit from BasePlugin (use new Plugin protocol)
  - Delete git history (use `git mv` only)
  - Skip unit tests

  **Parallelizable**: YES (with 3, 4, 5, 6)

  **References**:
  - `src/plugins/mock_danmaku/plugin.py:1-200` - Current implementation
  - `src/perception/text/mock_danmaku_provider.py:1-150` - Existing MockDanmakuProvider (if exists)
  - `src/core/providers/input_provider.py:1-100` - InputProvider base class
  - `refactor/migration/PLUGIN_MIGRATION_GUIDE.md` - Migration steps
  - `src/core/data_types/raw_data.py:1-50` - RawData structure

  **Acceptance Criteria**:
  - [ ] New MockDanmakuPlugin created at `src/perception/text/mock_danmaku/plugin.py`
  - [ ] Implements Plugin protocol (no BasePlugin inheritance)
  - [ ] setup() returns List[Provider] containing MockDanmakuInputProvider
  - [ ] Uses EventBus for communication (emit, on)
  - [ ] Old file moved with `git mv` (history preserved, verified with `git log`)
  - [ ] Unit test created: `tests/test_mock_danmaku_migration.py`
  - [ ] Unit tests pass: `python -m pytest tests/test_mock_danmaku_migration.py -v` → PASS
  - [ ] Config template created: `src/perception/text/mock_danmaku/config-template.toml`

- [ ] 3. Migrate subtitle plugin

  **What to do:**
  - Analyze current `src/plugins/subtitle/plugin.py` (CustomTkinter GUI)
  - Create new SubtitleOutputProvider in `src/rendering/visual_rendering/subtitle_provider.py`
  - Create SubtitlePlugin (new protocol) in `src/rendering/visual_rendering/subtitle/plugin.py`
  - Write unit tests in `tests/test_subtitle_migration.py`
  - Use `git mv` to archive old file
  - Update config to new structure

  **Must NOT do:**
  - Keep CustomTkinter GUI logic in plugin (move to provider)
  - Use service registration (switch to EventBus subscription)

  **Parallelizable**: YES (with 2, 4, 5, 6)

  **References**:
  - `src/plugins/subtitle/plugin.py:1-300` - Current GUI implementation
  - `src/providers/subtitle_provider.py:1-200` - Existing SubtitleProvider (if exists)
  - `src/core/providers/output_provider.py:1-100` - OutputProvider base class
  - `src/expression/render_parameters.py:1-100` - ExpressionParameters structure

  **Acceptance Criteria**:
  - [ ] New SubtitlePlugin created at `src/rendering/visual_rendering/subtitle/plugin.py`
  - [ ] SubtitleOutputProvider implements render(parameters: ExpressionParameters)
  - [ ] GUI logic encapsulated in provider, not plugin
  - [ ] Plugin aggregates provider correctly
  - [ ] Uses EventBus (subscribe to intent events)
  - [ ] Unit tests pass
  - [ ] Config updated to new structure

- [ ] 4. Migrate sticker plugin

  **What to do:**
  - Analyze current `src/plugins/sticker/plugin.py` (emoji sticker display)
  - Create new StickerOutputProvider in `src/rendering/visual_rendering/sticker_provider.py`
  - Create StickerPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 2, 3, 5, 6)

  **References**:
  - `src/plugins/sticker/plugin.py:1-200` - Current implementation
  - `src/providers/sticker_provider.py:1-150` - Existing StickerProvider
  - `src/rendering/visual_rendering/` - Target directory structure

  **Acceptance Criteria**:
  - [ ] StickerOutputProvider renders sticker from ExpressionParameters
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 5. Migrate emotion_judge plugin

  **What to do:**
  - Analyze current `src/plugins/emotion_judge/plugin.py` (LLM-based emotion analysis)
  - This plugin processes data and triggers hotkeys - fits in Layer 4 (Understanding)
  - Create EmotionJudgeProcessor in `src/understanding/emotion_judge.py`
  - Create EmotionJudgePlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Must NOT do:**
  - Keep LLM client in plugin (move to provider or use service)
  - Mix processing and output logic (separate concerns)

  **Parallelizable**: YES (with 2, 3, 4, 6)

  **References**:
  - `src/plugins/emotion_judge/plugin.py:1-200` - Current implementation
  - `src/understanding/` - Target directory structure
  - `src/core/providers/decision_provider.py:1-100` - Similar processing pattern

  **Acceptance Criteria**:
  - [ ] EmotionJudgeProcessor handles Intent → action/hotkey generation
  - [ ] Uses EventBus for communication
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 6. Migrate keyword_action plugin

  **What to do:**
  - Analyze current `src/plugins/keyword_action/plugin.py`
  - Create KeywordActionProcessor in `src/understanding/keyword_action.py`
  - Create KeywordActionPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 2, 3, 4, 5, 6)

  **References**:
  - `src/plugins/keyword_action/plugin.py:1-150` - Current implementation
  - `src/understanding/` - Target directory

  **Acceptance Criteria**:
  - [ ] KeywordActionProcessor processes keywords → actions
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

---

### Phase 2: Medium Complexity Plugins (P2 Priority)

**Goal**: Migrate 6 medium-complexity plugins

- [ ] 7. Migrate bili_danmaku plugin

  **What to do:**
  - Analyze current `src/plugins/bili_danmaku/plugin.py` (Bilibili API polling)
  - Create BilibiliDanmakuInputProvider in `src/perception/text/danmaku/bilibili_danmaku_provider.py`
  - Create BilibiliDanmakuPlugin (new protocol) in `src/perception/text/danmaku/plugin.py`
  - Write unit tests
  - Use `git mv` to archive old file
  - Create directory structure: `src/perception/text/danmaku/`
  - Update config

  **Must NOT do:**
  - Keep BasePlugin inheritance
  - Use service registration

  **Parallelizable**: YES (with 8, 9)

  **References**:
  - `src/plugins/bili_danmaku/plugin.py:1-300` - Current implementation
  - `src/plugins/bili_danmaku_official/plugin.py:1-300` - Similar API pattern
  - `src/perception/text/` - Target directory

  **Acceptance Criteria**:
  - [ ] BilibiliDanmakuInputProvider collects danmaku → RawData
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 8. Migrate bili_danmaku_official plugin

  **What to do:**
  - Analyze current `src/plugins/bili_danmaku_official/plugin.py` (official Bilibili API)
  - Create BilibiliDanmakuOfficialInputProvider in `src/perception/text/danmaku/official_provider.py`
  - Create BilibiliDanmakuOfficialPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 7, 9)

  **References**:
  - `src/plugins/bili_danmaku_official/plugin.py:1-300` - Current implementation
  - Task 7 - Similar Bilibili migration

  **Acceptance Criteria**:
  - [ ] Official API provider works correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 9. Migrate bili_danmaku_official_maicraft plugin

  **What to do:**
  - Analyze current `src/plugins/bili_danmaku_official_maicraft/plugin.py` (Bilibili + Minecraft)
  - Create providers for both danmaku input and Minecraft interaction
  - Create plugin aggregating both providers
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 7, 8)

  **References**:
  - `src/plugins/bili_danmaku_official_maicraft/plugin.py:1-300` - Current implementation
  - Tasks 7-8 - Bilibili and Minecraft patterns

  **Acceptance Criteria**:
  - [ ] Both providers work correctly
  - [ ] Plugin aggregates providers correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 10. Migrate vtube_studio plugin

  **What to do:**
  - Analyze current `src/plugins/vtube_studio/plugin.py` (VTS control + lip sync)
  - Create VTSOutputProvider in `src/rendering/virtual_rendering/vts_provider.py` (if not exists)
  - Create VTSLipSyncProvider in `src/rendering/audio_rendering/vts_lip_sync.py`
  - Create VTubeStudioPlugin (new protocol) aggregating both providers
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Must NOT do:**
  - Mix VTS control and lip sync in single provider (separate concerns)

  **Parallelizable**: YES (with 7, 8, 9)

  **References**:
  - `src/plugins/vtube_studio/plugin.py:1-400` - Current implementation
  - `src/providers/vts_provider.py:1-200` - Existing VTSProvider
  - `src/rendering/` - Target directory structure

  **Acceptance Criteria**:
  - [ ] VTSOutputProvider controls avatar
  - [ ] VTSLipSyncProvider handles lip sync
  - [ ] Plugin aggregates both providers correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 11. Migrate tts plugin

  **What to do:**
  - Analyze current `src/plugins/tts/plugin.py` (Edge TTS + OmniTTS)
  - Create EdgeTTSProvider in `src/rendering/audio_rendering/edge_tts_provider.py`
  - Create OmniTTSProvider in `src/rendering/audio_rendering/omni_tts_provider.py` (if not exists)
  - Create TTSPlugin (new protocol) aggregating both providers
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Must NOT do:**
  - Keep both TTS engines in single provider (separate concerns)

  **Parallelizable**: YES (with 7, 8, 9, 10)

  **References**:
  - `src/plugins/tts/plugin.py:1-500` - Current implementation
  - `src/providers/tts_provider.py:1-300` - Existing TTSProvider
  - `src/providers/omni_tts_provider.py:1-200` - Existing OmniTTSProvider

  **Acceptance Criteria**:
  - [ ] Both TTS providers work correctly
  - [ ] Plugin aggregates providers correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 12. Migrate gptsovits_tts plugin

  **What to do:**
  - Analyze current `src/plugins/gptsovits_tts/plugin.py` (GPT-SoVITS TTS)
  - Create GPTSoVITSProvider in `src/rendering/audio_rendering/gptsovits_tts_provider.py`
  - Create GPTSoVITSPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 7, 8, 9, 10, 11)

  **References**:
  - `src/plugins/gptsovits_tts/plugin.py:1-300` - Current implementation
  - Task 11 - Similar TTS migration pattern

  **Acceptance Criteria**:
  - [ ] Provider works correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

---

### Phase 3: Complex Plugins (P3 Priority)

**Goal**: Migrate 8 complex plugins

- [ ] 13. Migrate read_pingmu plugin

  **What to do:**
  - Analyze current `src/plugins/read_pingmu/plugin.py` (screen reading)
  - Create ReadPingmuInputProvider in `src/perception/visual/read_pingmu_provider.py`
  - Create ReadPingmuPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 14, 15, 16, 17, 18, 19, 20)

  **References**:
  - `src/plugins/read_pingmu/plugin.py:1-400` - Current implementation
  - `src/perception/visual/` - Target directory

  **Acceptance Criteria**:
  - [ ] Provider captures screen input correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 14. Migrate remote_stream plugin

  **What to do:**
  - Analyze current `src/plugins/remote_stream/plugin.py` (remote streaming)
  - Create RemoteStreamInputProvider in `src/perception/audio/stream_input_provider.py`
  - Create RemoteStreamPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 13, 15, 16, 17, 18, 19, 20)

  **References**:
  - `src/plugins/remote_stream/plugin.py:1-300` - Current implementation

  **Acceptance Criteria**:
  - [ ] Provider handles remote stream correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 15. Migrate screen_monitor plugin

  **What to do:**
  - Analyze current `src/plugins/screen_monitor/plugin.py` (screen monitoring)
  - Create ScreenMonitorInputProvider in `src/perception/visual/screen_monitor_provider.py`
  - Create ScreenMonitorPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 13, 14, 16, 17, 18, 19, 20)

  **References**:
  - `src/plugins/screen_monitor/plugin.py:1-300` - Current implementation

  **Acceptance Criteria**:
  - [ ] Provider monitors screen correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 16. Migrate stt plugin

  **What to do:**
  - Analyze current `src/plugins/stt/plugin.py` (iFlytek API + VAD)
  - Create STTInputProvider in `src/perception/audio/stt_provider.py`
  - Create STTPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 13, 14, 15, 17, 18, 19, 20)

  **References**:
  - `src/plugins/stt/plugin.py:1-500` - Current implementation
  - `src/plugins/funasr_stt/` - Alternative STT implementation (if exists)

  **Acceptance Criteria**:
  - [ ] Provider handles speech-to-text correctly
  - [ ] VAD logic preserved
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 17. Migrate warudo plugin

  **What to do:**
  - Analyze current `src/plugins/warudo/plugin.py` (VRM/Vtuber)
  - Create WarudoOutputProvider in `src/rendering/virtual_rendering/warudo_provider.py`
  - Create WarudoPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 13, 14, 15, 16, 18, 19, 20)

  **References**:
  - `src/plugins/warudo/plugin.py:1-400` - Current implementation
  - `src/plugins/vrchat/plugin.py:1-400` - Similar VR integration

  **Acceptance Criteria**:
  - [ ] Provider controls Warudo correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 18. Migrate vrchat plugin

  **What to do:**
  - Analyze current `src/plugins/vrchat/plugin.py` (VRChat integration)
  - Create VRChatOutputProvider in `src/rendering/virtual_rendering/vrchat_provider.py`
  - Create VRChatPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 13, 14, 15, 16, 17, 18, 19, 20)

  **References**:
  - `src/plugins/vrchat/plugin.py:1-400` - Current implementation

  **Acceptance Criteria**:
  - [ ] Provider controls VRChat correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 19. Migrate obs_control plugin

  **What to do:**
  - Analyze current `src/plugins/obs_control/plugin.py` (OBS Studio control)
  - Create OBSOutputProvider in `src/rendering/virtual_rendering/obs_provider.py`
  - Create OBSControlPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 13, 14, 15, 16, 17, 18, 19, 20)

  **References**:
  - `src/plugins/obs_control/plugin.py:1-400` - Current implementation

  **Acceptance Criteria**:
  - [ ] Provider controls OBS correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 20. Migrate maicraft plugin

  **What to do:**
  - Analyze current `src/plugins/minecraft/plugin.py` (Minecraft interaction)
  - Create MinecraftInputProvider in `src/perception/game/minecraft_provider.py`
  - Create MinecraftCommandProvider in `src/understanding/game/minecraft_command.py`
  - Create MinecraftPlugin (new protocol) aggregating both providers
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Must NOT do:**
  - Keep factory pattern in plugin (providers should be separate entities)

  **Parallelizable**: YES (with 13, 14, 15, 16, 17, 18, 19, 20)

  **References**:
  - `src/plugins/minecraft/plugin.py:1-500` - Current implementation
  - `src/understanding/game/` - Target directory for command provider

  **Acceptance Criteria**:
  - [ ] Both providers work correctly
  - [ ] Plugin aggregates providers correctly
  - [ ] Factory pattern converted to separate providers
  - [ ] Unit tests pass
  - [ ] Config updated

---

### Phase 4: Service/Utility Plugins

**Goal**: Migrate remaining service and utility plugins

- [ ] 21. Migrate omni_tts plugin

  **What to do:**
  - Analyze current `src/plugins/omni_tts/plugin.py` (TTS engine service)
  - This plugin provides TTS service - should become part of OutputProvider layer
  - Create OmniTTSProvider in `src/rendering/audio_rendering/omni_tts_provider.py` (if not exists)
  - Create OmniTTSPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Must NOT do**:
  - Keep as service plugin (convert to OutputProvider)

  **Parallelizable**: YES (with 22, 23, 24, 25, 26)

  **References**:
  - `src/plugins/omni_tts/plugin.py:1-200` - Current implementation
  - `src/providers/omni_tts_provider.py:1-150` - Existing provider
  - Task 11 - Similar TTS migration

  **Acceptance Criteria**:
  - [ ] Provider works correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 22. Migrate dg_lab_service plugin

  **What to do:**
  - Analyze current `src/plugins/dg_lab_service/plugin.py` (DG Lab integration)
  - This is a service integration - convert to appropriate provider type
  - Create DGLabProvider in `src/rendering/` or `src/perception/`
  - Create DGLabPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 21, 23, 24, 25, 26)

  **References**:
  - `src/plugins/dg_lab_service/plugin.py:1-300` - Current implementation

  **Acceptance Criteria**:
  - [ ] Provider integrates correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 23. Migrate command_processor plugin

  **What to do:**
  - Analyze current `src/plugins/command_processor/plugin.py`
  - This is a processing plugin - fits in Layer 4 (Understanding)
  - Create CommandProcessor in `src/understanding/command_processor.py`
  - Create CommandProcessorPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 21, 22, 24, 25, 26)

  **References**:
  - `src/plugins/command_processor/plugin.py:1-200` - Current implementation
  - `src/understanding/` - Target directory

  **Acceptance Criteria**:
  - [ ] Processor handles commands correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 24. Migrate message_replayer plugin

  **What to do:**
  - Analyze current `src/plugins/message_replayer/plugin.py` (if exists)
  - This is a utility plugin - determine appropriate layer
  - Create appropriate provider(s)
  - Create MessageReplayerPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 21, 22, 23, 25, 26)

  **References**:
  - `src/plugins/message_replayer/plugin.py` - Current implementation (if exists)

  **Acceptance Criteria**:
  - [ ] Provider works correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 25. Migrate mainosaba plugin

  **What to do:**
  - Analyze current `src/plugins/mainosaba/plugin.py` (if exists)
  - Determine appropriate layer and provider type
  - Create appropriate provider(s)
  - Create MainosabaPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 21, 22, 23, 24, 25, 26)

  **References**:
  - `src/plugins/mainosaba/plugin.py` - Current implementation (if exists)

  **Acceptance Criteria**:
  - [ ] Provider works correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

- [ ] 26. Migrate arknights plugin

  **What to do:**
  - Analyze current `src/plugins/arknights/plugin.py` (if exists)
  - Determine appropriate layer and provider type
  - Create appropriate provider(s)
  - Create ArknightsPlugin (new protocol)
  - Write unit tests
  - Use `git mv` to archive old file
  - Update config

  **Parallelizable**: YES (with 21, 22, 23, 24, 25)

  **References**:
  - `src/plugins/arknights/plugin.py` - Current implementation (if exists)

  **Acceptance Criteria**:
  - [ ] Provider works correctly
  - [ ] Plugin aggregates provider correctly
  - [ ] Unit tests pass
  - [ ] Config updated

---

### Phase 5: Configuration Updates

**Goal**: Update all configuration files to new structure

- [ ] 27. Update root config.toml

  **What to do:**
  - Analyze current `config.toml` structure
  - Update `[plugins]` section to match new directory structure
  - Add `[perception]`, `[rendering]`, `[understanding]` sections as needed
  - Update `[pipelines]` section to reference new layer structure
  - Remove deprecated BasePlugin-specific settings
  - Document all changes in migration notes

  **Must NOT do**:
  - Break existing user configurations (preserve where possible)
  - Delete sections needed by unmigrated plugins

  **Parallelizable**: YES (with 28, 29, 30)

  **References**:
  - `config-template.toml` - Root config template
  - `refactor/design/plugin_system.md:100-150` - Configuration guidelines
  - Each plugin's config-template.toml - Individual plugin configs

  **Acceptance Criteria**:
  - [ ] Root config.toml updated with new structure
  - [ ] Old plugin paths migrated to new layer paths
  - [ ] All deprecated BasePlugin settings removed
  - [ ] Config validation passes (if exists)

- [ ] 28. Update plugin configs to new structure

  **What to do:**
  - For each migrated plugin, update its `config-template.toml`:
    - Update to match new Provider interface
    - Remove BasePlugin-specific settings
    - Add Provider-specific settings
    - Update config keys to match new architecture
    - Ensure backward compatibility where possible
    - Document breaking changes

  **Parallelizable**: YES (with 27, 29, 30)

  **References**:
  - Each plugin's old config-template.toml - Original configs
  - `refactor/design/plugin_system.md` - Config guidelines

  **Acceptance Criteria**:
  - [ ] All config-template.toml files updated
  - [ ] All configs use new Provider interface keys
  - [ ] All BasePlugin-specific settings removed
  - [ ] Documentation of breaking changes complete

- [ ] 29. Create config migration guide

  **What to do:**
  - Create `refactor/migration/CONFIG_MIGRATION_GUIDE.md` with:
    - List of all config changes
    - Breaking changes per plugin
    - Migration steps for users
    - Examples of old vs new config
    - Troubleshooting common config issues

  **Parallelizable**: YES (with 27, 28, 30)

  **References**:
  - Task 1 migration guide - Similar documentation pattern
  - All updated config files - Source of changes

  **Acceptance Criteria**:
  - [ ] Config migration guide created
  - [ ] Contains table of old vs new config keys
  - [ ] Contains migration examples
  - [ ] Document is in Chinese

- [ ] 30. Update AmaidesuCore config integration

  **What to do:**
  - Update `src/core/amaidesu_core.py` config loading logic:
    - Support new plugin directory structure
    - Support new Provider managers (already done)
    - Remove BasePlugin-specific config handling (after migration)
    - Add error handling for missing configs
    - Ensure backward compatibility for user configs where possible
    - Test config loading with new structure

  **Parallelizable**: YES (with 27, 28, 29)

  **References**:
  - `src/core/amaidesu_core.py:1-100` - Config loading logic
  - `src/core/plugin_manager.py:1-200` - Plugin manager config merging
  - `refactor/design/core_refactoring.md` - Core refactoring guidelines

  **Acceptance Criteria**:
  - [ ] AmaidesuCore loads new plugin configs correctly
  - [ ] Config merging works with new structure
  - [ ] Error handling for missing configs
  - [ ] Tests pass for config loading

---

### Phase 6: Cleanup & Finalization

**Goal**: Finalize migration and remove old code

- [ ] 31. Remove BasePlugin from migrated plugins

  **What to do:**
  - For all migrated plugins, remove BasePlugin imports
  - Ensure no plugin still inherits from BasePlugin
  - Verify all plugins use new Plugin protocol
  - Remove unused BasePlugin-specific code

  **Must NOT do**:
  - Delete BasePlugin class itself (keep for any unmigrated plugins)
  - Remove PluginManager's BasePlugin support (keep for any plugins we missed)

  **Parallelizable**: YES (with 32, 33)

  **References**:
  - `src/core/plugin_manager.py:1-100` - BasePlugin class
  - All migrated plugin files - Check for BasePlugin references

  **Acceptance Criteria**:
  - [ ] No migrated plugin imports BasePlugin
  - [ ] No migrated plugin inherits from BasePlugin
  - [ ] All plugins use new Plugin protocol
  - [ ] Lint passes: `ruff check .` (no unused imports)

- [ ] 32. Clean up archived files

  **What to do:**
  - Review all archived plugin files in `refactor/migration/archived/`
  - Add migration notes to each archived file
  - Create index of archived files
  - Consider removing old test files if replaced
  - Update git history references in docs

  **Parallelizable**: YES (with 31, 33)

  **References**:
  - `refactor/migration/archived/` - Archived files

  **Acceptance Criteria**:
  - [ ] All archived files have migration notes
  - [ ] Index of archived files created
  - [ ] Git history references updated

- [ ] 33. Update documentation

  **What to do:**
  - Update `README.md` with new plugin system:
    - Explain new Plugin vs Provider architecture
    - Update plugin list with new paths
    - Update configuration examples
    - Add migration notes for users
    - Update `AGENTS.md` if needed
    - Update design docs with implementation notes
    - Create migration summary document
  - Update `AGENTS.md` to reflect new architecture

  **Parallelizable**: YES (with 31, 32)

  **References**:
  - `README.md` - Main documentation
  - `AGENTS.md` - AI agent documentation
  - `refactor/design/` - Design documentation

  **Acceptance Criteria**:
  - [ ] README.md updated with new architecture
  - [ ] Plugin list updated with new paths
  - [ ] Migration notes added to README
  - [ ] All documentation is current

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 0, 1 | `refactor(migration): create test utilities and migration guide` | refactor/migration/PLUGIN_MIGRATION_GUIDE.md, tests/helpers/migration_test_utils.py |
| 2-6 | `refactor(plugin): migrate P1 plugins (mock_danmaku, subtitle, sticker, emotion_judge, keyword_action)` | src/perception/, src/rendering/, tests/ |
| 7-12 | `refactor(plugin): migrate P2 plugins (bili_danmaku, bili_danmaku_official, vtube_studio, tts, gptsovits_tts)` | src/perception/, src/rendering/, tests/ |
| 13-20 | `refactor(plugin): migrate P3 plugins (read_pingmu, remote_stream, screen_monitor, stt, warudo, vrchat, obs_control, maicraft)` | src/perception/, src/rendering/, tests/ |
| 21-26 | `refactor(plugin): migrate service/utility plugins (omni_tts, dg_lab_service, command_processor, message_replayer, mainosaba, arknights)` | src/perception/, src/rendering/, tests/ |
| 27-30 | `refactor(config): update configuration files and AmaidesuCore` | config.toml, src/core/amaidesu_core.py |
| 31-33 | `refactor(cleanup): finalize migration and update documentation` | README.md, refactor/migration/ |

---

## Success Criteria

### Verification Commands
```bash
# Run all migration tests
python -m pytest tests/test_*_migration.py -v

# Run all existing tests
python -m pytest tests/ -v

# Code quality check
ruff check .

# Check git history preservation
git log --follow -- src/perception/text/console_input_provider.py

# Verify no BasePlugin imports in migrated plugins
grep -r "from.*BasePlugin" src/perception/ src/rendering/ src/understanding/
```

### Final Checklist
- [ ] All 23 plugins migrated to new Plugin protocol
- [ ] All unit tests pass (including new migration tests)
- [ ] All configuration files updated to new structure
- [ ] No migrated plugin imports BasePlugin
- [ ] Git history preserved for all moved files (verified with `git log`)
- [ ] Documentation updated (README.md, migration guides)
- [ ] Old BasePlugin code removed from migrated plugins
- [ ] Code quality check passes (`ruff check .`)
- [ ] Application starts and loads migrated plugins
- [ ] All "Must NOT Have" items absent from code
