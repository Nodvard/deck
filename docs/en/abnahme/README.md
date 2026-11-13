# Acceptance Checklists

This directory links to the acceptance checklists for testing extensions on real devices in the local network. Each checklist provides roughly ten test points covering setup, normal operation, and failure modes.

The full checklists are in German, matching the primary language of the Nodvard Deck interface.

## Available Checklists

* [Pi-hole Acceptance Checklist](../../abnahme/ABNAHME-PIHOLE.md):
  Tests for Pi-hole v6 on hardware or container. Covers initial unconfigured state, URL normalization, vault password storage, 24-hour DNS statistics, blocking pauses (5, 15, and 60 minutes), resumption of blocking, top client rankings, gravity list updates (with a 10-minute timeout and 2,000-character log limit), domain allow/block validation, 30-second brute-force protection pause on failed authentication, timeouts, HTTP redirects, and detection of legacy Pi-hole v5.

* [Plex Acceptance Checklist](../../abnahme/ABNAHME-PLEX.md):
  Tests for Plex Media Server. Covers read-only operation and local network communication, vault token storage, server identification and version display, active streams (up to 50 sessions), playback method detection (direct play, direct stream, transcode), progress bars, library counters (up to 30 libraries, 4 parallel requests), recently added media, isolated partial failures, 5-minute protection pause on rejected tokens, TLS certificate fingerprint pinning, timeouts, and response size guards.

* [Synology DSM Acceptance Checklist](../../abnahme/ABNAHME-SYNOLOGY.md):
  Tests for Synology DiskStation and RackStation systems under DSM 7.x. Covers multi-device tab navigation (up to 10 devices), API discovery and session token redaction in logs, hardware metrics and DSM updates (including the 90 percent warning threshold on CPU), storage pools and RAID types, volume capacity warnings (90 percent threshold), drive SMART status and temperature thresholds (55 °C for HDD, 65 °C for SSD, 70 °C for NVMe), Hyper Backup monitoring (25-second timeout, 6-hour overdue detection), 5-minute background watcher with temperature hysteresis, 30-minute auto-block backoff, port confusion checks (HTTP 5000 vs HTTPS 5001), and TLS certificate handling.

## Structure of Test Points

Each test point in the checklists contains three parts:
1. **Step (Schritt):** The action to perform on the device or in the web interface.
2. **Expected Behavior (Erwartetes Verhalten):** What Nodvard Deck displays and how the backend handles the request.
3. **Failure Mode (Fehlerfall):** Signs of failure, error codes, and symptoms to watch out for.

All dynamic values (such as query counts, library sizes, or device names) are examples, and your personal values will differ. Fixed values defined in code (such as pause durations, timeouts, and warning thresholds) are marked accordingly.
