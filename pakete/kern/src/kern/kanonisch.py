"""Kanonisches JSON und Hashes (Signaturen, plan_hash, Audit-Kette)."""

import hashlib
import json
from typing import Any


def kanonisch(obj: Any) -> bytes:
    """JSON mit sortierten Schluesseln, ohne Leerraum, UTF-8; nicht endliche Floats sind ein Fehler."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_hex(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


def sha256_praefix(daten: bytes) -> str:
    return f"sha256:{sha256_hex(daten)}"
