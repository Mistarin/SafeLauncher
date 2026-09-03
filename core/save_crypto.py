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
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.logger import get_logger

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
