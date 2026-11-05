# Without the Interface: Setting Up Servers through the API

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.
>
> Deutsche Fassung: [../17-OHNE-OBERFLAECHE.md](../17-OHNE-OBERFLAECHE.md)

Normally you use the "Servers & access" page
([first setup, section 3](FIRST-SETUP.md#3-connect-your-first-server)). Only if the interface is not reachable (e.g. during the first setup
over SSH on the machine running Nodvard Deck) can everything also be done through the Nodvard Deck API with this small
helper script. There is no setup command and no connection check – you generate the key yourself, and you add the public part by hand to
`~/.ssh/authorized_keys`.

## Generate a key

Once, on the machine running Nodvard Deck:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/nodvard_ed25519 -C nodvard
cat ~/.ssh/nodvard_ed25519.pub        # this one line goes onto the servers
```

Without a passphrase (`-N ""`), because Nodvard Deck does not ask for a passphrase when connecting. Nodvard Deck gets an
encrypted copy of the private key of its own; you only need the file on this machine for adding it and for testing.

## Save the helper script

Once, on the same machine: paste the whole block into the shell; it creates `~/nodvard-server.py`. (The script's prompts
and messages are in German, like the interface.)

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

## Use it

```bash
python3 ~/nodvard-server.py
```

The script talks to `http://localhost:8080`. If Nodvard Deck runs on another port or machine, pass the address:
`NODVARD_DECK_URL=http://192.168.1.10:8081 python3 ~/nodvard-server.py`.

Log in with a Nodvard Deck account that may manage servers (owner or `admin`). The script shows all servers with their
**ID** (which is also in the browser address of a server page: `/hosts/<ID>`). With 1 you create a server, with 2 you
change the address, with 3 you store an SSH login (key `~/.ssh/nodvard_ed25519` or password), with 4 you show the
logins, and with 5 you forget a remembered server key (after the server has been reinstalled). If a server has a
stored SSH password, change its address in the interface: the new address deletes the password, so Nodvard Deck asks
for your own password there. In that case the script stops with "HTTP 403". Then store the SSH password again
with 3.
The script does not confirm fingerprints. After forgetting (5), Nodvard Deck only connects again once you have pressed "Check connection" in
the interface and confirmed the new fingerprint. The same applies to new servers as long as "Only remember new server
keys after my confirmation" is on (always, unless you have turned it off).

## Add the key on the server

The key on the server is then added by hand, e.g. for a user of its own:

```bash
sudo adduser --disabled-password --gecos "Nodvard Deck" nodvard
sudo install -d -m 700 -o nodvard -g nodvard /home/nodvard/.ssh
echo 'PASTE THE LINE FROM nodvard_ed25519.pub HERE' | sudo tee /home/nodvard/.ssh/authorized_keys
sudo chown nodvard:nodvard /home/nodvard/.ssh/authorized_keys
sudo chmod 600 /home/nodvard/.ssh/authorized_keys
sudo usermod -aG docker nodvard        # only where Docker runs
```

## Root rights without a password

For root rights without a password (see [first setup, appendix F](FIRST-SETUP.md#f-root-rights-without-a-password-and-the-docker-group) – practically
root!):

```bash
echo 'nodvard ALL=(root) NOPASSWD: ALL' > /tmp/nodvard-sudo
sudo visudo -cf /tmp/nodvard-sudo && sudo install -m 0440 -o root -g root /tmp/nodvard-sudo /etc/sudoers.d/nodvard-nodvard
rm /tmp/nodvard-sudo
sudo visudo -c
```

If the last `visudo -c` reports an error, delete the file again **immediately**
(`sudo rm /etc/sudoers.d/nodvard-nodvard`) – a broken sudo file can disable sudo. The file name must not contain a
dot, otherwise sudo ignores the file. To test, from the machine running Nodvard Deck:

```bash
ssh -i ~/.ssh/nodvard_ed25519 nodvard@SERVER-IP 'sudo -n true && echo sudo-ok; docker ps >/dev/null && echo docker-ok'
```
