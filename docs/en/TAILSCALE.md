# Connect servers to Nodvard Deck through Tailscale

Full German guide: [13-TAILSCALE.md](../13-TAILSCALE.md).

Install Tailscale on the Linux machine running Docker and Deck, and on each target server.
If Docker runs inside a VM, install it in that VM. Sign all devices into the same tailnet.
See [Linux installation](https://tailscale.com/docs/install/linux).

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale status
tailscale ip -4
```

Unprivileged LXC containers need `/dev/net/tun` for normal Tailscale networking.
See [Tailscale's LXC instructions](https://tailscale.com/docs/features/containers/lxc/lxc-unprivileged).
The shipped Compose files use Docker bridge networking for Deck, not host networking.
Reaching tailnet devices through the host depends on routing and firewall rules.
From your Compose directory, test the target SSH port inside the running Deck container; replace the IP and port:

```bash
docker compose exec -T nodvard-deck python -c "import socket; socket.create_connection(('100.100.10.20', 22), timeout=5).close(); print('SSH port reachable')"
```

In **Einstellungen → Server & Zugänge → Server hinzufügen**, enter the server's Tailscale IP.
Configure the usual SSH user, port and key, then use **Verbindung prüfen** and verify the host key fingerprint.
A full MagicDNS name also works if it resolves inside the Deck container; otherwise use the IP first.
Restrict access to the required servers and ports in your tailnet policy. No exit node is needed.
A [subnet router](https://tailscale.com/docs/features/subnet-routers) can reach devices without their own client.

For browser logins, Deck treats observed client IPs in `100.64.0.0/10` as internal:
the extra username guessing delay does not apply, but per-address and second-factor limits still apply.
A Tailscale address is not automatically a trusted proxy. Only register the actual reverse proxy's address,
never the whole tailnet. See the [reverse proxy guide](REVERSE-PROXY.md).
If only the host can reach the server, check forwarding and firewall rules between Docker and Tailscale.
If the SSH port works but login fails, check SSH credentials and host key confirmation in Deck.
