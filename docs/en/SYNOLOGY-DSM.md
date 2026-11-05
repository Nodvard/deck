# Synology DSM in Nodvard Deck

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.
>
> Deutsche Fassung: [../15-SYNOLOGY-DSM.md](../15-SYNOLOGY-DSM.md)

The module is still in **beta**. The "Synology DSM" page shows, for one or several Synology devices, model, DSM
version, load, temperature, storage pools, volumes, drives (with SMART), the Hyper Backup tasks and whether a DSM update
is available. Nodvard Deck only reads and changes nothing on the Synology. It asks DSM which version of each interface to
use, so DSM 7.1 to 7.4 work, also side by side. So far this has been tried against recreated DSM answers; feedback from
real devices helps.

It is switched on like any other module ([first setup, section 4](FIRST-SETUP.md#4-enable-extensions)).

## Setting up

**Account:** Create a separate DSM account just for Nodvard Deck (Control Panel → User & Group) and enter it in the
module's settings; the password goes into the vault. To be honest: DSM only hands out storage, load, updates and
Hyper Backup to accounts in the **"administrators"** group. An account outside that group works, but the page then
only shows what it may read and says for each part that rights are missing. Since the account is an administrator,
give it a long password of its own that you use nowhere else.

**Two-factor does not work:** Nodvard Deck cannot use an account with two-factor sign-in, it does not have the code from
the app; the page then says „Dieses Konto verlangt Zwei-Faktor“ (this account requires two-factor). So use the separate
account without two-factor. If DSM enforces two-factor for all administrators, that includes this account; then limit
the requirement to specific accounts in DSM's security settings.

**Certificate:** DSM often has a self-signed certificate. Then, in your own network, switch on „Selbstsigniertes
Zertifikat erlauben“ (allow self-signed certificate), or better enter the certificate's fingerprint under "Advanced"
(only with https://): Nodvard Deck then only talks to exactly that certificate.

## Several devices

**Several devices:** Under Settings → Extensions → Synology DSM, enter each device on its own (up to ten): with a short
name (e.g. "ds920" or "keller"), address, its own account, its own password in the vault and its own certificate
settings. The short name appears in notifications and on the tiles so you can tell the devices apart; do not change it
after setting up, because the password belongs to the name and would have to be entered again. The page shows a picker
of all devices with their state at the top. If a device cannot be queried, its reason and "Try again" („Erneut
versuchen“) show up at that device and the others are left alone. On the home page each device has its three tiles
(state, storage, Hyper Backup), with the short name in the title when there are several; new tiles show up under
"Manage widgets" („Widgets verwalten“). Notifications are separate too: with several devices their title names the short
name (with a single device, the model), and a problem or all-clear on one device does not touch the other. If a short
name appears twice (upper and lower case do not matter), Nodvard Deck queries none of those entries. If the spelling is
exactly the same, a password stored for that name is deleted when you save (after confirmation), so it never ends up at
another entry's address, and a new one is only accepted once one of the entries has been renamed and saved. If the names
differ only in upper and lower case, the password stays with the existing entry but is not used until one of the names
is changed.

**From one device to several:** If you already set the module up with one device, you enter nothing again. On the first
start with this version the existing device is adopted as the device „synology“: address, account, certificate
settings, the password (copied in the vault to „synology-password:synology“), the tiles on the home page and the state
of the notifications. Problems that were already reported therefore do not come again. The old single fields and the
old vault entry „synology-password“ stay as they were, so a step back to the older version still finds the device. As
long as the device „synology“ is in the list, the old entry stays in the vault; it belongs to the module and cannot be
deleted there on its own. If you later remove the device „synology“ or rename it, Nodvard Deck deletes the old entry
with it, and there is no way back to the older version after that. From now on you enter a new password at the device
„synology“. More devices simply get added.

## Wrong password

**Wrong password:** DSM blocks addresses automatically after several failed attempts. After a rejected sign-in Nodvard
Deck therefore does not ask again for 30 minutes, unless you press "Test connection" („Verbindung testen“), which lifts
the pause of all devices, or change that device's address, account or certificate settings. The pause applies only to
the device whose sign-in was rejected; the other devices are still queried. Saving other settings, or adding or removing
a device, does not change the pause. If you only replaced the password in the vault, press "Test connection".

## Notifications

**Notifications:** Every five minutes Nodvard Deck checks each device and reports when a storage pool or volume is not okay (e.g.
„Eingeschränkt“, degraded, after a drive failure), a drive warns (SMART, bad sectors, remaining life), the device or a
drive gets too hot, or Hyper Backup fails or is overdue. The message shows under "Needs attention" („Braucht
Aufmerksamkeit“) and reaches your phone through the configured channels; once everything is fine again, an all-clear
follows. Only changes are reported, not every check, and once more when it gets worse (e.g. from „Eingeschränkt“ to
„Ausgefallen“, crashed). A temperature only after it lasts 10 to 15 minutes (drive limit
under "Advanced", default 55 °C for hard drives, 65 °C for SSDs, 70 °C for NVMe). Repairing, expanding or scrubbing a
pool or volume does not count as a problem, nor does a state Nodvard Deck does not know; if it was reported before,
the all-clear only comes once it is „Normal“ again. While a pool is not „Normal“, its volumes report nothing of their
own. If a volume crashes, its pool's message names it when the pool has a message of its own (such as „Eingeschränkt“,
degraded) and becomes urgent; without a pool message (for example during a repair) the volume reports itself. A new DSM update arrives once per version as a quiet hint (level "info", not under
"Needs attention"). Both can be switched off in the module's settings. If the Synology cannot be reached or the
sign-in is rejected, the watch reports nothing new; the page and the tile show that.

## Hyper Backup

**Hyper Backup:** Each task shows its result, last and next run, in the Synology's local time. Overdue means: a
scheduled run has been missing for more than six hours, or there has been no successful run for two intervals between
runs plus six hours (54 hours for a daily backup). This is measured with the Synology's clock; if DSM does not provide
it, 14 extra hours allow for a different time zone. A backup or integrity check that is running is not overdue; during
the check the page shows „Integritätsprüfung läuft“ (integrity check running), but a failed last run stays visible. If
Hyper Backup is not installed, a quiet hint says so and the rest of the page stays. Nodvard Deck reads up to 64 tasks;
if Hyper Backup has more, the page says so. Hyper Backup gets 25 seconds for the list and the states together, counted
from the start of the query. If the list does not arrive in time, Hyper Backup shows „Hyper Backup hat nicht
rechtzeitig geantwortet“ (did not answer in time); if not all states arrive, the remaining tasks show „Stand
unbekannt“ (state unknown), with neither a message nor an all-clear. In both cases the rest of the page stays. Not yet
confirmed on real devices (beta): that DSM reports a task that is not backing up with the state `status: none`. If a
task keeps showing „Läuft gerade“ (running) although nothing runs, please let us know. Likewise, that the state
`error_detect` is the running integrity check comes from a user report, not from a description by Synology; if a task
shows „Integritätsprüfung läuft“ for days, please let us know as well.
