# Connect servers to Nodvard Deck through Tailscale

> Deutsche Version: [13-TAILSCALE.md](../13-TAILSCALE.md)

Tailscale connects the machine running Nodvard Deck and your servers through an encrypted VPN.
The devices belong to your Tailscale network, the tailnet. You normally do not need a public IPv4 address
at home or port forwarding on your router. Connections also work behind DS-Lite or CGNAT; if a direct
connection cannot be established, Tailscale can relay the encrypted traffic.
See [Tailscale's connection and firewall guidance](https://tailscale.com/docs/reference/faq/firewall-ports).

This guide uses Docker on a Linux host and a Linux target server with its regular SSH service.
Tailscale provides the network path. Deck still uses its own SSH credentials; signing into the tailnet
does not replace them. You do not need to enable Tailscale SSH for this setup.

## 1. Install Tailscale on both machines

Install Tailscale on the **machine running Docker and Nodvard Deck**, and on each server you want to
connect. If Docker runs in a VM, install Tailscale in that VM. Treat a Proxmox VM like any other Linux server.

Run these commands on both machines, on a supported Linux distribution including Raspberry Pi OS:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Open the displayed login page and sign both devices into the **same tailnet**. The first command downloads
and immediately runs Tailscale's installation script. Tailscale's
[Linux installation guide](https://tailscale.com/docs/install/linux) also links to installation through your
distribution's package repositories.

On each device, check that it is signed in and note its Tailscale IPv4 address:

```bash
tailscale status
tailscale ip -4
```

[Tailscale IPv4 addresses](https://tailscale.com/docs/concepts/tailscale-ip-addresses) belong to
`100.64.0.0/10`, from `100.64.0.0` to `100.127.255.255`. If you need an address for troubleshooting,
copy the address actually shown on the target server.

### Proxmox and LXC

An unprivileged LXC container needs access to `/dev/net/tun` for normal Tailscale networking.
If your Proxmox interface offers device passthrough, select the container, open
**Resources → Add → Device Passthrough**, enter `/dev/net/tun`, then shut the container down completely
and start it again. Install Tailscale inside that LXC container.

For older Proxmox versions, follow
[Tailscale's LXC instructions](https://tailscale.com/docs/features/containers/lxc/lxc-unprivileged).
The device permission applies to the LXC container in which Tailscale runs.

## 2. Check MagicDNS and the connection from Deck

1. In the Tailscale administration console, open the DNS settings and enable
   [MagicDNS](https://tailscale.com/docs/features/magicdns) if it is not already enabled.
2. Copy the target server's **full DNS name** from its device details. This guide uses the placeholder
   `server.example.ts.net`; replace it with the full name shown for your server.
3. On the Deck host, check that Tailscale can reach the target:

   ```bash
   tailscale ping server.example.ts.net
   ```

The shipped [compose.standalone.yml](../../deploy/compose.standalone.yml),
[compose.standalone-mit-helfer.yml](../../deploy/compose.standalone-mit-helfer.yml) and
[docker-compose.yml](../../deploy/docker-compose.yml) use Docker bridge networking for Deck.
Deck does not share the host network and these files do not include a Tailscale service.
`network_mode: none` in the helper variant applies only to the update helper.

Docker sends outgoing connections from the bridge network through the host. Reaching the tailnet requires
working routes and firewall rules on that host. The container also needs DNS resolution for the full
MagicDNS name. A successful host test does not establish either of these for the container.
See [Docker's bridge networking documentation](https://docs.docker.com/engine/network/drivers/bridge/).

Run the following commands **from the directory containing your Compose file**. They execute inside the
running Deck container. First check DNS resolution:

```bash
docker compose exec -T nodvard-deck python -c "import socket; print(socket.getaddrinfo('server.example.ts.net', 22, type=socket.SOCK_STREAM))"
```

Then check whether the server's SSH port is reachable:

```bash
docker compose exec -T nodvard-deck python -c "import socket; socket.create_connection(('server.example.ts.net', 22), timeout=5).close(); print('SSH port reachable')"
```

Replace `22` in both commands if the server uses another SSH port. A successful connection prints
`SSH port reachable`. This tests the network path, not SSH authentication.

If DNS fails, check MagicDNS and the container's DNS configuration. A full tailnet name avoids relying on
a DNS search suffix. For diagnosis, repeat the port test with the Tailscale IP shown on the server.
If the IP works, fix DNS before using the MagicDNS name in Deck, or temporarily use that IP as its address.
If neither works, check both devices with `tailscale status`, then check tailnet access rules, the SSH service,
host forwarding and firewalls.

## 3. Add the server and its SSH key in Deck

The following labels are the German labels shown by Deck. You need an owner or admin account to manage servers.

1. Open **Einstellungen → Server & Zugänge** (Settings → Servers & access) and press
   **Server hinzufügen** (Add server).
2. Enter a **Kurzname** (short name), for example `server`, and optionally an **Anzeigename** (display name).
   The short name must be lowercase with no spaces and cannot be changed later.
3. Under **Adresse (IP oder Name)** (Address), enter the full MagicDNS name, for example
   `server.example.ts.net`. Enter only the name, without `http://`, `https://` or a port.
4. Select **Linux** under **Betriebssystem** (Operating system) for the Linux server in this guide,
   then press **Anlegen** (Create).
5. On the server's page, in **SSH-Zugang** (SSH access), choose **SSH-Schlüssel erzeugen**
   (Generate SSH key, recommended). Set **Benutzer auf dem Server** (User on the server), normally `nodvard`,
   and **SSH-Port**, normally `22`. For a Proxmox node, use `root`. Press **Schlüssel erzeugen** (Generate key).
   Deck creates a key for this server and stores the private part encrypted.
6. Copy the displayed **Einrichtungsbefehl** (setup command). Log in to the target server through an existing
   administrator connection or its console and run the command there. It runs directly as root, otherwise
   through `sudo`. It creates the selected user if needed and adds the public key to that user's
   `~/.ssh/authorized_keys`. Review the options for **Root-Rechte ohne Passwort** (Root rights without a password)
   and **Zur Gruppe docker hinzufügen** (Add to the docker group); they grant the additional rights required
   by the selected modules.
7. Back in Deck, press **Verbindung prüfen** (Check connection). For a new server, compare the displayed
   fingerprint with the server's own host key through its console or another trusted connection. For an
   Ed25519 host key, run this **on the target server**:

   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```

   If Deck shows another key type, use the comparison command it provides. Confirm only when the fingerprints
   match. Deck then continues the connection check and verifies the SSH login.

If you already have a key, use **Eigenen Schlüssel einfügen** (Paste your own key) instead of generating one.
Enter the server user, SSH port and the complete **private key without a passphrase**, then press **Speichern**
(Save). The matching public key must be in that user's `authorized_keys`; Deck also provides a setup command
for this route. Pasting a public key into the private key field does not work.

Deck does not load the host's `~/.ssh` keys, SSH agent or SSH configuration. A successful `ssh` command on the
host therefore does not establish that the SSH credentials stored in Deck work.

For the full explanation of credentials, the setup command and host key confirmation, see
[first setup, section 3](FIRST-SETUP.md#3-connect-your-first-server).

## 4. Access rules and firewalls

- Allow the Deck host to reach the target server's SSH TCP port, normally `22`, in your
  [tailnet access policy](https://tailscale.com/docs/features/access-control).
- The target server's firewall must allow that SSH connection through Tailscale, and its regular SSH service
  must be running and listening on an address reachable through the tailnet. On the Deck host, firewall rules
  must also allow forwarding from the Docker bridge to the tailnet. Keep a working administrator connection
  while changing firewall rules.
- For this server connection you normally do not need to expose SSH on the public Internet or forward it
  through your router. You do not need an exit node. A
  [subnet router](https://tailscale.com/docs/features/subnet-routers) is an alternative for target devices
  without their own Tailscale client; that requires routes and access rules, and Deck then uses the target's
  reachable address in that network.
- For a server that stays on, consider the expiry of its Tailscale device key. See
  [Tailscale's server guidance](https://tailscale.com/docs/how-to/set-up-servers).

A blanket opening of UDP port `41641` is not required. Tailscale can use relays if direct connections are
unavailable. For restricted networks, consult
[Tailscale's firewall guidance](https://tailscale.com/docs/reference/faq/firewall-ports).

If you also **open Deck itself through Tailscale**, Deck treats observed client IPs in `100.64.0.0/10` as
internal for browser logins. The additional increasing delay for username guessing does not apply to these
addresses; per-address limits and limits for incorrect second-factor codes still apply. The address Deck
actually sees matters, and Docker or a proxy may change it. See the
[login rules](../../backend/src/nodvard_deck/core/login_limit.py).

A Tailscale address is **not automatically a trusted proxy**. Only if you use a reverse proxy, register the
actual proxy address seen by Deck under **Einstellungen → System → Vertrauenswürdiger Proxy**
(Settings → System → Trusted proxy). Do not register the whole tailnet: every registered device would be
allowed to supply client addresses. See the [reverse proxy guide](REVERSE-PROXY.md).
Deck accepts `100.64.0.0/10` as an internal range when validating proxy entries, and may show the warning
about an unregistered proxy for such an address. Neither grants trust automatically. See
[proxy validation](../../backend/src/nodvard_deck/core/trusted_proxies.py) and the
[proxy warning](../../backend/src/nodvard_deck/core/untrusted_proxies.py).

## 5. Common errors

| What you see | What to check |
|---|---|
| The host reaches the server, but the Deck container does not | Routing and forwarding from the Docker bridge to the tailnet, plus the host firewall |
| The Tailscale IP works, but the MagicDNS name does not | MagicDNS, the full name from the administration console and DNS resolution inside the Deck container; temporarily use the server's actual Tailscale IP |
| The SSH port times out | Both devices signed in and online, tailnet access policy, the configured SSH port and firewalls |
| The connection is refused | The regular SSH service is running and listening on the configured port and an address reachable through Tailscale |
| The port is reachable, but Deck says the key was rejected | Correct user, private key stored in Deck, matching public key in that user's `authorized_keys`, and successful execution of the setup command |
| A private key cannot be saved | Paste the complete private key without a passphrase, rather than its public part |
| Deck asks for a host key fingerprint | Compare it on the target server before confirming; a changed key needs investigation before using **Vergessen** (Forget) in **Server-Schlüssel** (Server key) |
| A previously working connection stops | `tailscale status`, device login, device key expiry, changed DNS name, SSH service and access rules |
