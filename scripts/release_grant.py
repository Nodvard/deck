#!/usr/bin/env python3
"""Freigabe eines Releases für den Update-Helfer: Schlüssel anlegen und eine Version signieren.

    python scripts/release_grant.py key --out <Ordner>                    # einmalig: Schlüssel current und next
    python scripts/release_grant.py sign v0.12.0 --key <current.pem> --expect-digest sha256:<aus release.yml> [--upload]

`sign` fragt den Digest bei ghcr.io ab und signiert nur, wenn er dem Digest aus dem Lauf von `release.yml` gleicht
(`--trust-registry` verzichtet auf diesen Vergleich). Es baut die Freigabe, lässt sie von der Prüfung des Helfers
gegenlesen und legt `freigabe.json` und `freigabe.json.sig` ab; mit `--upload` hängt `gh` beide an den Entwurf des
Releases. Läuft nur auf Nicos Rechner, nie in der CI: der private Schlüssel verlässt ihn nicht.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "deploy" / "updater"))

from nodvard_deck_updater import ed25519, policy, trust

REGISTRY = "ghcr.io"
REPOSITORY = policy.REPOSITORY.removeprefix(f"{REGISTRY}/")
GRANT_NAME = "freigabe.json"
SIG_NAME = "freigabe.json.sig"
VALID_S = 365 * 24 * 3600
TIMEOUT_S = 15
MAX_BODY_BYTES = 1024 * 1024
MIN_PASSWORD = 12
ACCEPT = "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json"
TAG_RE = re.compile(rf"v({policy.VERSION_RE.pattern})", re.ASCII)


class GrantError(Exception):
    """Abgelehnt; die Meldung sagt warum und was zu tun ist (Exit-Code 1)."""


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519 as crypto_ed25519
    except ImportError:
        raise GrantError("Dafür fehlt das Paket „cryptography“ (pip install cryptography).") from None
    return serialization, crypto_ed25519


def version_of(tag: str) -> str:
    match = TAG_RE.fullmatch(tag)
    if match is None:
        raise GrantError(f"„{tag}“ ist kein Tag einer fertigen Version (etwa v0.12.0); Vorabversionen bekommen keine "
                         "Freigabe.")
    return match.group(1)


def _get(url: str, headers: dict[str, str]) -> tuple[bytes, dict[str, str]]:
    if urllib.parse.urlsplit(url).hostname != REGISTRY:
        raise GrantError(f"Nur {REGISTRY} wird gefragt, nicht {url}.")
    request = urllib.request.Request(url, headers={"User-Agent": "nodvard-release", **headers})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        body = response.read(MAX_BODY_BYTES + 1)
        found = {key.lower(): value for key, value in response.headers.items()}
    if len(body) > MAX_BODY_BYTES:
        raise GrantError("Antwort von ghcr.io zu groß.")
    return body, found


def registry_digest(version: str) -> str:
    """Der Digest hinter `ghcr.io/nodvard/deck:<version>`, berechnet wie in der Update-Suche von Nodvard Deck."""
    query = urllib.parse.urlencode({"scope": f"repository:{REPOSITORY}:pull", "service": REGISTRY})
    try:
        raw, _ = _get(f"https://{REGISTRY}/token?{query}", {})
        token = json.loads(raw).get("token")
        body, headers = _get(f"https://{REGISTRY}/v2/{REPOSITORY}/manifests/{version}",
                             {"Authorization": f"Bearer {token}", "Accept": ACCEPT})
    except (OSError, ValueError, AttributeError) as exc:
        raise GrantError(f"ghcr.io kennt {REPOSITORY}:{version} nicht oder antwortet nicht ({exc}). Ist das Paket "
                         "öffentlich und das Image schon gebaut?") from None
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    named = headers.get("docker-content-digest")
    if named is not None and named != digest:
        raise GrantError(f"ghcr.io nennt {named}, das Manifest hat aber {digest}. Nichts signiert.")
    return digest


def expected_digest(value: str | None, trust_registry: bool) -> str | None:
    """Der Digest aus `--expect-digest`; ohne ihn geht es nur mit `--trust-registry`."""
    if trust_registry:
        if value is not None:
            raise GrantError("--expect-digest und --trust-registry schließen sich aus.")
        return None
    if value is None:
        raise GrantError("Den erwarteten Digest mit --expect-digest sha256:… angeben. Er steht im Lauf von "
                         "release.yml, Job image, Schritt „Bauen und veroeffentlichen“, Abschnitt „Digest“.")
    if not policy.is_digest(value):
        raise GrantError(f"„{value}“ ist kein Digest. Erwartet: sha256: und 64 Hex-Zeichen in Kleinbuchstaben.")
    return value


def checked_digest(version: str, expected: str | None) -> str:
    """Der Digest von ghcr.io; mit `expected` nur, wenn beide gleich sind."""
    digest = registry_digest(version)
    if expected is not None and digest != expected:
        raise GrantError(f"ghcr.io liefert für {version} {digest}, aus release.yml erwartet war {expected}. Nichts "
                         f"signiert. Erst prüfen, ob der erwartete Wert aus dem Lauf zu v{version} stammt (bei "
                         "mehreren Versuchen aus dem letzten). Stimmt er, zeigt ghcr.io auf ein Image, das dieser Lauf "
                         "nicht gebaut hat: nicht signieren, sondern herausfinden, wer das Tag zuletzt gesetzt hat.")
    return digest


def build_grant(version: str, digest: str, key_id: str, now: int) -> dict[str, object]:
    return {"fmt": policy.GRANT_FORMAT, "repo": policy.REPOSITORY, "version": version, "digest": digest,
            "key_id": key_id, "issued_at": now, "expires_at": now + VALID_S}


def load_private_key(path: Path, password: bytes):
    serialization, crypto_ed25519 = _crypto()
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=password)
    except (OSError, ValueError, TypeError) as exc:
        raise GrantError(f"Schlüssel {path} ließ sich nicht öffnen (falsches Passwort?): {exc}") from None
    if not isinstance(key, crypto_ed25519.Ed25519PrivateKey):
        raise GrantError(f"{path} ist kein Ed25519-Schlüssel.")
    return key


def _raw_public(key) -> bytes:
    serialization, _ = _crypto()
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def sign(tag: str, key, *, digest: str, now: int, keys: tuple[trust.TrustedKey, ...] = trust.TRUSTED_KEYS,
         ) -> tuple[bytes, str]:
    """Freigabe und Signatur für `tag`; vorher muss sie die Prüfung des Helfers mit `keys` bestehen."""
    version = version_of(tag)
    public = _raw_public(key)
    key_id = trust.key_id(public)
    if trust.find(key_id, keys) is None:
        raise GrantError(f"Der Schlüssel {key_id} steht nicht in deploy/updater/nodvard_deck_updater/trust.py; der "
                         "Update-Helfer würde die Freigabe ablehnen.")
    grant = build_grant(version, digest, key_id, now)
    data = policy.dumps(grant)
    sig = key.sign(data).hex()
    request = {"v": 2, "id": "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60", "action": "update", "version": version,
               "created_at": now, "digest": digest, "grant": grant, "sig": sig}
    try:
        policy.parse_request(policy.dumps(request), now=now, keys=keys)
    except policy.Refusal as exc:
        raise GrantError(f"Die Prüfung des Helfers lehnt die Freigabe ab ({exc.code}). Nichts geschrieben.") from None
    if not ed25519.verify(public, data, bytes.fromhex(sig)):
        raise GrantError("Gegenprobe der Signatur fehlgeschlagen. Nichts geschrieben.")
    return data, sig


def write_files(out: Path, data: bytes, sig: str) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    grant_path, sig_path = out / GRANT_NAME, out / SIG_NAME
    grant_path.write_bytes(data)
    sig_path.write_text(sig + "\n", encoding="ascii")
    return grant_path, sig_path


def upload(tag: str, files: tuple[Path, Path]) -> None:
    command = ["gh", "release", "upload", tag, *map(str, files), "--clobber"]
    try:
        done = subprocess.run(command, check=False)
    except FileNotFoundError:
        raise GrantError("„gh“ fehlt. Die beiden Dateien von Hand an den Entwurf hängen.") from None
    if done.returncode != 0:
        raise GrantError(f"Hochladen gescheitert (gh {done.returncode}). Die Dateien liegen in {files[0].parent}.")


def make_keys(out: Path, password: bytes) -> list[str]:
    """Legt je einen Schlüssel für `trust.ROLES` verschlüsselt an und gibt die Zeilen für `trust.py` zurück."""
    serialization, crypto_ed25519 = _crypto()
    if len(password) < MIN_PASSWORD:
        raise GrantError(f"Das Passwort braucht mindestens {MIN_PASSWORD} Zeichen.")
    paths = {role: out / f"release-ed25519-{role}.pem" for role in trust.ROLES}
    taken = [str(path) for path in paths.values() if path.exists()]
    if taken:
        raise GrantError(f"Gibt es schon, nichts überschrieben: {', '.join(taken)}")
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for role, path in paths.items():
        key = crypto_ed25519.Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(password))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        public = _raw_public(key)
        lines.append(f'    TrustedKey("{role}", bytes.fromhex("{public.hex()}")),  # {trust.key_id(public)}')
    return lines


def _password(prompt: str, *, twice: bool = False) -> bytes:
    first = getpass.getpass(prompt)
    if twice and getpass.getpass("Noch einmal: ") != first:
        raise GrantError("Die Passwörter sind verschieden.")
    return first.encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    key_cmd = sub.add_parser("key", help="Schlüssel current und next anlegen (einmalig)")
    key_cmd.add_argument("--out", type=Path, required=True)
    sign_cmd = sub.add_parser("sign", help="eine Version freigeben")
    sign_cmd.add_argument("tag")
    sign_cmd.add_argument("--key", type=Path, required=True)
    sign_cmd.add_argument("--out", type=Path, default=Path.cwd())
    sign_cmd.add_argument("--expect-digest", metavar="sha256:…", help="Digest aus dem Lauf von release.yml (Job image)")
    sign_cmd.add_argument("--trust-registry", action="store_true",
                          help="ohne Vergleich signieren, was ghcr.io gerade liefert")
    sign_cmd.add_argument("--upload", action="store_true", help="beide Dateien mit gh an den Entwurf hängen")
    args = parser.parse_args(argv)
    try:
        if args.command == "key":
            lines = make_keys(args.out, _password("Passwort für die Schlüssel: ", twice=True))
            print("Schlüssel angelegt. In trust.py bei TRUSTED_KEYS eintragen (nur diese öffentlichen Zeilen):")
            print("\n".join(lines))
            print("Die Datei mit „next“ getrennt aufbewahren, nie in Nextcloud oder im Repository.")
            return 0
        version = version_of(args.tag)
        expected = expected_digest(args.expect_digest, args.trust_registry)
        digest = checked_digest(version, expected)
        if expected is None:
            print(f"Ohne Vergleich: ghcr.io liefert für {version} {digest}. Nur weitermachen, wenn das der Digest aus "
                  "dem Lauf von release.yml ist.", file=sys.stderr)
        key = load_private_key(args.key, _password(f"Passwort für {args.key.name}: "))
        data, sig = sign(args.tag, key, digest=digest, now=int(time.time()))
        files = write_files(args.out, data, sig)
        print(f"Freigabe für {version}: {digest}, Schlüssel {json.loads(data)['key_id']}, 365 Tage gültig.")
        print(f"Geschrieben: {files[0]} und {files[1]}")
        if args.upload:
            upload(args.tag, files)
            print(f"An den Entwurf {args.tag} gehängt.")
        return 0
    except GrantError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
