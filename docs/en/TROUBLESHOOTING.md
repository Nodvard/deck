# Troubleshooting

Nodvard Deck is licensed under PolyForm Noncommercial: source-available, free for personal use. Below are 20 frequent issues during initial setup and everyday operation with symptoms, causes, and solutions.

## 1. Setup code not found
* **Symptom:** Opening the setup wizard at `/setup` prompts for an initial setup code that is not displayed on the web interface.
* **Cause:** Nodvard Deck generates this code randomly on the very first boot to prevent unauthorized initial claims and writes it exclusively into the container log.
* **Solution:** Inspect the container logs via `docker compose logs nodvard-deck` or inside your container management interface to locate the framed code string.

## 2. Port 8080 already allocated
* **Symptom:** Starting the container terminates with an error stating that port 8080 is already allocated or the address is in use.
* **Cause:** Another process on the host machine already binds port 8080, which is common on storage devices such as QNAP NAS management interfaces.
* **Solution:** Create a `.env` file containing `NODVARD_DECK_PORT=8081` or change the port mapping definition in your compose file to `8081:8080`.

## 3. SSH key rejected
* **Symptom:** Host connection testing fails during authentication with a message stating that the remote server rejected the provided key.
* **Cause:** The public key has not yet been appended to `~/.ssh/authorized_keys`, or an alternative server like Dropbear rejects standard restriction options.
* **Solution:** Execute the setup command shown in the host access settings directly on the target machine to install the key with matching daemon options.

## 4. Missing root privileges / sudoers rule absent
* **Symptom:** Administrative actions like quarantine handling, service restarts, or package upgrades fail with a message stating that root privileges and non-interactive sudo are missing.
* **Cause:** Nodvard Deck connects under a non-root account and lacks the specific passwordless sudo rule required to invoke the helper utilities.
* **Solution:** Run the server setup command again with the root privileges checkbox enabled so that the necessary checksummed sudoers rules are placed into `/etc/sudoers.d/`.

## 5. Docker socket permissions / missing docker group
* **Symptom:** The Docker module cannot inspect or manipulate containers on a managed host even though the `docker` tag is properly set on that server.
* **Cause:** The configured user is not a member of the local `docker` group, or a Proxmox unprivileged LXC container lacks nesting and keyctl features.
* **Solution:** Re-run the host setup script with the docker group option checked, or configure `nesting=1` and `keyctl=1` within the Proxmox container options.

## 6. Outdated root helpers after update
* **Symptom:** Under Settings → System → Updates the update helper reports `not ready`, or host access tests flag helper scripts as outdated in yellow.
* **Cause:** A newly deployed release of Nodvard Deck bundled updated helper binaries with refreshed checksums or expects a newer update helper container build.
* **Solution:** Pull the newest update helper image in your compose file and re-run the setup command on each affected host.

## 7. Proxmox token rejected or missing permissions
* **Symptom:** The Proxmox module reports `HTTP 401: Proxmox rejected access` or `HTTP 403: The token lacks a permission for this request`.
* **Cause:** The token secret was entered incorrectly, the token was deleted on the hypervisor, or the assigned Proxmox role lacks required permissions like `VM.Audit`.
* **Solution:** Save the correct token secret in the extension settings and verify that the corresponding Proxmox role possesses all necessary permissions on the target path.

## 8. Incorrect timezone configuration
* **Symptom:** Scheduled routines such as the morning briefing, database purges, or maintenance quiet windows execute at unexpected local times.
* **Cause:** The timezone selection was skipped during initial setup or configured with an incorrect region.
* **Solution:** Navigate to Settings → System, select your actual geographical location from the timezone picker, and save the form.

## 9. Reverse proxy misconfiguration and redirects
* **Symptom:** The interface continuously displays an insecure connection warning banner, or module connections fail with an error regarding unexpected redirects.
* **Cause:** An upstream reverse proxy fails to pass forwarded HTTPS headers, or issues HTTP redirects that Nodvard Deck rejects for security reasons.
* **Solution:** Add the proxy IP under Settings → System → Trusted Proxies and configure all module URLs using their direct HTTPS destination address.

## 10. Mobile app / ntfy push notifications absent
* **Symptom:** No push notifications arrive on the phone despite active status reporting or generated briefings.
* **Cause:** The topic subscribed in the mobile ntfy app does not match the channel setting, or the minimum priority filter suppresses informational alerts.
* **Solution:** Scan the QR code displayed inside the notification settings using your smartphone camera and verify delivery with the test message button.

## 11. Host key unconfirmed or changed
* **Symptom:** SSH operations stop with an error indicating that the server key is not yet confirmed or has changed.
* **Cause:** Strict host key validation requires confirmation of any new key fingerprint, or a server reinstall generated new identity keys.
* **Solution:** Verify the new fingerprint independently (for example directly on the server console). Then forget the server's old key under "Server keys", click Test Connection and confirm the new key. As long as the old key is stored, the new one cannot be confirmed.

## 12. Locked out / lost password or 2FA
* **Symptom:** Access to the administrative owner account is completely blocked due to forgotten credentials and lost authenticator codes.
* **Cause:** Login credentials and the set of ten emergency recovery codes are no longer accessible.
* **Solution:** In the folder with the Compose file on the Docker host: `docker compose exec nodvard-deck python -m nodvard_deck.admin list-users` shows the user names; then `docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password <username>` or `docker compose exec nodvard-deck python -m nodvard_deck.admin disable-2fa <username>`. The commands run inside the container, not on the host.

## 13. Rescue mode active / dashboard does not start
* **Symptom:** Following an update, the browser displays a white rescue page returning status 503 instead of the expected application.
* **Cause:** A database schema migration failed, storage space was insufficient for the pre-migration backup, or the database is newer than the container image.
* **Solution:** Obtain the rescue code from the container log and log into the rescue page. Out of disk space: free up space and choose "Retry". Failed migration: start the previous image version (reset the tag in the Compose file, `docker compose up -d`). The "Restore pre-update state" button only exists when the data comes from a newer version and a rollback copy is available.

## 14. File permissions on NAS `/app/data` denied
* **Symptom:** The container crashes on startup with a log entry stating that ownership on `/app/data` could not be set.
* **Cause:** A bind-mounted folder on a network share or NAS access control list denies write and ownership permissions to container user UID 1000.
* **Solution:** Switch to the standard `nodvard-deck-data` Docker volume or adjust file ownership and permissions on the host directory to allow UID 1000 write access.

## 15. Server unreachable / connection timeout
* **Symptom:** The terminal, file browser, and background metrics report a timeout, connection drop, or missing network route.
* **Cause:** The target device is turned off, a firewall blocks the configured SSH port, or the stored IP address contains a typo.
* **Solution:** Check the host address under Servers & Access and verify that the target system is reachable and listening on port 22.

## 16. Proxmox certificate rejected
* **Symptom:** Proxmox communication fails with an error indicating that the server certificate is not accepted or changed.
* **Cause:** Proxmox generates a self-signed TLS certificate whose fingerprint has not been approved within Nodvard Deck.
* **Solution:** Navigate to the connections tab in the Proxmox extension, open the certificate dialog, and confirm the displayed fingerprint.

## 17. Rate limit active after repeated attempts
* **Symptom:** Login or code input gets temporarily blocked with an error warning about too many attempts or concurrent sessions.
* **Cause:** Internal brute-force protection throttles repeated failed attempts for setup codes, passwords, or TOTP codes.
* **Solution:** Wait for the displayed waiting time before trying again. After many wrong codes within 24 hours an additional daily lock applies, so even a correct app code only works the next day. A recovery code still lets you in.

## 18. Backup restore fails or container stays down
* **Symptom:** After triggering a backup restore, the container remains stopped or the file upload gets rejected.
* **Cause:** The Docker container lacks the required `restart: unless-stopped` directive, or the backup archive was produced on a newer application version.
* **Solution:** Start the container manually from your compose folder and ensure the running image version matches or exceeds the backup version.

## 19. Ubuntu updates held back or delayed by phasing
* **Symptom:** Packages in the Nodvard Shield update manager appear permanently as delayed or held back and cannot be installed.
* **Cause:** Ubuntu delivers certain packages in staggered phases, or apt requires the removal of obsolete packages before applying the upgrade.
* **Solution:** Phasing: wait. Held-back dependencies: run `sudo apt full-upgrade` on the server and review the listed changes. Explicit hold: `apt-mark showhold` lists it; if unintended, `sudo apt-mark unhold <package>` lifts it. A `full-upgrade` does not lift such a hold.

## 20. Outdated mobile app during account security updates
* **Symptom:** Attempting to update a password or modify two-factor settings from a mobile app results in a rejection error.
* **Cause:** Older mobile app builds do not prompt for the mandatory secondary verification code required for security modifications.
* **Solution:** Update the mobile application to the latest version or make the account adjustments directly in a desktop browser session.
