"""Eigene Vorlagen für „Neuer Stack“: nur die Compose-Datei, ohne `.env`, und nie mit Werten, die wie Passwörter oder Tokens aussehen."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from .adopt import StoreProblem
from .editor import _write_private
from .git_fetch import _ComposeLoader, _shown

log = logging.getLogger(__name__)

STORE_FILE = "own_templates.json"
MAX_TEMPLATES = 100
MAX_COMPOSE = 64 * 1024
MAX_NAME = 60
MAX_SUMMARY = 300
MAX_FINDINGS = 5

_ID_RE = re.compile(r"^[0-9a-f]{16}\Z")
_NAME_RE = re.compile(r"-{0,2}[\w.-]{1,60}")
"""Was als Name eines Schlüssels oder Schalters in einer Meldung stehen darf; alles andere könnte ein Wert sein."""

_KEY_ENDINGS = (
    "password", "passwd", "passphrase", "passwort", "kennwort", "secret", "token", "requirepass", "apikey", "privatekey",
    "secretkey", "accesskey", "encryptionkey", "authkey", "masterkey", "appkey", "licensekey", "signingkey", "jwtkey",
    "secretkeybase",
)
_KEY_PARTS = {"pass", "pwd", "pw", "credential", "credentials"}
_HARMLESS = {"yes", "no", "true", "false", "on", "off", "0", "1"}
_SWITCH_PARTS = {"allow", "random", "empty", "enable", "enabled", "disable", "disabled", "require", "required", "use", "skip",
                 "generate", "reset", "no"}
"""Ein Schalter wie `MYSQL_RANDOM_ROOT_PASSWORD: "yes"` nennt neben dem Passwort, was er schaltet; `POSTGRES_PASSWORD: "1"` nicht."""
_REFERENCES = ("file://", "env://")

_VAR = r"\$\{[A-Za-z_][A-Za-z0-9_]{0,100}(?::?\?[^}]{0,200}|:?-)?\}|\$[A-Za-z_][A-Za-z0-9_]{0,100}"
_VAR_RE = re.compile(r"(?<!\$)(?:" + _VAR + ")")
"""`${DB_PASSWORD}`, `${DB_PASSWORD:?fehlt}` oder `$DB_PASSWORD`; eine Vorgabe wie `${PW:-geheim}` ist ein fester Wert,
`$$` ein Dollarzeichen."""
_SHELL_VAR_RE = re.compile(r"\$?(?:" + _VAR + ")")
"""In Befehlen auch `$$DB_PASSWORD`: Das wertet erst die Shell im Container aus."""

# Jedes Muster beginnt nur an einer Wortgrenze, die es selbst prüft; sonst wären lange Wörter quadratisch teuer.
_FLAG_RE = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]{0,59})(?:=|[ \t]+)(\S+)")
_URL_RE = re.compile(r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,30}://([^\s/@]{0,512})@")
_BRACED_RE = re.compile(r"\$\{[^}\s]{0,100}\}")
_COMMENT_RE = re.compile(r"(?:^|[ \t])#[ \t]*(?:-[ \t]*)?[\"']?([\w.-]{1,60})[ \t]*[:=][ \t]*(\S+)[ \t]*$", re.MULTILINE)
"""Ein auskommentierter Eintrag wie `# POSTGRES_PASSWORD: geheim`; Fließtext in Kommentaren bleibt außen vor."""
_EDGE = r"(?<![A-Za-z0-9_-])"
_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("einen privaten Schlüssel", re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----")),
    ("ein GitHub-Token", re.compile(_EDGE + r"(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{40,255})")),
    ("ein GitLab-Token", re.compile(_EDGE + r"glpat-[A-Za-z0-9_-]{20,255}")),
    ("einen AWS-Zugangsschlüssel", re.compile(_EDGE + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])")),
    ("ein Slack-Token", re.compile(_EDGE + r"xox[abposr]-[A-Za-z0-9-]{10,255}")),
    ("einen Tailscale-Schlüssel", re.compile(_EDGE + r"tskey-[a-z]{1,20}-[A-Za-z0-9-]{10,255}")),
    ("ein Anmelde-Token (JWT)", re.compile(_EDGE + r"eyJ[A-Za-z0-9_-]{8,4096}\.eyJ[A-Za-z0-9_-]{8,4096}\.[A-Za-z0-9_-]{8,4096}")),
)
_API_KEY_RE = re.compile(_EDGE + r"[sr]k[-_][A-Za-z0-9_-]{20,255}")
_MYSQL_CLIENTS = frozenset({
    "mysql", "mysqladmin", "mysqldump", "mysqlcheck", "mysqlimport", "mysqlshow", "mysqlslap", "mysqlbinlog", "mariadb",
    "mariadb-admin", "mariadb-dump", "mariadb-check", "mariadb-import", "mariadb-show", "mariadb-slap", "mariadb-binlog",
})
_SHELL_SPLIT_RE = re.compile(r"[;&|\n]")


def secret_key(name: str) -> bool:
    """Klingt der Name nach einem Geheimnis (`POSTGRES_PASSWORD`, `--requirepass`, `client_secret`, `TS_AUTHKEY`)?"""
    parts = re.findall(r"[a-z0-9]+", name.lower())
    return bool(parts) and ("".join(parts).endswith(_KEY_ENDINGS) or parts[-1] in _KEY_PARTS)


def _toggle(name: str) -> bool:
    return not _SWITCH_PARTS.isdisjoint(re.findall(r"[a-z0-9]+", name.lower()))


def literal(value: str, *, shell: bool = False, switch: bool = False) -> bool:
    """Steht da ein fester Wert, der ein Geheimnis sein kann, und nicht nur Variablen, ein Verweis oder bei einem Schalter
    etwas wie `yes` oder `1`?"""
    value = value.strip().strip("\"'")
    if (switch and value.lower() in _HARMLESS) or value.startswith(_REFERENCES):
        return False
    return bool((_SHELL_VAR_RE if shell else _VAR_RE).sub("", value).strip())


def _url_password(userinfo: str) -> str | None:
    """Das Passwort aus `benutzer:passwort`; ein `:` in `${DB_USER:-app}` trennt nicht."""
    masked = _BRACED_RE.sub(lambda m: "x" * len(m.group()), userinfo)
    cut = masked.find(":")
    return None if cut < 0 else userinfo[cut + 1:]


def _flag(flag: str) -> str:
    # `-pMeinPasswort` ist Schalter und Wert in einem; so etwas nie nennen.
    return flag if re.fullmatch(r"--[\w.-]{1,58}|-[A-Za-z]", flag) else "einem Schalter"


def _mysql_password(words: list[str]) -> int | None:
    """Stelle des Worts, mit dem ein MySQL- oder MariaDB-Programm sein Passwort als `-pWERT` bekommt; mit Leerzeichen
    danach fragt es nach dem Passwort, und bei anderen Programmen heißt `-p` oft Port."""
    client = False
    for index, word in enumerate(words):
        word = word.strip("\"'")
        if word.rsplit("/", 1)[-1] in _MYSQL_CLIENTS:
            client = True
        elif client and word.startswith("-p") and len(word) > 2 and literal(word[2:], shell=True):
            return index
    return None


def _api_key(match: str) -> bool:
    # Zufällige Schlüssel mischen Ziffern und Großbuchstaben; Image- oder Dienstnamen wie sk-backup-dienst nicht.
    body = match[3:]
    return sum(c.isdigit() for c in body) >= 3 and any(c.isupper() for c in body)


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _name(key: str) -> str | None:
    return key if _NAME_RE.fullmatch(key) else None


def _where(path: tuple[str, ...]) -> str:
    if len(path) >= 2 and path[0] == "services" and _name(path[1]):
        return f" („{_shown(path[1], 60)}“)"
    return f" ({path[0]})" if path and _name(path[0]) else ""


def _set(node: yaml.ScalarNode, key: str) -> bool:
    return not node.tag.endswith(":null") and literal(str(node.value), switch=_toggle(key))


def _nodes(root: yaml.Node) -> Iterator[tuple[yaml.Node, tuple[str, ...]]]:
    """Jeder Knoten genau einmal; Anker und Verweise zeigen auf denselben Knoten, das bleibt so linear."""
    seen: set[int] = set()
    todo: list[tuple[yaml.Node, tuple[str, ...]]] = [(root, ())]
    while todo:
        node, path = todo.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        yield node, path
        if isinstance(node, yaml.MappingNode):
            todo.extend((value, (*path, str(key.value) if isinstance(key, yaml.ScalarNode) else "")) for key, value in node.value)
        elif isinstance(node, yaml.SequenceNode):
            todo.extend((item, path) for item in node.value)


def _node_findings(root: yaml.Node) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node, path in _nodes(root):
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and isinstance(value, yaml.ScalarNode) and secret_key(str(key.value)) and _set(value, str(key.value)):
                    name = _name(str(key.value)) or "ein Eintrag"
                    found.append((value.start_mark.line + 1, f"{name}{_where(path)} hat einen festen Wert"))
        elif isinstance(node, yaml.SequenceNode):
            items = node.value
            at = _mysql_password([str(item.value) if isinstance(item, yaml.ScalarNode) else "" for item in items])
            if at is not None:
                found.append((items[at].start_mark.line + 1, f"der Befehl{_where(path)} gibt bei -p ein festes Passwort mit"))
            for index, item in enumerate(items):
                if not isinstance(item, yaml.ScalarNode):
                    continue
                key, sep, value = str(item.value).partition("=")
                key = key.strip()
                if sep and _name(key) and secret_key(key) and literal(value, shell=key.startswith("-"), switch=_toggle(key)):
                    shown = f"{_flag(key)}{_where(path)} hat" if key.startswith("-") else f"{key}{_where(path)} hat"
                    found.append((item.start_mark.line + 1, f"{shown} einen festen Wert"))
                follower = items[index + 1] if index + 1 < len(items) else None
                if (not sep and key.startswith("-") and _name(key) and secret_key(key) and isinstance(follower, yaml.ScalarNode)
                        and not str(follower.value).startswith("-") and literal(str(follower.value), shell=True, switch=_toggle(key))):
                    found.append((item.start_mark.line + 1, f"der Befehl{_where(path)} gibt bei {_flag(key)} einen festen Wert mit"))
        elif isinstance(node, yaml.ScalarNode) and isinstance(node.value, str):
            if any(_mysql_password(part.split()) is not None for part in _SHELL_SPLIT_RE.split(node.value)):
                found.append((node.start_mark.line + 1, f"der Befehl{_where(path)} gibt bei -p ein festes Passwort mit"))
            for match in _FLAG_RE.finditer(node.value):
                flag, value = match.groups()
                if secret_key(flag) and not value.startswith("-") and literal(value, shell=True, switch=_toggle(flag)):
                    found.append((node.start_mark.line + 1, f"der Befehl{_where(path)} gibt bei {_flag(flag)} einen festen Wert mit"))
    return found


def _text_findings(text: str) -> list[tuple[int, str]]:
    """Muster im ganzen Text, auch in Kommentaren."""
    found = [(_line(text, m.start()), f"sieht aus wie {what}") for what, pattern in _TOKEN_PATTERNS for m in pattern.finditer(text)]
    found += [(_line(text, m.start()), "sieht aus wie ein API-Schlüssel") for m in _API_KEY_RE.finditer(text) if _api_key(m.group())]
    found += [(_line(text, m.start()), "eine Adresse enthält ein Passwort (Benutzer:Passwort@)")
              for m in _URL_RE.finditer(text) if (password := _url_password(m.group(1))) is not None and literal(password)]
    found += [(_line(text, m.start()), f"ein Kommentar nennt einen Wert für {m.group(1)}")
              for m in _COMMENT_RE.finditer(text) if secret_key(m.group(1)) and literal(m.group(2), switch=_toggle(m.group(1)))]
    return found


def _structure(text: str) -> yaml.Node:
    try:
        root = yaml.compose(text, Loader=_ComposeLoader)
    except yaml.YAMLError as exc:
        # Die Meldung von PyYAML zitiert die Zeile und damit womöglich ein Geheimnis; nur die Stelle nennen.
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        where = f" (Zeile {mark.line + 1})" if mark is not None else ""
        raise ValueError(f"Die Datei ist kein gültiges YAML{where}.") from None
    except RecursionError:
        raise ValueError("Die Datei ist zu tief verschachtelt.") from None
    services = None
    if isinstance(root, yaml.MappingNode):
        services = next((v for k, v in root.value if isinstance(k, yaml.ScalarNode) and k.value == "services"), None)
    if not isinstance(services, yaml.MappingNode) or not services.value:
        raise ValueError("Eine Vorlage braucht unter services mindestens einen Dienst.")
    return root


def check_compose(text: str) -> None:
    """Gültige Compose-Datei mit Diensten und ohne feste Geheimnisse, sonst `ValueError` mit den Fundstellen (ohne Werte)."""
    root = _structure(text)
    found = sorted(set(_node_findings(root) + _text_findings(text)))
    if found:
        lines = "; ".join(f"Zeile {line}: {what}" for line, what in found[:MAX_FINDINGS])
        more = f" und {len(found) - MAX_FINDINGS} weitere" if len(found) > MAX_FINDINGS else ""
        raise ValueError(
            f"Vorlagen speichern keine Passwörter, Tokens oder Schlüssel. Gefunden: {lines}{more}. Schreib stattdessen eine "
            "Variable wie ${DB_PASSWORD}. Den Wert trägst du nach dem Anlegen des Stacks in eine .env in seinem Ordner ein "
            "(etwa über „Dateien“) und startest ihn erst danach."
        )


def _clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name or len(name) > MAX_NAME or any(not c.isprintable() for c in name):
        raise ValueError(f"Der Name fehlt, ist länger als {MAX_NAME} Zeichen oder enthält Steuerzeichen.")
    return name


def _clean_summary(raw: str) -> str:
    summary = " ".join(raw.split())
    if len(summary) > MAX_SUMMARY or any(not c.isprintable() for c in summary):
        raise ValueError(f"Die Beschreibung ist länger als {MAX_SUMMARY} Zeichen oder enthält Steuerzeichen.")
    return summary


@dataclass(frozen=True)
class OwnTemplate:
    id: str
    name: str
    summary: str
    compose: str
    updated_at: str


def _entry(template_id: Any, raw: Any) -> OwnTemplate | None:
    """Ein Eintrag aus der Datei; was nicht mehr passt (etwa von Hand verändert), fällt weg."""
    if not (isinstance(template_id, str) and _ID_RE.match(template_id) and isinstance(raw, dict)):
        return None
    compose = raw.get("compose")
    if not isinstance(compose, str) or len(compose.encode()) > MAX_COMPOSE:
        return None
    try:
        return OwnTemplate(template_id, _clean_name(str(raw.get("name") or "")), _clean_summary(str(raw.get("summary") or "")), compose,
                           str(raw.get("updated_at") or ""))
    except ValueError:
        return None


class StoreBroken(StoreProblem):
    """Die Datei ist kaputt; das nächste Speichern legt sie beiseite und beginnt leer."""


class OwnTemplates:
    """Die eigenen Vorlagen als JSON im Datenordner des Moduls, für alle mit dem Recht, Stacks anzulegen."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / STORE_FILE
        self._lock = asyncio.Lock()

    def _read(self, *, before_write: bool = False) -> dict[str, OwnTemplate]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8")).get("templates")
            if not isinstance(raw, dict):
                raise TypeError("templates ist keine Zuordnung")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            log.warning("docker_templates_unreadable path=%s error=%s", self._path, exc)
            raise StoreProblem("Die eigenen Vorlagen ließen sich gerade nicht lesen. Bitte später noch einmal.") from exc
        except (ValueError, TypeError, AttributeError) as exc:
            log.warning("docker_templates_broken path=%s", self._path)
            aside = self._path.with_name(f"{self._path.name}.kaputt-{datetime.now(UTC):%Y%m%d%H%M%S}")
            if not before_write:
                raise StoreBroken(
                    "Die Datei mit den eigenen Vorlagen ist beschädigt. Beim nächsten Speichern einer Vorlage wird sie als "
                    f"{STORE_FILE}.kaputt-… beiseitegelegt und eine neue Liste begonnen."
                ) from exc
            try:
                os.replace(self._path, aside)
            except OSError as move:
                raise StoreProblem("Die beschädigte Datei der eigenen Vorlagen ließ sich nicht beiseitelegen.") from move
            return {}
        return {t.id: t for t in (_entry(k, v) for k, v in raw.items()) if t}

    def _write(self, data: dict[str, OwnTemplate]) -> None:
        plain = {t.id: {k: v for k, v in asdict(t).items() if k != "id"} for t in data.values()}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            _write_private(self._path, json.dumps({"templates": plain}, indent=1, sort_keys=True, ensure_ascii=False).encode())
        except OSError as exc:
            raise StoreProblem(
                f"Die Vorlage ließ sich nicht speichern ({exc.strerror or type(exc).__name__}). "
                "Ist der Datenordner voll oder schreibgeschützt?"
            ) from exc

    async def all(self) -> list[OwnTemplate]:
        data = await asyncio.to_thread(self._read)
        return sorted(data.values(), key=lambda t: (t.name.casefold(), t.id))

    async def get(self, template_id: str) -> OwnTemplate | None:
        return (await asyncio.to_thread(self._read)).get(template_id)

    async def save(self, template_id: str | None, name: str, summary: str, compose: str) -> OwnTemplate:
        """Neu anlegen (`template_id` ist `None`) oder ersetzen; `LookupError` bei unbekannter Kennung, `FileExistsError`
        bei vergebenem Namen, `OverflowError` bei voller Liste."""
        async with self._lock:
            data = await asyncio.to_thread(self._read, before_write=True)
            if template_id is not None and template_id not in data:
                raise LookupError(template_id)
            if any(t.name.casefold() == name.casefold() and t.id != template_id for t in data.values()):
                raise FileExistsError(name)
            if template_id is None and len(data) >= MAX_TEMPLATES:
                raise OverflowError(MAX_TEMPLATES)
            saved = OwnTemplate(template_id or secrets.token_hex(8), name, summary, compose, datetime.now(UTC).isoformat(timespec="seconds"))
            data[saved.id] = saved
            await asyncio.to_thread(self._write, data)
        return saved

    async def remove(self, template_id: str) -> bool:
        async with self._lock:
            data = await asyncio.to_thread(self._read, before_write=True)
            if data.pop(template_id, None) is None:
                return False
            await asyncio.to_thread(self._write, data)
        return True


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=4 * MAX_NAME)
    summary: str = Field(default="", max_length=4 * MAX_SUMMARY)
    compose: str = Field(max_length=MAX_COMPOSE)


def _listed(template: OwnTemplate) -> dict[str, Any]:
    return {"id": template.id, "name": template.name, "summary": template.summary, "updated_at": template.updated_at}


async def _checked(body: TemplateIn) -> tuple[str, str, str]:
    compose = body.compose.replace("\r\n", "\n")
    if len(compose.encode()) > MAX_COMPOSE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"Eine Vorlage darf höchstens {MAX_COMPOSE // 1024} KB groß sein.")
    try:
        name, summary = _clean_name(body.name), _clean_summary(body.summary)
        await asyncio.to_thread(check_compose, compose)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return name, summary, compose


def own_template_routes(store: OwnTemplates) -> APIRouter:
    router = APIRouter()
    gone = "Diese Vorlage gibt es nicht (mehr)."

    def _known(template_id: str) -> str:
        if not _ID_RE.match(template_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=gone)
        return template_id

    def _unavailable(exc: StoreProblem) -> HTTPException:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    async def _save(template_id: str | None, body: TemplateIn) -> dict[str, Any]:
        name, summary, compose = await _checked(body)
        try:
            return asdict(await store.save(template_id, name, summary, compose))
        except LookupError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=gone) from None
        except FileExistsError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Es gibt schon eine Vorlage „{name}“. Wähl einen anderen Namen.") from None
        except OverflowError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"Es sind schon {MAX_TEMPLATES} eigene Vorlagen gespeichert. Lösch erst eine, die du nicht mehr brauchst.") from None
        except StoreProblem as exc:
            raise _unavailable(exc) from None

    @router.get("/own-templates")
    async def own_templates() -> dict[str, Any]:
        """Die eigenen Vorlagen nach Namen sortiert, ohne Compose-Datei; `problem` nennt eine beschädigte Datei."""
        try:
            return {"templates": [_listed(t) for t in await store.all()], "max": MAX_TEMPLATES, "problem": None}
        except StoreBroken as exc:
            return {"templates": [], "max": MAX_TEMPLATES, "problem": str(exc)}
        except StoreProblem as exc:
            raise _unavailable(exc) from None

    @router.get("/own-templates/{template_id}")
    async def own_template(template_id: str) -> dict[str, Any]:
        """Eine eigene Vorlage mit Compose-Datei."""
        try:
            found = await store.get(_known(template_id))
        except StoreProblem as exc:
            raise _unavailable(exc) from None
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=gone)
        return asdict(found)

    @router.post("/own-templates", status_code=status.HTTP_201_CREATED)
    async def create_own_template(body: TemplateIn) -> dict[str, Any]:
        """Eine Compose-Datei als neue eigene Vorlage speichern."""
        return await _save(None, body)

    @router.put("/own-templates/{template_id}")
    async def update_own_template(template_id: str, body: TemplateIn) -> dict[str, Any]:
        """Name, Beschreibung und Compose-Datei einer eigenen Vorlage ersetzen."""
        return await _save(_known(template_id), body)

    @router.delete("/own-templates/{template_id}")
    async def delete_own_template(template_id: str) -> dict[str, bool]:
        """Eine eigene Vorlage löschen; Stacks, die daraus angelegt wurden, bleiben, wie sie sind."""
        try:
            removed = await store.remove(_known(template_id))
        except StoreProblem as exc:
            raise _unavailable(exc) from None
        if not removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=gone)
        return {"deleted": True}

    return router
