"""Verifiziert die im Konzept zugesagten Invarianten am echten Typ -- nicht nur
in der Dokumentation behauptet, sondern hier erzwungen (docs/01 und docs/02)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import nodvard_sdk as sdk


@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # die alten Klassennamen stehen als Aliase in __all__ (und warnen)
def test_sdk_exports_are_complete():
    missing = [name for name in sdk.__all__ if not hasattr(sdk, name)]
    assert missing == []


def test_action_request_requires_nonempty_reason():
    """Die Begruendungspflicht ist im Datentyp
    verankert, nicht nur in Anwendungslogik -- sie kann nicht versehentlich
    umgangen werden."""
    with pytest.raises(ValidationError):
        sdk.ActionRequest(
            action_type="shell.exec",
            proposed_by=sdk.Actor.ai("qwen"),
            reason="   ",
        )

    req = sdk.ActionRequest(
        action_type="shell.exec",
        proposed_by=sdk.Actor.ai("qwen"),
        reason="Container abgestuerzt",
    )
    assert req.risk == sdk.Risk.MEDIUM
    assert req.reason == "Container abgestuerzt"


def test_widget_without_declarative_view_is_rejected():
    """docs/02-EXTENSION-API.md §4: 'Kein Widget ohne deklarative Form' -- sonst
    saehe die Android-App von der Extension nichts."""
    with pytest.raises(ValidationError):
        sdk.WidgetSpec(id="x", title="X", data_endpoint="w/x")  # kein view=...

    widget = sdk.WidgetSpec(
        id="x", title="X", data_endpoint="w/x", view=sdk.StatView(value="{{ v }}")
    )
    assert widget.view.kind == "stat"


def test_manifest_rejects_invalid_id():
    with pytest.raises(ValidationError):
        sdk.ExtensionManifest(
            id="Nodvard Shield",
            name="x",
            version="0.1.0",
            api_version="0.1",
            entrypoint="m:E",
        )


def test_manifest_rejects_unknown_permission():
    with pytest.raises(ValidationError):
        sdk.ExtensionManifest(
            id="valid-id",
            name="x",
            version="0.1.0",
            api_version="0.1",
            entrypoint="m:E",
            permissions=["root.everything"],
        )


def test_manifest_derives_table_and_api_prefix():
    m = sdk.ExtensionManifest(
        id="shield",
        name="x",
        version="0.1.0",
        api_version="0.1",
        entrypoint="m:E",
        permissions=["hosts.execute"],
    )
    assert m.table_prefix == "ext_shield_"
    assert m.api_prefix == "/api/v1/ext/shield"


def _renamed_manifest(**extra):
    return sdk.ExtensionManifest(
        id="renamed-ext", name="x", version="0.1.0", api_version="0.1", entrypoint="m:E", **extra
    )


def test_manifest_without_legacy_ids_keeps_one_table_prefix():
    m = _renamed_manifest()
    assert m.legacy_ids == []
    assert m.table_prefixes == ("ext_renamed_ext_",)
    assert m.table_prefix == "ext_renamed_ext_"


def test_manifest_legacy_ids_add_old_table_prefixes_after_the_own_one():
    m = _renamed_manifest(legacy_ids=["old-ext", "older-ext"])
    assert m.table_prefixes == ("ext_renamed_ext_", "ext_old_ext_", "ext_older_ext_")
    assert m.table_prefix == "ext_renamed_ext_"
    assert m.api_prefix == "/api/v1/ext/renamed-ext"


@pytest.mark.parametrize(
    "legacy_ids",
    [["renamed-ext"], ["Old-Ext"], ["old_ext"], ["x"], ["old-ext", "old-ext"]],
)
def test_manifest_rejects_invalid_legacy_ids(legacy_ids):
    """Eine alte Kennung muss eine gueltige Kennung sein, darf nicht die eigene sein und nicht doppelt
    vorkommen -- sonst waere unklar, welche gespeicherte Zeile gilt."""
    with pytest.raises(ValidationError):
        _renamed_manifest(legacy_ids=legacy_ids)


def test_load_manifest_reads_legacy_ids(tmp_path):
    (tmp_path / "extension.toml").write_text(
        '[extension]\nid = "renamed-ext"\nname = "x"\nversion = "0.1.0"\napi_version = "0.1"\n'
        'entrypoint = "m:E"\nlegacy_ids = ["old-ext"]\n',
        encoding="utf-8",
    )
    assert sdk.load_manifest(tmp_path / "extension.toml").legacy_ids == ["old-ext"]


def test_sdk_compatibility_check():
    assert sdk.is_compatible("0.1")
    assert not sdk.is_compatible("0.2")


def test_notify_result_and_would_suppress_are_part_of_the_notify_contract():
    """`ctx.notify.send()` liefert `NotifyResult`; ohne Angabe gilt "nicht unterdrueckt".
    `would_suppress()` ist Teil des Protokolls (nur fragen, nichts anlegen)."""
    from nodvard_sdk.context import NotifyHandle

    result = sdk.NotifyResult(notification_id="n-1")
    assert result.suppressed is False
    assert sdk.NotifyResult(notification_id="n-1", suppressed=True).suppressed is True
    assert callable(getattr(NotifyHandle, "would_suppress", None))
    # Die Version bleibt: die Erweiterung ist rein additiv (alte Extensions laufen weiter).
    assert sdk.is_compatible("0.1")


def test_host_requirement_spec_defaults_and_export():
    """Additiver Erweiterungspunkt: eine Extension meldet, was sie auf einem Server braucht.
    Die API-Version bleibt, der Typ ist nur neu."""
    assert "HostRequirementSpec" in sdk.__all__
    spec = sdk.HostRequirementSpec(id="docker-group", label="Docker ohne sudo")
    assert spec.check_command is None
    assert spec.ok_text == "" and spec.fail_hint == ""
    assert spec.unix_group is None
    assert spec.needs_root is False and spec.root_reason is None
    assert spec.tags == [] and spec.os_families == ["linux"] and spec.order == 100
    # Keine gemeinsame Liste zwischen Instanzen.
    spec.tags.append("x")
    assert sdk.HostRequirementSpec(id="b", label="B").tags == []
    assert sdk.API_VERSION == "0.1.0"


def test_ui_handle_protocol_offers_register_host_requirement():
    from nodvard_sdk.context import UiHandle

    assert hasattr(UiHandle, "register_host_requirement")
