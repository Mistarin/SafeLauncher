"""CI checks for the client/server security boundary.

This deliberately uses only the standard library and the tracked Git tree so
it also runs before optional build tooling is installed.  It checks both the
desktop release inputs and the server source for accidentally committed
credentials, while allowing test fixtures to contain clearly fake values.
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
    "project.convex.site",
    "scheme-less.convex.site",
    "test-deployment.eu-west-1.convex.site",
    "test.convex.site",
    "your-central-deployment.eu-west-1.convex.site",
}
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
TOKEN_LIKE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|vercel_[A-Za-z0-9_]{20,})\b"
)
JWT_LIKE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
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

    if findings:
        print("Security boundary audit failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    print(f"Security boundary audit passed for {len(paths)} tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
