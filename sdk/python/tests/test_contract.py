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
        id="nexus-soc",
        name="x",
        version="0.1.0",
        api_version="0.1",
        entrypoint="m:E",
        permissions=["hosts.execute"],
    )
    assert m.table_prefix == "ext_nexus_soc_"
    assert m.api_prefix == "/api/v1/ext/nexus-soc"


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
