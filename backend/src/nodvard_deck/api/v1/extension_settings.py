"""Einstellungen, Geheimnisse und Verbindungstest einer Erweiterung. `{id}` darf auch eine alte Kennung sein
(`legacy_ids`), es gilt dann die heutige (`extensions.resolve_extension`)."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from nodvard_sdk import sha256_fingerprint
from pydantic import BaseModel, Field
from sqlalchemy import select

from ... import edition
from ...branding import load_branding
from ...core import vault
from ...core.cron import cron_trigger
from ...core.schema_check import PATTERN_MAX_LENGTH as _PATTERN_MAX_LENGTH
from ...edition import display_text
from ...core.schema_check import bound as _bound
from ...core.schema_check import number_text as _number_text
from ...core.schema_check import pattern_matches as _pattern_matches
from ...core.schema_check import range_text as _range_text
from ...ext.runtime import get_extension_runtime
from ...models import ExtensionRecord, Secret
from ...services import audit as audit_service
from ...services import extension_settings, extension_setup, extension_test
from ...services import extensions as extensions_service
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission
from ..models import RequestModel
from ..responses import error_responses
from .auth import confirm_stored_secret_change
from .extensions import resolve_extension
from .secrets import SecretConfirm

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
    """Nur nach `PUT .../settings`: Labels der Geheimnisse, die gelöscht wurden, weil sich ihr Ziel geändert hat."""

    model_config = {"populate_by_name": True}


class ExtensionSettingsIn(SecretConfirm):
    """Werte; Passwort und Code nur, wenn die Änderung hinterlegte Zugangsdaten löscht (sonst 403 `confirm_required`)."""

    values: dict[str, Any]


class FingerprintIn(SecretConfirm):
    connection: str = Field(min_length=1, max_length=200)
    """Name des Eintrags, etwa der Proxmox-Verbindung."""
    sha256: str = Field(max_length=200)


class SecretIn(SecretConfirm):
    label: str
    value: str = Field(min_length=1, max_length=4096)


class TestIn(RequestModel):
    __test__ = False  # kein pytest-Testfall, nur ein Name

    mode: Literal["connection", "message"] = "connection"
    """`connection`: Verbindung prüfen. `message`: eine Testnachricht über den Benachrichtigungskanal senden."""


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


def schema_for(ext_id: str) -> dict[str, Any] | None:
    """Zuerst `settings.schema.json` (Titel, Beschreibungen, `x-secrets`), sonst `ctx.settings.declare()`.
    `ext_id` ist die heutige Kennung, nicht die Speicher-Kennung `ExtensionRecord.id`."""
    runtime = get_extension_runtime()
    manifest = getattr(runtime.discovered.get(ext_id), "manifest", None)
    if getattr(manifest, "settings_schema", None):
        return manifest.settings_schema
    loaded = runtime.loaded.get(ext_id)
    return loaded.settings_schema if loaded is not None else None


_secret_slots_spec = extension_setup.secret_slots_spec


_SCHEDULE_HINT = (
    "Erlaubt sind fünf Angaben: Minute (0–59), Stunde (0–23), Tag (1–31), Monat (1–12) und Wochentag (0–7), "
    "zum Beispiel „0 2 * * *“ für täglich um 2 Uhr."
)


def _check_schedule(value: str, schema: dict[str, Any], path: str) -> None:
    """Ein Zeitplan-Feld muss sich planen lassen (wie beim Anmelden des Jobs, `core.cron.cron_trigger`), sonst
    legt es die Extension beim nächsten Start lahm."""
    if value == "":
        return  # leer: es gilt der Standard der Extension
    try:
        cron_trigger(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"„{_field_name(schema, path)}“: Der Zeitplan „{value}“ ist ungültig. {_SCHEDULE_HINT}"
        ) from exc


def _field_name(schema: dict[str, Any], path: str) -> str:
    title = schema.get("title")
    return title if isinstance(title, str) and title else path


def _check_number(value: float, schema: dict[str, Any], path: str) -> None:
    """Zahlenfelder: `minimum` und `maximum` gelten einschließlich der Grenzen (wie `rangeError` in der Oberfläche)."""
    # NaN und unendlich nimmt der JSON-Leser von Python an, gespeichert wären sie kein gültiges JSON mehr
    # und die Seite ließe sich nicht mehr laden. Nur ein `float` kann das sein: `math.isfinite` auf einer riesigen ganzen
    # Zahl (`10**400`) würfe `OverflowError`.
    if isinstance(value, float) and not math.isfinite(value):
        raise HTTPException(status_code=422, detail=f"„{_field_name(schema, path)}“: Bitte eine Zahl eintragen.")
    low, high = _bound(schema, "minimum"), _bound(schema, "maximum")
    if (low is not None and value < low) or (high is not None and value > high):
        raise HTTPException(
            status_code=422,
            detail=f"„{_field_name(schema, path)}“: {_range_text(low, high)} Eingetragen ist {_number_text(value)}.",
        )


_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "array": (list,),
    "object": (dict,),
}


def _check(value: Any, schema: dict[str, Any], path: str) -> None:
    """Bewusst schmal (keine jsonschema-Abhängigkeit): Typ, Pflichtfelder, Enum, `pattern`, Zeitplan-Felder
    (`x-widget: "schedule"`), `minimum`/`maximum` bei Zahlen, `maxItems` bei Listen."""
    expected = schema.get("type")
    if value is None:
        return
    if expected in _TYPES:
        ok = isinstance(value, _TYPES[expected]) and not (expected in ("integer", "number") and isinstance(value, bool))
        if not ok:
            raise HTTPException(status_code=422, detail=f"„{path}“ hat den falschen Typ (erwartet {expected}).")
        if expected in ("integer", "number"):
            _check_number(value, schema, path)
    if "enum" in schema and value not in schema["enum"]:
        raise HTTPException(
            status_code=422,
            detail=f"„{_field_name(schema, path)}“ muss einer dieser Werte sein: {', '.join(map(str, schema['enum']))}.",
        )
    if isinstance(value, str) and value != "" and isinstance(schema.get("pattern"), str):
        limit = schema["maxLength"] if isinstance(schema.get("maxLength"), int) and schema["maxLength"] > 0 else _PATTERN_MAX_LENGTH
        if len(value) > limit:
            raise HTTPException(status_code=422, detail=f"„{path}“ ist zu lang (höchstens {limit} Zeichen).")
        if not _pattern_matches(schema["pattern"], value):
            raise HTTPException(
                status_code=422,
                detail=f"„{path}“: {schema.get('x-pattern-message') or 'Das Format stimmt nicht.'}",
            )
    if isinstance(value, str) and schema.get("x-widget") == "schedule":
        _check_schedule(value, schema, path)
    if expected == "object" and isinstance(value, dict):
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if value.get(key) in (None, ""):
                raise HTTPException(status_code=422, detail=f"„{path}.{key}“ ist ein Pflichtfeld.")
        for key, sub in props.items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}")
    max_items = schema.get("maxItems")
    if expected == "array" and isinstance(value, list) and isinstance(max_items, int) and len(value) > max_items:
        raise HTTPException(status_code=422, detail=f"„{_field_name(schema, path)}“: höchstens {max_items} Einträge.")
    if expected == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{i + 1}]")


async def _record(session, ext_id: str, *, change: bool = False) -> tuple[str, ExtensionRecord]:  # noqa: ANN001
    """(heutige Kennung, Registry-Zeile) zur Kennung aus der Adresse, siehe `resolve_extension`."""
    return await resolve_extension(session, ext_id, change=change, not_found="Unbekannte Erweiterung.")


def _display_schema(value: Any, settings: Any) -> Any:
    if isinstance(value, list):
        return [_display_schema(item, settings) for item in value]
    if isinstance(value, dict):
        return {
            key: display_text(settings, item) if key in {"title", "description"} and isinstance(item, str)
            else _display_schema(item, settings)
            for key, item in value.items()
        }
    return value


async def _settings_out(
    session, ext_id: str, values: dict[str, Any], secrets_cleared: list[str] | None = None, *, settings=None  # noqa: ANN001
) -> ExtensionSettingsOut:
    schema = schema_for(ext_id)
    schema = _display_schema(schema, settings)
    slots = []
    for spec in _secret_slots_spec(schema, values):
        stored = set((await session.execute(select(Secret.label).where(Secret.label.in_(spec["labels"])))).scalars())
        # Ersetzen und Entfernen nehmen dieses Label; es muss das sein, unter dem das Modul das Geheimnis liest.
        label = next((candidate for candidate in spec["labels"] if candidate in stored), spec["labels"][0])
        slots.append(SecretSlot(
            label=label, title=spec.get("title") or spec["label"], description=spec.get("description"),
            item=spec.get("item"), is_set=bool(stored), optional=bool(spec.get("optional")),
        ))
    return ExtensionSettingsOut(schema=schema, values=values, secrets=slots, secrets_cleared=secrets_cleared or [])


@router.get(
    "/{ext_id}/settings",
    response_model=ExtensionSettingsOut,
    response_model_by_alias=True,
    responses=error_responses(401, 403, 404, 409, 507),
)
async def get_extension_settings(ext_id: str, session: SessionDep, settings: SettingsDep) -> ExtensionSettingsOut:
    ext_id, record = await _record(session, ext_id)
    return await _settings_out(session, ext_id, dict(record.settings or {}), settings=settings)


@router.put(
    "/{ext_id}/settings",
    response_model=ExtensionSettingsOut,
    response_model_by_alias=True,
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 503, 507),
)
async def put_extension_settings(
    ext_id: str, payload: ExtensionSettingsIn, request: Request, session: SessionDep, settings: SettingsDep,
    user: CurrentUser,
) -> ExtensionSettingsOut | JSONResponse:
    ext_id, record = await _record(session, ext_id, change=True)
    schema = schema_for(ext_id)
    if not schema or not schema.get("properties"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Diese Erweiterung hat keine Einstellungen.")

    known = schema["properties"]
    # Gespeichert werden nur Schlüssel des Schemas, interne Zustände der Erweiterung bleiben unangetastet.
    # `x-hidden`-Felder pflegt die Erweiterung über eigene Routen (z. B. die Profile der Gameserver);
    # eine alte Kopie aus der Oberfläche darf sie nicht überschreiben.
    writable = {k: v for k, v in known.items() if not (isinstance(v, dict) and v.get("x-hidden"))}
    incoming = {k: v for k, v in payload.values.items() if k in writable}
    _check(incoming, {"type": "object", "properties": writable}, "Einstellungen")

    async with extension_settings.lock_for(record.id):
        await session.refresh(record, ["settings"])
        stored = dict(record.settings or {})
        merged = {**stored, **incoming}
        # Ein `null` entfernt den Wert, dann gilt wieder der Standard.
        for key, value in incoming.items():
            if value is None:
                merged.pop(key, None)
        # Pflichtfelder gelten für den Stand nach dem Zusammenführen (die Oberfläche schickt nur Geändertes);
        # ein Standardwert im Schema zählt wie in der Einrichtungsprüfung als gesetzt.
        required = [k for k in schema.get("required") or [] if (known.get(k) or {}).get("default") is None]
        _check(merged, {"type": "object", "properties": {}, "required": required}, "Einstellungen")
        changed = sorted(k for k in incoming if stored.get(k) != merged.get(k))

        # Ein Geheimnis gehört zu seinem Ziel: wer die Adresse ändert, gibt es neu ein.
        # Gemeldet (Antwort und Protokoll) wird nur, was wirklich im Tresor lag.
        stored_rows = []
        to_clear = extension_setup.secrets_to_clear(schema, stored, merged)
        for label in to_clear:
            row = (await session.execute(select(Secret).where(Secret.label == label))).scalar_one_or_none()
            if row is not None:
                stored_rows.append(row)
        exposed = []
        for label in extension_setup.secrets_exposed_by_tls(schema, stored, merged):
            if label not in to_clear and (
                await session.execute(select(Secret.id).where(Secret.label == label))
            ).first() is not None:
                exposed.append(label)
        if exposed and not stored_rows:
            # Die Zugangsdaten gingen künftig auch an einen Server, dessen Zertifikat bisher abgelehnt wurde.
            refused = await confirm_stored_secret_change(
                request, session, settings, user, payload.current_password, payload.totp_code, "tresor_ersetzen",
                "Mit dieser Änderung prüft Nodvard Deck das Zertifikat schwächer, und die hinterlegten Zugangsdaten "
                "gehen dann auch an einen Server, der bisher abgelehnt wurde. Bitte zur Bestätigung das aktuelle "
                "Passwort eingeben.",
            )
            if refused is not None:
                return refused
        if stored_rows:
            # Löscht gespeicherte Zugangsdaten wie „Löschen“ auf der Seite Tresor, also mit derselben Bestätigung.
            refused = await confirm_stored_secret_change(
                request, session, settings, user, payload.current_password, payload.totp_code, "tresor_loeschen",
                "Mit dieser Änderung werden hinterlegte Zugangsdaten gelöscht. Bitte zur Bestätigung das aktuelle "
                "Passwort eingeben.",
            )
            if refused is not None:
                return refused
        cleared = []
        for row in stored_rows:
            cleared.append(row.label)
            await vault.delete_secret(session, row.id)
        if cleared:
            await session.flush()

        record.settings = merged
        await session.flush()
        await session.commit()

    loaded = get_extension_runtime().loaded.get(ext_id)
    if loaded is not None:
        await loaded.ctx.settings._notify_changed(merged)

    await extension_setup.clear_last_test(session, ext_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="extension.settings", outcome="success",
        target_type="extension", target_id=ext_id,
        detail={
            "changed": changed, **({"secrets_cleared": cleared} if cleared else {}),
            **({"tls_weakened": exposed} if exposed else {}),
        },
    )
    await extensions_service.notify_secret_changed(session, cleared)
    return await _settings_out(session, ext_id, merged, cleared, settings=settings)


@router.put(
    "/{ext_id}/settings/fingerprint",
    response_model=ExtensionSettingsOut,
    response_model_by_alias=True,
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 503, 507),
)
async def pin_extension_fingerprint(
    ext_id: str, payload: FingerprintIn, request: Request, session: SessionDep, settings: SettingsDep,
    user: CurrentUser,
) -> ExtensionSettingsOut | JSONResponse:
    """Merkt den Fingerabdruck eines einzelnen Eintrags und lässt alles andere, wie es gespeichert ist; betrifft das
    hinterlegte Zugangsdaten, nur mit Passwort und gegebenenfalls Code (403 `confirm_required`)."""
    ext_id, record = await _record(session, ext_id, change=True)
    schema = schema_for(ext_id)
    lists = extension_setup.pinnable_lists(schema)
    if not lists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Diese Erweiterung merkt keine Fingerabdrücke.")
    try:
        pin = sha256_fingerprint(payload.sha256)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    async with extension_settings.lock_for(record.id):
        await session.refresh(record, ["settings"])
        stored = dict(record.settings or {})
        found = next(
            (
                (key, index)
                for key in lists if isinstance(stored.get(key), list)
                for index, item in enumerate(stored[key])
                if isinstance(item, dict) and item.get("name") == payload.connection
            ),
            None,
        )
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diesen Eintrag gibt es nicht.")
        key, index = found
        items = list(stored[key])
        entry = {**items[index], extension_setup.PIN_FIELD: pin}
        if extension_setup.SKIP_VERIFY_FIELD in schema["properties"][key]["items"]["properties"]:
            entry[extension_setup.SKIP_VERIFY_FIELD] = False
        items[index] = entry
        merged = {**stored, key: items}

        exposed = []
        for label in extension_setup.secrets_exposed_by_tls(schema, stored, merged):
            if (await session.execute(select(Secret.id).where(Secret.label == label))).first() is not None:
                exposed.append(label)
        if exposed:
            refused = await confirm_stored_secret_change(
                request, session, settings, user, payload.current_password, payload.totp_code, "tresor_ersetzen",
                "Die hinterlegten Zugangsdaten gehen künftig an den Server mit diesem neuen Zertifikat. Bitte zur "
                "Bestätigung das aktuelle Passwort eingeben.",
            )
            if refused is not None:
                return refused
        record.settings = merged
        await session.flush()
        await session.commit()

    loaded = get_extension_runtime().loaded.get(ext_id)
    if loaded is not None:
        await loaded.ctx.settings._notify_changed(merged)
    await extension_setup.clear_last_test(session, ext_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="extension.settings", outcome="success",
        target_type="extension", target_id=ext_id,
        detail={"changed": [key], **({"tls_weakened": exposed} if exposed else {})},
    )
    return await _settings_out(session, ext_id, merged, settings=settings)


@router.put(
    "/{ext_id}/secrets",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 503, 507),
)
async def put_extension_secret(
    ext_id: str, payload: SecretIn, request: Request, session: SessionDep, settings: SettingsDep, user: CurrentUser
) -> JSONResponse | None:
    """Hinterlegt ein Geheimnis der Erweiterung; ein schon gespeichertes ersetzt es nur mit Passwort und
    gegebenenfalls Code (403 `confirm_required`, sonst wie `PUT /secrets/{id}/value`)."""
    ext_id, record = await _record(session, ext_id, change=True)
    schema = schema_for(ext_id)
    values = dict(record.settings or {})
    slot = extension_setup.secret_slot_for(schema, values, payload.label)
    if slot is None:
        raise HTTPException(status_code=422, detail="Dieses Geheimnis gehört nicht zu dieser Erweiterung.")
    if extension_setup.secret_name_twice(values, slot):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Der Name „{slot['item']}“ kommt mehrfach vor. Erst einen der Einträge umbenennen und die "
                "Einstellungen speichern, danach die Zugangsdaten hinterlegen."
            ),
        )
    if extension_setup.secret_target_missing(schema, values, slot):
        # Ohne gespeichertes Ziel gehört das Geheimnis zu keinem Server; es bekäme sonst die
        # Adresse, die später als Erstes eingetragen wird (auch die eines fremden Servers).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Erst die Adresse eintragen und die Einstellungen speichern, danach die Zugangsdaten hinterlegen.",
        )

    keyring = vault.load_keyring(settings)
    existing = (await session.execute(select(Secret).where(Secret.label == payload.label))).scalar_one_or_none()
    if existing is not None:
        refused = await confirm_stored_secret_change(
            request, session, settings, user, payload.current_password, payload.totp_code, "tresor_ersetzen",
            "Hier sind schon Zugangsdaten hinterlegt. Zum Ersetzen bitte das aktuelle Passwort eingeben.",
        )
        if refused is not None:
            return refused
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
    await extensions_service.notify_secret_changed(session, [payload.label])
    return None


@router.delete(
    "/{ext_id}/secrets",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 503, 507),
)
async def delete_extension_secret(
    ext_id: str,
    label: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    payload: Annotated[SecretConfirm | None, Body()] = None,
) -> JSONResponse | None:
    """Entfernt ein Geheimnis der Erweiterung (nur Labels aus `x-secrets`), ein gespeichertes nur mit Passwort und
    gegebenenfalls Code im Body (403 `confirm_required`). Idempotent: ein nicht gesetztes Geheimnis ist kein Fehler."""
    ext_id, record = await _record(session, ext_id, change=True)
    if extension_setup.secret_slot_for(schema_for(ext_id), dict(record.settings or {}), label) is None:
        raise HTTPException(status_code=422, detail="Dieses Geheimnis gehört nicht zu dieser Erweiterung.")
    existing = (await session.execute(select(Secret).where(Secret.label == label))).scalar_one_or_none()
    if existing is not None:
        confirm = payload or SecretConfirm()
        refused = await confirm_stored_secret_change(
            request, session, settings, user, confirm.current_password, confirm.totp_code, "tresor_loeschen",
            "Zum Entfernen der hinterlegten Zugangsdaten bitte das aktuelle Passwort eingeben.",
        )
        if refused is not None:
            return refused
        await vault.delete_secret(session, existing.id)
        await session.flush()
    await extension_setup.clear_last_test(session, ext_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="extension.secret_removed", outcome="success",
        target_type="extension", target_id=ext_id, detail={"label": label, "existed": existing is not None},
    )
    if existing is not None:
        await extensions_service.notify_secret_changed(session, [label])
    return None


async def _known_secret_values(session, settings, ext_id: str, values: dict[str, Any]) -> list[str]:  # noqa: ANN001
    """Klartexte der Geheimnisse dieser Erweiterung, nur um sie aus Fehlertexten zu schwärzen
    (`extension_test.scrub`). Sie verlassen diese Funktion nie."""
    keyring = vault.load_keyring(settings)
    out: list[str] = []
    for spec in _secret_slots_spec(schema_for(ext_id), values):
        rows = (await session.execute(select(Secret).where(Secret.label.in_(spec["labels"])))).scalars().all()
        for row in rows:
            try:
                out.append(await vault.read_secret_plaintext(session, keyring, row.id))
            except Exception:  # noqa: BLE001 - ein unlesbares Geheimnis darf den Test nicht verhindern
                continue
    return out


@router.post(
    "/{ext_id}/test",
    response_model=TestOut,
    responses=error_responses(401, 403, 404, 409, 422, 429, 503, 507),
)
async def test_extension(
    ext_id: str,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    payload: Annotated[TestIn, Body()] = TestIn(),
) -> TestOut:
    """Verbindung der Erweiterung prüfen, mit dem Recht zum Bearbeiten der Einstellungen (`extensions.manage`).
    Das Ergebnis wird als „letzter Test“ gemerkt (`needs_setup` in der Erweiterungsliste) und protokolliert."""
    ext_id, record = await _record(session, ext_id, change=True)
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

    extension_test.check_rate_limit(user.id)

    values = dict(record.settings or {})
    schema = schema_for(ext_id)
    secrets = await _known_secret_values(session, settings, ext_id, values)
    if payload.mode == "message":
        branding = await load_branding(session, edition.of(settings))
        outcome = await extension_test.send_test_message(
            channel=channel, settings=values, schema=schema, secrets=secrets, product_name=branding.product_name
        )
    else:
        outcome = await extension_test.run_connection_test(
            loaded, settings=values, schema=schema, secrets=secrets, channel=channel
        )
    del secrets  # nach dem Schwärzen nicht länger als nötig im Speicher halten

    await extension_setup.record_test(
        session, ext_id, ok=outcome.ok, message=outcome.message, actor_id=user.id,
        mode=payload.mode, kind=outcome.kind,
    )
    return TestOut(
        ok=outcome.ok,
        message=outcome.message,
        details=[TestDetailOut(name=d.name, ok=d.ok, message=d.message) for d in outcome.details] or None,
    )
