"""Pre-push secret scanner used by coddie-ship.

Deliberately noisy rather than clever: a false positive costs a human ten
seconds, a leaked token costs a rotation. Patterns come from
config.yaml -> security.secret_patterns, so teams can extend them.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

BINARY_HINT = b"\x00"
MAX_BYTES = 2_000_000


class Hit:
    def __init__(self, path: str, line_no: int, pattern: str, excerpt: str):
        self.path = path
        self.line_no = line_no
        self.pattern = pattern
        self.excerpt = excerpt

    def __str__(self) -> str:
        return f"{self.path}:{self.line_no}  [{self.pattern}]  {self.excerpt}"


def _compile(patterns: list) -> list:
    out = []
    for raw in patterns or []:
        try:
            out.append((raw, re.compile(raw)))
        except re.error as exc:
            print(f"warning: skipping invalid secret pattern {raw!r}: {exc}")
    return out


def _mask(line: str) -> str:
    """Show enough of the line to locate it, never enough to use it."""
    text = line.strip()
    if len(text) > 120:
        text = text[:120] + " ..."
    # Any quoted literal of a plausible secret length keeps only its first chars.
    text = re.sub(
        r"(['\"])([^'\"]{6,})\1",
        lambda m: f"{m.group(1)}{m.group(2)[:3]}***{m.group(1)}",
        text,
    )
    # Unquoted long runs (env assignments, bare tokens) get the same treatment.
    return re.sub(r"([A-Za-z0-9_\-]{8})[A-Za-z0-9_\-]{6,}", r"\1***", text)


def changed_files(staged: bool = True, base: str = None) -> list:
    if base:
        cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"]
    elif staged:
        cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", "--cached"]
    else:
        cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def scan_paths(paths: list, patterns: list, never_commit: list = None) -> tuple:
    """Return (hits, forbidden_paths)."""
    compiled = _compile(patterns)
    hits = []
    forbidden = []
    for rel in paths:
        for glob in never_commit or []:
            if fnmatch.fnmatch(rel.replace("\\", "/"), str(glob)):
                forbidden.append(rel)
                break
        path = Path(rel)
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        raw = path.read_bytes()
        if BINARY_HINT in raw[:4096]:
            continue
        text = raw.decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if len(line) > 4000:
                continue
            for label, rx in compiled:
                if rx.search(line):
                    hits.append(Hit(rel, i, label[:40], _mask(line)))
                    break
    return hits, forbidden
