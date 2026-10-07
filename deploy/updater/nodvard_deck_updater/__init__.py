"""Update-Helfer fuer Nodvard Deck: ein eigenes kleines Programm neben dem Dashboard.

Der Helfer hat Zugriff auf den Socket des Docker-Dienstes und tauscht auf Wunsch des Dashboards dessen
Container gegen eine neuere offizielle Version (oder einmal zurueck auf den Vorgaenger). Weil er damit so viel
darf wie root auf dem Rechner, ist er bewusst klein und streng:

* **Nur Standardbibliothek.** Kein `subprocess`, kein `os.system`/`exec*`/`spawn*`, kein `eval`/`exec`/
  `compile`, kein `pickle`/`marshal`, kein Netz ausser `AF_UNIX`. `tests/test_code_guard.py` prueft das ueber
  den Syntaxbaum fuer **jedes** Modul dieses Pakets, auch fuer spaeter hinzukommende.
* **Keine Einstellungen aus der Umgebung** ausser dem Namen des Compose-Dienstes
  (`NODVARD_DECK_UPDATER_SERVICE`). Das Repository `ghcr.io/nodvard/deck` ist eine Konstante
  (`policy.REPOSITORY`); Tests aendern es nur ueber Parameter, nie ueber die Umgebung.
* **Das Dashboard ist nicht vertrauenswuerdig.** Es schreibt nur kleine Anforderungen (Aktion + Version) in
  den Kanal; alles andere entscheidet der Helfer aus seinem eigenen Zustand (`/state`) und der Engine.

Module:

* `policy` -- reine Funktionen: Image-Referenz und Tag, Versionen, Anforderung pruefen, Grenzen,
  Rueckweg-Slot, die festen Codes des Protokolls.
* `channel` -- der Kanal (`/channel`): einrichten und pruefen, Anforderungen sicher lesen, Status atomar
  schreiben.
* `state` -- der eigene Zustand (`/state`): Sperre, `state.json`, `journal.json`, atomar mit fsync.
* `engine` -- die Engine-API ueber `AF_UNIX`: feste Allowlist der Endpunkte, API-Version, Grenzen.
* `target` -- eigene ID, Ziel finden, Vorpruefung (nur lesend).
* `clone` -- reine Funktionen: `create`-Body aus dem Inspect, Nachkontrolle.
* `flow` -- der Ablauf: Update und Rueckweg mit Journal, Beobachten bis zum ersten `healthy`, Rueckbau vor dem Commit,
  Wiederaufnahme nach einem Absturz (`Flow.recover`). Nur dieses Modul veraendert etwas an der Engine.
* `__main__` -- Schleife alle 5 s: zuerst einen liegen gebliebenen Vorgang wieder aufnehmen, dann Vorpruefung,
  Anforderungen pruefen und mit `flow` ausfuehren, Status schreiben; dazu der Heartbeat (30 s) und `--selftest`.
"""

__version__ = "0.7.0"
"""Version des Helfers (`status.helper_version`): dieselbe wie das Dashboard-Release, mit dem er gebaut wird.
`scripts/release.py` hebt sie zusammen mit `version.py` an, und `backend/tests/test_release_script.py` sorgt dafuer,
dass beide nie auseinanderlaufen. Von Hand nie aendern."""
