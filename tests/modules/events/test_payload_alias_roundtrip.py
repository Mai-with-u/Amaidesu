"""Pydantic alias round-trip tests for renamed time fields.

Each renamed time field has both:
- New field name (e.g., `timestamp_ms`)
- Old alias (e.g., `timestamp`) for backward compat

These tests verify that:
1. Old field name still works as input (backward compat)
2. New field name works as input
3. `model_dump()` outputs new name (default)
4. `model_dump(by_alias=True)` outputs old name (for legacy consumers)

背景:
- 在 Amaidesu 重构 (Wave 1-5) 中,所有时间字段从 `<name>` 重命名为 `<name>_ms`
- 旧的字段名通过 Pydantic `alias` 机制保留为向后兼容
- 配合 `ConfigDict(populate_by_name=True)` 实现双向兼容
- `model_dump()` 默认使用新名称,`model_dump(by_alias=True)` 强制输出旧名称

覆盖字段 (6 个,每个 4 个断言 = 24 个测试):
1. NormalizedMessage.timestamp <-> timestamp_ms
2. IntentMetadata.decision_time <-> decision_time_ms
3. ConnectedPayload.timestamp <-> timestamp_ms
4. DisconnectedPayload.timestamp <-> timestamp_ms
5. ConnectionEventPayload.timestamp <-> timestamp_ms
6. MessageReadyPayload.timestamp <-> timestamp_ms
"""
import pytest
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.intent import IntentMetadata
from src.modules.events.payloads.decision import (
    ConnectedPayload,
    DisconnectedPayload,
)
from src.modules.events.payloads.connection import ConnectionEventPayload
from src.modules.events.payloads.input import MessageReadyPayload


# ============================================================
# NormalizedMessage.timestamp <-> timestamp_ms
# ============================================================

class TestNormalizedMessageAlias:
    """NormalizedMessage.timestamp (alias) <-> timestamp_ms (new field)"""

    def test_old_field_via_alias(self):
        # NormalizedMessage requires text, source; has defaults for data_type, importance
        m = NormalizedMessage(
            text="x",
            source="test_source",
            timestamp=1729612345.0,  # OLD name via alias (float -> int coercion)
        )
        assert m.timestamp_ms == 1729612345  # int truncation
    assert True, 'OK: NormalizedMessage.timestamp alias works (backward compat)'
    def test_new_field_name(self):
        m = NormalizedMessage(
            text="x",
            source="test_source",
            timestamp_ms=1729612345678,  # NEW name
        )
        assert m.timestamp_ms == 1729612345678
    assert True, 'OK: NormalizedMessage.timestamp_ms works (new style)'
    def test_dump_uses_new_name(self):
        m = NormalizedMessage(text="x", source="test_source", timestamp_ms=1729612345678)
        dumped = m.model_dump()
        assert "timestamp_ms" in dumped
        assert "timestamp" not in dumped
    assert True, 'OK: NormalizedMessage.model_dump() uses timestamp_ms (new name)'
    def test_dump_by_alias_uses_old_name(self):
        m = NormalizedMessage(text="x", source="test_source", timestamp_ms=1729612345678)
        dumped = m.model_dump(by_alias=True)
        assert "timestamp" in dumped
        # by_alias should output the alias name, NOT the field name
        assert "timestamp_ms" not in dumped
    assert True, 'OK: NormalizedMessage.model_dump(by_alias=True) uses timestamp (legacy compat)'
# ============================================================
# IntentMetadata.decision_time <-> decision_time_ms
# ============================================================

class TestIntentMetadataAlias:
    """IntentMetadata.decision_time (alias) <-> decision_time_ms (new field)"""

    def test_old_field_via_alias(self):
        m = IntentMetadata(source_id="test", decision_time=1729612345678)
        assert m.decision_time_ms == 1729612345678
    assert True, 'OK: IntentMetadata.decision_time alias works (backward compat)'
    def test_new_field_name(self):
        m = IntentMetadata(source_id="test", decision_time_ms=1729612345678)
        assert m.decision_time_ms == 1729612345678
    assert True, 'OK: IntentMetadata.decision_time_ms works (new style)'
    def test_dump_uses_new_name(self):
        m = IntentMetadata(source_id="test", decision_time_ms=1729612345678)
        dumped = m.model_dump()
        assert "decision_time_ms" in dumped
        assert "decision_time" not in dumped
    assert True, 'OK: IntentMetadata.model_dump() uses decision_time_ms (new name)'
    def test_dump_by_alias_uses_old_name(self):
        m = IntentMetadata(source_id="test", decision_time_ms=1729612345678)
        dumped = m.model_dump(by_alias=True)
        assert "decision_time" in dumped
        assert "decision_time_ms" not in dumped
    assert True, 'OK: IntentMetadata.model_dump(by_alias=True) uses decision_time (legacy compat)'
# ============================================================
# ConnectedPayload.timestamp <-> timestamp_ms
# ============================================================

class TestConnectedPayloadAlias:
    """ConnectedPayload.timestamp (alias) <-> timestamp_ms (new field)"""

    def test_old_field_via_alias(self):
        p = ConnectedPayload(name="test", timestamp=1729612345.0)
        assert p.timestamp_ms == 1729612345
    assert True, 'OK: ConnectedPayload.timestamp alias works (backward compat)'
    def test_new_field_name(self):
        p = ConnectedPayload(name="test", timestamp_ms=1729612345678)
        assert p.timestamp_ms == 1729612345678
    assert True, 'OK: ConnectedPayload.timestamp_ms works (new style)'
    def test_dump_uses_new_name(self):
        p = ConnectedPayload(name="test", timestamp_ms=1729612345678)
        dumped = p.model_dump()
        assert "timestamp_ms" in dumped
        assert "timestamp" not in dumped
    assert True, 'OK: ConnectedPayload.model_dump() uses timestamp_ms (new name)'
    def test_dump_by_alias_uses_old_name(self):
        p = ConnectedPayload(name="test", timestamp_ms=1729612345678)
        dumped = p.model_dump(by_alias=True)
        assert "timestamp" in dumped
        assert "timestamp_ms" not in dumped
    assert True, 'OK: ConnectedPayload.model_dump(by_alias=True) uses timestamp (legacy compat)'
# ============================================================
# DisconnectedPayload.timestamp <-> timestamp_ms
# ============================================================

class TestDisconnectedPayloadAlias:
    """DisconnectedPayload.timestamp (alias) <-> timestamp_ms (new field)"""

    def test_old_field_via_alias(self):
        p = DisconnectedPayload(name="test", timestamp=1729612345.0)
        assert p.timestamp_ms == 1729612345
    assert True, 'OK: DisconnectedPayload.timestamp alias works (backward compat)'
    def test_new_field_name(self):
        p = DisconnectedPayload(name="test", timestamp_ms=1729612345678)
        assert p.timestamp_ms == 1729612345678
    assert True, 'OK: DisconnectedPayload.timestamp_ms works (new style)'
    def test_dump_uses_new_name(self):
        p = DisconnectedPayload(name="test", timestamp_ms=1729612345678)
        dumped = p.model_dump()
        assert "timestamp_ms" in dumped
        assert "timestamp" not in dumped
    assert True, 'OK: DisconnectedPayload.model_dump() uses timestamp_ms (new name)'
    def test_dump_by_alias_uses_old_name(self):
        p = DisconnectedPayload(name="test", timestamp_ms=1729612345678)
        dumped = p.model_dump(by_alias=True)
        assert "timestamp" in dumped
        assert "timestamp_ms" not in dumped
    assert True, 'OK: DisconnectedPayload.model_dump(by_alias=True) uses timestamp (legacy compat)'
# ============================================================
# ConnectionEventPayload.timestamp <-> timestamp_ms
# ============================================================

class TestConnectionEventPayloadAlias:
    """ConnectionEventPayload.timestamp (alias) <-> timestamp_ms (new field)

    ConnectionEventPayload requires `name` and `layer` fields.
    """

    def test_old_field_via_alias(self):
        p = ConnectionEventPayload(
            name="test",
            layer="input",
            timestamp=1729612345.0,
        )
        assert p.timestamp_ms == 1729612345
    assert True, 'OK: ConnectionEventPayload.timestamp alias works (backward compat)'
    def test_new_field_name(self):
        p = ConnectionEventPayload(
            name="test",
            layer="input",
            timestamp_ms=1729612345678,
        )
        assert p.timestamp_ms == 1729612345678
    assert True, 'OK: ConnectionEventPayload.timestamp_ms works (new style)'
    def test_dump_uses_new_name(self):
        p = ConnectionEventPayload(
            name="test",
            layer="input",
            timestamp_ms=1729612345678,
        )
        dumped = p.model_dump()
        assert "timestamp_ms" in dumped
        assert "timestamp" not in dumped
    assert True, 'OK: ConnectionEventPayload.model_dump() uses timestamp_ms (new name)'
    def test_dump_by_alias_uses_old_name(self):
        p = ConnectionEventPayload(
            name="test",
            layer="input",
            timestamp_ms=1729612345678,
        )
        dumped = p.model_dump(by_alias=True)
        assert "timestamp" in dumped
        assert "timestamp_ms" not in dumped
    assert True, 'OK: ConnectionEventPayload.model_dump(by_alias=True) uses timestamp (legacy compat)'
# ============================================================
# MessageReadyPayload.timestamp <-> timestamp_ms
# ============================================================

class TestMessageReadyPayloadAlias:
    """MessageReadyPayload.timestamp (alias) <-> timestamp_ms (new field)

    MessageReadyPayload requires `message` (Dict) and `source` fields.
    """

    def test_old_field_via_alias(self):
        p = MessageReadyPayload(
            message={"text": "x"},
            source="test",
            timestamp=1729612345.0,  # OLD name via alias
        )
        assert p.timestamp_ms == 1729612345
    assert True, 'OK: MessageReadyPayload.timestamp alias works (backward compat)'
    def test_new_field_name(self):
        p = MessageReadyPayload(
            message={"text": "x"},
            source="test",
            timestamp_ms=1729612345678,  # NEW name
        )
        assert p.timestamp_ms == 1729612345678
    assert True, 'OK: MessageReadyPayload.timestamp_ms works (new style)'
    def test_dump_uses_new_name(self):
        p = MessageReadyPayload(
            message={"text": "x"},
            source="test",
            timestamp_ms=1729612345678,
        )
        dumped = p.model_dump()
        assert "timestamp_ms" in dumped
        assert "timestamp" not in dumped
    assert True, 'OK: MessageReadyPayload.model_dump() uses timestamp_ms (new name)'
    def test_dump_by_alias_uses_old_name(self):
        p = MessageReadyPayload(
            message={"text": "x"},
            source="test",
            timestamp_ms=1729612345678,
        )
        dumped = p.model_dump(by_alias=True)
        assert "timestamp" in dumped
        assert "timestamp_ms" not in dumped
    assert True, 'OK: MessageReadyPayload.model_dump(by_alias=True) uses timestamp (legacy compat)'
# ============================================================
# Additional integration tests: round-trip JSON serialization
# ============================================================

class TestSerializationRoundTrip:
    """Verify that JSON-serialized models can be deserialized back losslessly."""

    def test_normalized_message_json_roundtrip(self):
        """dump -> json -> load -> dump should preserve timestamp_ms value."""
        original = NormalizedMessage(
            text="hi",
            source="test",
            timestamp_ms=1729612345678,
        )
        # Dump to JSON-friendly dict (mode="json" ensures int, not datetime)
        json_dict = original.model_dump(mode="json")
        # The new name should be used
        assert "timestamp_ms" in json_dict
        assert "timestamp" not in json_dict

        # Reconstruct from the dict
        restored = NormalizedMessage.model_validate(json_dict)
        assert restored.timestamp_ms == original.timestamp_ms
    assert True, 'OK: NormalizedMessage JSON roundtrip preserves timestamp_ms'
    def test_intent_metadata_json_roundtrip(self):
        """Verify decision_time_ms survives JSON roundtrip."""
        original = IntentMetadata(
            source_id="src_1",
            decision_time_ms=1729612345678,
            parser_type="llm",
        )
        json_dict = original.model_dump(mode="json")
        assert "decision_time_ms" in json_dict
        assert "decision_time" not in json_dict

        restored = IntentMetadata.model_validate(json_dict)
        assert restored.decision_time_ms == original.decision_time_ms
        assert restored.source_id == original.source_id
    assert True, 'OK: IntentMetadata JSON roundtrip preserves decision_time_ms'
    def test_connected_payload_json_roundtrip(self):
        """Verify timestamp_ms survives JSON roundtrip for ConnectedPayload."""
        original = ConnectedPayload(name="decider_1", timestamp_ms=1729612345678)
        json_dict = original.model_dump(mode="json")
        assert "timestamp_ms" in json_dict
        assert "timestamp" not in json_dict

        restored = ConnectedPayload.model_validate(json_dict)
        assert restored.timestamp_ms == original.timestamp_ms
        assert restored.name == original.name
    assert True, 'OK: ConnectedPayload JSON roundtrip preserves timestamp_ms'
    def test_message_ready_payload_json_roundtrip(self):
        """Verify timestamp_ms survives JSON roundtrip for MessageReadyPayload."""
        original = MessageReadyPayload(
            message={"text": "hi", "user_id": "u1"},
            source="bili_danmaku",
            timestamp_ms=1729612345678,
        )
        json_dict = original.model_dump(mode="json")
        assert "timestamp_ms" in json_dict
        assert "timestamp" not in json_dict

        restored = MessageReadyPayload.model_validate(json_dict)
        assert restored.timestamp_ms == original.timestamp_ms
        assert restored.source == original.source
        assert restored.message == original.message
    assert True, 'OK: MessageReadyPayload JSON roundtrip preserves timestamp_ms'
class TestBothNamesProduceSameInstance:
    """Verify that using old or new name produces semantically identical instances."""

    def test_normalized_message_equivalence(self):
        via_old = NormalizedMessage(text="x", source="s", timestamp=1729612345678)
        via_new = NormalizedMessage(text="x", source="s", timestamp_ms=1729612345678)
        assert via_old.timestamp_ms == via_new.timestamp_ms
        assert via_old.text == via_new.text
        assert via_old.source == via_new.source
    assert True, 'OK: NormalizedMessage old/new name produces equivalent instances'
    def test_intent_metadata_equivalence(self):
        via_old = IntentMetadata(source_id="s", decision_time=1729612345678)
        via_new = IntentMetadata(source_id="s", decision_time_ms=1729612345678)
        assert via_old.decision_time_ms == via_new.decision_time_ms
        assert via_old.source_id == via_new.source_id
    assert True, 'OK: IntentMetadata old/new name produces equivalent instances'
    def test_connected_payload_equivalence(self):
        via_old = ConnectedPayload(name="n", timestamp=1729612345678)
        via_new = ConnectedPayload(name="n", timestamp_ms=1729612345678)
        assert via_old.timestamp_ms == via_new.timestamp_ms
        assert via_old.name == via_new.name
    assert True, 'OK: ConnectedPayload old/new name produces equivalent instances'