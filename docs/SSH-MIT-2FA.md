# Server mit Zwei-Faktor-Authentifizierung (2FA) anbinden

> English version: [en/SSH-WITH-2FA.md](en/SSH-WITH-2FA.md)

Nodvard Deck greift auf Server automatisiert im Hintergrund zu: für Auslastung, Dienste, Container und Updates. Verlangt ein Server bei der SSH-Anmeldung einen zweiten Faktor (z. B. Einmalcode per App oder Push-Freigabe), kann das Dashboard diesen interaktiv nicht eingeben. Die Verbindung scheitert dann beim Verbindungstest oder bricht nach einer Zeitüberschreitung ab.

Hier erfährst du, wie du Server mit 2FA anbindest, ohne den Schutz für deine persönlichen Anmeldungen aufzugeben.

## Die Grundregel: Eigener Nutzer mit Schlüssel und 2FA-Ausnahme

Schalte 2FA auf dem Server niemals komplett ab. Richte stattdessen eine gezielte Ausnahme für den automatisierten Zugang von Nodvard Deck ein.

1. **Eigener Nutzer:** Verwende für Deck immer den vorgeschlagenen Benutzer `nodvard` statt deines persönlichen Nutzers oder `root`.
2. **Nur SSH-Schlüssel, kein Passwort:** Der Einrichtungsbefehl von Deck richtet den Nutzer ohne Passwort ein. Die Anmeldung klappt nur mit dem im Tresor liegenden SSH-Schlüssel.
3. **Ausnahme nur für diesen Nutzer:** Die 2FA-Prüfung wird nur für den Nutzer `nodvard` übersprungen. Für alle anderen Konten bleibt der zweite Faktor aktiv.
4. **Rechte beschränken:** Belasse es bei der schlanken Variante ohne Root-Rechte oder verwende die Hilfsskripte mit Prüfsumme von Deck statt einer vollen sudo-Regel.

## 1. OpenSSH (sshd_config)

Erzwingt OpenSSH den zweiten Faktor über die Direktive `AuthenticationMethods` (z. B. Schlüssel plus Tastatureingabe für TOTP), kannst du für den Deck-Nutzer einen Match-Block anlegen.

Erstelle auf dem Zielserver die Datei `/etc/ssh/sshd_config.d/99-nodvard.conf` (oder trage den Block ganz am Ende von `/etc/ssh/sshd_config` ein):

```sshd
Match User nodvard
    AuthenticationMethods publickey
```

Konfiguration prüfen und SSH-Dienst neu laden:

```bash
sudo sshd -t
sudo systemctl reload ssh   # auf manchen Systemen: sudo systemctl reload sshd
```

Alle anderen Nutzer müssen weiterhin Schlüssel und Einmalcode eingeben. Der Nutzer `nodvard` meldet sich direkt mit seinem Schlüssel an.

## 2. Google Authenticator (PAM)

Wird der zweite Faktor über das PAM-Modul `pam_google_authenticator.so` verlangt, kannst du den Nutzer `nodvard` vor dem Modulaufruf überspringen.

Wichtig: Das Überspringen im PAM allein reicht nicht. Deck schickt bei Schlüsselzugängen nur den Schlüssel und beantwortet keine Tastatur-Abfrage. Der Match-Block aus Abschnitt 1 mit `AuthenticationMethods publickey` für den Nutzer `nodvard` muss zusätzlich gesetzt sein; der PAM-Schritt verhindert nur, dass das Modul trotzdem einen Code verlangt.

Öffne auf dem Zielserver die Datei `/etc/pam.d/sshd`:

```pam
# 2FA fuer den Nutzer nodvard ueberspringen
auth [success=1 default=ignore] pam_succeed_if.so user = nodvard

# Bestehende Google-Authenticator-Zeile
auth required pam_google_authenticator.so
```

Die Anweisung bewirkt: Lautet der Benutzername `nodvard`, wird genau ein nachfolgendes Modul (`pam_google_authenticator.so`) übersprungen. Für alle anderen Benutzer wird der Code weiterhin verlangt.

## 3. Duo Security (pam_duo oder login_duo)

Bei Duo Security gibt es zwei typische Integrationswege:

### Variante A: PAM-Modul (pam_duo)

Wird Duo über PAM geladen, trage in `/etc/pam.d/sshd` vor der Duo-Zeile eine Bedingung ein:

```pam
auth [success=1 default=ignore] pam_succeed_if.so user = nodvard
auth required pam_duo.so
```

Alternativ kannst du in `/etc/duo/pam_duo.conf` eine Gruppe für 2FA festlegen (z. B. `group = duo-users`) und den Nutzer `nodvard` nicht in diese Gruppe aufnehmen.

### Variante B: login_duo über sshd_config

Wird Duo über `ForceCommand` aufgerufen, hebst du diesen Befehl für `nodvard` am Ende von `/etc/ssh/sshd_config` auf:

```sshd
Match User nodvard
    ForceCommand none
```

Prüfe die Konfiguration mit `sudo sshd -t` und lade den Dienst neu.

## 4. Keycloak oder Authentik (SSO per PAM)

Auch hier gilt: Der Match-Block aus Abschnitt 1 bleibt Pflicht, die PAM-Ausnahme kommt dazu.

Nutzt der Server Keycloak oder Authentik für SSH-Logins (z. B. über OIDC- oder LDAP-Module mit MFA-Zwang), scheitern nicht-interaktive Zugänge.

1. **Lokales Konto anlegen:** Lege den Nutzer `nodvard` als lokales Systemkonto in `/etc/passwd` an, falls nicht bereits geschehen.
2. **Kein Passwort setzen:** Sperre das Passwort mit `sudo passwd -l nodvard`.
3. **SSO-Modul in PAM für nodvard überspringen:** In `/etc/pam.d/sshd` vor dem entsprechenden Modul (z. B. `pam_oidc.so`):

```pam
auth [success=1 default=ignore] pam_succeed_if.so user = nodvard
```

Lokale Schlüsselanmeldungen für `nodvard` laufen durch, während interaktive Logins über den Identity Provider abgesichert bleiben.

## 5. Tailscale SSH

Verwendest du Tailscale SSH, wird der SSH-Zugriff nicht über klassische Port-ACLs geregelt, sondern im Abschnitt `ssh` deiner Tailscale-Richtlinie (Access Control Policy). Dort kann eine interaktive Freigabe (`"action": "check"`) aktiv sein, die regelmäßige Bestätigungen im Browser verlangt.

Damit automatisierte Hintergrundverbindungen von Nodvard Deck ohne interaktive Browser-Prüfung möglich sind, ergänze im `ssh`-Abschnitt deiner Richtlinie vor den allgemeinen Prüfregeln eine gezielte Freigabe für das Dashboard mit `"action": "accept"`, zum Beispiel:

```json
"ssh": [
  {
    "action": "accept",
    "src": ["tag:nodvard-deck"],
    "dst": ["tag:server"],
    "users": ["nodvard"]
  }
]
```

Die Aktion `"accept"` erlaubt die direkte Verbindung ohne Browser-Check, während persönliche Zugänge von Administratoren weiter über `"action": "check"` geschützt bleiben können. Achte darauf, dass im `dst`-Feld kein Port angegeben wird (die Port-Regelung betrifft nur Netzwerk-ACLs).

Weitere Hinweise zur Netzwerkanbindung über Tailscale findest du in [docs/13-TAILSCALE.md](13-TAILSCALE.md) sowie in der [Tailscale-Dokumentation zu Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh).

## Was du nicht tun solltest

- **2FA niemals global abschalten:** Deaktiviere 2FA nicht für den gesamten Server oder alle Nutzer, nur um Deck anzubinden.
- **Kein Passwort für nodvard vergeben:** Vergib für das Deck-Konto kein Passwort.
- **Keine 2FA-Ausnahme für root:** Richte eine Ausnahme niemals für das Konto `root` ein. Nutze immer einen separaten Nutzer.
- **Optionen in authorized_keys nicht entfernen:** Die vom Einrichtungsbefehl eingetragenen Einschränkungen (`restrict,pty`) schützen den Schlüssel zusätzlich.
- **Keine unnötigen Root-Rechte:** Nutze die Hilfsskripte von Deck oder die schlanke Variante ohne Root-Rechte.

## Verbindung prüfen

Nachdem du die Ausnahme eingerichtet hast:

1. Öffne im Dashboard **Einstellungen → Server & Zugänge**.
2. Wähle den Server aus und klicke auf **Verbindung prüfen**.
3. Bestätige bei neuen Servern den angezeigten Server-Fingerabdruck.
4. Bei Erfolg leuchtet der Zugang grün und das Terminal sowie alle Module können den Server erreichen.
