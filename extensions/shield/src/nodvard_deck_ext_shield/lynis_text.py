"""Lynis auf Deutsch: zu den haeufigsten Hinweisen des Haertungs-Audits eine kurze deutsche Ueberschrift, "Was heisst
das?" und "Was kannst du tun?" -- fest hinterlegt, ohne KI.

Lynis schreibt seine Funde englisch und knapp ("AUTH-9286: Configure maximum password age"). Viele davon sind fuer ein
Heimnetz bewusst nicht noetig; das sagen die Texte offen, statt jeden Hinweis wie einen Fehler aussehen zu lassen. Wo eine
Aenderung etwas kaputt machen kann, das Nodvard Deck selbst braucht (SSH-Anmeldung, Kernel-Werte fuer Docker und
Proxmox), steht die Warnung im Text.

Schluessel ist die Kennung des Lynis-Tests (`SSH-7408`); alle Saetze stehen in `LYNIS_TEXTS` und nur dort -- eine
spaetere englische Oberflaeche uebersetzt nur diese Sammlung. Unbekannte Kennungen bleiben einfach englisch.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_UNNEEDED = "Für ein Heimnetz meist nicht nötig – du kannst es bewusst so lassen."

LYNIS_TEXTS: dict[str, dict[str, str]] = {
    "SSH-7408": {
        "title": "SSH-Einstellung härten",
        "what": "Eine Einstellung des SSH-Dienstes ist lockerer als empfohlen. In Klammern steht, welche, und der empfohlene Wert.",
        "todo": "Den Wert in /etc/ssh/sshd_config ändern und SSH neu laden. Vorsicht: Nodvard Deck meldet sich selbst per SSH "
                "an – bei PermitRootLogin und Port erst nachsehen, mit welchem Benutzer und Port. MaxSessions nicht senken "
                "(Standard 10), sonst scheitern gleichzeitige Aktionen und das Terminal. AllowTcpForwarding no bricht eigene "
                "SSH-Tunnel.",
    },
    "AUTH-9230": {
        "title": "Passwörter mit mehr Rechenrunden speichern",
        "what": "Gespeicherte Passwörter sind mit wenigen Rechenrunden geschützt. Mehr Runden machen das Erraten gestohlener Passwörter langsamer.",
        "todo": "In /etc/login.defs die Runden erhöhen (SHA_CRYPT_MIN_ROUNDS und SHA_CRYPT_MAX_ROUNDS). Das gilt erst für neu "
                "gesetzte Passwörter. Auf neueren Systemen (Debian 12, Ubuntu 22.04 und neuer, Proxmox 8) nutzt die Anmeldung "
                "yescrypt, dort wirken diese Werte nicht – dann bewusst so lassen.",
    },
    "AUTH-9262": {
        "title": "Zu schwache Passwörter ablehnen",
        "what": "Beim Ändern eines Passworts prüft nichts, ob es zu schwach ist.",
        "todo": "Das Paket libpam-pwquality installieren. Meldest du dich nur mit SSH-Schlüsseln an, ist das kaum wichtig.",
    },
    "AUTH-9282": {
        "title": "Ablaufdatum für Konten",
        "what": "Konten mit Passwort laufen nie ab.",
        "todo": f"{_UNNEEDED} Sonst mit `chage` ein Ablaufdatum setzen – aber nie für das Konto, mit dem sich Nodvard Deck anmeldet.",
    },
    "AUTH-9286": {
        "title": "Passwort-Alter festlegen",
        "what": "Für Passwörter ist kein Mindest- oder Höchstalter gesetzt (PASS_MIN_DAYS und PASS_MAX_DAYS in /etc/login.defs).",
        "todo": "Heute empfiehlt man eher ein starkes Passwort ohne Zwangswechsel. " + _UNNEEDED,
    },
    "AUTH-9308": {
        "title": "Kein Passwort für den Rettungsmodus",
        "what": "Wer direkt am Gerät sitzt, kommt beim Start über den Rettungsmodus ohne Passwort an root.",
        "todo": "Nur wichtig, wenn Fremde an den Rechner kommen. Sonst bewusst so lassen.",
    },
    "AUTH-9328": {
        "title": "Strengere Rechte für neue Dateien",
        "what": "Neue Dateien sind standardmäßig für alle Benutzer lesbar (umask 022).",
        "todo": "In /etc/login.defs `UMASK 027` setzen. Gilt nur für neue Dateien; bei Diensten, die Dateien teilen, vorher ausprobieren.",
    },
    "BOOT-5122": {
        "title": "Passwort für den Bootloader",
        "what": "Wer am Gerät sitzt, kann beim Start über GRUB Einstellungen ändern und so root werden.",
        "todo": "Nur wichtig, wenn Fremde an den Rechner kommen. Sonst bewusst so lassen.",
    },
    "BOOT-5264": {
        "title": "Systemdienste abschotten",
        "what": "Viele Dienste nutzen die Schutzfunktionen von systemd nicht (eigene Rechte, abgeschotteter Speicher).",
        "todo": "Mit `systemd-analyze security` ansehen. Das ist Feinarbeit je Dienst und für ein Heimnetz kein Muss.",
    },
    "KRNL-5820": {
        "title": "Speicherabbilder bei Abstürzen abschalten",
        "what": "Stürzt ein Programm ab, kann es seinen Speicher – mit Passwörtern darin – in eine Datei schreiben.",
        "todo": "In /etc/security/limits.conf die Zeile `* hard core 0` eintragen; mit systemd zusätzlich `Storage=none` in "
                "/etc/systemd/coredump.conf.",
    },
    "KRNL-6000": {
        "title": "Kernel-Werte (sysctl) weichen ab",
        "what": "Einige Werte des Kernels sind lockerer, als Lynis empfiehlt, meist für Netzwerkpakete.",
        "todo": "Welche es sind, steht auf dem Server in /var/log/lynis.log. Jeden Wert einzeln prüfen, bevor du ihn in "
                "/etc/sysctl.d/ setzt. Nicht ändern: kernel.modules_disabled (sperrt das Nachladen von Treibern, Docker und "
                "Proxmox brechen), net.ipv4.conf.all.forwarding (braucht Docker und Proxmox) und rp_filter bei mehreren "
                "Netzkarten, Bridges oder VPN.",
    },
    "FILE-6310": {
        "title": "Eigene Partition für einen Ordner",
        "what": "Läuft dieser Ordner (z. B. /home, /tmp oder /var) voll, ist auch das System betroffen.",
        "todo": "Lässt sich nur bei einer Neuinstallation sinnvoll ändern. Sonst bewusst so lassen und den Platz im Blick behalten.",
    },
    "USB-1000": {
        "title": "USB-Speicher abschalten",
        "what": "Angesteckte USB-Sticks und -Platten werden erkannt.",
        "todo": "Nur sinnvoll, wenn Fremde an den Rechner kommen. Hängen Sicherungsplatten per USB dran, nicht abschalten.",
    },
    "STRG-1846": {
        "title": "Firewire-Speicher abschalten",
        "what": "Der Treiber für Firewire-Speicher lässt sich laden.",
        "todo": _UNNEEDED,
    },
    "NAME-4028": {
        "title": "DNS-Domänenname fehlt",
        "what": "Für den Server ist kein Domänenname eingetragen.",
        "todo": _UNNEEDED,
    },
    "NETW-2705": {
        "title": "Weniger als zwei DNS-Server",
        "what": "Lynis hat keine zwei antwortenden DNS-Server gefunden. Fällt der eine aus, lassen sich keine Namen mehr auflösen.",
        "todo": "Einen zweiten DNS-Server eintragen. Läuft alles bewusst über einen Pi-hole, so lassen.",
    },
    "NETW-3200": {
        "title": "Selten gebrauchte Netzwerkprotokolle",
        "what": "Die Protokolle dccp, sctp, rds oder tipc könnten geladen werden; fast nie braucht sie jemand.",
        "todo": "Abschalten mit einer Datei in /etc/modprobe.d/, z. B. `install dccp /bin/true`. " + _UNNEEDED,
    },
    "FIRE-4512": {
        "title": "Firewall ohne Regeln",
        "what": "Die Firewall ist geladen, hat aber keine Regeln.",
        "todo": "Steht der Server hinter dem Router im Heimnetz, ist das oft gewollt. Sonst eine Firewall einrichten (z. B. ufw), "
                "aber vorher SSH (Port 22) freigeben – sonst sperrst du dich und Nodvard Deck aus. Auf Proxmox die Firewall von "
                "Proxmox nutzen; auf Docker-Servern gilt ufw nicht für veröffentlichte Container-Ports.",
    },
    "FIRE-4513": {
        "title": "Ungenutzte Firewall-Regeln",
        "what": "Einige Firewall-Regeln wurden noch nie getroffen.",
        "todo": "Zur Kenntnis nehmen. Mit Docker oder Proxmox ist das normal.",
    },
    "PKGS-7346": {
        "title": "Reste entfernter Pakete",
        "what": "Von entfernten Paketen liegen noch Einstellungsdateien herum.",
        "todo": "`dpkg -l | grep '^rc'` zeigt sie, `apt purge <paket>` räumt sie weg. Harmlos, wenn du es lässt.",
    },
    "PKGS-7370": {
        "title": "debsums installieren oder regelmäßig laufen lassen",
        "what": "Mit debsums lässt sich prüfen, ob Programmdateien verändert wurden; Lynis rät, es zu installieren oder regelmäßig laufen zu lassen.",
        "todo": "Das prüft der Datei-Wächter von Nodvard Shield schon selbst. Optional: `apt install debsums`.",
    },
    "PKGS-7392": {
        "title": "Pakete mit Sicherheitslücken",
        "what": "Für installierte Pakete gibt es Sicherheitsupdates (oder Lynis konnte das nicht sauber prüfen).",
        "todo": "In der Update-Zentrale von Nodvard Shield die Sicherheitsupdates einspielen.",
    },
    "PKGS-7394": {
        "title": "apt-show-versions installieren",
        "what": "Ein Hilfsprogramm, mit dem Lynis offene Updates genauer erkennt.",
        "todo": "Optional: `apt install apt-show-versions`. Offene Updates zeigt dir auch die Update-Zentrale.",
    },
    "PKGS-7420": {
        "title": "Updates automatisch einspielen",
        "what": "Sicherheitsupdates werden nicht von selbst eingespielt.",
        "todo": "In Nodvard Shield die automatischen Updates einschalten oder `unattended-upgrades` einrichten. Auf Proxmox kein "
                "unattended-upgrades, dort die Update-Zentrale nutzen.",
    },
    "LOGG-2154": {
        "title": "Protokolle zusätzlich woanders speichern",
        "what": "Die Protokolle liegen nur auf diesem Server. Ein Angreifer könnte sie löschen.",
        "todo": f"{_UNNEEDED} Sonst rsyslog an einen Protokoll-Server schicken lassen.",
    },
    "LOGG-2190": {
        "title": "Gelöschte Dateien noch in Benutzung",
        "what": "Programme halten Dateien offen, die schon gelöscht sind (oft nach Updates).",
        "todo": "Nach Sicherheitsupdates läuft so noch der alte, verwundbare Code. Den betroffenen Dienst oder den Server neu "
                "starten.",
    },
    "ACCT-9622": {
        "title": "Mitschreiben, welche Programme liefen",
        "what": "Es wird nicht festgehalten, welche Programme gelaufen sind.",
        "todo": f"{_UNNEEDED} Sonst: `apt install acct`.",
    },
    "ACCT-9626": {
        "title": "Auslastung dauerhaft mitschreiben",
        "what": "Die Auslastung des Servers wird nicht dauerhaft festgehalten (sysstat).",
        "todo": f"Die Auslastung zeigt dir auch Nodvard Deck. {_UNNEEDED} Sonst sysstat installieren und in "
                "/etc/default/sysstat `ENABLED=\"true\"` setzen.",
    },
    "ACCT-9628": {
        "title": "Sicherheitsvorgänge mitschreiben (auditd)",
        "what": "Dateizugriffe und Rechtewechsel werden nicht mitgeschrieben.",
        "todo": "Optional: `apt install auditd`. Ohne eigene Regeln bringt es wenig.",
    },
    "FINT-4350": {
        "title": "Wichtige Dateien auf Änderungen prüfen",
        "what": "Kein Programm wie AIDE prüft, ob sich wichtige Systemdateien ändern.",
        "todo": "Das übernimmt der Datei-Wächter von Nodvard Shield für die wichtigsten Dateien. AIDE ist optional.",
    },
    "HRDN-7222": {
        "title": "Compiler nur für root",
        "what": "Jeder Benutzer darf Programme übersetzen (gcc, cc).",
        "todo": "Ohne fremde Benutzer auf dem Server unwichtig. Sonst die Rechte auf root beschränken oder den Compiler entfernen.",
    },
    "HRDN-7230": {
        "title": "Virenscanner fehlt",
        "what": "Lynis findet keinen Scanner für Schadsoftware (z. B. ClamAV oder rkhunter).",
        "todo": "Den Virenschutz von Nodvard Shield auf diesem Server einrichten (ClamAV).",
    },
    "BANN-7126": {
        "title": "Rechtlicher Hinweis beim Anmelden",
        "what": "Beim Anmelden am Gerät steht kein Hinweis, dass unbefugter Zugriff verboten ist (/etc/issue).",
        "todo": _UNNEEDED,
    },
    "BANN-7130": {
        "title": "Rechtlicher Hinweis bei der Netzwerk-Anmeldung",
        "what": "Bei der Anmeldung übers Netz steht kein Hinweis, dass unbefugter Zugriff verboten ist (/etc/issue.net).",
        "todo": _UNNEEDED,
    },
    "MAIL-8818": {
        "title": "Mail-Server verrät sein Programm",
        "what": "Der Mail-Server nennt beim Verbinden, welches Programm er ist (z. B. Postfix).",
        "todo": "In /etc/postfix/main.cf `smtpd_banner = $myhostname ESMTP` setzen.",
    },
    "DEB-0280": {
        "title": "Eigener Temp-Ordner je Benutzer",
        "what": "Alle Benutzer teilen sich denselben Ordner für temporäre Dateien.",
        "todo": f"Optional: `apt install libpam-tmpdir`. {_UNNEEDED}",
    },
    "DEB-0810": {
        "title": "Bekannte Fehler vor Updates anzeigen",
        "what": "apt-listbugs zeigt vor einem Update bekannte Fehler der neuen Pakete (fehlt oder ist nicht eingeschaltet).",
        "todo": "Optional. Es fragt bei Updates nach und hält automatische Updates eher auf.",
    },
    "DEB-0811": {
        "title": "Änderungen vor Updates anzeigen",
        "what": "apt-listchanges zeigt vor einem Update, was sich ändert (fehlt oder ist nicht eingeschaltet).",
        "todo": f"Optional: `apt install apt-listchanges`. {_UNNEEDED}",
    },
    "DEB-0831": {
        "title": "needrestart installieren",
        "what": "Nach Updates prüft nichts, welche Dienste neu starten müssen.",
        "todo": "`apt install needrestart`. Achtung: bei Updates ohne Rückfrage startet needrestart Dienste womöglich selbst "
                "neu; wer das nicht will, setzt in /etc/needrestart/needrestart.conf `$nrconf{restart} = 'l';`. Den "
                "Neustart-Bedarf zeigt auch die Update-Zentrale.",
    },
    "DEB-0880": {
        "title": "fail2ban fehlt oder nutzt nur die Grundeinstellung",
        "what": "Entweder sperrt nichts Adressen nach vielen falschen Anmeldeversuchen, oder fail2ban läuft nur mit der "
                "mitgelieferten jail.conf (Lynis rät zu einer eigenen jail.local).",
        "todo": "Fehlt fail2ban: im Einbruchschutz von Nodvard Shield installieren lassen. Sonst /etc/fail2ban/jail.conf nach "
                "jail.local kopieren und dort anpassen.",
    },
    "TOOL-5002": {
        "title": "Werkzeug zur Verwaltung vieler Server",
        "what": "Lynis findet kein Werkzeug wie Ansible oder Puppet.",
        "todo": _UNNEEDED,
    },
    "LYNIS": {
        "title": "Hinweis von Lynis selbst",
        "what": "Lynis meldet etwas über sich oder den Rechner: meist, dass diese Lynis-Version veraltet ist (neuere Prüfungen "
                "fehlen), seltener, dass der Rechnername nicht vollständig eingerichtet ist.",
        "todo": "Ist Lynis veraltet: aktualisieren (Paket aus der Quelle von CISOfy oder `apt upgrade`). Beim Rechnernamen "
                "/etc/hostname und /etc/hosts prüfen.",
    },
}

LYNIS_WEIGHT: dict[str, int] = {
    # 3 = wichtig: bekannte Luecken in installierten Paketen.
    "PKGS-7392": 3,
    # 2 = sinnvoll. SSH bewusst nicht hoeher: der Hinweis steht auf fast jedem Server, und eine falsche Aenderung kann
    # Nodvard Deck aussperren. Kernel-Werte (KRNL-6000) fehlen hier ganz: sie weichen auf jedem Docker- oder
    # Proxmox-Server ab und brauchen Einzelfall-Pruefung.
    "SSH-7408": 2, "PKGS-7420": 2, "DEB-0880": 2, "HRDN-7230": 2, "FIRE-4512": 2, "LOGG-2190": 2,
}
"""Wie viel ein Hinweis bringt, fuer die Liste "Deine wichtigsten Baustellen" (nur Gewicht 2 und 3). Alles andere ist 1:
fuer ein Heimnetz meist nicht noetig. Grobe Einordnung, kein Punktesystem."""

_ID_RE = re.compile(r"^\s*([A-Z]{2,5}-\d{4}|LYNIS)\b")


def lynis_id(entry: str) -> str | None:
    """Die Kennung eines gespeicherten Fundes ("AUTH-9286: Configure ...") oder None."""
    match = _ID_RE.match(entry or "")
    return match.group(1) if match else None


def lynis_help(entries: Iterable[str]) -> dict[str, dict[str, str | int]]:
    """Die deutschen Texte zu allen bekannten Kennungen in `entries`: `{Kennung: {"title", "what", "todo", "weight"}}`
    (`weight` aus `LYNIS_WEIGHT`, sonst 1)."""
    found: dict[str, dict[str, str | int]] = {}
    for entry in entries:
        key = lynis_id(entry)
        if key and key in LYNIS_TEXTS:
            found[key] = {**LYNIS_TEXTS[key], "weight": LYNIS_WEIGHT.get(key, 1)}
    return found
