"""Tests for render_timeout config migration helper."""
import warnings

from src.stages.output.manager import OutputHandlerManager


def test_old_field_triggers_deprecation_warning():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        mgr = OutputHandlerManager(event_bus=None, config={'render_timeout': 10.0})
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 1
        assert mgr.render_timeout_ms == 10000
    print('OK: old field triggers DeprecationWarning, converts 10s → 10000ms')


def test_new_field_no_warning():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        mgr = OutputHandlerManager(event_bus=None, config={'render_timeout_ms': 15000})
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0
        assert mgr.render_timeout_ms == 15000
    print('OK: new field works without warning')


def test_default_value():
    mgr = OutputHandlerManager(event_bus=None, config={})
    assert mgr.render_timeout_ms == 10000
    print('OK: default 10000ms when no field set')
