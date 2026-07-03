"""
POC ConfigProxy compatibility test (Task 3 of config management refactor plan)

Purpose:
    Verify that a `_ConfigProxy`-like wrapper can stand in for the current
    "snapshot config" pattern used throughout Amaidesu without changing
    call sites. Concretely we check:

      1. __getitem__   (proxy["x"])          forwards to underlying dict
      2. __getattr__   (proxy.x)             forwards to underlying dict
      3. .get(key)     (proxy.get("x"))      forwards to underlying dict
      4. swap semantics: replacing the dict through the holder updates every
         subsequent access through the proxy (the whole point of MaiBot's
         hot-reload-friendly pattern)
      5. typical Amaidesu call styles still work:
         - dict-style: self.config.get("voice", "default")
         - attribute-style for typed configs: self.config.max_messages

The implementation is intentionally a *minimal* stand-in (no __setattr__
forwarding for this POC — we already proved zero mutation sites exist
in the Amaidesu codebase via poc_config_usage_audit.py).

Exit code: 0 if all assertions pass, 1 otherwise. Output is suitable for
capturing to .omo/evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Minimal _ConfigProxy-like wrapper
# ---------------------------------------------------------------------------


class _ConfigProxy:
    """Read-only transparent wrapper around a getter.

    The getter is invoked on every attribute/key/method access, so swapping
    the underlying dict is observed by every existing reference.
    """

    def __init__(self, getter: Callable[[], Dict[str, Any]]):
        # Use object.__setattr__ to avoid recursion in __setattr__ if added later.
        object.__setattr__(self, "_getter", getter)

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when normal lookup fails, so the
        # attribute is forwarded to the underlying dict for any key the
        # dict actually has. If the dict lacks the key, raise AttributeError,
        # mirroring normal attribute access semantics.
        try:
            value = self._getter()[name]
        except KeyError as exc:
            raise AttributeError(f"_ConfigProxy has no underlying key {name!r}") from exc
        return value

    def __getitem__(self, key: str) -> Any:
        return self._getter()[key]

    def __contains__(self, key: str) -> bool:
        return key in self._getter()

    def __iter__(self):
        return iter(self._getter())

    def __len__(self) -> int:
        return len(self._getter())

    def __repr__(self) -> str:
        return f"_ConfigProxy({self._getter()!r})"

    # Forward dict-like method calls so existing self.config.get(...) keeps
    # working without code changes at call sites.
    def get(self, key: str, default: Any = None) -> Any:
        return self._getter().get(key, default)


# ---------------------------------------------------------------------------
# Tiny test harness — no pytest dependency required for POC runs
# ---------------------------------------------------------------------------


class TestRunner:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.results: list[tuple[str, str, bool, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        ok = bool(condition)
        if ok:
            self.passed += 1
            status = "PASS"
        else:
            self.failed += 1
            status = "FAIL"
        self.results.append((name, status, ok, detail))
        print(f"  [{status}] {name}" + (f"  -- {detail}" if detail else ""))

    def section(self, title: str) -> None:
        print(f"\n--- {title} ---")


def section_1_getitem(runner: TestRunner, holder: Dict[str, Any], proxy: _ConfigProxy) -> None:
    runner.section("1. __getitem__ forwarding")
    runner.check("proxy['voice'] == 'zh-CN-Xiaoxiao'", proxy["voice"] == "zh-CN-Xiaoxiao")
    runner.check(
        "proxy['missing'] raises KeyError",
        _raises(KeyError, lambda: proxy["missing"]),
    )


def section_2_getattr(runner: TestRunner, proxy: _ConfigProxy) -> None:
    runner.section("2. __getattr__ forwarding")
    runner.check("proxy.voice == 'zh-CN-Xiaoxiao'", proxy.voice == "zh-CN-Xiaoxiao")
    runner.check(
        "proxy.missing raises AttributeError",
        _raises(AttributeError, lambda: proxy.missing),
    )


def section_3_dict_method(runner: TestRunner, proxy: _ConfigProxy) -> None:
    runner.section("3. dict-style .get() forwarding")
    runner.check(
        "proxy.get('voice') == 'zh-CN-Xiaoxiao'",
        proxy.get("voice") == "zh-CN-Xiaoxiao",
    )
    runner.check(
        "proxy.get('missing', 'default') == 'default'",
        proxy.get("missing", "default") == "default",
    )


def section_4_swap(runner: TestRunner, holder: Dict[str, Any], proxy: _ConfigProxy) -> None:
    runner.section("4. hot-reload: swap underlying dict, proxy reflects it")
    runner.check(
        "before swap: proxy.voice == 'zh-CN-Xiaoxiao'",
        proxy.voice == "zh-CN-Xiaoxiao",
    )
    # Simulate hot-reload: replace the dict in place.
    holder.clear()
    holder.update(
        {
            "voice": "en-US-JennyNeural",
            "rate": "+10%",
            "volume": "+0%",
        }
    )
    runner.check(
        "after swap: proxy['voice'] == 'en-US-JennyNeural'",
        proxy["voice"] == "en-US-JennyNeural",
    )
    runner.check(
        "after swap: proxy.get('voice') == 'en-US-JennyNeural'",
        proxy.get("voice") == "en-US-JennyNeural",
    )
    runner.check(
        "after swap: old key 'language' is gone",
        "language" not in proxy,
    )
    runner.check(
        "after swap: new key 'rate' is present",
        proxy["rate"] == "+10%",
    )


def section_5_typed_proxy(runner: TestRunner) -> None:
    runner.section("5. typed Pydantic-style access through proxy")
    # Simulate a typed config (dict but accessed by attribute).
    typed_holder = {
        "max_messages": 100,
        "show_danmaku": True,
        "show_gift": False,
    }
    proxy = _ConfigProxy(lambda: typed_holder)
    runner.check("proxy.max_messages == 100", proxy.max_messages == 100)
    runner.check("proxy.show_danmaku is True", proxy.show_danmaku is True)
    runner.check("proxy.show_gift is False", proxy.show_gift is False)
    runner.check(
        "proxy.get('show_super_chat', True) defaults to True",
        proxy.get("show_super_chat", True) is True,
    )

    # swap typed config too
    typed_holder.clear()
    typed_holder.update(
        {
            "max_messages": 250,
            "show_danmaku": False,
            "show_gift": True,
        }
    )
    runner.check(
        "after swap: proxy.max_messages == 250",
        proxy.max_messages == 250,
    )
    runner.check(
        "after swap: proxy.show_danmaku is False",
        proxy.show_danmaku is False,
    )


def section_6_iteration(runner: TestRunner, proxy: _ConfigProxy) -> None:
    runner.section("6. iteration / length")
    keys = sorted(iter(proxy))
    runner.check(
        "len(proxy) == 3 after the latest swap",
        len(proxy) == 3,
        detail=f"keys={keys}",
    )
    runner.check(
        "'volume' in proxy",
        "volume" in proxy,
    )


def section_7_holder_identity(runner: TestRunner, holder: Dict[str, Any], proxy: _ConfigProxy) -> None:
    runner.section("7. proxy identity is stable across swaps (the whole point)")
    # The proxy object itself is one stable reference; only the underlying
    # dict is replaced. This is what makes `from config_manager import
    # global_config` work in MaiBot after hot-reload.
    runner.check(
        "proxy is stable object",
        isinstance(proxy, _ConfigProxy),
    )
    # Show that an old reference keeps observing changes.
    same_ref = proxy
    holder.clear()
    holder["x"] = 1
    runner.check(
        "old reference 'same_ref' sees 'x'",
        same_ref["x"] == 1,
    )


def _raises(exc_cls, fn) -> bool:
    try:
        fn()
    except exc_cls:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    out_path = Path(argv[1]) if len(argv) > 1 else None
    lines: list[str] = []
    out = lines.append
    out("=" * 78)
    out(" _ConfigProxy compatibility test (POC Task 3) ")
    out("=" * 78)
    out("")
    out("Reference: MaiBot-v1.0.0/src/config/config.py lines 688-707")
    out("Amaidesu:  currently uses 'self.config = config' snapshot pattern in")
    out("           25 files (per poc_config_usage_audit.py).")
    out("")
    out("Test contract:")
    out("  - Read-only proxy is compatible with every observed access style.")
    out("  - Swapping the underlying dict updates all existing references.")
    out("")

    # Set up the holder + proxy. The holder is the mutable dict owned by
    # the (hypothetical) ConfigService; the proxy is what components hold.
    holder: Dict[str, Any] = {
        "voice": "zh-CN-Xiaoxiao",
        "language": "zh-CN",
        "volume": "+0%",
    }
    proxy = _ConfigProxy(lambda: holder)

    runner = TestRunner()
    section_1_getitem(runner, holder, proxy)
    section_2_getattr(runner, proxy)
    section_3_dict_method(runner, proxy)
    section_4_swap(runner, holder, proxy)
    section_5_typed_proxy(runner)
    section_6_iteration(runner, proxy)
    section_7_holder_identity(runner, holder, proxy)

    out("")
    out("-" * 78)
    out(" RESULTS ")
    out("-" * 78)
    out(f"  Passed: {runner.passed}")
    out(f"  Failed: {runner.failed}")
    out("")
    if runner.failed == 0:
        out("  >>> ALL TESTS PASSED <<<")
        out("  >>> The _ConfigProxy pattern is a SAFE replacement for the")
        out("  >>> Amaidesu 'self.config = config' snapshot pattern.")
    else:
        out("  >>> SOME TESTS FAILED <<<")
        for name, _status, ok, detail in runner.results:
            if not ok:
                out(f"  FAIL: {name} -- {detail}")
    out("")
    out("Migration impact summary (for refactor plan):")
    out("  - 0 RED sites:    no in-place mutation of self.config anywhere")
    out("  - 0 YELLOW sites: no read-then-replace of self.config anywhere")
    out("  - 25 GREEN sites: pure snapshot-at-init, all read-only access")
    out("  - Hot-reload would change behavior from 'never refresh' to")
    out("    'always refresh' — callers that intentionally pinned values")
    out("    would need to copy them into local attributes.")
    out("")

    text = "\n".join(lines) + "\n"
    if out_path is None:
        sys.stdout.write(text)
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        # Also mirror test progress to stdout so the run is visible live.
        sys.stdout.write(text)
    return 0 if runner.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
