#!/usr/bin/env python3
"""Validate the SafeLauncher AI cache without running the application."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AI_ROOT = ROOT / ".ai"
GENERATED = AI_ROOT / "generated"
REQUIRED = ["CONTEXT.md", "ARCHITECTURE.md", "MODULES.md", "TASK.md", "INDEX.md", "STATUS.md", "manifest.json"]
GENERATED_REQUIRED = [
    "files.json", "symbols.json", "imports.json", "connections.json", "request-keys.json",
    "database-schema.json", "services.json", "tests.json", "source-hashes.json",
]
SECRET_PATTERN = re.compile(r"(?:-----BEGIN [^-]+ PRIVATE KEY-----|AKIA[0-9A-Z]{16}|password\s*[:=]\s*[^<`\n]+|secret[_-]?key\s*[:=]\s*[^<`\n]+)", re.I)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate_links(errors: list[str], path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        target = target.strip().split()[0].strip("<>")
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target_path, _, anchor = target.partition("#")
        resolved = (path.parent / target_path).resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            fail(errors, f"{path.relative_to(ROOT)} links outside repository: {target}")
            continue
        if not resolved.exists():
            fail(errors, f"{path.relative_to(ROOT)} has broken link: {target}")
        elif anchor and resolved.suffix.lower() == ".md":
            body = resolved.read_text(encoding="utf-8")
            heading_slugs = set()
            for heading in re.findall(r"^#+\s+(.+?)\s*$", body, re.M):
                slug = re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
                heading_slugs.add(slug)
            if anchor not in heading_slugs:
                fail(errors, f"{path.relative_to(ROOT)} has missing anchor: {target}")


def main() -> int:
    errors: list[str] = []
    for name in REQUIRED:
        if not (AI_ROOT / name).exists():
            fail(errors, f"missing .ai/{name}")
    for name in GENERATED_REQUIRED:
        if not (GENERATED / name).exists():
            fail(errors, f"missing .ai/generated/{name}")

    for path in AI_ROOT.rglob("*.md"):
        if "generated" not in path.parts:
            validate_links(errors, path)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            fail(errors, f"cannot read {path}: {exc}")
            continue
        if SECRET_PATTERN.search(content):
            fail(errors, f"possible secret pattern in {path.relative_to(ROOT)}")
        if "/home/" in content or "XDG_DATA_HOME=" in content or "XDG_CONFIG_HOME=" in content:
            # Repository-relative source links are allowed; personal runtime
            # paths and environment values are not cache content.
            for line in content.splitlines():
                if "/home/" in line and "project-context-cache" not in line:
                    fail(errors, f"personal absolute path in {path.relative_to(ROOT)}")
                    break

    try:
        manifest = json.loads((AI_ROOT / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            fail(errors, "unsupported manifest schema version")
        for name in GENERATED_REQUIRED:
            json.loads((GENERATED / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"invalid generated JSON: {exc}")

    if errors:
        print("AI cache validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("AI cache validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
