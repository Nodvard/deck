"""Prüfung von Ed25519-Signaturen (RFC 8032), nur `verify`, nur Standardbibliothek.

Der Helfer prüft damit Freigaben, die außerhalb von Nodvard Deck signiert wurden. Er kennt nur öffentliche Schlüssel
und öffentliche Daten, darum muss die Rechnung nicht zeitgleich laufen. Streng: `s < L`, der öffentliche Schlüssel
kanonisch kodiert und nicht von kleiner Ordnung, `R` nur in der kanonischen Kodierung.
"""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

# Punkte in erweiterten Koordinaten (X, Y, Z, T) mit x = X/Z, y = Y/Z, x*y = T/Z.
_Point = tuple[int, int, int, int]
_IDENTITY: _Point = (0, 1, 1, 0)


def _add(p: _Point, q: _Point) -> _Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _multiply(scalar: int, point: _Point) -> _Point:
    result = _IDENTITY
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _is_identity(point: _Point) -> bool:
    return point[0] % _P == 0 and (point[1] - point[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


def _decode(encoded: bytes) -> _Point | None:
    value = int.from_bytes(encoded, "little")
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, value >> 255)
    return None if x is None else (x, y, 1, x * y % _P)


def _encode(point: _Point) -> bytes:
    z_inverse = pow(point[2], _P - 2, _P)
    x, y = point[0] * z_inverse % _P, point[1] * z_inverse % _P
    return (y | (x & 1) << 255).to_bytes(32, "little")


_BASE_Y = 4 * pow(5, _P - 2, _P) % _P
_BASE_X = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_BASE: _Point = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % _P)


def _public_point(public: object) -> _Point | None:
    if not isinstance(public, bytes) or len(public) != 32:
        return None
    point = _decode(public)
    if point is None or _encode(point) != public or _is_identity(_multiply(8, point)):
        return None
    return point


def valid_public_key(public: bytes) -> bool:
    """Ob `public` ein kanonisch kodierter Punkt der Kurve ist, der nicht in einer kleinen Untergruppe liegt."""
    return _public_point(public) is not None


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Ob `signature` (64 Byte) eine gültige Ed25519-Signatur von `message` unter `public` (32 Byte) ist."""
    if not isinstance(message, bytes) or not isinstance(signature, bytes) or len(signature) != 64:
        return False
    point = _public_point(public)
    if point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    negated = ((_P - point[0]) % _P, point[1], point[2], (_P - point[3]) % _P)
    # [s]B - [k]A muss R sein; der Vergleich über die Kodierung lässt nur ein kanonisch kodiertes R gelten.
    return _encode(_add(_multiply(s, _BASE), _multiply(k, negated))) == signature[:32]
