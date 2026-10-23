"""Update-Helfer für Nodvard Deck: ein eigenes kleines Programm neben dem Dashboard.

Der Helfer hat Zugriff auf den Socket des Docker-Dienstes und tauscht auf Wunsch des Dashboards dessen
Container gegen eine neuere offizielle Version (oder einmal zurück auf den Vorgänger). Weil er damit so viel
darf wie root auf dem Rechner, ist er bewusst klein und streng:

* **Nur Standardbibliothek.** Kein `subprocess`, kein `os.system`/`exec*`/`spawn*`, kein `eval`/`exec`/
  `compile`, kein `pickle`/`marshal`, kein Netz außer `AF_UNIX`. `tests/test_code_guard.py` prüft das über
  den Syntaxbaum für **jedes** Modul dieses Pakets, auch für später hinzukommende.
* **Keine Einstellungen aus der Umgebung** außer dem Namen des Compose-Dienstes
  (`NODVARD_DECK_UPDATER_SERVICE`). Das Repository `ghcr.io/nodvard/deck` ist eine Konstante
  (`policy.REPOSITORY`); Tests ändern es nur über Parameter, nie über die Umgebung.
* **Das Dashboard ist nicht vertrauenswürdig.** Es schreibt nur kleine Anforderungen (Aktion + Version) in
  den Kanal; alles andere entscheidet der Helfer aus seinem eigenen Zustand (`/state`) und der Engine.

Module:

* `policy` -- reine Funktionen: Image-Referenz und Tag, Versionen, Anforderung prüfen, Grenzen,
  Rückweg-Slot, die festen Codes des Protokolls.
* `channel` -- der Kanal (`/channel`): einrichten und prüfen, Anforderungen sicher lesen, Status atomar
  schreiben.
* `state` -- der eigene Zustand (`/state`): Sperre, `state.json`, `journal.json`, atomar mit fsync.
* `engine` -- die Engine-API über `AF_UNIX`: feste Allowlist der Endpunkte, API-Version, Grenzen.
* `target` -- eigene ID, Ziel finden, Vorprüfung (nur lesend).
* `clone` -- reine Funktionen: `create`-Body aus dem Inspect, Nachkontrolle.
* `flow` -- der Ablauf: Update und Rückweg mit Journal, Beobachten bis zum ersten `healthy`, Rückbau vor dem Commit,
  Wiederaufnahme nach einem Absturz (`Flow.recover`). Nur dieses Modul verändert etwas an der Engine.
* `__main__` -- Schleife alle 5 s: zuerst einen liegen gebliebenen Vorgang wieder aufnehmen, dann Vorprüfung,
  Anforderungen prüfen und mit `flow` ausführen, Status schreiben; dazu der Heartbeat (30 s) und `--selftest`.
"""

__version__ = "0.11.0"
"""Version des Helfers (`status.helper_version`): dieselbe wie das Dashboard-Release, mit dem er gebaut wird.
`scripts/release.py` hebt sie zusammen mit `version.py` an, und `backend/tests/test_release_script.py` sorgt dafuer,
dass beide nie auseinanderlaufen. Von Hand nie aendern."""
