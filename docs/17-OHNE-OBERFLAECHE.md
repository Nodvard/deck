# Ohne Oberfläche: Server über die API einrichten

> English version: [en/WITHOUT-INTERFACE.md](en/WITHOUT-INTERFACE.md)

Normalerweise nimmst du die Seite „Server & Zugänge“
([Erst-Einrichtung, Abschnitt 3](11-ERST-EINRICHTUNG.md#3-den-ersten-server-verbinden)). Nur wenn die Oberfläche nicht
erreichbar ist (z. B. beim ersten Einrichten per SSH auf dem Rechner mit Nodvard Deck), geht
alles auch über die Nodvard-Deck-API mit diesem kleinen Hilfsskript. Es gibt keinen Einrichtungsbefehl und keine
Verbindungsprüfung – den Schlüssel erzeugst du selbst, und den öffentlichen Teil trägst du von
Hand in `~/.ssh/authorized_keys` ein.

## Schlüssel erzeugen

Einmal, auf dem Rechner mit Nodvard Deck:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/nodvard_ed25519 -C nodvard
cat ~/.ssh/nodvard_ed25519.pub        # diese eine Zeile kommt auf die Server
```

Ohne Passphrase (`-N ""`), weil Nodvard Deck beim Verbinden keine Passphrase abfragt. Nodvard Deck
bekommt eine eigene, verschlüsselte Kopie des privaten Schlüssels; die Datei auf diesem Rechner
brauchst du nur noch zum Eintragen und Testen.

## Hilfsskript speichern

Einmal, auf demselben Rechner: den ganzen Block in die Shell kopieren, er legt `~/nodvard-server.py` an.

```bash
cat > ~/nodvard-server.py <<'EOF'
#!/usr/bin/env python3
"""Nodvard Deck: Server anlegen, Adresse korrigieren, SSH-Zugang hinterlegen (docs/17)."""
import getpass, json, os, urllib.error, urllib.request

API = os.environ.get("NODVARD_DECK_URL", "http://localhost:8080").rstrip("/") + "/api/v1"
token = None

class Fehler(Exception):
    pass

def call(method, path, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raise Fehler(f"HTTP {err.code}: {err.read().decode(errors='replace')[:300]}") from None

def ask(text, default=""):
    value = input(f"{text}{f' [{default}]' if default else ''}: ").strip()
    return value or default

try:
    status, data = call("POST", "/auth/login", {
        "username": ask("Nodvard-Deck-Benutzer").lower(),
        "password": getpass.getpass("Passwort: "),
        "client_type": "cli",
    })
    if status == 202:  # Zwei-Faktor-Anmeldung ist eingeschaltet
        status, data = call("POST", "/auth/mfa", {"mfa_token": data["mfa_token"], "code": ask("Code aus der App")})
except Fehler as exc:
    raise SystemExit(f"Anmeldung fehlgeschlagen: {exc}")
token = data["access_token"]

while True:
    try:
        _, hosts = call("GET", "/hosts")
    except Fehler as exc:
        raise SystemExit(f"{exc} -- nach 15 Minuten läuft die Anmeldung ab, dann Skript neu starten.")
    print()
    for h in sorted(hosts, key=lambda h: h["name"]):
        print(f"  {h['id']}  {h['name']:<26} {h['address']:<16} {h['os_family']}")
    print("\n1 = Server anlegen  2 = Adresse ändern  3 = SSH-Zugang hinterlegen  4 = SSH-Zugänge zeigen")
    print("5 = gemerkten SSH-Host-Schlüssel vergessen  q = Ende")
    choice = ask("Auswahl", "q")
    try:
        if choice == "1":
            name = ask("Kurzname, klein und ohne Leerzeichen (z. B. pi-host)")
            _, h = call("POST", "/hosts", {
                "name": name,
                "display_name": ask("Anzeigename", name),
                "address": ask("IP-Adresse"),
                "os_family": ask("Betriebssystem (linux/windows)", "linux"),
                "tags": [t.strip() for t in ask("Markierungen mit Komma (z. B. docker), leer = keine").split(",") if t.strip()],
            })
            print("Angelegt, ID:", h["id"])
        elif choice == "2":
            host_id = ask("ID des Servers")
            call("PATCH", f"/hosts/{host_id}", {"address": ask("Neue IP-Adresse")})
            print("Gespeichert.")
        elif choice == "3":
            host_id = ask("ID des Servers")
            user = ask("SSH-Benutzer", "nodvard")
            port = int(ask("SSH-Port", "22"))
            if ask("Anmeldung mit Schlüssel (s) oder Passwort (p)?", "s") == "p":
                kind, secret = "ssh_password", getpass.getpass("SSH-Passwort: ")
            else:
                with open(os.path.expanduser(ask("Privater Schlüssel", "~/.ssh/nodvard_ed25519"))) as f:
                    kind, secret = "ssh_key", f.read()
            call("POST", f"/hosts/{host_id}/credentials",
                 {"kind": kind, "username": user, "port": port, "secret_value": secret, "is_default": True})
            print("SSH-Zugang gespeichert und ab jetzt der Standard für diesen Server.")
        elif choice == "4":
            _, creds = call("GET", f"/hosts/{ask('ID des Servers')}/credentials")
            for c in creds:
                print(f"  {c['kind']:<13} {c['username']} Port {c['port']}{'  (Standard)' if c['is_default'] else ''}")
            if not creds:
                print("  Noch kein SSH-Zugang hinterlegt.")
        elif choice == "5":
            host_id = ask("ID des Servers")
            call("DELETE", f"/hosts/{host_id}/known-hosts/{ask('Schlüsseltyp aus der Fehlermeldung', 'ssh-ed25519')}")
            print("Vergessen. Jetzt in der Oberfläche „Verbindung prüfen“ drücken und den neuen Fingerabdruck bestätigen – vorher verbindet sich Nodvard Deck nicht mehr mit dem Server.")
        elif choice.lower() == "q":
            break
    except (Fehler, OSError, ValueError) as exc:
        print("Fehler:", exc)
EOF
```

## Benutzen

```bash
python3 ~/nodvard-server.py
```

Das Skript spricht mit `http://localhost:8080`. Läuft Nodvard Deck auf einem anderen Port oder Rechner, gib die Adresse
mit: `NODVARD_DECK_URL=http://192.168.1.10:8081 python3 ~/nodvard-server.py`.

Anmelden mit einem Nodvard-Deck-Konto, das Server verwalten darf (Inhaber oder `admin`). Das Skript
zeigt alle Server mit ihrer **ID** (die steht auch in der Browser-Adresse einer Server-Seite:
`/hosts/<ID>`). Mit 1 legst du einen Server an, mit 2 änderst du die Adresse, mit 3 hinterlegst du
einen SSH-Zugang (Schlüssel `~/.ssh/nodvard_ed25519` oder Passwort), mit 4 zeigst du die Zugänge,
mit 5 vergisst du einen gemerkten Server-Schlüssel (nach einer Neuinstallation des Servers).
Hat ein Server ein gespeichertes SSH-Passwort, änderst du seine Adresse in der Oberfläche: Die neue Adresse löscht das
Passwort, deshalb fragt Nodvard Deck dort nach deinem eigenen Passwort. Das Skript bricht in diesem Fall mit
„HTTP 403“ ab. Danach hinterlegst du das SSH-Passwort mit 3 neu.
Fingerabdrücke bestätigt das Skript nicht. Nach „Vergessen“ (5) verbindet sich Nodvard Deck erst wieder, wenn du in
der Oberfläche „Verbindung prüfen“ gedrückt und den neuen Fingerabdruck bestätigt hast. Dasselbe gilt für neue Server,
solange „Neue Server-Schlüssel erst nach meiner Bestätigung merken“ an ist (ohne eigene Einstellung immer).

## Schlüssel auf dem Server eintragen

Der Schlüssel auf dem Server wird dann von Hand eingetragen, z. B. für einen eigenen Benutzer:

```bash
sudo adduser --disabled-password --gecos "Nodvard Deck" nodvard
sudo install -d -m 700 -o nodvard -g nodvard /home/nodvard/.ssh
echo 'HIER DIE ZEILE AUS nodvard_ed25519.pub' | sudo tee /home/nodvard/.ssh/authorized_keys
sudo chown nodvard:nodvard /home/nodvard/.ssh/authorized_keys
sudo chmod 600 /home/nodvard/.ssh/authorized_keys
sudo usermod -aG docker nodvard        # nur wo Docker läuft
```

## Root-Rechte ohne Passwort

Für Root-Rechte ohne Passwort (siehe [Erst-Einrichtung, Anhang F](11-ERST-EINRICHTUNG.md#f-root-rechte-ohne-passwort-und-gruppe-docker) – praktisch root!):

```bash
echo 'nodvard ALL=(root) NOPASSWD: ALL' > /tmp/nodvard-sudo
sudo visudo -cf /tmp/nodvard-sudo && sudo install -m 0440 -o root -g root /tmp/nodvard-sudo /etc/sudoers.d/nodvard-nodvard
rm /tmp/nodvard-sudo
sudo visudo -c
```

Meldet das letzte `visudo -c` einen Fehler, die Datei **sofort** wieder löschen
(`sudo rm /etc/sudoers.d/nodvard-nodvard`) – eine kaputte sudo-Datei kann sudo lahmlegen. Der
Dateiname darf keinen Punkt enthalten, sonst ignoriert sudo die Datei. Prüfen, vom Rechner mit Nodvard Deck aus:

```bash
ssh -i ~/.ssh/nodvard_ed25519 nodvard@SERVER-IP 'sudo -n true && echo sudo-ok; docker ps >/dev/null && echo docker-ok'
```
