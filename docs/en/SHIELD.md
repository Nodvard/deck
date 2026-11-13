# Nodvard Shield

## CVE matching and external checks

The Shield overview has two cards per server. **CVE matching** ("CVE-Abgleich") lists installed packages with
known vulnerabilities, including installed version, CVE, severity and the fixed version when available.
The card counts high and critical findings. **View updates** ("Updates ansehen") opens the Update Center
for that server. Findings do not install updates. Failed or unsupported checks do not mean a server is safe.

The bundled Shield helper version 24 reads the installed package list with `dpkg-query` or `rpm`.
Without a current helper, Shield attempts the same read-only query using the available access rights.
Package names and versions only travel from the managed server to Deck. Deck downloads public security data,
caches it for 24 hours and matches packages locally. Package lists, names and installed versions are never
sent to the data providers. No account with an external security service is required. Matching considers
the distribution, its release, package epochs and distribution version suffixes.

Sources are the [Debian Security Tracker](https://security-tracker.debian.org/tracker/data/json),
[public Ubuntu security data](https://documentation.ubuntu.com/security/security-updates/osv/) and
[Red Hat security data](https://access.redhat.com/security/data).
Fedora uses public Bodhi security advisories because no official OVAL/CSAF feed is available. These cover
published security updates; unpublished fixes and advisories without identifiable CVEs are not fully covered.
Debian matches use the distribution codename; Ubuntu uses the official JSON data in the OSV archive.
Ubuntu Pro/ESM data is kept separate from regular Ubuntu packages. Releases for which only Pro data remains
are reported as unsupported. RHEL 8 and 9 use OVAL including unfixed vulnerabilities; RHEL 10 and later use
published CSAF security advisories. Those advisories do not fully cover unfixed vulnerabilities.
OVAL rules that cannot be evaluated from package inventory and ambiguous product variants are reported as
check problems. Third party packages and unsupported distribution releases may fall outside the data sources.

**External checks** ("Prüfung von außen") make short TCP connections from Deck to common ports and known
services on the server. The first successful run establishes a baseline. Later runs warn about newly open
ports. A failed run does not replace the last successful baseline. Checks run from the home network, from the
machine running Deck, not from the internet. Whether a port is reachable from the internet also depends on the
router and its port forwarding and is not checked. This does not scan UDP or all 65,535 TCP ports.

Known HTTPS services are checked for certificate expiry, issuer, hostname match, trust and weak TLS protocols.
Warnings explain certificates expiring in less than 14 days, self-signed certificates and hostname mismatches.
A self-signed certificate is vouched for by the endpoint itself. This may be intentional on a private network,
but does not provide independent confirmation of its identity. When Deck cannot perform a protocol check,
the card reports that limitation instead of treating the result as safe.

**Check now** ("Jetzt prüfen") starts both read-only checks. The initial security data download may take longer.
Disable external checks per server or globally using **Allow external checks** ("Außenprüfung erlauben") in
Shield settings. Disabled external checks make no connections, including during manual runs. Previous results
remain visible with their original timestamp. Duplicate starts and switch changes are rejected while a run is active.

The shared **CVE matching and external checks** schedule defaults to 06:30 every day in the installation's
timezone. Change the time or disable scheduled runs in Shield settings. Checks use the same selected Linux
servers with SSH credentials as antivirus checks. They do not change packages, firewall rules or certificates.
Viewing requires `soc.read`; starting checks and changing the per-server switch requires `soc.manage`.

## Nodvard AI: configure a provider

Select a provider under **Settings → Extensions → Nodvard Shield**.
**Ollama (local)** remains the default. For OpenRouter, vLLM or llama.cpp, select
**OpenAI-compatible** (`openai_compat`) and enter the base URL and exact model name.
A local server might use `http://127.0.0.1:8080/v1`. Inside a container, `127.0.0.1` refers to
the Deck container itself. Use an address reachable from Deck there. The provider must support
`POST /v1/chat/completions` without streaming. An existing `/v1` is not appended twice;
provider prefixes before `/v1` are preserved.

Store the token in the matching **vault field**, never in the base URL or model name.
Local providers without authentication do not need a token. A provider's primary and fallback
servers share one token. Only configure a fallback server that is allowed to use the same
credentials. The fallback model is used if the primary model does not respond. Without a
fallback server, a primary failure is reported as an unavailable connection.

Save the settings and token before pressing **Test connection** ("Verbindung testen").
The test uses the saved configuration, sends a small request and shows response time and model.
If `GET /v1/models` is available, it also shows the model list. An unavailable model list does
not prevent use; you can enter the model name manually. For rejected credentials, check the
token in the vault. For a timeout, check the address, port and reachability from Deck.
Testing requires permission to change settings.

A public base URL automatically shows **Daten verlassen dein Netz** ("Data leaves your network").
This also applies to a public fallback server. The notice is omitted for localhost, private IP
networks and Tailscale names. It describes the configured address rather than verifying the
provider: a private server can also forward requests. The selected provider receives the current
request and the server or container data included in it. Before using an external provider,
consider which data you want to send there.

## Fail2ban settings per server

Under **Shield → Intrusion protection** ("Einbruchschutz"), each server with Fail2ban installed has a
**Fail2ban settings** ("Fail2ban-Einstellungen") card. Opening it reads the current server configuration.
Ban duration (`bantime`) and detection window (`findtime`) use seconds; `maxretry` is the number of failed
attempts within that window that triggers a ban. Durations range from 1 to 31,536,000 seconds; retries range
from 1 to 1,000.
If the service or SSH jail is unavailable, the card shows saved managed settings or defaults with a notice.
The Fail2ban service must be running to activate changes.

**Additional exceptions** ("Weitere Ausnahmen") accepts IPv4 and IPv6 addresses and CIDR networks, one per line.
Shield automatically includes the SSH source address reported by the server for Deck's connection. It is
visible and cannot be removed. This address comes from SSH, independently of the browser address. If it cannot
be determined or changes after approval, Shield refuses the change. Close the card and reload the page.

`sshd` is mandatory. Additional jails discovered in the server's jail configuration can be selected. All selected
jails receive the same durations, retry count and exceptions. Deselected discovered jails are disabled in the
managed file. If another configuration file overrides Deck's exception, Shield refuses to apply the change.
It checks the effective configuration of all active jails using `fail2ban-client -d`.

**Apply settings** ("Einstellungen übernehmen") proposes a medium risk action through the action gate, using
the same approval process as other Shield changes. The change is recorded in the audit log. The helper only
writes `/etc/fail2ban/jail.d/nodvard.local`; it leaves `jail.conf` and `jail.local` unchanged, including during
installation. A systemd backend previously configured by Shield for `sshd` is preserved.
It runs `fail2ban-client -t` before activation and only runs `fail2ban-client reload` after a successful test.
If testing or reloading fails, the previous file is restored. A failed reload may already have partially changed
the running service; fix the error and apply again.

The bundled Shield helper version 18 or later is required. Run server setup again after updating Deck if Shield
reports an outdated helper. The new `fail2ban-config-read` and `fail2ban-config-write` subcommands accept fixed
arguments and validate allowed content instead of accepting an arbitrary root command.

The existing status and banned IP lists per jail remain read-only queries. Reading the card requires Shield
read permission. Applying changes additionally requires management and execution permissions and the relevant
action approval.
