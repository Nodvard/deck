"""Die öffentlichen Schlüssel, mit denen eine Freigabe für ein Update signiert sein muss.

Nur Konstanten: kein Schlüssel kommt aus Umgebung, Kanal oder Dashboard. `current` signiert die Freigaben, `next` liegt
getrennt bereit für einen Wechsel; beide signieren nie etwas anderes als Freigaben. Solange `TRUSTED_KEYS` leer ist,
gilt keine Freigabe.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

ROLES = ("current", "next")


def key_id(public: bytes) -> str:
    """Die Kennung eines öffentlichen Schlüssels: die ersten 8 Byte seiner SHA-256, hexadezimal."""
    return hashlib.sha256(public).hexdigest()[:16]


@dataclass(frozen=True)
class TrustedKey:
    role: str
    public: bytes

    @property
    def key_id(self) -> str:
        return key_id(self.public)


TRUSTED_KEYS: tuple[TrustedKey, ...] = (
    # 916fff4eb943eb5d
    TrustedKey("current", bytes.fromhex("95fa6e379df26ace8c74eaed4e976a5c5d7df8a9ba8d28b9791f32d8c20b507b")),
    # 939c49421d468701
    TrustedKey("next", bytes.fromhex("0026831b6107eccaee061697934735029d969e971673182f60eb08ae8977b591")),
)


def find(wanted: str, keys: tuple[TrustedKey, ...] = TRUSTED_KEYS) -> TrustedKey | None:
    """Der vertraute Schlüssel mit dieser Kennung; Tests geben eigene Schlüssel nur als Parameter mit."""
    return next((key for key in keys if key.key_id == wanted), None)
