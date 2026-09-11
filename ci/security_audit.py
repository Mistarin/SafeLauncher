"""CI checks for the client/server security boundary.

This deliberately uses only the standard library and Git so it also runs
before optional build tooling is installed. It checks both the desktop release
inputs and the server source for accidentally committed credentials, while
allowing test fixtures to contain clearly fake values. When full history is
available, it also checks reachable historical snapshots and commit messages;
this prevents a sensitive deployment origin from being removed in a later
commit while remaining exposed in Git history.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
RELEASE_PREFIXES = (
    "main.py",
    "database.py",
    "core/",
    "ui/",
    "packaging/",
    "setup/",
    "launcher.sh",
    "run_app.sh",
    "install_desktop_entry.sh",
    "requirements.txt",
)
SECRET_NAMES = (
    "AUTH0_CLIENT_SECRET",
    "CONVEX_DEPLOY_KEY",
    "CONVEX_GATEWAY_KEY",
    "SAFELAUNCHER_GATEWAY_KEY",
    "SAFELAUNCHER_SECRET_KEY",
    "VERCEL_TOKEN",
)
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?(?:" + "|".join(map(re.escape, SECRET_NAMES)) + r")\s*=\s*([^\s,;}\n]+)"
)
DIRECT_ENV_ASSIGNMENT = re.compile(
    r"(?im)(?:os\.environ|process\.env)\s*\[\s*['\"](?:"
    + "|".join(map(re.escape, SECRET_NAMES))
    + r")[ '\"]+\]\s*=\s*['\"]([^'\"]+)['\"]"
)
CONVEX_URL = re.compile(
    r"https?://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.convex\.(?:site|cloud)(?:[/#[?][^\s\"'<>)]*)?",
    re.IGNORECASE,
)
ALLOWED_CONVEX_FIXTURE_HOSTS = {
    "central-profile.convex.site",
    "central-profile.eu-west-1.convex.site",
    "mytest.convex.site",
    "my-project.convex.site",
    "my-saves.convex.site",
    "project.convex.site",
    "scheme-less.convex.site",
    "test-deployment.eu-west-1.convex.site",
    "test.convex.site",
    "your-central-deployment.convex.site",
    "your-central-deployment.eu-west-1.convex.site",
    "your-profile-service.convex.site",
    "your-project.convex.site",
}
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
TOKEN_LIKE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|vercel_[A-Za-z0-9_]{20,})\b"
)
JWT_LIKE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
HISTORY_GREP_PATTERN = (
    r"https?://[A-Za-z0-9.-]+\.convex\.(site|cloud)"
    r"|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r"|\b(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|vercel_[A-Za-z0-9_]{20,})\b"
    r"|\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
PRIVATE_FILE = re.compile(r"(?:^|/)(?:\.env(?:\..*)?|.*\.(?:pem|p12|pfx|key))$", re.IGNORECASE)
PLACEHOLDER_VALUES = {
    "\"\"",
    "''",
    "<same",
    "<your",
    "<a-long",
    "process.env",
    "os.environ.get(\"",
    "getenv(\"",
}


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [ROOT / item for item in result.stdout.decode().split("\0") if item]


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def is_release_input(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return any(relative == prefix or relative.startswith(prefix) for prefix in RELEASE_PREFIXES)


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def historical_commits() -> list[str]:
    result = subprocess.run(
        ["git", "rev-list", "--all"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return [commit for commit in result.stdout.splitlines() if commit]


def audit_history(findings: list[str]) -> None:
    """Reject secrets and real Convex origins in reachable Git history.

    CI checks out the repository with full history. Local invocations against a
    shallow checkout still audit whatever history is available; this keeps the
    audit useful for developers without weakening the CI behavior.
    """

    commits = historical_commits()
    if not commits:
        return

    for commit in commits:
        result = subprocess.run(
            ["git", "grep", "--no-color", "-I", "-n", "-E", HISTORY_GREP_PATTERN, commit, "--"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 2:
            raise RuntimeError(result.stderr.strip() or f"git grep failed for {commit}")
        if result.returncode != 0:
            continue

        for raw_line in result.stdout.splitlines():
            # git grep emits <commit>:<path>:<line>:<text>. Limit the split
            # from the left so a URL's own ``https://`` is kept intact.
            snapshot_text = raw_line.split(":", 3)[-1]
            url_match = CONVEX_URL.search(snapshot_text)
            if url_match:
                host = (urlsplit(url_match.group(0)).hostname or "").lower()
                if host not in ALLOWED_CONVEX_FIXTURE_HOSTS:
                    findings.append(
                        f"history {commit[:12]}: unapproved concrete Convex URL in a reachable snapshot"
                    )
                    continue

            if PRIVATE_KEY.search(snapshot_text):
                findings.append(f"history {commit[:12]}: private-key block in a reachable snapshot")
            elif TOKEN_LIKE.search(snapshot_text):
                findings.append(f"history {commit[:12]}: provider token in a reachable snapshot")
            elif JWT_LIKE.search(snapshot_text):
                findings.append(f"history {commit[:12]}: JWT-looking credential in a reachable snapshot")

    log_result = subprocess.run(
        ["git", "log", "--all", "--format=%H%x00%B"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    for record in log_result.stdout.split("\0"):
        for match in CONVEX_URL.finditer(record):
            host = (urlsplit(match.group(0)).hostname or "").lower()
            if host not in ALLOWED_CONVEX_FIXTURE_HOSTS:
                findings.append("history: unapproved concrete Convex URL in a commit message")
                break
        for pattern, label in (
            (PRIVATE_KEY, "private-key block"),
            (TOKEN_LIKE, "provider token"),
            (JWT_LIKE, "JWT-looking credential"),
        ):
            if pattern.search(record):
                findings.append(f"history: {label} in a commit message")


def main() -> int:
    findings: list[str] = []
    paths = tracked_paths()
    texts: dict[Path, str] = {}

    for path in paths:
        text = read_text(path)
        if text is None:
            continue
        texts[path] = text
        relative = path.relative_to(ROOT).as_posix()

        if PRIVATE_FILE.fullmatch(relative) and not relative.endswith(".env.example"):
            findings.append(f"{relative}: private credential file is tracked")

        for pattern, label in (
            (PRIVATE_KEY, "private-key block"),
            (TOKEN_LIKE, "provider token"),
            (JWT_LIKE, "JWT-looking credential"),
        ):
            match = pattern.search(text)
            if match:
                findings.append(f"{relative}:{line_number(text, match.start())}: {label}")

        for pattern in (SECRET_ASSIGNMENT, DIRECT_ENV_ASSIGNMENT):
            for match in pattern.finditer(text):
                value = match.group(1).strip().lower()
                if not any(value.startswith(placeholder) for placeholder in PLACEHOLDER_VALUES):
                    findings.append(
                        f"{relative}:{line_number(text, match.start())}: credential assigned in tracked source"
                    )

    # A desktop build may contain all core/ui Python constants and strings.
    # It must know the gateway, but never a concrete Convex deployment URL.
    for path, text in texts.items():
        if not is_release_input(path):
            continue
        match = CONVEX_URL.search(text)
        if match:
            relative = path.relative_to(ROOT).as_posix()
            findings.append(
                f"{relative}:{line_number(text, match.start())}: raw Convex URL in desktop release input"
            )

    for path, text in texts.items():
        for match in CONVEX_URL.finditer(text):
            host = (urlsplit(match.group(0)).hostname or "").lower()
            if host not in ALLOWED_CONVEX_FIXTURE_HOSTS:
                findings.append(
                    f"{path.relative_to(ROOT).as_posix()}:{line_number(text, match.start())}: unapproved concrete Convex URL"
                )

    telemetry = texts.get(ROOT / "core/telemetry.py", "")
    if "OFFICIAL_PROFILE_GATEWAY_URL" not in telemetry or ".convex." in telemetry:
        findings.append("core/telemetry.py: telemetry must use the profile gateway, never a Convex origin")

    try:
        audit_history(findings)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        findings.append(f"history audit could not complete: {exc}")

    if findings:
        print("Security boundary audit failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    print(f"Security boundary audit passed for {len(paths)} tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
