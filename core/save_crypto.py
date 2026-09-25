"""
Client-side payload encryption for cloud saves (AES-256-GCM envelope).

Threat model: protects save contents against anyone who obtains stored blobs
or download URLs without database access. The key material mirrors what lives
in the user's Convex account row, so this is NOT zero-knowledge encryption;
a future passphrase-derived scheme can replace key retrieval transparently
(same format, different key source).

Envelope layout on disk/network:
    [1 byte version][12-byte nonce][ciphertext+16-byte tag]
"""

import base64
import hashlib
import os
import struct
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.logger import get_logger
from core.save_models import SaveOperationCancelled

logger = get_logger("SaveCrypto")

ENVELOPE_VERSION = 1
NONCE_LEN = 12
_HEADER = struct.Struct(">B")


class SaveCryptoError(Exception):
    """Raised when decryption fails (corruption or wrong key)."""


def encrypt_save(plaintext: bytes, data_key_b64: str) -> bytes:
    """Seal an (already zipped) save archive with the user's key."""
    try:
        key = _key_from_b64(data_key_b64)
        nonce = os.urandom(NONCE_LEN)
        header = _HEADER.pack(ENVELOPE_VERSION)
        sealed = AESGCM(key).encrypt(nonce, plaintext, header)
        return header + nonce + sealed
    except SaveCryptoError:
        raise
    except Exception as e:  # defensive: never leak half-ciphertexts silently
        raise SaveCryptoError(f"Encryption failed: {e}") from e


def decrypt_save(envelope: bytes, data_key_b64: str) -> bytes:
    """Open an encrypted save blob; raises SaveCryptoError on any failure."""
    if len(envelope) < _HEADER.size + NONCE_LEN + 16:
        raise SaveCryptoError("Encrypted save is truncated.")
    version, = _HEADER.unpack_from(envelope)
    if version != ENVELOPE_VERSION:
        raise SaveCryptoError(f"Unsupported envelope version {version}.")
    nonce = envelope[_HEADER.size:_HEADER.size + NONCE_LEN]
    ciphertext = envelope[_HEADER.size + NONCE_LEN:]
    header = _HEADER.pack(version)
    try:
        return AESGCM(_key_from_b64(data_key_b64)).decrypt(nonce, ciphertext, header)
    except Exception as e:
        raise SaveCryptoError(f"Decryption failed (wrong key or corrupt data): {e}") from e


def encrypt_save_file(
    plaintext_path: str,
    envelope_path: str,
    data_key_b64: str,
    *,
    max_plaintext_bytes: int | None = None,
    cancel_check=None,
    chunk_size: int = 1024 * 1024,
) -> tuple[str, int, int]:
    """Encrypt a zip file without loading it into memory.

    The on-disk format is identical to :func:`encrypt_save`: the one-byte
    version and nonce are authenticated as AAD, followed by ciphertext and
    the normal 16-byte GCM tag.  The returned tuple is ``(sha256, plain_size,
    envelope_size)``.
    """
    key = _key_from_b64(data_key_b64)
    nonce = os.urandom(NONCE_LEN)
    header = _HEADER.pack(ENVELOPE_VERSION)
    digest = hashlib.sha256()
    plain_size = 0
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    try:
        with open(plaintext_path, "rb") as source, open(envelope_path, "wb") as target:
            os.chmod(envelope_path, 0o600)
            target.write(header)
            target.write(nonce)
            while True:
                if cancel_check and cancel_check():
                    raise SaveOperationCancelled()
                chunk = source.read(max(1, int(chunk_size)))
                if not chunk:
                    break
                plain_size += len(chunk)
                if max_plaintext_bytes is not None and plain_size > int(max_plaintext_bytes):
                    raise SaveCryptoError("Plaintext save archive exceeds the size limit.")
                digest.update(chunk)
                target.write(encryptor.update(chunk))
            target.write(encryptor.finalize())
            target.write(encryptor.tag)
            target.flush()
            os.fsync(target.fileno())
        return digest.hexdigest(), plain_size, os.path.getsize(envelope_path)
    except SaveOperationCancelled:
        try:
            os.unlink(envelope_path)
        except OSError:
            pass
        raise
    except SaveCryptoError:
        try:
            os.unlink(envelope_path)
        except OSError:
            pass
        raise
    except Exception as exc:
        try:
            os.unlink(envelope_path)
        except OSError:
            pass
        raise SaveCryptoError(f"Encryption failed: {exc}") from exc


def decrypt_save_file(
    envelope_path: str,
    plaintext_path: str,
    data_key_b64: str,
    *,
    max_plaintext_bytes: int | None = None,
    cancel_check=None,
    chunk_size: int = 1024 * 1024,
) -> int:
    """Decrypt an envelope using bounded memory and authenticated staging.

    GCM authentication is only known after the final tag is processed.  The
    plaintext is therefore written to a private staging file and atomically
    moved into place only after authentication succeeds.  Ciphertext is
    copied to a second private temp file so the whole envelope never enters
    RAM and a failed authentication never exposes partial plaintext.
    """
    key = _key_from_b64(data_key_b64)
    try:
        envelope_size = os.path.getsize(envelope_path)
    except OSError as exc:
        raise SaveCryptoError(f"Encrypted save is unavailable: {exc}") from exc
    minimum = _HEADER.size + NONCE_LEN + 16
    if envelope_size < minimum:
        raise SaveCryptoError("Encrypted save is truncated.")
    if max_plaintext_bytes is not None:
        expected_plain_size = envelope_size - minimum
        if expected_plain_size > int(max_plaintext_bytes):
            raise SaveCryptoError("Encrypted save exceeds the size limit.")

    temp_dir = os.path.dirname(os.path.abspath(plaintext_path)) or None
    payload_path = None
    staged_path = None
    try:
        with open(envelope_path, "rb") as envelope:
            header = envelope.read(_HEADER.size)
            version, = _HEADER.unpack(header)
            if version != ENVELOPE_VERSION:
                raise SaveCryptoError(f"Unsupported envelope version {version}.")
            nonce = envelope.read(NONCE_LEN)
            ciphertext_bytes = envelope_size - minimum
            fd, payload_path = tempfile.mkstemp(prefix=".sl-cipher-", dir=temp_dir)
            os.close(fd)
            os.chmod(payload_path, 0o600)
            with open(payload_path, "wb") as payload:
                remaining = ciphertext_bytes
                while remaining:
                    if cancel_check and cancel_check():
                        raise SaveOperationCancelled()
                    chunk = envelope.read(min(max(1, int(chunk_size)), remaining))
                    if not chunk:
                        raise SaveCryptoError("Encrypted save is truncated.")
                    payload.write(chunk)
                    remaining -= len(chunk)
                tag = envelope.read(16)
                if len(tag) != 16:
                    raise SaveCryptoError("Encrypted save is truncated.")

        fd, staged_path = tempfile.mkstemp(prefix=".sl-plain-", dir=temp_dir)
        os.close(fd)
        os.chmod(staged_path, 0o600)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        plain_size = 0
        with open(payload_path, "rb") as payload, open(staged_path, "wb") as output:
            while True:
                if cancel_check and cancel_check():
                    raise SaveOperationCancelled()
                chunk = payload.read(max(1, int(chunk_size)))
                if not chunk:
                    break
                plain_size += len(chunk)
                if max_plaintext_bytes is not None and plain_size > int(max_plaintext_bytes):
                    raise SaveCryptoError("Decrypted save archive exceeds the size limit.")
                output.write(decryptor.update(chunk))
            try:
                output.write(decryptor.finalize())
            except Exception as exc:
                raise SaveCryptoError("Decryption failed (wrong key or corrupt data).") from exc
            output.flush()
            os.fsync(output.fileno())
        os.replace(staged_path, plaintext_path)
        staged_path = None
        os.chmod(plaintext_path, 0o600)
        return plain_size
    except SaveOperationCancelled:
        raise
    except SaveCryptoError:
        raise
    except Exception as exc:
        raise SaveCryptoError(f"Decryption failed (wrong key or corrupt data): {exc}") from exc
    finally:
        for path in (payload_path, staged_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def generate_data_key_b64() -> str:
    """Generate a fresh random 256-bit key, base64 encoded."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


def _key_from_b64(data_key_b64: str) -> bytes:
    try:
        raw = base64.b64decode(data_key_b64.encode("ascii"), validate=True)
    except Exception as e:
        raise SaveCryptoError("Malformed data key encoding.") from e
    if len(raw) != 32:
        raise SaveCryptoError(f"Data key must be 32 bytes, got {len(raw)}.")
    return raw
