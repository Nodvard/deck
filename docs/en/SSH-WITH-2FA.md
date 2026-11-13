# Connecting Servers with Two-Factor Authentication (2FA)

> German version: [../SSH-MIT-2FA.md](../SSH-MIT-2FA.md)

Nodvard Deck accesses managed servers automatically in the background to collect metrics, monitor services, manage containers, and run updates. If a server requires a second factor during SSH login (such as an authenticator app code or push confirmation), the dashboard cannot respond to interactive prompts. As a result, connection tests fail or time out.

This guide explains how to connect servers with 2FA enabled without lowering protection for your interactive sessions.

## Core Principle: Dedicated User with Key and 2FA Bypass

Never disable 2FA globally across the entire server. Instead, create a targeted bypass rule solely for the automated Nodvard Deck connection.

1. **Dedicated User:** Always use the dedicated user `nodvard` rather than your personal account or `root`.
2. **Key-Only Authentication:** Deck creates this user without a password. Authentication relies strictly on the SSH key stored in Deck's encrypted vault.
3. **Targeted Bypass:** The 2FA prompt is bypassed only for user `nodvard`. Interactive logins for all other users remain fully protected.
4. **Least Privilege:** Keep the minimal setup without root privileges, or use Deck's helper scripts with checksums rather than a full sudo rule.

## 1. OpenSSH (sshd_config)

When OpenSSH enforces 2FA via the `AuthenticationMethods` directive (e.g., requiring both public key and keyboard-interactive input), configure a Match block for the Deck user.

Create `/etc/ssh/sshd_config.d/99-nodvard.conf` on the target server (or append the block to the end of `/etc/ssh/sshd_config`):

```sshd
Match User nodvard
    AuthenticationMethods publickey
```

Verify syntax and reload the SSH service:

```bash
sudo sshd -t
sudo systemctl reload ssh   # on some distributions: sudo systemctl reload sshd
```

Human users must still supply both public key and 2FA credentials. User `nodvard` can authenticate immediately using its public key.

## 2. Google Authenticator (PAM)

If two-factor authentication is handled by `pam_google_authenticator.so`, skip the module for user `nodvard`.

Important: skipping the PAM module alone is not enough. For key-based access Deck only presents the key and never answers a keyboard prompt. The Match block from section 1 with `AuthenticationMethods publickey` for user `nodvard` must be in place as well; the PAM step only prevents the module from still asking for a code.

Edit `/etc/pam.d/sshd` on the target server:

```pam
# Skip 2FA prompt for user nodvard
auth [success=1 default=ignore] pam_succeed_if.so user = nodvard

# Existing Google Authenticator module
auth required pam_google_authenticator.so
```

When the logging-in user is `nodvard`, PAM skips the next single module (`pam_google_authenticator.so`). All other users are prompted as usual.

## 3. Duo Security (pam_duo or login_duo)

Duo Security can be adjusted depending on your setup:

### Option A: PAM Module (pam_duo)

In `/etc/pam.d/sshd`, insert a condition before the Duo module line:

```pam
auth [success=1 default=ignore] pam_succeed_if.so user = nodvard
auth required pam_duo.so
```

Alternatively, configure a dedicated group in `/etc/duo/pam_duo.conf` (such as `group = duo-users`) and omit user `nodvard` from this group.

### Option B: login_duo via sshd_config

If Duo is invoked via `ForceCommand`, override it for `nodvard` at the bottom of `/etc/ssh/sshd_config`:

```sshd
Match User nodvard
    ForceCommand none
```

Validate with `sudo sshd -t` and reload the SSH service.

## 4. Keycloak or Authentik (SSO via PAM)

The same applies here: the Match block from section 1 remains mandatory, the PAM exception comes on top.

When Linux servers integrate with Keycloak or Authentik via PAM (e.g., using OIDC or LDAP modules with mandatory MFA), non-interactive sessions cannot open web authentication flows.

1. **Local Account:** Ensure `nodvard` exists as a local user in `/etc/passwd`.
2. **Lock Password:** Lock password authentication with `sudo passwd -l nodvard`.
3. **Bypass SSO Module in PAM:** In `/etc/pam.d/sshd`, add a condition before the SSO module (e.g., `pam_oidc.so`):

```pam
auth [success=1 default=ignore] pam_succeed_if.so user = nodvard
```

Local SSH key authentication succeeds for `nodvard`, while human logins remain protected by the identity provider.

## 5. Tailscale SSH

When using Tailscale SSH, access is not configured via standard port-based network ACLs, but in the `"ssh"` section of your Tailscale policy file. That section may enforce interactive browser re-authentication via `"action": "check"`.

To allow automated background connections from Nodvard Deck without interactive browser prompts, insert an explicit allow rule with `"action": "accept"` in the `"ssh"` section before any interactive check rules, for example:

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

The `"action": "accept"` setting permits background connections without browser prompts, while interactive logins by human administrators can remain protected under `"action": "check"`. Note that the `"dst"` field does not include a port number (port restrictions apply only to network ACLs).

For general Tailscale setup and networking details, see [docs/13-TAILSCALE.md](../13-TAILSCALE.md) (English summary: [TAILSCALE.md](TAILSCALE.md)) and the [official Tailscale SSH documentation](https://tailscale.com/docs/features/tailscale-ssh).

## What NOT to Do

- **Never disable 2FA globally:** Do not turn off 2FA across the server just to connect Deck.
- **Do not assign a password to nodvard:** Keep the account passwordless.
- **Never bypass 2FA for root:** Never configure a 2FA exemption for the `root` user. Always isolate access to the dedicated user.
- **Keep authorized_keys restrictions:** Do not delete options like `restrict,pty` added by Deck's setup command.
- **Avoid unnecessary root rights:** Use Deck's helper scripts or run the slim variant without root privileges.

## Testing the Connection

After configuring the exemption:

1. In the dashboard, navigate to **Settings → Servers & access** (**Einstellungen → Server & Zugänge**).
2. Select the server and click **Check connection** (**Verbindung prüfen**).
3. If this is the first connection, verify and confirm the server fingerprint.
4. Once verified, the access badge turns green and all extensions can connect.
