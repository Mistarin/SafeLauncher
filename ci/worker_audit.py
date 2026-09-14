"""Fail when feature-local request executors are introduced.

This is intentionally small and dependency-free so it can run in CI and in a
developer checkout. QThreads and local process threads are audited in the
curated .ai report; this guard targets the most common accidental regression:
The desktop path must use RequestManager for remote scheduling. Compatibility
workers perform their fallback work on their owning SafeQThread, and the
synchronous icon fallback remains serial. This guard intentionally has no
allowlist: a newly introduced executor requires an explicit architecture
review before it can be added.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (ROOT / "core", ROOT / "ui")
PATTERN = re.compile(r"\b(?:ThreadPoolExecutor|ProcessPoolExecutor)\b")


def main() -> int:
    findings: list[str] = []
    for scan_root in SCAN_ROOTS:
        for path in sorted(scan_root.rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not PATTERN.search(line):
                    continue
                findings.append(f"{relative}:{line_number}: {line.strip()}")

    if findings:
        print("Unapproved feature-local executor(s) found:")
        print("\n".join(findings))
        print("Document or migrate the executor before merging.")
        return 1

    print("Worker audit passed: no feature-local executor paths found under core/ or ui/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
