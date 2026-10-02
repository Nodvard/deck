"""Einstellungen einer Extension ueber die Oberflaeche pflegen (Einstellungen ->
Erweiterungen -> Konfigurieren).

- `GET  /extensions/{id}/settings` liefert Schema (aus `settings.schema.json` bzw.
  `ctx.settings.declare()`), aktuelle Werte und welche Geheimnisse gesetzt sind.
- `PUT  /extensions/{id}/settings` prueft die Werte grob gegen das Schema und
  speichert NUR die Schluessel, die das Schema kennt -- interne Zustaende einer
  Extension (z. B. `acknowledged_unprotected` bei backups) bleiben unangetastet.
  Felder mit `x-hidden` gehoeren den eigenen Routen der Extension und werden hier nie
  uebernommen; was nicht mitgeschickt wird, bleibt wie gespeichert, ein `null` entfernt
  den Wert (dann gilt wieder der Standard). Aendert sich ein Ziel, an das ein Geheimnis
  gebunden ist (`x-secrets[].x-secret-bound-to`), wird dieses Geheimnis geloescht (auch bei
  einer ersten Adresse und wenn nur eines von mehreren gebundenen Feldern wechselt).
  Laeuft die Extension, geht das Speichern ueber `ctx.settings.set()`, damit ihr
  `on_settings_changed` sofort greift.
- `PUT  /extensions/{id}/secrets` setzt oder ersetzt ein Geheimnis im Vault. Welche
  Labels erlaubt sind, beschreibt das Schema unter `x-secrets`; gelesen werden
  Geheimnisse hier nie. Ist es an Felder gebunden (`x-secret-bound-to`), die alle noch leer
  sind, antwortet die Route mit 409: erst die Adresse speichern, dann das Geheimnis.
- `DELETE /extensions/{id}/secrets?label=...` entfernt ein solches Geheimnis wieder
  (idempotent).
- `POST /extensions/{id}/test` prueft die Verbindung (`health()` der Extension bzw. bei
  Benachrichtigungskanaelen eine Testnachricht) und antwortet `{ok, message, details?}`
  auf Deutsch, ohne Geheimnisse im Text. Hoechstens 10 Tests je Minute und Nutzer.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...branding import load_branding
from ...core import rate_limit, vault
from ...ext.runtime import get_extension_runtime
from ...models import ExtensionRecord, Secret
from ...services import audit as audit_service
from ...services import extension_setup, extension_test
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission

router = APIRouter(
    prefix="/extensions",
    tags=["extensions"],
    dependencies=[Depends(require_permission("extensions.manage"))],
)


class SecretSlot(BaseModel):
    label: str
    title: str
    description: str | None = None
    item: str | None = None
    """Bei Geheimnissen je Listeneintrag (z. B. Proxmox-Verbindung): dessen Name."""
    is_set: bool
    optional: bool = False
    """`x-secrets[].optional`: die Erweiterung arbeitet auch ohne dieses Geheimnis."""


class ExtensionSettingsOut(BaseModel):
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    values: dict[str, Any]
    secrets: list[SecretSlot]
    secrets_cleared: list[str] = Field(default_factory=list)
    """Nur nach `PUT .../settings`: Labels der Geheimnisse, die geloescht wurden, weil sich
    das Ziel geaendert hat, zu dem sie gehoerten (sie sind dann wieder „nicht gesetzt“)."""

    model_config = {"populate_by_name": True}


class ExtensionSettingsIn(BaseModel):
    values: dict[str, Any]


class SecretIn(BaseModel):
    label: str
    value: str = Field(min_length=1, max_length=4096)


class TestIn(BaseModel):
    __test__ = False  # kein pytest-Testfall, nur ein Name

    mode: Literal["connection", "message"] = "connection"
    """`connection`: Verbindung pruefen. `message`: eine Testnachricht ueber den
    Benachrichtigungskanal der Extension senden."""


class TestDetailOut(BaseModel):
    __test__ = False

    name: str
    ok: bool
    message: str


class TestOut(BaseModel):
    __test__ = False

    ok: bool
    message: str
    details: list[TestDetailOut] | None = None
    """Bei Erweiterungen mit mehreren Verbindungen: das Ergebnis je Verbindung."""


# Hoechstens 10 Tests je Minute und Nutzer -- ein Test ruft echte Server an.
_TEST_LIMIT = 10
_TEST_WINDOW_S = 60
_test_window = rate_limit.SlidingWindow(_TEST_LIMIT, _TEST_WINDOW_S)


def schema_for(ext_id: str) -> dict[str, Any] | None:
    """`settings.schema.json` zuerst -- dort stehen Titel, Beschreibungen und
    `x-secrets` fuer die Oberflaeche. `ctx.settings.declare()` nur als Rueckfall
    fuer Extensions ohne Schema-Datei."""
    runtime = get_extension_runtime()
    manifest = getattr(runtime.discovered.get(ext_id), "manifest", None)
    if getattr(manifest, "settings_schema", None):
        return manifest.settings_schema
    loaded = runtime.loaded.get(ext_id)
    return loaded.settings_schema if loaded is not None else None


_secret_slots_spec = extension_setup.secret_slots_spec


_PATTERN_MAX_LENGTH = 2000
"""Laengere Werte werden vor der Musterpruefung abgelehnt (die Pruefung laeuft synchron)."""


def _js_anchors(pattern: str) -> str:
    """`$` heisst in JavaScript "Ende des Textes", in Python auch "vor einem `\\n` am Ende".
    Damit Oberflaeche und Backend gleich urteilen, wird ein `$` ausserhalb von Zeichenklassen
    zu `\\Z`."""
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "$":
            out.append("\\Z")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _pattern_matches(pattern: str, value: str) -> bool:
    try:
        return re.search(_js_anchors(pattern), value) is not None
    except re.error:
        return True  # ein kaputtes Muster im Schema darf das Speichern nie verhindern


_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "array": (list,),
    "object": (dict,),
}


def _check(value: Any, schema: dict[str, Any], path: str) -> None:
    """Bewusst schmal (keine jsonschema-Abhaengigkeit): Typ, Pflichtfelder, Enum, `pattern`."""
    expected = schema.get("type")
    if value is None:
        return
    if expected in _TYPES:
        ok = isinstance(value, _TYPES[expected]) and not (expected in ("integer", "number") and isinstance(value, bool))
        if not ok:
            raise HTTPException(status_code=422, detail=f"„{path}“ hat den falschen Typ (erwartet {expected}).")
    if "enum" in schema and value not in schema["enum"]:
        raise HTTPException(status_code=422, detail=f"„{path}“ muss einer dieser Werte sein: {', '.join(map(str, schema['enum']))}.")
    if isinstance(value, str) and value != "" and isinstance(schema.get("pattern"), str):
        limit = schema["maxLength"] if isinstance(schema.get("maxLength"), int) and schema["maxLength"] > 0 else _PATTERN_MAX_LENGTH
        if len(value) > limit:
            raise HTTPException(status_code=422, detail=f"„{path}“ ist zu lang (höchstens {limit} Zeichen).")
        if not _pattern_matches(schema["pattern"], value):
            raise HTTPException(
                status_code=422,
                detail=f"„{path}“: {schema.get('x-pattern-message') or 'Das Format stimmt nicht.'}",
            )
    if expected == "object" and isinstance(value, dict):
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if value.get(key) in (None, ""):
                raise HTTPException(status_code=422, detail=f"„{path}.{key}“ ist ein Pflichtfeld.")
        for key, sub in props.items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}")
    if expected == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{i + 1}]")


async def _record(session, ext_id: str) -> ExtensionRecord:  # noqa: ANN001 - AsyncSession
    record = await session.get(ExtensionRecord, ext_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Erweiterung.")
    return record


async def _settings_out(
    session, ext_id: str, values: dict[str, Any], secrets_cleared: list[str] | None = None  # noqa: ANN001
) -> ExtensionSettingsOut:
    schema = schema_for(ext_id)
    slots = []
    for spec in _secret_slots_spec(schema, values):
        exists = (await session.execute(select(Secret.id).where(Secret.label == spec["label"]))).first() is not None
        slots.append(SecretSlot(
            label=spec["label"], title=spec.get("title") or spec["label"], description=spec.get("description"),
            item=spec.get("item"), is_set=exists, optional=bool(spec.get("optional")),
        ))
    return ExtensionSettingsOut(schema=schema, values=values, secrets=slots, secrets_cleared=secrets_cleared or [])


@router.get("/{ext_id}/settings", response_model=ExtensionSettingsOut, response_model_by_alias=True)
async def get_extension_settings(ext_id: str, session: SessionDep) -> ExtensionSettingsOut:
    record = await _record(session, ext_id)
    return await _settings_out(session, ext_id, dict(record.settings or {}))


@router.put("/{ext_id}/settings", response_model=ExtensionSettingsOut, response_model_by_alias=True)
async def put_extension_settings(
    ext_id: str, payload: ExtensionSettingsIn, session: SessionDep, user: CurrentUser
) -> ExtensionSettingsOut:
    record = await _record(session, ext_id)
    schema = schema_for(ext_id)
    if not schema or not schema.get("properties"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Diese Erweiterung hat keine Einstellungen.")

    known = schema["properties"]
    # `x-hidden`-Felder pflegt die Erweiterung ueber ihre eigenen Routen (z. B. die Profile
    # der Gameserver); eine alte Kopie aus der Oberflaeche darf sie nicht ueberschreiben.
    writable = {k: v for k, v in known.items() if not (isinstance(v, dict) and v.get("x-hidden"))}
    incoming = {k: v for k, v in payload.values.items() if k in writable}
    _check(incoming, {"type": "object", "properties": writable}, "Einstellungen")

    stored = dict(record.settings or {})
    merged = {**stored, **incoming}
    for key, value in incoming.items():
        if value is None:
            merged.pop(key, None)
    # Pflichtfelder gelten fuer den Stand nach dem Zusammenfuehren (die Oberflaeche schickt nur
    # Geaendertes); ein Standardwert im Schema zaehlt wie in der Einrichtungs-Pruefung als gesetzt.
    required = [k for k in schema.get("required") or [] if (known.get(k) or {}).get("default") is None]
    _check(merged, {"type": "object", "properties": {}, "required": required}, "Einstellungen")
    changed = sorted(k for k in incoming if stored.get(k) != merged.get(k))

    # Ein Geheimnis gehoert zu seinem Ziel: wer die Adresse aendert, gibt es neu ein.
    # Gemeldet (Antwort und Protokoll) wird nur, was wirklich im Tresor lag.
    cleared = []
    for label in extension_setup.secrets_to_clear(schema, stored, merged):
        row = (await session.execute(select(Secret).where(Secret.label == label))).scalar_one_or_none()
        if row is not None:
            await vault.delete_secret(session, row.id)
            cleared.append(label)
    if cleared:
        await session.flush()

    loaded = get_extension_runtime().loaded.get(ext_id)
    if loaded is not None:
        # Eigene Session der Extension; sie ruft danach on_settings_changed auf.
        await session.commit()
        await loaded.ctx.settings.set(merged)
        await session.refresh(record)
    else:
        record.settings = merged
        await session.flush()

    await extension_setup.clear_last_test(session, ext_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="extension.settings", outcome="success",
        target_type="extension", target_id=ext_id,
        detail={"changed": changed, **({"secrets_cleared": cleared} if cleared else {})},
    )
    return await _settings_out(session, ext_id, merged, cleared)


@router.put("/{ext_id}/secrets", status_code=status.HTTP_204_NO_CONTENT)
async def put_extension_secret(
    ext_id: str, payload: SecretIn, session: SessionDep, settings: SettingsDep, user: CurrentUser
) -> None:
    record = await _record(session, ext_id)
    schema = schema_for(ext_id)
    values = dict(record.settings or {})
    slot = next((s for s in _secret_slots_spec(schema, values) if s["label"] == payload.label), None)
    if slot is None:
        raise HTTPException(status_code=422, detail="Dieses Geheimnis gehört nicht zu dieser Erweiterung.")
    if extension_setup.secret_target_missing(schema, values, slot):
        # Ohne gespeichertes Ziel gehoert das Geheimnis zu keinem Server; es bekaeme sonst die
        # Adresse, die später als Erstes eingetragen wird (auch die eines fremden Servers).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Erst die Adresse eintragen und die Einstellungen speichern, danach die Zugangsdaten hinterlegen.",
        )

    keyring = vault.load_keyring(settings)
    existing = (await session.execute(select(Secret).where(Secret.label == payload.label))).scalar_one_or_none()
    if existing is not None:
        await vault.replace_secret_value(session, keyring, existing.id, payload.value)
    else:
        await vault.create_secret(
            session, keyring, label=payload.label, kind="generic", plaintext=payload.value,
            owner_ext_id=ext_id, created_by_user_id=user.id,
        )
    await extension_setup.clear_last_test(session, ext_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="extension.secret_set", outcome="success",
        target_type="extension", target_id=ext_id, detail={"label": payload.label, "replaced": existing is not None},
    )


@router.delete("/{ext_id}/secrets", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extension_secret(ext_id: str, label: str, session: SessionDep, user: CurrentUser) -> None:
    """Entfernt ein Geheimnis der Erweiterung (nur Labels aus `x-secrets`). Idempotent:
    ein nicht gesetztes Geheimnis ist kein Fehler."""
    record = await _record(session, ext_id)
    allowed = {s["label"] for s in _secret_slots_spec(schema_for(ext_id), dict(record.settings or {}))}
    if label not in allowed:
        raise HTTPException(status_code=422, detail="Dieses Geheimnis gehört nicht zu dieser Erweiterung.")
    existing = (await session.execute(select(Secret).where(Secret.label == label))).scalar_one_or_none()
    if existing is not None:
        await vault.delete_secret(session, existing.id)
        await session.flush()
    await extension_setup.clear_last_test(session, ext_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="extension.secret_removed", outcome="success",
        target_type="extension", target_id=ext_id, detail={"label": label, "existed": existing is not None},
    )


async def _known_secret_values(session, settings, ext_id: str, values: dict[str, Any]) -> list[str]:  # noqa: ANN001
    """Die Klartexte der Geheimnisse dieser Erweiterung -- nur, um sie aus Fehlertexten zu
    schwaerzen (`extension_test.scrub`). Verlassen diese Funktion nie."""
    keyring = vault.load_keyring(settings)
    out: list[str] = []
    for spec in _secret_slots_spec(schema_for(ext_id), values):
        row = (await session.execute(select(Secret).where(Secret.label == spec["label"]))).scalar_one_or_none()
        if row is None:
            continue
        try:
            out.append(await vault.read_secret_plaintext(session, keyring, row.id))
        except Exception:  # noqa: BLE001 - ein unlesbares Geheimnis darf den Test nicht verhindern
            continue
    return out


@router.post("/{ext_id}/test", response_model=TestOut)
async def test_extension(
    ext_id: str,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    payload: Annotated[TestIn, Body()] = TestIn(),
) -> TestOut:
    """Verbindung der Erweiterung pruefen. Gleiches Recht wie beim Bearbeiten der
    Einstellungen (`extensions.manage`). Das Ergebnis wird als „letzter Test“ gemerkt
    (`needs_setup` in der Erweiterungsliste) und im Protokoll vermerkt."""
    record = await _record(session, ext_id)
    runtime = get_extension_runtime()
    loaded = runtime.loaded.get(ext_id)
    if loaded is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Die Erweiterung ist ausgeschaltet. Bitte erst einschalten, dann testen.",
        )
    channel = extension_test.find_channel(runtime, ext_id)
    if payload.mode == "message" and channel is None:
        raise HTTPException(status_code=422, detail="Diese Erweiterung kann keine Nachrichten senden.")

    wait = _test_window.hit(user.id)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Zu viele Tests hintereinander. Bitte in {rate_limit.wait_text(wait)} erneut versuchen.",
            headers={"Retry-After": str(wait)},
        )

    values = dict(record.settings or {})
    schema = schema_for(ext_id)
    secrets = await _known_secret_values(session, settings, ext_id, values)
    if payload.mode == "message":
        branding = await load_branding(session)
        outcome = await extension_test.send_test_message(
            channel=channel, settings=values, schema=schema, secrets=secrets, product_name=branding.product_name
        )
    else:
        outcome = await extension_test.run_connection_test(
            loaded, settings=values, schema=schema, secrets=secrets, channel=channel
        )
    del secrets  # nach dem Schwaerzen nicht laenger als noetig im Speicher halten

    await extension_setup.save_last_test(session, ext_id, ok=outcome.ok, message=outcome.message)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="extension.test",
        outcome="success" if outcome.ok else "failure", target_type="extension", target_id=ext_id,
        detail={"mode": payload.mode, "result": outcome.kind},
    )
    return TestOut(
        ok=outcome.ok,
        message=outcome.message,
        details=[TestDetailOut(name=d.name, ok=d.ok, message=d.message) for d in outcome.details] or None,
    )
