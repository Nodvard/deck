"""Sicherungen des Datenordners (Format, Verschluesselung, Online-Kopie, Ablage).

Aufbau einer Sicherungsdatei (`nodvard-deck-sicherung-YYYYMMDD-HHMMSS.ndbak`):

    Zeile 1  NODVARD-DECK-BACKUP/1
    Zeile 2  JSON-Kopf (Klartext, nicht geheim, hoechstens 4 KiB)
    Rest     age-Datei (https://age-encryption.org/v1), Inhalt ein tar.gz

Wer die Datei ohne Nodvard Deck oeffnen will: die ersten zwei Zeilen abschneiden
(`tail -n +3 datei.ndbak > datei.age`) und mit `age -d` entschluesseln -- mit dem
Einmal-Passwort (Modus "passwort") oder dem Wiederherstellungsschluessel (Modus
"schluessel", `age -d -i schluessel.txt`).

Module:
    crypto    age ueber pyrage, Argon2id -> X25519, eigenes age-scrypt (Streaming), Deckel
    format    Kopf, Manifest, Namens- und Pfadregeln (reine Funktionen)
    snapshot  Online-Kopie der Datenbank, tar-Strom mit Pruefsummen, Lesen und Pruefen
    store     Zielordner (Allowlist), Ablage, Pruefsummen-Datei, Rotation
    tickets   Einmal-Tickets fuer den Download
"""
