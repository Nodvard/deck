# Security Policy

*Deutsch: [unten](#deutsch).*

Nodvard Dock manages Docker on your servers over SSH and stores the SSH keys for them, so we take security reports
seriously.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report them privately through GitHub's private vulnerability reporting for this repository:
[Report a vulnerability](https://github.com/nodvard/dock/security/advisories/new) (tab **Security → Report a
vulnerability**), or by e-mail to **kontakt@nodvard.com**.

Please include:

- the version (the image tag you use, or the `version` field of `/api/v1/health`),
- what happens and what it allows an attacker to do,
- steps to reproduce or a proof of concept,
- if possible, a suggested fix.

You will get a first answer within **7 days**. We will keep you informed while we work on a fix and credit you in the
release notes if you wish.

## Supported versions

Nodvard Dock is in beta. Security fixes are made for the **latest release only**.

## Before you install

- **Whoever can use Nodvard Dock can control your servers.** The SSH keys are stored encrypted in its vault, but the
  user interface uses them. Choose a strong password, turn on two-factor login and do not expose Nodvard Dock to the
  internet; reach it from your own network or through a VPN.
- **The `docker` group on a server is practically root.** This is true for every tool of this kind. For servers where
  that is too much, use the "operate only" mode (tag `docker-bedienen`) described in the README. A `docker` group
  membership or full sudo rule set up earlier stays in place, though; "Verbindung prüfen" (check connection) shows it.
- **Back up the data volume** `nodvard-dock-data`, or use the backup in the settings: it holds the vault and its key.

## Deutsch

Nodvard Dock verwaltet Docker auf deinen Servern über SSH und speichert die SSH-Schlüssel dafür. Sicherheitsmeldungen
nehmen wir deshalb ernst.

**Sicherheitslücken bitte nicht als öffentliches Issue melden,** sondern vertraulich über
[Report a vulnerability](https://github.com/nodvard/dock/security/advisories/new) (Reiter **Security**) oder per
E-Mail an **kontakt@nodvard.com**. Hilfreich sind die Version (Image-Tag oder das Feld `version` von
`/api/v1/health`), was passiert und was ein Angreifer damit erreichen kann, die Schritte zum Nachstellen und, wenn du
einen hast, ein Vorschlag zur Behebung.

Eine erste Antwort bekommst du innerhalb von **7 Tagen**. Wir halten dich auf dem Laufenden und nennen dich auf Wunsch
in den Release-Notes.

Nodvard Dock ist eine Beta. Sicherheitskorrekturen gibt es nur für die **jeweils neueste Version**.

**Vor der Installation:**

- **Wer Nodvard Dock bedienen darf, kann deine Server steuern.** Die SSH-Schlüssel liegen verschlüsselt im Tresor,
  die Oberfläche benutzt sie aber. Darum: ein starkes Passwort, Zwei-Faktor an und Nodvard Dock nicht offen ins
  Internet stellen, sondern nur aus dem eigenen Netz oder über ein VPN erreichen.
- **Die Gruppe `docker` auf einem Server heißt praktisch root.** Das gilt für jedes Werkzeug dieser Art. Wo dir das zu
  viel ist, nimm den Modus „Nur bedienen“ (Markierung `docker-bedienen`), beschrieben im README. Eine früher
  eingetragene Gruppe docker oder volle sudo-Regel bleibt allerdings bestehen; „Verbindung prüfen“ zeigt sie an.
- **Sichere das Volume** `nodvard-dock-data` oder nutze die Sicherung in den Einstellungen: Darin liegen der Tresor und
  sein Schlüssel.
