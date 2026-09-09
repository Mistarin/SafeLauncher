"""Terminal-based setup wizard for SafeLauncher private cloud saves."""

from __future__ import annotations

import os
import sys
import shutil
import secrets
import subprocess
import tempfile
import zipfile
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import requests
from PyQt6.QtCore import QSettings

from core.host_process import host_process_env
from core.cloud_detector import (
    discover_local_cloud_backend,
    detect_local_cloud_installation,
    inspect_system_compatibility,
)
from core.version import MIN_CONVEX_BACKEND_VERSION, is_version_outdated
from core.secret_store import get_secret, set_secret, delete_secret


def _convex_cli_env(server_dir: Path) -> dict[str, str]:
    """Return the host environment plus Convex dotenv configuration.

    ``convex dev`` writes project configuration to ``.env.local``.  Some
    Convex CLI versions do not load that file for a non-interactive
    ``convex deploy`` invocation, so pass the values explicitly.  Shell
    variables remain authoritative over dotenv values.
    """
    env = host_process_env()
    # Load the highest-priority files first.  Explicit shell variables already
    # present in ``env`` always win through setdefault().
    for filename in (".env.production.local", ".env.production", ".env.local", ".env"):
        env_file = server_dir / filename
        if not env_file.is_file():
            continue
        try:
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key or not all(ch.isalnum() or ch == "_" for ch in key) or key[0].isdigit():
                    continue
                # dotenv permits an inline comment after an unquoted value.
                if value and value[0] not in ('"', "'") and " #" in value:
                    value = value.split(" #", 1)[0].rstrip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                env.setdefault(key, value)
        except OSError as exc:
            print(f"  [!] Could not read {env_file}: {exc}")
    # Convex deployment identifiers are commonly written as
    # ``dev:name``.  Normalize whitespace around the separator, including
    # values inherited from the shell, before passing them to Node.
    deployment = env.get("CONVEX_DEPLOYMENT", "").strip()
    if ":" in deployment:
        scope, name = deployment.split(":", 1)
        env["CONVEX_DEPLOYMENT"] = f"{scope.strip()}:{name.strip()}"
    elif deployment:
        env["CONVEX_DEPLOYMENT"] = deployment
    return env


def _has_convex_project_config(env: dict[str, str]) -> bool:
    """Whether Convex has either a local deployment or production deploy key."""
    return bool(env.get("CONVEX_DEPLOYMENT") or env.get("CONVEX_DEPLOY_KEY"))


def deploy_key_prerequisites(server_dir: Optional[Path] = None) -> dict[str, Any]:
    """Describe whether the local Convex CLI can mint a production deploy key."""
    path = Path(server_dir).expanduser() if server_dir else None
    npx_path = shutil.which("npx")
    convex_config = Path.home() / ".convex" / "config.json"
    return {
        "has_npx": bool(npx_path),
        "npx_path": npx_path or "",
        "has_backend": bool(path and path.is_dir()),
        "backend_path": str(path) if path and path.is_dir() else "",
        "has_convex_project": bool(path and _has_convex_project_config(_convex_cli_env(path))),
        "has_cli_login": convex_config.is_file() and convex_config.stat().st_size > 0,
    }


def _redact_deploy_output(output: str, secrets_to_redact: tuple[str, ...] = ()) -> str:
    """Remove credential-shaped values before diagnostics reach the UI/logs."""
    text = str(output or "")
    for secret in secrets_to_redact:
        if secret:
            text = text.replace(secret, "[secret redacted]")
    text = re.sub(r"(?i)(CONVEX_DEPLOY_KEY\s*[=:]\s*)[^\s\"']+", r"\1[redacted]", text)
    # Convex keys may identify a team/project before the deployment prefix;
    # anything containing the token separator is credential-shaped output.
    text = re.sub(r"(?<![\w])[^\s\"']+\|[^\s\"']+", "[deploy-key redacted]", text)
    return text[-3000:]


def generate_deploy_key(
    server_dir: Path,
    key_name: str,
    *,
    timeout: int = 90,
) -> dict[str, str | bool]:
    """Mint a production-scoped Convex deploy key without exposing it in output."""
    server_dir = Path(server_dir).expanduser().resolve()
    key_name = str(key_name or "").strip()
    if not server_dir.is_dir():
        return {"ok": False, "error": "Select a valid SafeLauncherCloud project directory."}
    if not key_name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,63}", key_name):
        return {"ok": False, "error": "Key name must be 2–64 letters, numbers, dots, dashes, or underscores."}
    if not shutil.which("npx"):
        return {"ok": False, "error": "Node.js/npm (npx) is not installed on this machine."}

    fd, env_path = tempfile.mkstemp(prefix=".safelauncher-deploy-key-", suffix=".env")
    os.close(fd)
    try:
        os.chmod(env_path, 0o600)
        env = _convex_cli_env(server_dir)
        # Token creation requires the user's interactive Convex login.  A
        # deploy key inherited from a project dotenv file or the parent
        # process would make the CLI reject this command, so never pass one
        # while minting its replacement.
        env.pop("CONVEX_DEPLOY_KEY", None)
        result = subprocess.run(
            ["npx", "convex", "deployment", "token", "create", key_name, "--prod", "--save-env", env_path],
            cwd=str(server_dir), env=env, capture_output=True, text=True, timeout=timeout, check=False,
        )
        safe_output = _redact_deploy_output("\n".join((result.stdout or "", result.stderr or "")))
        if result.returncode != 0:
            return {"ok": False, "error": f"Convex could not generate the deploy key (exit code {result.returncode}).\n{safe_output}"}
        values = {}
        for line in Path(env_path).read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "CONVEX_DEPLOY_KEY":
                values["key"] = value.strip().strip('"').strip("'")
                break
        generated = values.get("key", "")
        if not generated or any(ch.isspace() for ch in generated):
            return {"ok": False, "error": "Convex completed without returning a usable deploy key."}
        return {"ok": True, "key": generated, "message": "Production deploy key generated."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Deploy-key generation timed out. Check Convex CLI login and retry."}
    except OSError as exc:
        return {"ok": False, "error": f"Could not run the Convex CLI: {exc}"}
    finally:
        try:
            os.unlink(env_path)
        except OSError:
            pass


def validate_deploy_key(key: str, server_dir: Optional[Path] = None, *, timeout: int = 90) -> dict[str, str | bool]:
    """Validate a key syntactically and, when possible, with Convex dry-run."""
    value = str(key or "").strip()
    if not value or any(ch.isspace() for ch in value):
        return {"ok": False, "verified": False, "error": "The deploy key cannot be empty or contain whitespace."}
    if "|" not in value or ":" not in value.split("|", 1)[0]:
        return {"ok": False, "verified": False, "error": "This does not look like a Convex deployment key."}
    if not server_dir or not Path(server_dir).is_dir() or not shutil.which("npx"):
        return {"ok": True, "verified": False, "message": "Key format accepted; CLI verification is unavailable here."}
    env = _convex_cli_env(Path(server_dir))
    env["CONVEX_DEPLOY_KEY"] = value
    try:
        result = subprocess.run(
            ["npx", "convex", "deploy", "--dry-run"],
            cwd=str(Path(server_dir).resolve()), env=env, capture_output=True, text=True, timeout=timeout, check=False,
        )
        if result.returncode == 0:
            return {"ok": True, "verified": True, "message": "Convex accepted the deploy key for a non-destructive dry run."}
        diagnostic = (result.stdout or "") + "\n" + (result.stderr or "")
        diagnostic = diagnostic.replace(value, "[deploy-key redacted]")
        return {"ok": False, "verified": False, "error": f"Convex rejected the deploy key or project configuration.\n{_redact_deploy_output(diagnostic)}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "verified": False, "error": "Deploy-key verification timed out."}
    except OSError as exc:
        return {"ok": False, "verified": False, "error": f"Could not run the Convex CLI: {exc}"}


def _site_url_from_backend_checkout(server_dir: Path) -> str:
    """Read the site URL belonging to the checkout being deployed.

    Global auto-discovery can find another SafeLauncherCloud checkout. Deployment
    verification must follow the exact project directory passed to this function.
    """
    for filename in (".env.production.local", ".env.production", ".env.local", ".env"):
        env_file = server_dir / filename
        if not env_file.is_file():
            continue
        try:
            values = {}
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
            site_url = values.get("CONVEX_SITE_URL", "").strip()
            if site_url:
                return site_url.rstrip("/")
            convex_url = values.get("CONVEX_URL", "").strip()
            if convex_url:
                return convex_url.replace(".convex.cloud", ".convex.site").rstrip("/")
        except OSError:
            continue
    return ""


def download_server_repository(
    target_dir: Optional[Path] = None, *, prefer_archive: bool = False
) -> Optional[Path]:
    """Download or clone the SafeLauncherCloud backend repository.

    ``prefer_archive`` is used by the in-app updater.  It deliberately avoids
    cloning into (or updating) a user-owned checkout: the caller supplies a
    temporary destination which is discarded after the deployment.
    """
    import shutil
    import subprocess
    import tempfile
    import zipfile
    from pathlib import Path

    if target_dir:
        target = target_dir
    else:
        root_dir = Path(__file__).resolve().parent.parent
        parent_dir = root_dir.parent
        # Avoid downloading into /tmp when running inside AppImage (_MEIPASS)
        if getattr(sys, "frozen", False) or str(parent_dir).startswith(("/tmp", "/var/tmp")):
            target = Path.home() / "SafeLauncherCloud"
        else:
            target = parent_dir / "SafeLauncherDatabase"

    print(f"  Downloading server files into {target}...")

    # Method 1: git clone using host environment
    if not prefer_archive and shutil.which("git"):
        try:
            res = subprocess.run(
                ["git", "clone", "https://github.com/Mistarin/SafeLauncherCloud.git", str(target)],
                capture_output=True,
                text=True,
                env=host_process_env(),
            )
            if res.returncode == 0 and target.is_dir():
                marker = target / "ImHereJustToExist.txt"
                if not marker.exists():
                    marker.touch()
                return target
        except Exception:
            pass

    # Method 2: HTTP ZIP download fallback
    try:
        zip_url = "https://github.com/Mistarin/SafeLauncherCloud/archive/refs/heads/main.zip"
        resp = requests.get(zip_url, timeout=30)
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
            tf.write(resp.content)
            tmp_zip = tf.name

        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            for member in zf.infolist():
                parts = member.filename.split("/", 1)
                if len(parts) > 1 and parts[1]:
                    dest_file = target / parts[1]
                    # GitHub archives are expected to contain a single root
                    # directory.  Keep that assumption explicit so a damaged
                    # or malicious archive cannot escape the staging folder.
                    if not dest_file.resolve().is_relative_to(target.resolve()):
                        raise ValueError(f"unsafe archive member: {member.filename}")
                    if member.is_dir():
                        dest_file.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(dest_file, "wb") as dst:
                            dst.write(src.read())

        Path(tmp_zip).unlink(missing_ok=True)
        marker = target / "ImHereJustToExist.txt"
        if not marker.exists():
            marker.touch()
        return target
    except Exception as e:
        print(f"  [✖] Download failed: {e}")
        return None


def deploy_convex_backend(
    existing_path: Optional[str] = None,
    *,
    assume_yes: bool = False,
    deployment_env: Optional[Dict[str, str]] = None,
    expected_site_url: str = "",
) -> Optional[str]:
    """Build, connect, and deploy Convex backend functions.

    ``assume_yes`` is for a GUI caller that has already obtained explicit
    confirmation in its own dialog. Convex otherwise asks in the subprocess'
    hidden stdin, which makes a Settings-triggered redeploy hang forever.
    ``deployment_env`` lets an ephemeral source checkout deploy to an already
    linked Convex project without copying its private dotenv files.
    """
    import shutil
    import subprocess
    from pathlib import Path

    root_dir = Path(__file__).resolve().parent.parent
    parent_dir = root_dir.parent

    server_dir = Path(existing_path) if existing_path else None
    if not server_dir or not server_dir.is_dir():
        candidates = [
            parent_dir / "SafeLauncherDatabase",
            parent_dir / "SafeLauncherCloud",
            Path.home() / "Main" / "Programming" / "SafeLauncherDatabase",
            Path.home() / "Main" / "Programming" / "SafeLauncherCloud",
            Path.home() / "SafeLauncherDatabase",
            Path.home() / "SafeLauncherCloud",
        ]
        for c in candidates:
            if c.is_dir():
                server_dir = c
                break

    if not server_dir or not server_dir.is_dir():
        downloaded = download_server_repository()
        if not downloaded:
            return None
        server_dir = downloaded

    # A redeploy must not silently deploy an old checkout.  In-app updates use
    # ``deploy_latest_convex_backend`` below, which always stages fresh source
    # rather than mutating a user checkout with ``git pull``.
    limits_file = server_dir / "convex" / "lib" / "limits.ts"
    local_backend_version = ""
    try:
        limits_text = limits_file.read_text(encoding="utf-8")
        match = re.search(r'BACKEND_VERSION\s*=\s*["\']([^"\']+)', limits_text)
        local_backend_version = match.group(1).strip() if match else ""
    except OSError:
        pass
    if local_backend_version and is_version_outdated(local_backend_version, MIN_CONVEX_BACKEND_VERSION):
        print(
            f"  [✖] Backend source is v{local_backend_version}; the minimum is "
            f"v{MIN_CONVEX_BACKEND_VERSION}. Use the app-managed updater or a newer checkout."
        )
        return None

    compat = inspect_system_compatibility()
    if not compat["has_npm"]:
        if compat["is_steamos"] or compat["is_immutable"]:
            print("\n  \033[93m[!] Steam Deck / Immutable OS detected without Node.js & npm.\033[0m")
            print("  Because rootfs is read-only, pacman cannot be used to install npm.")
            print("\n  \033[1mRecommended Alternatives:\033[0m")
            print("  1) \033[92m1-Click Web Deployment:\033[0m Deploy in your browser with zero CLI setup:")
            print("     https://github.com/Mistarin/SafeLauncherCloud")
            print("     Then re-run this wizard and select Option 1 (Connect to existing backend).")
            print("\n  2) \033[96mUser-space NVM Installation\033[0m (installs Node.js in home directory):")
            print("     curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash")
            print("     source ~/.bashrc && nvm install 20\n")
        else:
            print(f"  [✖] Node.js & npm are required for deployment. ({compat['reason']})")
            print("      Install Node.js via your package manager (e.g. sudo pacman -S nodejs npm or sudo apt install nodejs npm),")
            print("      or deploy via web browser at https://github.com/Mistarin/SafeLauncherCloud\n")
        return None

    clean_env = _convex_cli_env(server_dir)
    if deployment_env:
        # Only deployment identity is inherited.  The staged source remains
        # isolated from arbitrary user dotenv configuration and secrets.
        for key in ("CONVEX_DEPLOYMENT", "CONVEX_DEPLOY_KEY"):
            value = str(deployment_env.get(key, "") or "").strip()
            if value:
                clean_env[key] = value

    def run_deploy_command(command: List[str], *, timeout: int) -> subprocess.CompletedProcess:
        """Run a deploy command and preserve its useful diagnostics for the UI."""
        result = subprocess.run(
            command,
            cwd=str(server_dir),
            check=False,
            env=clean_env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        output = _redact_deploy_output(
            output,
            (
                clean_env.get("CONVEX_DEPLOY_KEY", ""),
                clean_env.get("SAFELAUNCHER_SECRET_KEY", ""),
            ),
        )
        if output:
            print(output)
        if result.returncode != 0:
            detail = output[-3000:] if output else "no diagnostic output"
            display_command = " ".join(command)
            if command[:4] == ["npx", "convex", "env", "set"]:
                display_command = "npx convex env set SAFELAUNCHER_SECRET_KEY [redacted]"
            raise RuntimeError(f"{display_command} exited with code {result.returncode}: {detail}")
        return result

    print(f"\n  [Deploy] Installing dependencies in {server_dir}...")
    try:
        run_deploy_command(["npm", "install"], timeout=180)
        
        if not _has_convex_project_config(clean_env):
            print("\n  [Convex] First-time setup: linking this folder to your Convex project.")
            print("  Sign in when prompted, choose an existing project or create a new one, and wait for setup to finish.")
            run_deploy_command(["npx", "convex", "dev", "--once"], timeout=300)
            # ``convex dev --once`` writes .env.local. Reload it before deploy.
            clean_env = _convex_cli_env(server_dir)
            if not _has_convex_project_config(clean_env):
                print("  [✖] Convex setup finished without a deployment configuration.")
                print("      Run 'npx convex dev' in the backend folder, then start setup again.")
                return None

        # New private deployments should be locked down by default. A
        # deployment-scoped key intentionally has only deployment:deploy, so
        # it cannot (and must not be asked to) mutate Convex environment
        # variables. The API secret is initialized by the logged-in setup
        # path and is preserved for later deploy-key-only updates.
        using_deploy_key = bool(clean_env.get("CONVEX_DEPLOY_KEY", "").strip())
        if using_deploy_key:
            print("  [Security] Using the deployment-scoped key; preserving existing Convex environment variables.")
        else:
            secret_key = (
                clean_env.get("SAFELAUNCHER_SECRET_KEY", "").strip()
                or get_secret("cloud_secret_key", legacy_name="cloud_secret_key")
            )
            if not secret_key:
                secret_key = secrets.token_urlsafe(32)
                print("\n  [Security] Generated a random SafeLauncher secret key.")
            print("  [Security] Applying the secret key to the Convex deployment...")
            try:
                run_deploy_command(
                    ["npx", "convex", "env", "set", "SAFELAUNCHER_SECRET_KEY", secret_key],
                    timeout=60,
                )
            except RuntimeError:
                print("  [✖] Could not apply the SafeLauncher secret key to Convex.")
                print("      The deployment was not connected locally; fix Convex authentication and retry.")
                return None
            if not set_secret("cloud_secret_key", secret_key):
                print("  [✖] Convex accepted the secret key, but SafeLauncher could not save it locally.")
                return None
            print("  [✔] Secret key pushed to Convex and saved in SafeLauncher.")
        
        deploy_command = ["npx", "convex", "deploy"]
        if assume_yes:
            deploy_command.append("--yes")
        print(f"  [Deploy] Deploying backend functions with '{' '.join(deploy_command)}'...")
        run_deploy_command(deploy_command, timeout=300)
    except Exception as e:
        print(f"  [✖] Deployment encountered an error: {e}")
        return None

    from core.cloud_detector import detect_local_cloud_installation
    info = detect_local_cloud_installation()
    site_url = expected_site_url.strip().rstrip("/") or _site_url_from_backend_checkout(server_dir)
    if not site_url and info and info.get("site_url"):
        site_url = info["site_url"].rstrip("/")
    if site_url:
        verify_key = get_secret("cloud_secret_key", legacy_name="cloud_secret_key")
        headers = {"Authorization": f"Bearer {verify_key}", "X-SafeLauncher-Key": verify_key} if verify_key else {}
        deployed_version = ""
        for attempt in range(3):
            try:
                probe = requests.get(f"{site_url}/api/health", headers=headers, timeout=6)
                if probe.status_code == 200:
                    deployed_version = str((probe.json() or {}).get("version") or "").strip()
                    if deployed_version and not is_version_outdated(deployed_version, MIN_CONVEX_BACKEND_VERSION):
                        break
            except (requests.RequestException, ValueError, AttributeError):
                pass
            if attempt < 2:
                time.sleep(1)
        if not deployed_version or is_version_outdated(deployed_version, MIN_CONVEX_BACKEND_VERSION):
            print(
                f"\n  [✖] Deployment command finished, but the endpoint still reports "
                f"backend v{deployed_version or 'unknown'} (required v{MIN_CONVEX_BACKEND_VERSION}+)."
            )
            print("      Check the Convex deployment selected by the backend checkout and retry.")
            return None
        QSettings("SafeLauncher", "SafeLauncher").setValue("convex_site_url", site_url)
        print(f"\n  [✔] Deployment complete! Backend v{deployed_version} at {site_url}")
        return site_url
    return None


def deploy_latest_convex_backend(
    config_source_path: Optional[str] = None, *, assume_yes: bool = False
) -> Optional[str]:
    """Deploy the latest official backend without retaining server source files.

    Convex CLI must read the function source in order to typecheck and bundle
    it, but users do not need to clone or maintain that source.  This helper
    downloads the official archive into a private temporary directory, borrows
    only the Convex deployment identity from an existing checkout (or a saved
    deploy key), deploys non-interactively, and always removes the staging
    directory afterwards.
    """
    settings = QSettings("SafeLauncher", "SafeLauncher")
    deployment_env: Dict[str, str] = {}
    if config_source_path:
        source_path = Path(config_source_path)
        if source_path.is_dir():
            source_env = _convex_cli_env(source_path)
            for key in ("CONVEX_DEPLOYMENT", "CONVEX_DEPLOY_KEY"):
                value = str(source_env.get(key, "") or "").strip()
                if value:
                    deployment_env[key] = value

    saved_deploy_key = get_secret("convex_deploy_key", legacy_name="convex_deploy_key")
    if saved_deploy_key:
        deployment_env["CONVEX_DEPLOY_KEY"] = saved_deploy_key
    saved_deployment = str(settings.value("convex_deployment", "", type=str) or "").strip()
    if saved_deployment and "CONVEX_DEPLOY_KEY" not in deployment_env:
        deployment_env.setdefault("CONVEX_DEPLOYMENT", saved_deployment)

    if not _has_convex_project_config(deployment_env):
        print("  [✖] No Convex deployment credential is available for the managed update.")
        print("      Complete Cloud Setup once, or add a deployment key in Settings → Cloud.")
        return None

    expected_site_url = str(settings.value("convex_site_url", "", type=str) or "").strip()
    with tempfile.TemporaryDirectory(prefix="safelauncher-cloud-") as temp_root:
        stage_dir = Path(temp_root) / "SafeLauncherCloud"
        print("  [Deploy] Fetching the latest SafeLauncherCloud source into temporary staging...")
        source_dir = download_server_repository(stage_dir, prefer_archive=True)
        if not source_dir:
            return None
        deployed_url = deploy_convex_backend(
            str(source_dir),
            assume_yes=assume_yes,
            deployment_env=deployment_env,
            expected_site_url=expected_site_url,
        )
        # A local checkout is only a bootstrap source.  Retain the non-secret
        # deployment reference after a successful update so it can be removed
        # without disabling later app-managed redeploys. A deploy key remains
        # the portable option when the machine is not logged into the CLI.
        if deployed_url and deployment_env.get("CONVEX_DEPLOYMENT"):
            settings.setValue("convex_deployment", deployment_env["CONVEX_DEPLOYMENT"])
        return deployed_url


def run_cloud_setup_wizard() -> int:
    """Run interactive terminal setup wizard for private cloud save backend."""
    # Terminal ANSI styling
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    def banner(title: str, color=CYAN):
        width = 66
        line = "─" * (width - len(title) - 6)
        print(f"\n{color}{BOLD}┌── {title} {line}┐{RESET}")

    def footer(color=CYAN):
        print(f"{color}{BOLD}└{'─' * 64}┘{RESET}")

    settings = QSettings("SafeLauncher", "SafeLauncher")
    current_url = settings.value("convex_site_url", "", type=str).strip()
    discovered_url = discover_local_cloud_backend()
    active_url = current_url or discovered_url or ""
    current_key = get_secret("cloud_secret_key", legacy_name="cloud_secret_key")
    active_backend_version = ""
    active_backend_outdated = False

    # Fast probe: if already configured and reachable, show active status banner
    if active_url:
        try:
            headers = {}
            if current_key:
                headers["Authorization"] = f"Bearer {current_key}"
                headers["X-SafeLauncher-Key"] = current_key

            resp_health = requests.get(f"{active_url}/api/health", headers=headers, timeout=3)
            if resp_health.status_code == 200:
                try:
                    health_data = resp_health.json()
                    active_backend_version = str(health_data.get("version") or "").strip()
                    if not active_backend_version:
                        resp_version = requests.get(f"{active_url}/api/version", headers=headers, timeout=3)
                        if resp_version.status_code == 200:
                            active_backend_version = str(resp_version.json().get("version") or "").strip()
                    active_backend_outdated = bool(active_backend_version) and is_version_outdated(
                        active_backend_version, MIN_CONVEX_BACKEND_VERSION
                    )
                except (ValueError, AttributeError, requests.RequestException):
                    pass
                resp_me = requests.get(f"{active_url}/api/me", headers=headers, timeout=3)
                quota_info = ""
                if resp_me.status_code == 200:
                    data = resp_me.json()
                    used_mb = data.get("bytesUsed", 0) / (1024 * 1024)
                    quota_mb = data.get("quotaBytes", 0) / (1024 * 1024)
                    game_count = len(data.get("games", []))
                    quota_info = f"{used_mb:.1f} MB used of {quota_mb:.0f} MB · {game_count} game(s) synced"

                banner("Active Cloud Save Backend", GREEN)
                print(f"  {GREEN}{BOLD}✔ Status:{RESET}     Connected & Synchronizing")
                print(f"  {BOLD}• Endpoint:{RESET}   {active_url}")
                if quota_info:
                    print(f"  {BOLD}• Storage:{RESET}    {quota_info}")
                if current_key:
                    print(f"  {BOLD}• Security:{RESET}   Secret Key Configured ({'*' * len(current_key)})")
                footer(GREEN)

                reconf = input(f"\n{CYAN}{BOLD}➜{RESET} Reconfigure or change backend settings? [y/N]: ").strip().lower()
                if reconf not in ("y", "yes"):
                    print(f"{GREEN}✔ Existing cloud configuration preserved.{RESET}\n")
                    return 0
                print("")
        except Exception:
            pass

    # Step 1: Choose Setup Mode
    banner("[1/3] Choose Setup Mode", CYAN)
    print("  SafeLauncher stores game saves encrypted on your personal Convex cloud (1 GB free storage).\n")

    compat = inspect_system_compatibility()
    if compat["is_steamos"] or compat["is_immutable"]:
        print(f"  {YELLOW}{BOLD}● Host Diagnostic:{RESET} Steam Deck / Immutable OS detected.")
        if not compat["has_npm"]:
            print(f"    {DIM}(Node.js not installed on host. Connect mode or Web deployment recommended){RESET}\n")
        else:
            print(f"    {GREEN}Node.js ({compat['node_version']}) available.{RESET}\n")
    elif compat["can_deploy_locally"]:
        print(f"  {GREEN}{BOLD}✔ Host Diagnostic:{RESET} Node.js ({compat['node_version'] or 'detected'}) & npm are ready.\n")
    print(f"  {BOLD}1) Connect to an already created cloud database{RESET}  {GREEN}(Recommended){RESET}")
    print(f"     {DIM}• For secondary devices (such as a laptop, Steam Deck, or another PC).{RESET}")
    print(f"     {DIM}• You do NOT need Node.js, npm, git, or server files on this machine!{RESET}")
    print(f"     {DIM}• Simply enter your .convex.site URL and start syncing immediately.{RESET}\n")
    print(f"  {BOLD}2) Set up a new private cloud database from scratch{RESET}")
    print(f"     {DIM}• First-time setup: Downloads server repository and guides 'npx convex deploy'.{RESET}")
    if active_backend_outdated:
        print(f"  {BOLD}3) Redeploy the existing cloud backend{RESET}  {YELLOW}(Update available){RESET}")
        print(f"     {DIM}• Found backend v{active_backend_version}; SafeLauncher requires v{MIN_CONVEX_BACKEND_VERSION}+.{RESET}")
        print(f"     {DIM}• Keeps the existing deployment and data while applying the latest backend limits and fixes.{RESET}")
    footer(CYAN)

    mode_prompt = "[1/3]" if active_backend_outdated else "[1/2]"
    mode_choice = input(f"  {CYAN}{BOLD}➜{RESET} Choose setup mode {mode_prompt} (default: 1): ").strip()
    redeploy_existing = active_backend_outdated and mode_choice in ("3", "redeploy", "upgrade", "update")
    is_new_setup = mode_choice in ("2", "new", "deploy")

    local_info = None
    if is_new_setup or redeploy_existing:
        # Deployment & Auto-Detection
        local_info = detect_local_cloud_installation()
        if local_info:
            banner("[1b/3] Backend Auto-Detection", CYAN)
            print(f"  {GREEN}{BOLD}✔ Found local server files:{RESET} {local_info['path']}")
            if local_info.get("site_url"):
                print(f"  {GREEN}{BOLD}✔ Extracted from .env.local:{RESET}  {local_info['site_url']}")
                if local_info.get("deployment"):
                    print(f"  {DIM}• Convex deployment:{RESET}        {local_info['deployment']}")
                if redeploy_existing:
                    print(f"\n  {YELLOW}Redeploying the existing backend to v{MIN_CONVEX_BACKEND_VERSION}+...{RESET}")
                    deployed_url = deploy_convex_backend(local_info["path"])
                    if deployed_url:
                        local_info["site_url"] = deployed_url
            else:
                print(f"  {YELLOW}● Server folder found, but .env.local is not initialized yet.{RESET}")
                redeploy_prompt = (
                    f"\n  {CYAN}{BOLD}➜{RESET} Redeploy Convex backend now? [Y/n]: "
                    if redeploy_existing else
                    f"\n  {CYAN}{BOLD}➜{RESET} Deploy Convex backend now? [Y/n]: "
                )
                redeploy = input(redeploy_prompt).strip().lower()
                if redeploy not in ("n", "no"):
                    deployed_url = deploy_convex_backend(local_info["path"])
                    if deployed_url:
                        local_info["site_url"] = deployed_url
            footer(CYAN)
        else:
            banner("[1b/3] Backend Setup & Download", CYAN)
            print("  SafeLauncher stores encrypted game saves on your private Convex cloud.")
            print("  Convex provides 1 GB free cloud storage without monthly fees.\n")
            print(f"  {YELLOW}● SafeLauncherDatabase files not found on this system.{RESET}")
            download_choice = input(f"  {CYAN}{BOLD}➜{RESET} Download SafeLauncherDatabase now? [Y/n]: ").strip().lower()
            if download_choice not in ("n", "no"):
                downloaded_dir = download_server_repository()
                if downloaded_dir:
                    local_info = detect_local_cloud_installation() or {"path": str(downloaded_dir), "site_url": ""}
                    print(f"  {GREEN}{BOLD}✔ Server files ready in:{RESET} {downloaded_dir}")
                    deploy_choice = input(f"\n  {CYAN}{BOLD}➜{RESET} Link and deploy Convex backend now? [Y/n]: ").strip().lower()
                    if deploy_choice not in ("n", "no"):
                        deployed_url = deploy_convex_backend(str(downloaded_dir))
                        if deployed_url:
                            local_info["site_url"] = deployed_url
            else:
                print(f"\n  {BOLD}Manual deployment steps:{RESET}")
                print(f"   {DIM}1.{RESET} Clone:  {YELLOW}git clone https://github.com/Mistarin/SafeLauncherCloud.git SafeLauncherCloud{RESET}")
                print(f"   {DIM}2.{RESET} Deploy: {YELLOW}cd SafeLauncherCloud && npm install && npx convex deploy{RESET}")
                print(f"   {DIM}3.{RESET} Copy your project's {BOLD}.convex.site{RESET} URL from the terminal output.")
            footer(CYAN)
    else:
        local_info = detect_local_cloud_installation()

    # The deployment helper may have generated and stored a secret key in
    # another setup step. Refresh it before asking for connection details.
    current_key = get_secret("cloud_secret_key", legacy_name="cloud_secret_key")

    default_url = current_url or (local_info.get("site_url") if local_info else "") or ""

    # Step 2: Connection settings
    banner("[2/3] Connection Configuration", CYAN)
    prompt = f"  {CYAN}{BOLD}➜{RESET} Convex Site URL [{default_url}]: " if default_url else f"  {CYAN}{BOLD}➜{RESET} Convex Site URL (e.g. https://my-saves.convex.site): "
    entered_url = input(prompt).strip()
    site_url = entered_url if entered_url else default_url

    if not site_url:
        print(f"\n  {RED}✖ Site URL cannot be empty. Setup aborted.{RESET}\n")
        return 1

    site_url = site_url.rstrip("/")
    if not site_url.startswith("http://") and not site_url.startswith("https://"):
        site_url = "https://" + site_url

    print(f"\n  {BOLD}Secret Access Key (Recommended):{RESET}")
    print(f"     {DIM}Acts as a private password for your server endpoint. It stops anyone else{RESET}")
    print(f"     {DIM}who finds your public .convex.site URL from uploading files and filling{RESET}")
    print(f"     {DIM}up your 1 GB free Convex storage quota.{RESET}")
    print(f"     {DIM}• If you configured a secret key on your server, enter it below.{RESET}")
    print(f"     {DIM}• If this is a new setup, enter a passphrase to configure it on Convex now.{RESET}")
    print(f"     {DIM}• Press Enter to skip (leaves the server open to anyone with the URL).{RESET}\n")

    key_prompt = f"  {CYAN}{BOLD}➜{RESET} Secret Access Key [{current_key}]: " if current_key else f"  {CYAN}{BOLD}➜{RESET} Secret Access Key (press Enter to skip): "
    entered_key = input(key_prompt).strip()
    secret_key = entered_key if entered_key else current_key

    if is_new_setup and secret_key and local_info and local_info.get("path") and shutil.which("npx"):
        try:
            print(f"\n  {DIM}Configuring SAFELAUNCHER_SECRET_KEY on your Convex deployment...{RESET}")
            subprocess.run(
                ["npx", "convex", "env", "set", "SAFELAUNCHER_SECRET_KEY", secret_key],
                cwd=local_info["path"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=25,
                env=_convex_cli_env(Path(local_info["path"])),
            )
            print(f"  {GREEN}✔ Configured SAFELAUNCHER_SECRET_KEY in Convex environment!{RESET}")
        except Exception as e:
            print(f"  {YELLOW}[!] Could not auto-set secret in Convex: {e}{RESET}")

    footer(CYAN)

    # Step 3: Verification
    banner("[3/3] Live Verification", CYAN)
    print(f"  Testing connection to {site_url}...")
    try:
        headers = {}
        if secret_key:
            headers["Authorization"] = f"Bearer {secret_key}"
            headers["X-SafeLauncher-Key"] = secret_key

        # 1. Health probe
        resp = requests.get(f"{site_url}/api/health", headers=headers, timeout=6)
        if resp.status_code != 200:
            print(f"\n  {RED}✖ Health probe failed (HTTP {resp.status_code}). Check your Convex deployment.{RESET}\n")
            return 1
        print(f"  {GREEN}✔ Backend health probe passed.{RESET}")

        # 2. Account overview probe
        def _fmt_bytes(n: float) -> str:
            n = float(n)
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024 or unit == "GB":
                    return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
                n /= 1024
            return f"{n:.1f} GB"

        resp_me = requests.get(f"{site_url}/api/me", headers=headers, timeout=6)
        if resp_me.status_code in (401, 403):
            print(f"\n  {RED}✖ Quota verification failed: the backend rejected the credentials "
                  f"(HTTP {resp_me.status_code}). The Secret Key is missing or wrong — "
                  f"cloud sync will not work until it matches.{RESET}\n")
            return 1
        if resp_me.status_code == 200:
            data = resp_me.json()
            print(f"  {GREEN}✔ Quota verification:{RESET} "
                  f"{_fmt_bytes(data.get('bytesUsed', 0))} used of {_fmt_bytes(data.get('quotaBytes', 0))} · "
                  f"max {_fmt_bytes(data.get('maxSaveBytes', 0))} per save · "
                  f"keeping last {data.get('keepVersions', '?')} generations")

            # 3. Data key probe — save payloads are encrypted client-side with it.
            try:
                resp_key = requests.get(f"{site_url}/api/key", headers=headers, timeout=6)
                if resp_key.status_code == 200 and resp_key.json().get("dataKeyB64"):
                    print(f"  {GREEN}✔ Data encryption key available.{RESET}")
                else:
                    print(f"  {YELLOW}● Warning: /api/key returned HTTP {resp_key.status_code}; "
                          f"save uploads will fail until it succeeds.{RESET}")
            except requests.RequestException as key_err:
                print(f"  {YELLOW}● Warning: data key probe failed ({key_err}).{RESET}")
        else:
            print(f"  {YELLOW}● Warning: /api/me returned HTTP {resp_me.status_code}. (Check secret key if configured).{RESET}")

        # Save to local configuration
        settings.setValue("cloud_mode", "convex")
        settings.setValue("convex_site_url", site_url)
        if secret_key:
            set_secret("cloud_secret_key", secret_key)
        else:
            delete_secret("cloud_secret_key")

        footer(CYAN)

        banner("Setup Complete", GREEN)
        print(f"  {GREEN}{BOLD}✔ SafeLauncher is now connected to your private cloud backend.{RESET}")
        print("  Game saves will sync automatically with AES-256-GCM encryption.")
        footer(GREEN)
        print("")
        return 0

    except Exception as err:
        print(f"\n  {RED}✖ Connection failed: {err}{RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(run_cloud_setup_wizard())
