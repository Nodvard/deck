# Harden SSH with Shield

Each server card in **Intrusion protection** offers **Harden SSH**. Deck must use an SSH key for that server. Password access disables hardening; **Store key** opens **Servers & credentials**, where the key must be set as the default credential.

If the public Deck key is missing from the login user's `authorized_keys`, **Install Deck key** adds it as a separate action. Existing keys remain intact. Key installation, hardening and undo all pass through the action gate at high risk and are recorded in the audit log.

This requires the current Shield helper, version 26 or later. If the helper is missing or outdated, run the server setup command again. Hardening changes only `/etc/ssh/sshd_config.d/nodvard-haertung.conf`:

```text
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
MaxAuthTries 3
```

For a Deck login as `root`, the file uses `PermitRootLogin prohibit-password`. Root access with a key stays enabled. The card and approval explicitly warn about this exception.

If Deck logs in as another user but root already has keys in `authorized_keys`, the file also uses `PermitRootLogin prohibit-password`. Anyone logging in as root with a key is not locked out; only password logins are blocked. The card and approval mention this case. Users who log in with a password today need a key afterwards. Because of `MaxAuthTries 3`, an SSH client with many agent keys should name the right one explicitly (`IdentitiesOnly yes`).

The helper runs `sshd -t` before writing and before reloading. It also checks the effective values with `sshd -T`. If the configuration directory is not included or another setting takes precedence, hardening fails without rewriting existing configuration.

After reloading, Deck opens a new connection with the same key and verifies the stored server key. Password and keyboard interactive authentication are disabled for this probe. If it fails, Deck removes its file using the original connection and reloads SSH. The action is reported as failed. Without confirmation, the server attempts rollback itself after 60 seconds, even if Deck is unavailable. If Deck cannot confirm rollback, the result says so explicitly.

**Undo hardening** removes the file, validates the remaining configuration and reloads SSH. Other SSH settings then apply again. A separately installed key remains in place. The card shows the checked state or **Unknown state** if it cannot be read. **Check SSH state** refreshes it.

`GET /api/v1/ext/shield/defender/hosts/{host_id}/ssh-hardening` requires `soc.read`. `POST` to the same path requires `soc.manage` and `hosts.execute`, accepts `{"mode":"on"}`, `{"mode":"off"}` or `{"mode":"add-key"}`, and returns an action proposal. Approval requires `actions.approve:high`.
