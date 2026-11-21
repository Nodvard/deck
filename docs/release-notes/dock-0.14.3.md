# Nodvard Dock 0.14.3

2026-11-21

Sicherheit und Korrekturen

## Behoben

- Wenn Docker-Ordner im Dateimanager nicht gelesen werden können, steht der Grund jetzt im Protokoll.
- Sind zu viele Tabs offen, versucht die Oberfläche nicht mehr ständig neu zu verbinden, sondern zeigt einen Hinweis mit „Erneut verbinden“.
- Docker: Image-Updates laufen immer im Projektordner des Stacks. Damit werden ${PWD} und relative Pfade auch ohne gespeicherte Variablen richtig ausgewertet.
- Docker schützt den eigenen Container auch bei einem eigenen Hostnamen, erkennt Portainer-Ordner genauer und behält Prüfergebnisse bei kurzen Lesefehlern. Abgestürzte Stack-Container werden als Problem gezählt, während der Dateimanager bei einem nicht erreichbaren Docker-Dienst leer bleibt.
- Fehlerhafte Meldungsarten einer Erweiterung blockieren die Einstellungen nicht mehr.
- Beim Speichern von Moduleinstellungen bleiben gleichzeitige Änderungen an verborgenen Werten des Moduls erhalten.

## Sicherheit

- Im Protokoll sehen Konten ohne das Recht, Befehle auf Servern auszuführen, keine Fehlermeldungen und andere Texte von den Servern mehr, weder in der Begründung noch in den Einzelheiten. Kennungen, Namen und Zahlen bleiben sichtbar.
- Ein Download-Link für eine Sicherung gilt nur noch, solange du an diesem Gerät angemeldet bist und dein Passwort gleich bleibt. Nach dem Abmelden oder einem neuen Passwort holst du den Link einfach neu.
- Wer andere Konten nicht verwalten darf, sieht deren Namen und Rollen nicht mehr im Protokoll und bekommt keine Meldungen mehr zu fremden Konten. Ereignisse und Warnungen zum eigenen Konto bleiben sichtbar, ohne den Namen dessen, der den Zugang geändert hat.
- Taucht eine schon erneuerte Anmeldung später noch einmal auf, beendet Deck diese Anmeldung samt allen Erneuerungen und vermerkt das im Protokoll. Eine kopierte Anmeldung endet so spätestens, wenn das eigene Gerät sich wieder meldet, auch nach einem Passwortwechsel.
- Eine laufende automatische Sicherung kann nur noch der Owner abbrechen. Jedes Abbrechen eines Laufs und jeder abgelehnte Versuch steht jetzt im Protokoll.
- Wer bei einem Modul mit hinterlegten Zugangsdaten die Zertifikatsprüfung abschaltet, einen anderen Fingerabdruck einträgt oder den gemerkten entfernt, bestätigt das jetzt mit dem Passwort.
- Beim Hochladen auf einen Server folgt der Dateimanager Verknüpfungen und nutzt Ordner im Zielpfad nur noch, wenn sie root oder dem Konto gehören, mit dem Deck auf dem Server angemeldet ist, und kein anderes Konto sie austauschen kann. Als root angemeldet geht ein Upload in den Ordner eines anderen Benutzers deshalb nicht mehr. Die Meldung erklärt, wie es weitergeht: mit dem Konto des Besitzers verbinden oder in einen Ordner von root hochladen und dort verschieben. Auf Windows-Servern prüft Deck dabei nur die gemeldeten Schreibrechte, nicht den Besitzer der Ordner.
- Live-Aktualisierungen der Module, etwa von Nodvard Shield, bekommt nur noch, wer die zugehörige Kachel sehen darf. Kontomeldungen zu anderen Konten erscheinen live nur bei Leuten, die andere Konten sehen dürfen, und Ereignisse ohne feste Zuordnung werden gar nicht mehr live verteilt. Wer andere Konten nicht sehen darf, liest auch live in Meldungen zum eigenen Konto nicht den Namen des Kontos, das die Änderung gemacht hat.
- Live-Meldungen kommen sofort an, auch wenn ein anderer Browser gerade nicht mitliest. Ein Konto kann höchstens 20 Live-Verbindungen gleichzeitig offen halten.
- Werden einem Konto Rechte entzogen, bekommt es auch Live-Meldungen, die gerade unterwegs sind, nur noch in der Fassung, die zu den neuen Rechten passt.
- Wird ein Passwort geändert oder zurückgesetzt, lässt sich eine gerade begonnene Anmeldung mit dem alten Passwort nicht mehr mit dem Zwei-Faktor-Code abschließen. Man meldet sich dann einfach mit dem neuen Passwort an.
- Module können über andere Module keine Aktionen, Befehle, Terminals oder Konsolen mehr direkt starten und dabei Freigaben und Protokollierung umgehen.
