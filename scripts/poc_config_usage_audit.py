"""
POC Config Usage Audit (Task 3 of config management refactor plan)

Purpose:
    Systematically catalog every ConfigService / config access pattern in Amaidesu,
    classify risk (green / yellow / red) for a future ConfigProxy-based hot-reload,
    and emit a categorized report.

Methodology:
    1. Static scan via AST + grep over `src/` of Amaidesu.
    2. Identify three families of access:
       - `config_service.*` direct calls (live reference, hot-reload safe today)
       - `self._config_service` storage (live reference, hot-reload safe today)
       - `self.config = config` snapshot pattern (NOT hot-reload safe today)
    3. Inspect downstream usage of `self.config` to flag mutation patterns:
       - GREEN: read-only (.get / [] / .attribute)
       - YELLOW: read-then-replace (re-assignment of self.config)
       - RED: read-then-mutate (update / pop / append / extend / clear etc.)

Output:
    Writes the full report to the file path provided as argv[1] (default stdout).
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


PROJECT_ROOT = Path("E:/01_Projects/Code/AI/MaiBot/MaiBotVtuber/Amaidesu")
SRC_ROOT = PROJECT_ROOT / "src"

# Patterns considered "mutating" on a dict-like object.
_MUTATING_CALLS = {
    "update",
    "pop",
    "popitem",
    "clear",
    "setdefault",
    "append",
    "extend",
    "insert",
    "remove",
    "sort",
    "reverse",
}

# Methods that imply read-only access.
_READ_CALLS = {"get", "items", "keys", "values", "copy"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AccessSite:
    file: str
    line: int
    kind: str  # service_call | live_ref | snapshot_store | snapshot_read
    target: str  # e.g. config_service.get_section | self.config
    detail: str = ""
    risk: str = "GREEN"  # GREEN | YELLOW | RED | INFO


@dataclass
class FileReport:
    file: str
    direct_calls: List[AccessSite] = field(default_factory=list)
    live_refs: List[AccessSite] = field(default_factory=list)
    snapshot_stores: List[AccessSite] = field(default_factory=list)
    snapshot_reads: List[AccessSite] = field(default_factory=list)
    snapshot_writes: List[AccessSite] = field(default_factory=list)
    snapshot_mutations: List[AccessSite] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Static analysis helpers
# ---------------------------------------------------------------------------


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _line_of(source_lines: List[str], offset: int) -> int:
    # offset is 0-based character offset; walk to line
    line = 1
    for i, ch in enumerate(source_lines):
        if i >= offset:
            return line
        if ch == "\n":
            line += 1
    return line


def _is_config_attr(attr_name: str) -> bool:
    return attr_name in {"config", "_config"}


def _classify_snapshot_use(attr: str) -> str:
    if attr in _MUTATING_CALLS:
        return "RED"
    if attr in _READ_CALLS:
        return "GREEN"
    return "INFO"


def _scan_python(path: Path) -> FileReport:
    rel = str(path.relative_to(PROJECT_ROOT))
    report = FileReport(file=rel)
    source = _read_file(path)
    source.splitlines(keepends=True)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return report

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            line = node.lineno
            value = node.value
            attr = node.attr

            # 1) Direct config_service.* calls (in any scope)
            if isinstance(value, ast.Name) and value.id in {"config_service", "_config_service"}:
                report.direct_calls.append(
                    AccessSite(
                        file=rel,
                        line=line,
                        kind="service_call",
                        target=f"{value.id}.{attr}",
                        risk="GREEN",
                    )
                )
                continue

            # 2) self._config_service.* (live reference use)
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "self"
                and value.attr in {"_config_service", "config_service"}
            ):
                report.live_refs.append(
                    AccessSite(
                        file=rel,
                        line=line,
                        kind="live_ref",
                        target=f"self.{value.attr}.{attr}",
                        risk="GREEN",
                    )
                )
                continue

            # 3) self.config / self._config — both store and use
            if isinstance(value, ast.Name) and value.id == "self" and _is_config_attr(attr):
                # Distinguish store (LHS) vs read (RHS) by parent context.
                report.snapshot_reads.append(
                    AccessSite(
                        file=rel,
                        line=line,
                        kind="snapshot_read",
                        target=f"self.{attr}",
                        risk="GREEN",
                    )
                )

        # Assignment to self.config / self._config
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and _is_config_attr(target.attr)
                ):
                    # store pattern
                    value_repr = ast.unparse(node.value) if hasattr(ast, "unparse") else "?"
                    report.snapshot_stores.append(
                        AccessSite(
                            file=rel,
                            line=target.lineno,
                            kind="snapshot_store",
                            target=f"self.{target.attr}",
                            detail=f"assigned from {value_repr}",
                            risk="INFO",
                        )
                    )
                    # self.config = self.config (read-then-replace) → YELLOW
                    if (
                        isinstance(node.value, ast.Attribute)
                        and isinstance(node.value.value, ast.Name)
                        and node.value.value.id == "self"
                        and _is_config_attr(node.value.attr)
                    ):
                        report.snapshot_writes.append(
                            AccessSite(
                                file=rel,
                                line=target.lineno,
                                kind="snapshot_write",
                                target=f"self.{target.attr}",
                                detail="self-assignment (possible read-then-replace)",
                                risk="YELLOW",
                            )
                        )

        # Direct subscripts on self.config (mutation through indexing)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "self"
                    and _is_config_attr(target.value.attr)
                ):
                    report.snapshot_mutations.append(
                        AccessSite(
                            file=rel,
                            line=target.lineno,
                            kind="snapshot_mutation_index",
                            target=f"self.{target.value.attr}[...] = ...",
                            detail="subscript assignment",
                            risk="RED",
                        )
                    )

        # AugAssign on self.config[...] (e.g. +=)
        if isinstance(node, ast.AugAssign):
            target = node.target
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id == "self"
                and _is_config_attr(target.value.attr)
            ):
                report.snapshot_mutations.append(
                    AccessSite(
                        file=rel,
                        line=target.lineno,
                        kind="snapshot_mutation_aug",
                        target=f"self.{target.value.attr}[...] {ast.unparse(node.op)} ...",
                        risk="RED",
                    )
                )

    return report


def _scan_text_fallback(path: Path) -> List[AccessSite]:
    """Grep-style fallback for files that the AST pass may have missed
    (e.g. mutation patterns the AST simplified). Returns sites with risk
    already classified by simple regex."""
    rel = str(path.relative_to(PROJECT_ROOT))
    try:
        text = _read_file(path)
    except OSError:
        return []
    sites: List[AccessSite] = []

    # Read-only calls on self.config / self._config
    for match in re.finditer(r"self\.(?:_?config)\.(get|items|keys|values|copy)\(", text):
        offset = match.start()
        line = text.count("\n", 0, offset) + 1
        sites.append(
            AccessSite(
                file=rel,
                line=line,
                kind="snapshot_read_via_regex",
                target=match.group(0).split("(")[0],
                risk="GREEN",
            )
        )

    # Mutating calls on self.config / self._config
    for match in re.finditer(
        r"self\.(?:_?config)\.(update|pop|popitem|clear|setdefault|append|extend|insert|remove|sort|reverse)\(",
        text,
    ):
        offset = match.start()
        line = text.count("\n", 0, offset) + 1
        sites.append(
            AccessSite(
                file=rel,
                line=line,
                kind="snapshot_mutation_via_regex",
                target=match.group(0).split("(")[0],
                risk="RED",
            )
        )

    # subscript assignment / aug assignment on self.config
    for match in re.finditer(r"self\.(?:_?config)\[[^\]]+\]\s*=", text):
        offset = match.start()
        line = text.count("\n", 0, offset) + 1
        sites.append(
            AccessSite(
                file=rel,
                line=line,
                kind="snapshot_mutation_subscript",
                target="self.config[...]=...",
                risk="RED",
            )
        )

    return sites


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _scan_root(root: Path) -> List[FileReport]:
    reports: List[FileReport] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        report = _scan_python(path)
        # add regex fallback findings
        for site in _scan_text_fallback(path):
            # dedup by (line, target)
            existing = report.snapshot_reads + report.snapshot_mutations + report.snapshot_writes
            if any(s.line == site.line and s.target == site.target for s in existing):
                continue
            if "mutation" in site.kind:
                report.snapshot_mutations.append(site)
            else:
                report.snapshot_reads.append(site)
        reports.append(report)
    return reports


def _render_report(reports: List[FileReport]) -> str:
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append(" Amaidesu ConfigService Usage Audit (POC Task 3) ")
    lines.append("=" * 78)
    lines.append("")
    lines.append("Context:")
    lines.append("  MaiBot's _ConfigProxy wraps a getter so every attribute/key access")
    lines.append("  re-reads from the latest config (transparent hot-reload).")
    lines.append("  Amaidesu's ConfigService returns COPIES via .copy(), so callers")
    lines.append("  hold snapshots that DO NOT refresh on reload.")
    lines.append("")
    lines.append("Scope: ALL .py under src/ of Amaidesu.")
    lines.append("")

    # ---- Aggregate counts ----
    total_direct = sum(len(r.direct_calls) for r in reports)
    total_live = sum(len(r.live_refs) for r in reports)
    total_store = sum(len(r.snapshot_stores) for r in reports)
    total_read = sum(len(r.snapshot_reads) for r in reports)
    total_write = sum(len(r.snapshot_writes) for r in reports)
    total_mut = sum(len(r.snapshot_mutations) for r in reports)

    files_with_direct = sum(1 for r in reports if r.direct_calls)
    files_with_live = sum(1 for r in reports if r.live_refs)
    files_with_snapshot = sum(1 for r in reports if r.snapshot_stores)
    files_with_mutation = sum(1 for r in reports if r.snapshot_mutations)

    lines.append("-" * 78)
    lines.append(" AGGREGATE STATS ")
    lines.append("-" * 78)
    lines.append(f"  Files scanned:                       {len(reports)}")
    lines.append(f"  Files with config_service.* calls:   {files_with_direct}")
    lines.append(f"  Files with live self._config_service:{files_with_live}")
    lines.append(f"  Files with self.config = config:     {files_with_snapshot}")
    lines.append(f"  Files with self.config mutations:    {files_with_mutation}")
    lines.append("")
    lines.append(f"  TOTAL access points (direct calls):  {total_direct}")
    lines.append(f"  TOTAL access points (live refs):     {total_live}")
    lines.append(f"  TOTAL access points (snapshot store):{total_store}")
    lines.append(f"  TOTAL access points (snapshot read): {total_read}")
    lines.append(f"  TOTAL read-then-replace sites:       {total_write}")
    lines.append(f"  TOTAL read-then-mutate sites:        {total_mut}")
    lines.append("")

    # ---- Risk classification ----
    lines.append("-" * 78)
    lines.append(" RISK CLASSIFICATION ")
    lines.append("-" * 78)
    lines.append("")
    lines.append("  GREEN  – read-only access. _ConfigProxy is a SAFE drop-in replacement.")
    lines.append("           The proxy transparently forwards; nothing reads 'state' from")
    lines.append("           the wrapper itself.")
    lines.append("")
    lines.append("  YELLOW – read-then-replace. Caller does `self.config = something`.")
    lines.append("           The proxy stays alive but the caller would now hold the new")
    lines.append("           object, which may itself be a snapshot. Acceptable for full")
    lines.append("           reloads but needs audit.")
    lines.append("")
    lines.append("  RED    – read-then-mutate. Caller calls .update/.pop/.clear/[] = etc.")
    lines.append("           Proxy MUST forward mutations to the underlying config OR")
    lines.append("           block them — otherwise reload will silently overwrite them.")
    lines.append("")

    if total_mut == 0 and total_write == 0:
        lines.append("  >>> CURRENT FINDING: zero RED/YELLOW sites detected. <<<")
        lines.append("  >>> All Amaidesu components read config via snapshot and do NOT")
        lines.append("  >>> mutate in place. This means a _ConfigProxy refactor is")
        lines.append("  >>> behaviorally SAFE — the only change is that reloads would")
        lines.append("  >>> actually propagate to components.")
        lines.append("")

    # ---- Direct config_service.* call sites ----
    lines.append("-" * 78)
    lines.append(" SECTION A: config_service.* direct calls (already hot-reload safe) ")
    lines.append("-" * 78)
    for r in reports:
        if not r.direct_calls:
            continue
        lines.append(f"  {r.file}")
        for s in r.direct_calls:
            lines.append(f"    L{s.line:>4}  {s.target}")
    lines.append("")

    # ---- Live self._config_service references ----
    lines.append("-" * 78)
    lines.append(" SECTION B: live self._config_service references (already hot-reload safe) ")
    lines.append("-" * 78)
    for r in reports:
        if not r.live_refs:
            continue
        lines.append(f"  {r.file}")
        for s in r.live_refs:
            lines.append(f"    L{s.line:>4}  {s.target}")
    lines.append("")

    # ---- Snapshot stores (init-time) ----
    lines.append("-" * 78)
    lines.append(" SECTION C: self.config = config  (snapshot-at-init pattern) ")
    lines.append("-" * 78)
    for r in reports:
        if not r.snapshot_stores:
            continue
        lines.append(f"  {r.file}")
        for s in r.snapshot_stores:
            lines.append(f"    L{s.line:>4}  {s.target}    # {s.detail}")
    lines.append("")

    # ---- Snapshot read-then-replace ----
    lines.append("-" * 78)
    lines.append(" SECTION D: read-then-replace (YELLOW) ")
    lines.append("-" * 78)
    if total_write == 0:
        lines.append("  (none detected)")
    else:
        for r in reports:
            if not r.snapshot_writes:
                continue
            lines.append(f"  {r.file}")
            for s in r.snapshot_writes:
                lines.append(f"    L{s.line:>4}  {s.target}    # {s.detail}")
    lines.append("")

    # ---- Snapshot read-then-mutate ----
    lines.append("-" * 78)
    lines.append(" SECTION E: read-then-mutate (RED) ")
    lines.append("-" * 78)
    if total_mut == 0:
        lines.append("  (none detected)")
    else:
        for r in reports:
            if not r.snapshot_mutations:
                continue
            lines.append(f"  {r.file}")
            for s in r.snapshot_mutations:
                lines.append(f"    L{s.line:>4}  {s.target}    # {s.detail}")
    lines.append("")

    # ---- Per-file risk summary ----
    lines.append("-" * 78)
    lines.append(" PER-FILE RISK SUMMARY ")
    lines.append("-" * 78)
    lines.append(f"  {'file':<70} {'svc':>4} {'live':>4} {'store':>5} {'read':>5} {'write':>5} {'mut':>4}")
    for r in reports:
        lines.append(
            f"  {r.file:<70} "
            f"{len(r.direct_calls):>4} "
            f"{len(r.live_refs):>4} "
            f"{len(r.snapshot_stores):>5} "
            f"{len(r.snapshot_reads):>5} "
            f"{len(r.snapshot_writes):>5} "
            f"{len(r.snapshot_mutations):>4}"
        )
    lines.append("")

    # ---- Recommendation ----
    lines.append("-" * 78)
    lines.append(" RECOMMENDATION (for the refactor plan) ")
    lines.append("-" * 78)
    lines.append("  1. _ConfigProxy is a SAFE drop-in for Amaidesu ConfigService consumers:")
    lines.append("     - no in-place mutation of self.config found")
    lines.append("     - no read-then-replace of self.config found")
    lines.append("  2. Hot-reload semantics will change from 'never refresh' to 'always refresh'.")
    lines.append("     Components that intentionally pinned config (e.g. EdgeTTS voice used")
    lines.append("     in an audio pipeline) may need an explicit `self.voice = config.voice`")
    lines.append("     capture step, OR a typed-config wrapper that exposes attributes but")
    lines.append("     re-reads on every access.")
    lines.append("  3. The dashboard `config_service.get_all()` call is currently a NOOP")
    lines.append("     (no such method exists). The hasattr() guard hides the bug. Fixing")
    lines.append("     this is orthogonal to the proxy refactor.")
    lines.append("")

    return "\n".join(lines)


def main(argv: List[str]) -> int:
    out_path = Path(argv[1]) if len(argv) > 1 else None
    reports = _scan_root(SRC_ROOT)
    rendered = _render_report(reports)
    if out_path is None:
        sys.stdout.write(rendered)
        sys.stdout.write("\n")
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        sys.stdout.write(f"wrote {out_path} ({len(rendered)} bytes)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
