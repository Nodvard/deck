# Nodvard Deck Behind a Reverse Proxy

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.
>
> Deutsche Fassung: [../12-REVERSE-PROXY.md](../12-REVERSE-PROXY.md)

This guide is for you if you do not open Nodvard Deck directly at `http://<host>:8080`, but through a reverse proxy such
as **Nginx Proxy Manager**, **Traefik** or **Caddy**, usually with HTTPS and an address of your own like
`https://deck.example.org`. Nodvard Deck does not ship a proxy or certificates. Your proxy does that; this guide is only
about making the two work well together.

Without a proxy you need none of this. Just leave the setting empty.

## Contents

1. [What the setting is for](#1-what-the-setting-is-for)
2. [Set up the proxy](#2-set-up-the-proxy)
3. [Find the proxy's address](#3-find-the-proxys-address)
4. [Enter the address](#4-enter-the-address)
5. [Check that everything works](#5-check-that-everything-works)
6. [Security: what to keep in mind](#6-security-what-to-keep-in-mind)
7. [Troubleshooting](#7-troubleshooting)

## 1. What the setting is for

With a proxy in front of Nodvard Deck, every connection comes from the proxy. Nodvard Deck then only sees the proxy's
address:

- The **lock after too many failed sign-ins** counts all devices as one. If someone mistypes often, everybody else may
  be locked out for a few minutes too.
- The **audit log** shows the same address, the proxy's, for every sign-in.
- Nodvard Deck does not know that you came in over **HTTPS**. The sign-in cookie then does not get the "send over HTTPS
  only" flag (`Secure`).

That is why a proxy writes into every request who it really comes from (`X-Forwarded-For`) and whether the browser used
HTTPS (`X-Forwarded-Proto`). But anyone can send these headers themselves. Nodvard Deck therefore only believes them
from addresses you explicitly enter as a **trusted proxy**. From everyone else they are ignored.

## 2. Set up the proxy

First set up your proxy so that you reach Nodvard Deck at the new address. The target is always the Nodvard Deck
container on port **8080** (without HTTPS; the proxy handles that).

The easiest and safest setup is to run the proxy and Nodvard Deck in the **same Docker network**. The proxy then
reaches Nodvard Deck by its container name, and the proxy's address is unambiguous (see section 3).

### Nginx Proxy Manager

1. "Hosts" → "Proxy Hosts" → "Add Proxy Host".
2. "Domain Names": your address, e.g. `deck.example.org`.
3. "Scheme": `http`, "Forward Hostname / IP": `nodvard-deck` (the container name, if both share a Docker network) or
   the host's address, "Forward Port": `8080`.
4. **Turn on "Websockets Support".** This is required: the terminal, the console and the live view run over it.
5. "Block Common Exploits" may stay on. If something unusual fails (e.g. a search with special characters), turn it off
   to test.
6. Tab "SSL": request a certificate (Let's Encrypt), turn on "Force SSL".

Nginx Proxy Manager sets `X-Forwarded-For` and `X-Forwarded-Proto` by itself; there is nothing to add. If you upload
large backups through the proxy (Settings → System → Restore, „Einstellungen → System → Wiederherstellen“), a size
limit in the proxy may apply. The line `client_max_body_size 0;` in the "Advanced" tab helps.

Example with both in the same Compose project and a fixed address for the proxy:

```yaml
name: nodvard-deck

services:
  nodvard-deck:
    image: ghcr.io/nodvard/deck:latest
    restart: unless-stopped
    # No "ports:" needed: the proxy reaches Nodvard Deck in the shared network.
    environment:
      NODVARD_DECK_TRUSTED_PROXIES: "172.18.0.5"
    volumes:
      - nodvard_deck_data:/app/data
    networks: [proxy]

  npm:
    image: jc21/nginx-proxy-manager:latest
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "81:81"   # Nginx Proxy Manager admin interface
    volumes:
      - ./npm/data:/data
      - ./npm/letsencrypt:/etc/letsencrypt
    networks:
      proxy:
        ipv4_address: 172.18.0.5

networks:
  proxy:
    ipam:
      config:
        - subnet: 172.18.0.0/16

volumes:
  nodvard_deck_data:
    name: nodvard-deck-data
```

### Traefik

Traefik reads its configuration from labels on the Nodvard Deck container:

```yaml
services:
  nodvard-deck:
    image: ghcr.io/nodvard/deck:latest
    restart: unless-stopped
    labels:
      - traefik.enable=true
      - traefik.http.routers.deck.rule=Host(`deck.example.org`)
      - traefik.http.routers.deck.entrypoints=websecure
      - traefik.http.routers.deck.tls.certresolver=letsencrypt
      - traefik.http.services.deck.loadbalancer.server.port=8080
    networks: [proxy]
```

Traefik sets `X-Forwarded-For` and `X-Forwarded-Proto` itself and passes WebSockets through without extra
configuration. Enter the address of the Traefik container in the `proxy` network.

### Caddy

In the `Caddyfile`, one line per address is enough:

```
deck.example.org {
    reverse_proxy nodvard-deck:8080
}
```

Caddy gets the certificate itself, sets `X-Forwarded-For` and `X-Forwarded-Proto` and passes WebSockets through. Enter
the address of the Caddy container (or of the host Caddy runs on).

## 3. Find the proxy's address

**The easiest way:** open Nodvard Deck through the proxy's new address and go to **Settings → System → Trusted proxy**
(„Einstellungen → System → Vertrauenswürdiger Proxy“). At the top it says how your request arrives right now, for
example:

> „Deine Anfrage kommt gerade von 172.18.0.5 (direkt) über http.“ (Your request currently comes from 172.18.0.5
> (directly) over http.)

If the proxy already sends its headers but is not entered yet, the card shows a hint and the button **"Enter this
address"** („Diese Adresse eintragen“). It copies exactly that one address into the field; then "Save" („Speichern“).

What you see there depends on how the proxy and Nodvard Deck are connected:

- **Proxy in the same Docker network** (e.g. the same Compose project): you see the proxy container's address, e.g.
  `172.18.0.5`. You can also look it up with
  `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' <proxy-container-name>`.
  This address can change when the container is recreated. So give the proxy a fixed address (`ipv4_address`, see the
  example above) or enter the network (e.g. `172.18.0.0/16`), but only if nothing but your own containers is attached
  to that network. The network also contains its gateway (next item), so only enter it if port 8080 is not published
  to the outside (section 6).
- **Proxy in another Docker project on the same host** that reaches Nodvard Deck through the host's port 8080: you
  usually see the gateway of Nodvard Deck's Docker network, e.g. `172.18.0.1` (`docker network inspect <network>` shows
  it under "Gateway"). Other connections arrive through that address too: anything running on the host itself, and
  with Docker Desktop (Windows/macOS) or rootless Docker even every connection to port 8080, including those from other
  devices in your home network. If you enter the gateway, those devices could claim any sender address. Better: also
  attach the proxy to Nodvard Deck's network (`docker network connect <network> <proxy-container>`) and enter its
  address in that network.
- **Proxy on another machine** in your home network, e.g. `192.168.2.10`: you see that address. Enter exactly this one
  address, not your whole home network.

## 4. Enter the address

There are two ways:

- **In the interface:** Settings → System → Trusted proxy. One address or small network per line, then "Save". The
  change applies immediately, without a restart. It is recorded in the audit log with the old and new value. You see
  the card with the permissions for settings and system (role "admin" or the owner).
- **Through the environment variable** `NODVARD_DECK_TRUSTED_PROXIES` in your Compose file, several entries separated by
  commas, e.g. `NODVARD_DECK_TRUSTED_PROXIES: "172.18.0.5, fd00:1::5"`. When it is set, only it applies. The card then
  only shows the list; you change it in the Compose file (then `docker compose up -d`). Invalid entries are skipped and
  show up as a warning in the container log.

Allowed are IPv4 and IPv6 addresses and networks written like `172.18.0.0/16`, at most 20 entries. Nodvard Deck
refuses networks that are too large: for public IPv4 addresses anything larger than `/24`, inside the private ranges
(`10.x`, `172.16.x` to `172.31.x`, `192.168.x`, `100.64.x` to `100.127.x`, `127.x`, `169.254.x`) anything larger than
the range itself (at most `/8`), for IPv6 anything larger than `/48`. A Docker network with IPv6 is usually a `/64`,
which is fine. So `0.0.0.0/0` ("trust everyone") is not possible. If you write an address with a netmask such as
`192.168.2.10/24`, Nodvard Deck asks whether you mean just that host (`192.168.2.10`) or the whole network
(`192.168.2.0/24`).

If an entry means a network with more than 16 addresses (IPv4 larger than `/28`, IPv6 larger than `/124`), the card
shows a yellow warning, even for private networks. Saving still works, but the single address of your proxy is the
safest choice (section 6).

## 5. Check that everything works

1. Open Nodvard Deck through the proxy (`https://deck.example.org`) and look at the card. It should now say:
   > „Deine Anfrage kommt gerade von 192.168.2.40 über https, weitergeleitet von 172.18.0.5.“ (Your request currently
   > comes from 192.168.2.40 over https, forwarded by 172.18.0.5.)

   The first address is your device's; "https" means Nodvard Deck knows the connection is encrypted.
2. Sign out and in again, then reload the page. You stay signed in.
3. Under Settings → Audit log („Einstellungen → Protokoll“), your sign-in shows your device's address, not the proxy's.
4. Open a server's terminal once. If it does not connect, the proxy is missing WebSocket support.

## 6. Security: what to keep in mind

- **Never enter a whole network that ordinary devices also come from**, such as your home network `192.168.2.0/24`.
  Every entered device may tell Nodvard Deck any sender address it likes. That would allow bypassing the lock after
  failed sign-ins and would put wrong addresses into the audit log. Only enter your proxy, ideally as its single
  address.
- **Nodvard Deck stays reachable directly** over HTTP on port 8080, bypassing the proxy. If you do not want that, do not
  publish the port:
  - Proxy in the same Docker network: leave out the `ports:` lines of the `nodvard-deck` service entirely (see the
    example above).
  - Proxy running directly on the host: bind the port to the host only:

    ```yaml
    services:
      nodvard-deck:
        ports:
          - "127.0.0.1:8080:8080"
    ```
- **Entered a gateway or a whole Docker network?** Then do not publish port 8080 to the outside (no `ports:` lines, or
  only `127.0.0.1:8080:8080` as above). Otherwise, depending on your Docker setup, connections from other devices also
  arrive through the gateway and could fake their address. If you bind the port to `127.0.0.1`, connections from the
  host itself also show the gateway's address; that then only affects programs on that host.
- The proxy should set `X-Forwarded-For` itself or append its address. Nginx Proxy Manager, Traefik and Caddy do so out
  of the box. If a device sends a made-up address itself, it ends up to the left of the real one in the chain. Nodvard
  Deck reads the chain from the right and takes the first address that is not entered itself; whatever is to the
  left of it is not read. That holds as long as only your proxy is entered. If you enter a whole network, any device in
  it can claim an address, because Nodvard Deck then keeps reading the chain to the left across all devices of that
  network.
- The sign-in cookie gets `Secure` automatically as soon as the request came over HTTPS. The environment variable
  `NODVARD_DECK_COOKIE_SECURE` (`true`/`false`) still forces it if you need that.
- The image starts uvicorn with `--no-proxy-headers`. If you used to set `FORWARDED_ALLOW_IPS`, it no longer has an
  effect. Enter your proxy's address under Settings → System → Trusted proxy instead (section 4). If you start the
  container with your own `command:`, keep `--no-proxy-headers` in it, otherwise uvicorn evaluates the headers itself
  and the card shows confusing results.

## 7. Troubleshooting

| What you see | Cause | What helps |
|---|---|---|
| The card says "über http" although you come over HTTPS, or the audit log shows the proxy's address for every sign-in | The proxy's address is not entered (then the sign-in cookie lacks `Secure`, and the lock after failed sign-ins counts all devices as one), or it sends no `X-Forwarded-Proto` | Enter the address (section 3), check the proxy |
| The card shows "(direkt)" with the proxy's address but no hint | The proxy sends no `X-Forwarded-*` headers | Nginx Proxy Manager, Traefik and Caddy do this by default; add it to custom configurations |
| The card shows a gateway like `172.18.0.1` instead of the proxy | The proxy reaches Nodvard Deck through the host's port | Attach the proxy to Nodvard Deck's Docker network (section 3) |
| After a proxy update nothing works anymore | The proxy container got a new address | Give it a fixed address or enter your own Docker network |
| Terminal and live view do not connect | WebSockets are not passed through | Turn on "Websockets Support" in Nginx Proxy Manager |
| „ist zu groß“ (too large) when saving | Network too large (limits in section 4) | Enter only the proxy's address or a smaller network |
