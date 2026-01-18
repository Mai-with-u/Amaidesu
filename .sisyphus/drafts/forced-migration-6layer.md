# Draft: Forced Migration to New Plugin Protocol

## User's Objective
Forced migration of Amaidesu project from mixed Plugin/BasePlugin architecture to new Plugin protocol with complete 6-layer architecture.

## User's Explicit Request
User said: "好，直接强制迁移" (OK, directly force migration)

This means:
1. STOP supporting BasePlugin completely
2. FORCE all plugins to migrate to new Plugin protocol
3. REMOVE all backward compatibility code
4. IMPLEMENT complete 6-layer architecture as designed in refactor/design/
5. NO more mixed architecture

## Current State (from user)
- 25 plugins in src/plugins/
- Only 3 migrated: console_input, bili_danmaku, mock_danmaku
- gptsovits_tts (1021 lines) still uses deprecated BasePlugin
- PluginManager (506 lines):
  - Lines 20-289: BasePlugin (to be removed)
  - New Plugin protocol support (to be kept/enhanced)

## Migration Pattern (from bili_danmaku)

### Old Architecture:
```python
class OldPlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # Uses self.core to access AmaidesuCore
```

### New Architecture:
```python
class NewPlugin:  # Implements Plugin protocol
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._providers: List[Provider] = []

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Provider]:
        # Create and return Provider list
        pass

    async def cleanup(self):
        # Cleanup resources
        pass

    def get_info(self) -> Dict[str, Any]:
        # Return plugin info
        pass

plugin_entrypoint = NewPlugin
```

### Provider Classes:
- InputProvider: Data collection from external sources
- OutputProvider: Rendering to target devices
- DecisionProvider: Processing CanonicalMessage
- Separation of atomic capabilities
- EventBus communication
- Event subscriptions instead of service registration

## Design Requirements (from refactor/design/)

### 1. Remove BasePlugin entirely from plugin_manager.py
- Delete lines 20-289 (BasePlugin class)
- Remove all backward compatibility code

### 2. 6-Layer Architecture:
- Layer 1: Perception (InputProvider)
- Layer 2: Normalization (RawData to Text)
- Layer 3: Canonical (CanonicalMessage)
- Decision Layer: DecisionProvider (pluggable)
- Layer 4: Understanding (MessageBase to Intent)
- Layer 5: Expression (Intent to RenderParameters)
- Layer 6: Rendering (OutputProvider)

### 3. Configuration unification:
- [perception] section for input providers
- [rendering] section for output providers
- [decision] section for decision providers

### 4. EventBus communication:
- Replace service registration with event subscriptions
- Plugins emit and subscribe to events instead of registering services

## Research Findings

### Plugins Architecture Status (from direct inspection):

**Using OLD BasePlugin:**
1. gptsovits_tts - Uses BasePlugin (line 51: `from src.core.plugin_manager import BasePlugin`)

**Using NEW Plugin Protocol:**
1. bili_danmaku - Uses Plugin (line 10: `from src.core.plugin import Plugin`)
2. console_input - Uses Plugin (line 22: `from src.core.plugin import Plugin`)
3. mock_danmaku - Uses Plugin (per user statement)
4. tts - Uses new architecture (OutputProvider-based)
5. subtitle - Uses new architecture (OutputProvider-based, EventBus)
6. vtube_studio - Uses new architecture (OutputProvider-based)

**Remaining Plugins to Check (19 plugins):**
- command_processor
- mainosaba
- arknights
- dg_lab_service
- omni_tts
- maicraft
- obs_control
- vrchat
- warudo
- stt
- screen_monitor
- remote_stream
- read_pingmu
- keyword_action
- sticker
- emotion_judge
- llm_text_processor
- message_replayer
- dg-lab-do
- funasr_stt
- bili_danmaku_official
- bili_danmaku_official_maicraft

### Design Documents (from refactor/design/):
- plugin_system.md - 864 lines, complete plugin system design with Provider pattern
- layer_refactoring.md - 408 lines, 6-layer architecture specification
- decision_layer.md - 465 lines, pluggable decision provider design
- multi_provider.md - Concurrent multi-provider design
- core_refactoring.md - AmaidesuCore refactoring design
- pipeline_refactoring.md - Pipeline system refactoring
- http_server.md - HTTP server design
- data_cache.md - Data cache design for original data management
- overview.md - Architecture overview

### 6-Layer Architecture (from layer_refactoring.md):
1. **Layer 1: Perception (输入感知)** - Multiple InputProvider concurrent collection
   - Input: - | Output: RawData
   - Collects external original data (audio/text/image)
2. **Layer 2: Normalization (输入标准化)** - Convert all to Text
   - Input: RawData | Output: **Text**
   - Unified conversion to text for decision layer
3. **Layer 3: Canonical (中间表示)** - CanonicalMessage
   - Input: Text | Output: **CanonicalMessage**
   - Standardized message format
4. **Decision Layer (决策层)** - Pluggable DecisionProvider
   - MaiCoreDecisionProvider (default)
   - LocalLLMDecisionProvider (optional)
   - RuleEngineDecisionProvider (optional)
   - Output: MessageBase
5. **Layer 4: Understanding (表现理解)** - MessageBase to Intent
   - Input: MessageBase | Output: **Intent**
   - Parses decision layer response
6. **Layer 5: Expression (表现生成)** - Intent to RenderParameters
   - Input: Intent | Output: **RenderParameters**
   - Generates expression parameters, TTS text, subtitle text
7. **Layer 6: Rendering (渲染呈现)** - Multiple OutputProvider concurrent
   - Input: RenderParameters | Output: **Frame/Stream**
   - Renders to different targets (subtitle, TTS, VTS)

### Configuration Structure (from design docs):
- [perception] section for input providers
- [rendering] section for output providers
- [decision] section for decision providers
- [data_cache] section for cache configuration

### EventBus Communication Pattern:
- Replace service registration with event subscriptions
- Plugins emit and subscribe to events instead of registering services
- Event-based loose coupling

## Open Questions (to be answered during research)
- What is the current architecture status of remaining 19 plugins?
- What are the dependencies between plugins?
- Which layers (2-6) are currently missing or incomplete?
- What test infrastructure exists?
- What is the risk tolerance for breaking changes?
- What is the timeline for completion?
- Are there any edge cases or special handling requirements?

## Scope Boundaries (tentative - to be confirmed)
INCLUDE:
- Migration of all 25 plugins to new Plugin protocol
- Removal of BasePlugin from plugin_manager.py
- Implementation of missing 6-layer architecture components
- Configuration unification
- EventBus communication migration
- Verification of all functionality

EXCLUDE:
- (To be determined during interview)

## Risk Assessment (initial)
- Breaking changes to all plugins
- Loss of functionality if migration is incomplete
- Complex dependencies between plugins
- Potential runtime errors during transition
- Need for comprehensive testing
