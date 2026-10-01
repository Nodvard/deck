# Noch nicht veröffentlichte Änderungen

Jeder PR legt hier **eine** Datei `<branch-name-mit-bindestrichen>.toml` an (so gibt es nie Konflikte).
Inhalt sind 1 bis 3 `[[entries]]`-Blöcke, jeder mit `kind` (`neu`, `verbessert`, `behoben` oder `sicherheit`),
`text` (ein verständlicher deutscher Satz für Anwender, ohne Fachwörter und Dateinamen) und optional `prs = [45]`:

    [[entries]]
    kind = "behoben"
    text = "Die Meldungen-Seite lädt nach dem Löschen wieder richtig."

Reine Test-, CI- und Doku-Änderungen brauchen keinen Eintrag. Bei einem Release fasst
`python scripts/release.py <version>` alle Dateien hier zu `../versions/<version>.toml` zusammen und löscht sie.
