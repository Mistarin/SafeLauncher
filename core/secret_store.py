"""Small credential store used by SafeLauncher.

The desktop client needs two different credentials: the API secret used by
the cloud HTTP service and the Convex deploy key used by the CLI.  Keep both
behind one interface so UI code never needs to know where a secret is stored.

An OS keyring is preferred.  On headless/portable systems where no keyring is
available, the fallback is an AES-GCM encrypted file with restrictive
permissions.  The fallback protects configuration backups and accidental
plaintext exposure; it cannot protect against another process running as the
same user.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PyQt6.QtCore import QSettings

from core.logger import get_logger

logger = get_logger("SecretStore")

SERVICE_NAME = "SafeLauncher"
_NONCE_SIZE = 12
_FILE_MAGIC = b"SafeLauncherSecrets1\0"


def _data_dir() -> Path:
    root = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(root) / "safelauncher" / "credentials"


def _key_path() -> Path:
    return _data_dir() / "store.key"


def _payload_path() -> Path:
    return _data_dir() / "secrets.bin"


def _safe_chmod(path: Path, mode: int = 0o600) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _available_keyring():
    """Return a usable keyring module, or None for fail/headless backends."""
    try:
        import keyring

        backend = keyring.get_keyring()
        module = type(backend).__module__
        if module.startswith("keyring.backends.fail") or getattr(backend, "priority", 0) <= 0:
            return None
        return keyring
    except Exception:
        return None


def _load_file() -> dict[str, str]:
    key_file = _key_path()
    payload_file = _payload_path()
    if not key_file.is_file() or not payload_file.is_file():
        return {}
    try:
        key = key_file.read_bytes()
        raw = payload_file.read_bytes()
        if len(key) != 32 or not raw.startswith(_FILE_MAGIC):
            return {}
        body = raw[len(_FILE_MAGIC):]
        if len(body) < _NONCE_SIZE + 16:
            return {}
        plaintext = AESGCM(key).decrypt(body[:_NONCE_SIZE], body[_NONCE_SIZE:], _FILE_MAGIC)
        values = json.loads(plaintext.decode("utf-8"))
        return {str(k): str(v) for k, v in values.items() if isinstance(v, str)} if isinstance(values, dict) else {}
    except Exception as exc:
        logger.warning("Encrypted credential store could not be read: %s", exc)
        return {}


def _save_file(values: dict[str, str]) -> bool:
    directory = _data_dir()
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _safe_chmod(directory, 0o700)
        key_file = _key_path()
        if key_file.is_file():
            key = key_file.read_bytes()
        else:
            key = secrets.token_bytes(32)
            key_file.write_bytes(key)
            _safe_chmod(key_file)
        if len(key) != 32:
            return False
        nonce = secrets.token_bytes(_NONCE_SIZE)
        body = _FILE_MAGIC + nonce + AESGCM(key).encrypt(
            nonce, json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8"), _FILE_MAGIC
        )
        fd, temporary = tempfile.mkstemp(prefix=".secrets-", dir=str(directory))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
            os.replace(temporary, _payload_path())
            _safe_chmod(_payload_path())
            return True
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("Encrypted credential store could not be written: %s", exc)
        return False


def _remove_fallback_value(name: str) -> None:
    """Remove a migrated value so an old fallback cannot resurface later."""
    values = _load_file()
    if name not in values:
        return
    values.pop(name, None)
    _save_file(values)


def _legacy_value(name: str) -> str:
    try:
        settings = QSettings("SafeLauncher", "SafeLauncher")
        return str(settings.value(name, "", type=str) or "").strip()
    except Exception:
        return ""


def get_secret(name: str, default: str = "", *, legacy_name: Optional[str] = None) -> str:
    """Read a secret and migrate a legacy QSettings value on first access."""
    name = str(name or "").strip()
    if not name:
        return default
    keyring = _available_keyring()
    if keyring is not None:
        try:
            value = keyring.get_password(SERVICE_NAME, name)
            if value:
                return str(value).strip()
        except Exception:
            keyring = None
    value = _load_file().get(name, "").strip()
    if value:
        return value

    legacy = _legacy_value(legacy_name or name)
    if legacy:
        if set_secret(name, legacy):
            try:
                QSettings("SafeLauncher", "SafeLauncher").remove(legacy_name or name)
            except Exception:
                pass
        return legacy
    return default


def set_secret(name: str, value: str) -> bool:
    """Persist a secret without ever logging its value."""
    name = str(name or "").strip()
    value = str(value or "").strip()
    if not name:
        return False
    if not value:
        return delete_secret(name)
    keyring = _available_keyring()
    if keyring is not None:
        try:
            keyring.set_password(SERVICE_NAME, name, value)
            _remove_fallback_value(name)
            try:
                QSettings("SafeLauncher", "SafeLauncher").remove(name)
            except Exception:
                pass
            return True
        except Exception as exc:
            logger.warning("OS credential store unavailable; using encrypted fallback: %s", exc)
    values = _load_file()
    if value:
        values[name] = value
    else:
        values.pop(name, None)
    saved = _save_file(values)
    if saved:
        try:
            QSettings("SafeLauncher", "SafeLauncher").remove(name)
        except Exception:
            pass
    return saved


def delete_secret(name: str) -> bool:
    """Forget a secret from both the preferred and fallback stores."""
    name = str(name or "").strip()
    changed = False
    keyring = _available_keyring()
    if keyring is not None:
        try:
            keyring.delete_password(SERVICE_NAME, name)
            changed = True
        except Exception:
            pass
    values = _load_file()
    if name in values:
        values.pop(name, None)
        changed = _save_file(values) or changed
    try:
        QSettings("SafeLauncher", "SafeLauncher").remove(name)
        changed = True
    except Exception:
        pass
    return changed


def secret_suffix(name: str, length: int = 4) -> str:
    """Return a non-sensitive suffix for status displays."""
    value = get_secret(name)
    return value[-max(1, int(length)):] if value else ""
