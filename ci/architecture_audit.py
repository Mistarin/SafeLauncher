"""Guard migrated ownership boundaries without blanket legacy allowlists."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHARED_CONSTRUCTORS = {
    "RequestManager", "ResourceCache", "ArtworkClient", "SteamClient",
    "CloudSyncCoordinator", "CloudOperationService", "CloudStatusService",
    "CloudAccountService", "CloudCenterService", "CloudMetadataService",
    "AchievementResourceService", "AchievementPersistenceService",
    "CentralAuthSession", "WorkerSupervisor", "GameSessionManager", "OperationRegistry",
}
CONTROLLERS = (
    "managed_task_controller", "cloud_workflow_controller", "steam_metadata_controller",
    "profile_controller", "session_feature_controller", "shutdown_controller",
    "manual_restore_controller", "library_navigation_controller", "library_card_renderer",
    "settings_account_controller",
)


def inspect_source(relative, source):
    findings = []
    tree = ast.parse(source, filename=relative)
    if relative == "database.py":
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany", "executescript"}:
                findings.append(f"{relative}:{node.lineno}: SQL belongs in a local database repository or migrator")
    if relative.startswith("core/local_database/"):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {"ui", "PyQt6", "requests", "httpx"}:
                findings.append(f"{relative}:{node.lineno}: persistence imports presentation or transport")
    if relative == "ui/main_window.py":
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        constructor = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        for node in ast.walk(constructor):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SHARED_CONSTRUCTORS:
                findings.append(f"{relative}:{node.lineno}: shared {node.func.id} must be composed by ApplicationRuntime")
        if any(isinstance(base, ast.Name) and base.id.endswith("Mixin") for base in window.bases):
            findings.append(f"{relative}: MainWindow must not inherit feature workflow mixins")
    if relative in {"main.py", "ui/application_entrypoint.py"}:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "window" and node.func.attr.startswith("_"):
                    findings.append(f"{relative}:{node.lineno}: entrypoint calls private window API")
    if relative in {f"ui/{name}.py" for name in CONTROLLERS}:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in {"requests", "httpx", "sqlite3"}:
                        findings.append(f"{relative}:{node.lineno}: controller bypasses service/transport boundary")
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {"requests", "httpx", "sqlite3"}:
                findings.append(f"{relative}:{node.lineno}: controller bypasses service/transport boundary")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SHARED_CONSTRUCTORS:
                findings.append(f"{relative}:{node.lineno}: feature creates a second shared service")
    return findings


def main():
    paths = ["main.py", "ui/main_window.py", "ui/application_entrypoint.py"]
    paths.extend(f"ui/{name}.py" for name in CONTROLLERS)
    paths.append("database.py")
    paths.extend(str(path.relative_to(ROOT)) for path in (ROOT / "core/local_database").glob("*.py"))
    findings = []
    for relative in paths:
        findings.extend(inspect_source(relative, (ROOT / relative).read_text(encoding="utf-8")))
    if findings:
        print("Architecture boundary audit failed:\n  - " + "\n  - ".join(findings))
        return 1
    print("Architecture boundary audit passed: migrated service, entrypoint, and controller ownership.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
