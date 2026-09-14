#!/usr/bin/env python3
"""Build a safe, source-derived navigation manifest for SafeLauncher.

The output intentionally contains names and relationships, not source bodies,
runtime values, credentials, logs, databases, or cache contents.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AI_ROOT = ROOT / ".ai"
GENERATED = AI_ROOT / "generated"

TEXT_SUFFIXES = {
    ".py", ".md", ".ts", ".tsx", ".js", ".mjs", ".json", ".sh", ".yml",
    ".yaml", ".toml", ".txt", ".desktop", ".sql",
}
EXCLUDED_PARTS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist",
    "build", ".ai/generated",
}
SECRET_WORDS = re.compile(r"(?:password|passwd|secret|token|private[_-]?key|api[_-]?key|access[_-]?token)", re.I)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def is_excluded(path: Path) -> bool:
    value = path.relative_to(ROOT).as_posix()
    if value == ".ai/manifest.json" or value.startswith(".ai/generated/"):
        return True
    return any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts)


def source_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or is_excluded(path) or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=rel)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def safe_text(value: str) -> bool:
    return bool(value) and not SECRET_WORDS.search(value)


def python_symbols(path: Path, text: str) -> list[dict]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            results.append({
                "file": rel(path), "name": node.name, "kind": kind,
                "line": getattr(node, "lineno", 0),
            })
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "signal" in target.id.lower():
                    results.append({
                        "file": rel(path), "name": target.id, "kind": "signal",
                        "line": getattr(node, "lineno", 0),
                    })
    return sorted(results, key=lambda item: (item["file"], item["line"], item["name"]))


def python_imports(path: Path, text: str) -> list[dict]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                results.append({"file": rel(path), "import": item.name, "line": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            results.append({"file": rel(path), "import": module, "line": node.lineno})
    return sorted(results, key=lambda item: (item["file"], item["line"], item["import"]))


def text_connections(path: Path, text: str) -> list[dict]:
    results = []
    patterns = [
        (r"\b(request_cached|request_many_cached|request_many|request|subscribe|cancel|invalidate)\s*\(", "manager_call"),
        (r"\b(pyqtSignal|Signal)\s*\(", "signal_declaration"),
        (r"\.(connect|disconnect)\s*\(", "qt_connection"),
        (r"\b(CloudMetadataSync|CloudSaveSyncEngine|SteamClient|ArtworkClient|ResourceCache|RequestManager)\b", "architecture_symbol"),
    ]
    for pattern, kind in patterns:
        for match in re.finditer(pattern, text):
            line = text.count("\n", 0, match.start()) + 1
            results.append({"file": rel(path), "kind": kind, "value": match.group(1) if match.lastindex else match.group(0), "line": line})
    return sorted(results, key=lambda item: (item["file"], item["line"], item["kind"], item["value"]))


def request_keys(path: Path, text: str) -> list[dict]:
    results = []
    pattern = re.compile(r"\bRequestKey\s*\(\s*([\"'])([^\"']+)\1")
    for match in pattern.finditer(text):
        if not safe_text(match.group(2)):
            continue
        results.append({
            "file": rel(path), "resource": match.group(2),
            "line": text.count("\n", 0, match.start()) + 1,
        })
    return results


def database_schema(path: Path, text: str) -> list[dict]:
    if path.name != "database.py":
        return []
    results = []
    for match in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)", text, re.I | re.S):
        table = match.group(1)
        body = match.group(2)
        columns = []
        for line in body.splitlines():
            line = line.strip().strip(",")
            column = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+(INTEGER|TEXT|REAL|BLOB|BOOLEAN)", line, re.I)
            if column:
                columns.append({"name": column.group(1), "type": column.group(2).upper()})
        results.append({"table": table, "columns": columns, "line": text.count("\n", 0, match.start()) + 1})
    for match in re.finditer(r"ALTER TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+ADD COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z]+)", text, re.I):
        results.append({"table": match.group(1), "added_column": match.group(2), "type": match.group(3).upper(), "line": text.count("\n", 0, match.start()) + 1})
    return results


def service_routes(path: Path, text: str) -> list[dict]:
    if "services/" not in rel(path):
        return []
    results = []
    for pattern in (r"url\.pathname\s*===\s*[\"']([^\"']+)", r"path:\s*[\"']([^\"']+)", r"http\.route\s*\("):
        for match in re.finditer(pattern, text):
            value = match.group(1) if match.lastindex else "http.route"
            if safe_text(value):
                results.append({"file": rel(path), "route": value, "line": text.count("\n", 0, match.start()) + 1})
    return results


def tests_index(path: Path, text: str) -> list[dict]:
    if not (rel(path).startswith("tests/") or path.name == "test.py" or rel(path).startswith("ci/")):
        return []
    results = []
    for match in re.finditer(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)", text, re.M):
        results.append({"file": rel(path), "name": match.group(1), "kind": "test", "line": text.count("\n", 0, match.start()) + 1})
    for match in re.finditer(r"^\s*class\s+([A-Za-z0-9_]*(?:Test|Tests)[A-Za-z0-9_]*)", text, re.M):
        results.append({"file": rel(path), "name": match.group(1), "kind": "test_class", "line": text.count("\n", 0, match.start()) + 1})
    return results


def main() -> int:
    files = source_files()
    file_records = []
    symbols, imports, connections, keys, schema, services, tests = [], [], [], [], [], [], []
    hashes = {}
    for path in files:
        text = read(path)
        relative = rel(path)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = ""
        hashes[relative] = digest
        file_records.append({"path": relative, "suffix": path.suffix.lower(), "bytes": path.stat().st_size, "lines": text.count("\n") + (1 if text else 0)})
        if path.suffix.lower() == ".py":
            symbols.extend(python_symbols(path, text))
            imports.extend(python_imports(path, text))
        connections.extend(text_connections(path, text))
        keys.extend(request_keys(path, text))
        schema.extend(database_schema(path, text))
        services.extend(service_routes(path, text))
        tests.extend(tests_index(path, text))

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    outputs = {
        "files.json": {"generated_at": generated_at, "files": file_records},
        "symbols.json": {"generated_at": generated_at, "symbols": symbols},
        "imports.json": {"generated_at": generated_at, "imports": imports},
        "connections.json": {"generated_at": generated_at, "connections": connections},
        "request-keys.json": {"generated_at": generated_at, "request_keys": keys},
        "database-schema.json": {"generated_at": generated_at, "schema": schema},
        "services.json": {"generated_at": generated_at, "routes": services},
        "tests.json": {"generated_at": generated_at, "tests": tests},
        "source-hashes.json": {"generated_at": generated_at, "hashes": hashes},
    }
    GENERATED.mkdir(parents=True, exist_ok=True)
    for name, document in outputs.items():
        (GENERATED / name).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "repository": "SafeLauncher",
        "source_file_count": len(files),
        "generated_files": sorted(outputs),
        "excluded_runtime_data": [".git", ".venv", "__pycache__", "node_modules", "dist", ".ai/generated", ".ai/manifest.json"],
        "counts": {
            "symbols": len(symbols), "imports": len(imports), "connections": len(connections),
            "request_keys": len(keys), "tables": len(schema), "service_routes": len(services), "tests": len(tests),
        },
    }
    (AI_ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"source_files": len(files), **manifest["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
