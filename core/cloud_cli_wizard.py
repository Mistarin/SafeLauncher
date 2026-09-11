"""Terminal-based setup wizard for SafeLauncher private cloud saves."""

from __future__ import annotations

import os
import sys
import shutil
import secrets
import getpass
import subprocess
import tempfile
import zipfile
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any
from urllib.parse import urlparse

import requests
from PyQt6.QtCore import QSettings

from core.host_process import host_process_env
from core.host_process import is_sensitive_env_name
from core.cloud_detector import (
    discover_local_cloud_backend,
    detect_local_cloud_installation,
    inspect_system_compatibility,
)
from core.version import MIN_CONVEX_BACKEND_VERSION, is_version_outdated
from core.secret_store import get_secret, set_secret, delete_secret


_SENSITIVE_ENV_KEY_FRAGMENTS = (
    "API_KEY",
    "CLIENT_SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)
_SENSITIVE_ENV_KEYS = frozenset(
    {
        "CONVEX_DEPLOY_KEY",
        "CONVEX_GATEWAY_KEY",
        "SAFELAUNCHER_GATEWAY_KEY",
    }
)


def _close_response(response) -> None:
    """Close a one-shot requests response even when parsing/validation fails."""
    if response is None:
        return
    try:
        response.close()
    except Exception:
        pass


def _is_sensitive_env_key(name: str) -> bool:
    """Return whether a dotenv/process variable must not cross into a CLI.

    The backend checkout is source code, not a credential vault. Only the
    non-secret Convex project selectors are read from its dotenv files. A
    deploy key is reintroduced below from the OS credential store when the
    caller actually needs one.
    """
    normalized = str(name or "").strip().upper()
    return bool(normalized) and (
        normalized in _SENSITIVE_ENV_KEYS
        or any(fragment in normalized for fragment in _SENSITIVE_ENV_KEY_FRAGMENTS)
    )


def _convex_cli_env(server_dir: Path) -> dict[str, str]:
    """Return the host environment plus Convex dotenv configuration.

    ``convex dev`` writes project configuration to ``.env.local``.  Some
    Convex CLI versions do not load that file for a non-interactive
    ``convex deploy`` invocation, so pass the values explicitly.  Shell
    variables remain authoritative over dotenv values.
    """
    env = host_process_env()
    # Never inherit credentials from the desktop process or a shell session.
    # Callers that intentionally need a deploy key add it explicitly after
    # this boundary (validate_deploy_key/deploy_convex_backend).
    for key in list(env):
        if _is_sensitive_env_key(key) or is_sensitive_env_name(key):
            env.pop(key, None)
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
                if _is_sensitive_env_key(key) or is_sensitive_env_name(key):
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
    saved_deploy_key = get_secret("convex_deploy_key", legacy_name="convex_deploy_key")
    if saved_deploy_key:
        env["CONVEX_DEPLOY_KEY"] = saved_deploy_key
    return env


def _has_convex_project_config(env: dict[str, str]) -> bool:
    """Whether Convex has either a local deployment or production deploy key."""
    return bool(env.get("CONVEX_DEPLOYMENT") or env.get("CONVEX_DEPLOY_KEY"))


def _deployment_name_from_site_url(site_url: str) -> str:
    """Extract a standard Convex deployment name from its public site URL.

    Convex production URLs use ``<deployment-name>.<region>.convex.site``.
    A deployment name is sufficient for ``deployment token create`` and does
    not require a local ``.env.local`` project link. Custom domains and
    malformed URLs intentionally return an empty string so we do not guess.
    """
    try:
        host = (urlparse(str(site_url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    labels = host.split(".")
    if len(labels) < 3 or labels[-2:] != ["convex", "site"]:
        return ""
    candidate = labels[0]
    # Convex normally generates adjective-animal-number names, but imported
    # or older projects can use another DNS-safe deployment label.
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", candidate):
        return candidate
    return ""


def _configured_site_url() -> str:
    """Read the saved Convex site URL without requiring a project checkout."""
    settings = QSettings("SafeLauncher", "SafeLauncher")
    configured = str(settings.value("convex_site_url", "", type=str) or "").strip()
    if configured:
        return configured
    try:
        from core.cloud_backend import get_site_url
        return get_site_url().strip()
    except Exception:
        return ""


def _cloud_env_context(
    server_dir: Path,
    site_url: str = "",
    deployment: str = "",
) -> tuple[dict[str, str], list[str], str]:
    """Build an owner-authenticated Convex env command context.

    Deploy keys are deliberately removed here.  They are deployment-scoped
    credentials and must not be used to read or mutate Convex environment
    variables.  A standard Convex site URL gives us an unambiguous production
    deployment even when the staged checkout has no ``.env.local`` file.
    """
    server_dir = Path(server_dir).expanduser()
    env = _convex_cli_env(server_dir)
    env.pop("CONVEX_DEPLOY_KEY", None)
    env.pop("SAFELAUNCHER_SECRET_KEY", None)

    requested_url = str(site_url or "").strip() or _site_url_from_backend_checkout(server_dir)
    requested_url = requested_url or _configured_site_url()
    deployment_name = _deployment_name_from_site_url(requested_url)
    if deployment_name:
        # An inherited CONVEX_DEPLOYMENT can point at a development instance;
        # the explicit deployment option must be authoritative.
        env.pop("CONVEX_DEPLOYMENT", None)
        return env, ["--deployment", deployment_name], deployment_name

    explicit_deployment = str(deployment or "").strip()
    if explicit_deployment:
        env.pop("CONVEX_DEPLOYMENT", None)
        return env, ["--deployment", explicit_deployment], explicit_deployment

    deployment = str(env.get("CONVEX_DEPLOYMENT", "") or "").strip()
    if deployment:
        return env, [], deployment
    return env, [], ""


def _parse_convex_env_value(output: str, variable_name: str) -> str:
    """Extract a value from ``convex env get`` without logging it."""
    # Keep this parser intentionally conservative.  Convex prints the value
    # on its own line, while CLI/node diagnostics can appear around it.
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(output or ""))
    # Do not run the deploy-key redactor here: an API secret is allowed to be
    # an arbitrary string and may itself contain the token separator.
    clean = re.sub(
        r"\(?node:\d+\)?\s*ExperimentalWarning:\s*localStorage is not available "
        r"because --localstorage-file was not provided\.\s*"
        r"\(Use `node --trace-warnings \.\.\.` to show where the warning was created\)",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if not lines:
        return ""
    value = lines[-1]
    lower = value.casefold()
    missing_markers = {"", "undefined", "null", "not found", "not set", "no value"}
    if lower in missing_markers or lower.startswith("environment variable "):
        return ""
    prefix = f"{variable_name}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return value.strip()


def deploy_key_prerequisites(server_dir: Optional[Path] = None) -> dict[str, Any]:
    """Describe whether the local Convex CLI can mint a production deploy key."""
    path = Path(server_dir).expanduser() if server_dir else None
    npx_path = shutil.which("npx")
    convex_config = Path.home() / ".convex" / "config.json"
    env = _convex_cli_env(path) if path and path.is_dir() else {}
    deployment_from_url = _deployment_name_from_site_url(_configured_site_url())
    return {
        "has_npx": bool(npx_path),
        "npx_path": npx_path or "",
        "has_backend": bool(path and path.is_dir()),
        "backend_path": str(path) if path and path.is_dir() else "",
        "has_convex_project": bool(_has_convex_project_config(env) or deployment_from_url),
        "deployment_selector": str(env.get("CONVEX_DEPLOYMENT") or deployment_from_url),
        "has_cli_login": convex_config.is_file() and convex_config.stat().st_size > 0,
    }


def _redact_deploy_output(output: str, secrets_to_redact: tuple[str, ...] = ()) -> str:
    """Remove credential-shaped values before diagnostics reach the UI/logs."""
    text = str(output or "")
    # Node 25 emits this harmless warning when Convex runs without the
    # optional localStorage file. It obscures the actual Convex diagnostic
    # (and is unrelated to deploy-key authentication), so keep it out of the
    # wizard error panel while preserving all actionable output.
    text = re.sub(
        r"\(?node:\d+\)?\s*ExperimentalWarning:\s*localStorage is not available "
        r"because --localstorage-file was not provided\.\s*"
        r"\(Use `node --trace-warnings \.\.\.` to show where the warning was created\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    for secret in secrets_to_redact:
        if secret:
            text = text.replace(secret, "[secret redacted]")
    text = re.sub(
        r"(?im)(\b(?:CONVEX_DEPLOY_KEY|CONVEX_GATEWAY_KEY|SAFELAUNCHER_GATEWAY_KEY|"
        r"SAFELAUNCHER_SECRET_KEY|AUTH0_CLIENT_SECRET)\s*[=:]\s*)"
        r"(?:\"[^\r\n\"]*\"|'[^\r\n']*'|[^\r\n\s]+)",
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(\bAuthorization\s*:\s*Bearer\s+)[^\s\r\n]+",
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(\bX-SafeLauncher-(?:Gateway-Key|Key)\s*[:=]\s*)[^\s\r\n]+",
        r"\1[redacted]",
        text,
    )
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
        deployment_args = ["--prod"]
        if not env.get("CONVEX_DEPLOYMENT", "").strip():
            deployment_name = _deployment_name_from_site_url(_configured_site_url())
            if not deployment_name:
                return {
                    "ok": False,
                    "error": (
                        "Convex has no linked project in this checkout. Set the Convex Site URL in "
                        "Settings → Cloud and use a standard *.convex.site URL, or run "
                        "'npx convex dev' once in SafeLauncherCloud to link the project."
                    ),
                }
            # A deployment name targets the exact production deployment from
            # the saved URL and works even when this checkout has no .env.local.
            deployment_args = ["--deployment", deployment_name]
        result = subprocess.run(
            [
                "npx", "convex", "deployment", "token", "create", key_name,
                *deployment_args, "--save-env", env_path,
            ],
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
    working_dir = Path(server_dir).resolve()
    try:
        # Convex's dry-run deliberately does not write convex/_generated/. A
        # fresh checkout therefore fails during bundling even when the key is
        # valid, because the source imports those generated modules. Generate
        # the ignored local artifacts first, then tell deploy not to regenerate
        # them during the dry-run. Codegen is local preparation only; it never
        # deploys functions or changes the remote deployment.
        codegen = subprocess.run(
            ["npx", "convex", "codegen", "--typecheck", "disable"],
            cwd=str(working_dir), env=env, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        if codegen.returncode != 0:
            diagnostic = "\n".join((codegen.stdout or "", codegen.stderr or ""))
            diagnostic = diagnostic.replace(value, "[deploy-key redacted]")
            return {
                "ok": False,
                "verified": False,
                "error": (
                    "Convex could not prepare the local generated files for key verification.\n"
                    f"{_redact_deploy_output(diagnostic, (value,))}"
                ),
            }
        result = subprocess.run(
            [
                "npx", "convex", "deploy", "--dry-run", "--yes",
                "--codegen", "disable", "--typecheck", "disable",
            ],
            cwd=str(working_dir), env=env, capture_output=True, text=True,
            timeout=timeout, check=False,
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


def inspect_cloud_secret(
    server_dir: Path,
    *,
    site_url: str = "",
    deployment: str = "",
    timeout: int = 60,
) -> dict[str, str | bool]:
    """Check the production Convex environment for SafeLauncher's API secret.

    The value is returned only to the caller that needs to save it locally; it
    is never included in diagnostics.  ``convex env`` requires the user's
    Convex account login, so a deployment key is intentionally not a fallback
    for this operation.
    """
    path = Path(server_dir).expanduser().resolve()
    if not path.is_dir():
        return {"ok": False, "exists": False, "error": "The SafeLauncherCloud project directory is unavailable."}
    if not shutil.which("npx"):
        return {"ok": False, "exists": False, "error": "npx is not installed; Convex environment detection is unavailable."}

    env, selector_args, selected_deployment = _cloud_env_context(path, site_url, deployment)
    if not selected_deployment:
        return {
            "ok": False,
            "exists": False,
            "error": (
                "Convex production deployment could not be identified. Set the Convex Site URL or "
                "link the SafeLauncherCloud project with 'npx convex dev'."
            ),
        }

    command = ["npx", "convex", "env", "get", "SAFELAUNCHER_SECRET_KEY", *selector_args]
    try:
        result = subprocess.run(
            command,
            cwd=str(path),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "exists": False, "error": "Convex secret detection timed out."}
    except OSError as exc:
        return {"ok": False, "exists": False, "error": f"Could not run the Convex CLI: {exc}"}

    if result.returncode != 0:
        diagnostic = _redact_deploy_output("\n".join((result.stdout or "", result.stderr or "")))
        return {
            "ok": False,
            "exists": False,
            "error": (
                f"Convex could not inspect SAFELAUNCHER_SECRET_KEY for deployment {selected_deployment}.\n"
                f"{diagnostic or 'Sign in to Convex CLI or use the manual Secret Access Key field.'}"
            ),
        }

    value = _parse_convex_env_value(result.stdout or "", "SAFELAUNCHER_SECRET_KEY")
    if not value:
        return {
            "ok": True,
            "exists": False,
            "deployment": selected_deployment,
            "message": "No SafeLauncher Secret Access Key is configured on the production deployment.",
        }
    return {
        "ok": True,
        "exists": True,
        "secret": value,
        "deployment": selected_deployment,
        "message": "An existing SafeLauncher Secret Access Key was found on the production deployment.",
    }


def ensure_cloud_secret(
    server_dir: Path,
    *,
    site_url: str = "",
    deployment: str = "",
    provided_secret: str = "",
    timeout: int = 90,
) -> dict[str, str | bool]:
    """Make the local and production SafeLauncher API secret agree.

    The remote production environment is authoritative when it can be read.
    If it has no value, an explicitly supplied value, an existing local value,
    or a newly generated random value is configured there.  When Convex owner
    authentication is unavailable, an existing or explicitly entered local
    secret is saved but reported as unverified so an automatic deploy is not
    falsely treated as a cloud-secret failure.
    """
    local_secret = get_secret("cloud_secret_key", legacy_name="cloud_secret_key").strip()
    # Older/manual setups may still have the API secret in a private dotenv
    # file even though it has not yet been migrated into SafeLauncher's store.
    # Use it only as a candidate when the remote variable is absent; a remote
    # value remains authoritative below.
    if not local_secret:
        checkout_env = _convex_cli_env(Path(server_dir).expanduser())
        local_secret = str(checkout_env.get("SAFELAUNCHER_SECRET_KEY", "") or "").strip()
    entered_secret = str(provided_secret or "").strip()
    inspected = inspect_cloud_secret(
        server_dir,
        site_url=site_url,
        deployment=deployment,
        timeout=timeout,
    )

    if inspected.get("ok") and inspected.get("exists"):
        remote_secret = str(inspected.get("secret") or "").strip()
        if not remote_secret:
            return {"ok": False, "configured": False, "error": "Convex returned an empty SafeLauncher secret."}
        # The remote value wins if this machine had an obsolete local value.
        if not set_secret("cloud_secret_key", remote_secret):
            return {
                "ok": False,
                "configured": False,
                "error": "The existing Convex secret was found, but SafeLauncher could not save it locally.",
            }
        return {
            "ok": True,
            "configured": True,
            "verified": True,
            "source": "existing",
            "message": "Existing Cloud Save Secret Access Key detected and saved locally.",
        }

    if not inspected.get("ok"):
        fallback_secret = entered_secret or local_secret
        if fallback_secret:
            if not set_secret("cloud_secret_key", fallback_secret):
                return {
                    "ok": False,
                    "configured": False,
                    "error": "Convex could not be inspected and SafeLauncher could not save the local Secret Access Key.",
                }
            return {
                "ok": True,
                "configured": True,
                "verified": False,
                "source": "provided-local" if entered_secret else "local",
                "message": (
                    "Cloud Save Secret Access Key saved locally, but Convex could not be inspected; "
                    "confirm the same value is configured on the production deployment."
                ),
            }
        return {
            "ok": False,
            "configured": False,
            "error": str(inspected.get("error") or "Convex secret detection failed."),
        }

    secret = entered_secret or local_secret or secrets.token_urlsafe(32)
    env, selector_args, selected_deployment = _cloud_env_context(Path(server_dir), site_url, deployment)
    if not selected_deployment:
        return {
            "ok": False,
            "configured": False,
            "error": "Convex production deployment could not be identified for Secret Access Key setup.",
        }
    # Do not put the API secret in argv: it would be visible to process-list
    # observers. Convex reads stdin when `env set` receives a name without a
    # value and stdin is non-interactive.
    command = ["npx", "convex", "env", "set", "SAFELAUNCHER_SECRET_KEY", *selector_args]
    try:
        result = subprocess.run(
            command,
            cwd=str(Path(server_dir).expanduser().resolve()),
            env=env,
            capture_output=True,
            text=True,
            input=secret,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "configured": False, "error": "Convex Secret Access Key setup timed out."}
    except OSError as exc:
        return {"ok": False, "configured": False, "error": f"Could not run the Convex CLI: {exc}"}

    if result.returncode != 0:
        diagnostic = _redact_deploy_output(
            "\n".join((result.stdout or "", result.stderr or "")), (secret,)
        )
        return {
            "ok": False,
            "configured": False,
            "error": f"Convex could not configure the SafeLauncher Secret Access Key.\n{diagnostic or 'No diagnostic output.'}",
        }
    if not set_secret("cloud_secret_key", secret):
        return {
            "ok": False,
            "configured": False,
            "error": "Convex accepted the Secret Access Key, but SafeLauncher could not save it locally.",
        }
    source = "provided" if entered_secret else "existing-local" if local_secret else "generated"
    return {
        "ok": True,
        "configured": True,
        "verified": True,
        "source": source,
        "message": "Cloud Save Secret Access Key configured on Convex and saved locally.",
    }


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
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in {"CONVEX_SITE_URL", "CONVEX_URL"}:
                    continue
                value = value.strip().strip('"').strip("'")
                if key == "CONVEX_SITE_URL" and value:
                    return value.rstrip("/")
                if key == "CONVEX_URL" and value:
                    return value.replace(".convex.cloud", ".convex.site").rstrip("/")
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
    tmp_zip = None
    resp = None
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

        marker = target / "ImHereJustToExist.txt"
        if not marker.exists():
            marker.touch()
        return target
    except Exception as e:
        print(f"  [✖] Download failed: {e}")
        return None
    finally:
        _close_response(resp)
        if tmp_zip:
            Path(tmp_zip).unlink(missing_ok=True)


def deploy_convex_backend(
    existing_path: Optional[str] = None,
    *,
    assume_yes: bool = True,
    deployment_env: Optional[Dict[str, str]] = None,
    expected_site_url: str = "",
) -> Optional[str]:
    """Build, connect, and deploy Convex backend functions.

    ``assume_yes`` is enabled by default because every app-controlled deploy
    is preceded by SafeLauncher's own confirmation. Convex otherwise asks in
    the subprocess' stdin, which makes a Settings-triggered redeploy hang
    forever.
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
        # variables. Owner-authenticated setup reconciles the API secret
        # without overwriting an existing production value.
        using_deploy_key = bool(clean_env.get("CONVEX_DEPLOY_KEY", "").strip())
        if using_deploy_key:
            print("  [Security] Using the deployment-scoped key; preserving existing Convex environment variables.")
        else:
            print("  [Security] Detecting or initializing the SafeLauncher Secret Access Key...")
            secret_result = ensure_cloud_secret(
                server_dir,
                site_url=expected_site_url or _site_url_from_backend_checkout(server_dir),
                deployment=str((deployment_env or {}).get("CONVEX_DEPLOYMENT", "") or ""),
            )
            if not secret_result.get("ok"):
                print(f"  [✖] Could not prepare the SafeLauncher Secret Access Key: {secret_result.get('error', 'unknown error')}")
                print("      Fix Convex owner authentication or configure the secret in the dashboard, then retry.")
                return None
            print(f"  [✔] {secret_result.get('message', 'SafeLauncher Secret Access Key is ready.')}")
        
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
        # Deploy keys intentionally cannot read or mutate Convex environment
        # variables. Complete the separate SafeLauncher API-secret setup via
        # the authenticated owner CLI after the function deployment succeeds.
        # This also repairs older installations that were upgraded using only
        # a deploy key and therefore still showed “Cloud Save: Setup required”.
        secret_result = ensure_cloud_secret(
            server_dir,
            site_url=site_url,
            deployment=str((deployment_env or {}).get("CONVEX_DEPLOYMENT", "") or ""),
        )
        if secret_result.get("ok"):
            print(f"  [✔] {secret_result.get('message', 'Cloud Save secret is configured.')}")
        else:
            print(f"  [!] Backend deployed, but Cloud Save secret setup was not completed: {secret_result.get('error', 'unknown error')}")
        verify_key = get_secret("cloud_secret_key", legacy_name="cloud_secret_key")
        headers = {"Authorization": f"Bearer {verify_key}", "X-SafeLauncher-Key": verify_key} if verify_key else {}
        deployed_version = ""
        for attempt in range(3):
            probe = None
            try:
                probe = requests.get(f"{site_url}/api/health", headers=headers, timeout=6)
                if probe.status_code == 200:
                    deployed_version = str((probe.json() or {}).get("version") or "").strip()
                    if deployed_version and not is_version_outdated(deployed_version, MIN_CONVEX_BACKEND_VERSION):
                        break
            except (requests.RequestException, ValueError, AttributeError):
                pass
            finally:
                _close_response(probe)
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
    config_source_path: Optional[str] = None, *, assume_yes: bool = True
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

            resp_health = None
            try:
                resp_health = requests.get(f"{active_url}/api/health", headers=headers, timeout=3)
                health_status = resp_health.status_code
                health_data = resp_health.json() if health_status == 200 else {}
            finally:
                _close_response(resp_health)
            if health_status == 200:
                try:
                    active_backend_version = str(health_data.get("version") or "").strip()
                    if not active_backend_version:
                        resp_version = None
                        try:
                            resp_version = requests.get(f"{active_url}/api/version", headers=headers, timeout=3)
                            if resp_version.status_code == 200:
                                active_backend_version = str(resp_version.json().get("version") or "").strip()
                        finally:
                            _close_response(resp_version)
                    active_backend_outdated = bool(active_backend_version) and is_version_outdated(
                        active_backend_version, MIN_CONVEX_BACKEND_VERSION
                    )
                except (ValueError, AttributeError, requests.RequestException):
                    pass
                resp_me = None
                quota_info = ""
                try:
                    resp_me = requests.get(f"{active_url}/api/me", headers=headers, timeout=3)
                    if resp_me.status_code == 200:
                        data = resp_me.json()
                        used_mb = data.get("bytesUsed", 0) / (1024 * 1024)
                        quota_mb = data.get("quotaBytes", 0) / (1024 * 1024)
                        game_count = len(data.get("games", []))
                        quota_info = f"{used_mb:.1f} MB used of {quota_mb:.0f} MB · {game_count} game(s) synced"
                finally:
                    _close_response(resp_me)

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
    print(f"     {DIM}• First-time setup: Downloads server repository and guides 'npx convex deploy --yes'.{RESET}")
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
                print(f"   {DIM}2.{RESET} Deploy: {YELLOW}cd SafeLauncherCloud && npm install && npx convex deploy --yes{RESET}")
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

    print(f"\n  {BOLD}Secret Access Key (Required for the current backend):{RESET}")
    print(f"     {DIM}Acts as a private password for your server endpoint. It stops anyone else{RESET}")
    print(f"     {DIM}who finds your public .convex.site URL from uploading files and filling{RESET}")
    print(f"     {DIM}up your 1 GB free Convex storage quota.{RESET}")
    print(f"     {DIM}• Enter the same value configured as SAFELAUNCHER_SECRET_KEY on the server.{RESET}")
    print(f"     {DIM}• If this is a new setup, enter a passphrase to configure it on Convex now.{RESET}")
    print(f"     {DIM}• When a local backend is available, leaving this blank detects an existing key or generates one.{RESET}")
    print(f"     {DIM}• On a secondary device, enter the key already configured on the backend.{RESET}\n")

    key_prompt = (
        f"  {CYAN}{BOLD}➜{RESET} Secret Access Key [saved locally; press Enter to reuse]: "
        if current_key else
        f"  {CYAN}{BOLD}➜{RESET} Secret Access Key (required): "
    )
    try:
        entered_key = getpass.getpass(key_prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{YELLOW}Secret Access Key input cancelled.{RESET}\n")
        return 1
    secret_key = entered_key if entered_key else current_key

    if local_info and local_info.get("path") and shutil.which("npx"):
        try:
            print(f"\n  {DIM}Detecting or configuring SAFELAUNCHER_SECRET_KEY on your Convex deployment...{RESET}")
            secret_result = ensure_cloud_secret(
                Path(local_info["path"]),
                site_url=site_url,
                provided_secret=secret_key if is_new_setup else "",
            )
            if secret_result.get("ok"):
                secret_key = get_secret("cloud_secret_key", legacy_name="cloud_secret_key") or secret_key
                print(f"  {GREEN}✔ {secret_result.get('message', 'SafeLauncher Secret Access Key is ready.')} {RESET}")
            else:
                print(f"  {YELLOW}[!] Could not finish automatic secret setup: {secret_result.get('error', 'unknown error')}{RESET}")
        except Exception as e:
            print(f"  {YELLOW}[!] Could not auto-configure the Secret Access Key: {e}{RESET}")

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
        try:
            if resp.status_code != 200:
                print(f"\n  {RED}✖ Health probe failed (HTTP {resp.status_code}). Check your Convex deployment.{RESET}\n")
                return 1
        finally:
            _close_response(resp)
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
        me_ok = False
        try:
            if resp_me.status_code in (401, 403):
                print(f"\n  {RED}✖ Quota verification failed: the backend rejected the credentials "
                      f"(HTTP {resp_me.status_code}). The Secret Key is missing or wrong — "
                      f"cloud sync will not work until it matches.{RESET}\n")
                return 1
            if resp_me.status_code == 200:
                me_ok = True
                data = resp_me.json()
                print(f"  {GREEN}✔ Quota verification:{RESET} "
                      f"{_fmt_bytes(data.get('bytesUsed', 0))} used of {_fmt_bytes(data.get('quotaBytes', 0))} · "
                      f"max {_fmt_bytes(data.get('maxSaveBytes', 0))} per save · "
                      f"keeping last {data.get('keepVersions', '?')} generations")
            else:
                print(f"  {YELLOW}● Warning: /api/me returned HTTP {resp_me.status_code}. (Check secret key if configured).{RESET}")
        finally:
            _close_response(resp_me)

        # 3. Data key probe — save payloads are encrypted client-side with it.
        if me_ok:
            try:
                resp_key = requests.get(f"{site_url}/api/key", headers=headers, timeout=6)
                try:
                    if resp_key.status_code == 200 and resp_key.json().get("dataKeyB64"):
                        print(f"  {GREEN}✔ Data encryption key available.{RESET}")
                    else:
                        print(f"  {YELLOW}● Warning: /api/key returned HTTP {resp_key.status_code}; "
                              f"save uploads will fail until it succeeds.{RESET}")
                finally:
                    _close_response(resp_key)
            except requests.RequestException as key_err:
                print(f"  {YELLOW}● Warning: data key probe failed ({key_err}).{RESET}")

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
