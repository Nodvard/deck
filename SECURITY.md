# Security Policy

Nodvard Deck manages servers, VMs and containers and stores access credentials, so we take
security reports seriously.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report vulnerabilities privately through GitHub's private vulnerability reporting for this
repository: [Report a vulnerability](https://github.com/nodvard/deck/security/advisories/new)
(tab **Security → Report a vulnerability**), or by e-mail to **kontakt@nodvard.com**.

Please include:

- the affected version or commit,
- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- if possible, a suggested fix.

You will get a first response within **7 days**. We will keep you informed while we work on
a fix and credit you in the release notes if you wish.

## Supported versions

Nodvard Deck is in beta. Security fixes are made for the **latest release only**.

| Version | Supported |
|---|---|
| latest release | ✅ |
| older releases | ❌ |

## Hardening notes for operators

- Do not expose Nodvard Deck directly to the internet. Put it behind a VPN (e.g. WireGuard,
  Tailscale) or a reverse proxy with HTTPS.
- Enable two-factor login for every account.
- Give Nodvard Deck's Proxmox API token and SSH users only the rights they need
  (see [docs/en/PROXMOX-TOKEN.md](docs/en/PROXMOX-TOKEN.md)).
- Back up the data volume — it contains the encrypted secrets vault and its key.
