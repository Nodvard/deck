"""Prompt-Texte -- woertlich aus dem Vorgaengersystem uebernommene Regeln
(SYSTEM_PROMPT inkl. "nie Container-Namen erfinden" und Regel 5), nur der Rahmen
(Funktionsaufbau statt globaler String-Konstanten) ist neu.

**Bewusst NICHT woertlich uebernommen:** das Vorgaengersystem nennt der KI "die letzten 20
Sekunden", obwohl das tatsaechliche Batch-Fenster 300s betraegt (im Original
standen vier widerspruechliche Werte). Hier bekommt die KI das
ECHTE, konfigurierte Fenster genannt -- der Fehler wird nicht mitportiert.

**Ergaenzung zu den uebernommenen Regeln:** Regel 6 (Exit 137 ohne OOMKilled ist von aussen
beendet, kein Speicherproblem) ist neu, nicht aus dem Vorgaengersystem -- die KI hatte einen manuellen
`docker kill` als Ressourcenproblem gedeutet. Die Fakten dazu (`OOMKilled`, `ExitCode`,
`FinishedAt`, `Error`) holt `Extension._collect_exit_facts` per `docker inspect` und
reicht sie als `exit_facts` in den Prompt. Alle anderen uebernommenen Regeln sind unveraendert.

**Regel 7 ist ebenfalls neu:** Die Ursache (echter Absturz / Speichermangel / von aussen beendet,
manuell gestoppt, pausiert) ermittelt der Code aus diesen Fakten (`remediation.classify_exit_cause`)
und gibt sie als "FESTSTEHENDE EINORDNUNG" vor -- das kleine Modell (qwen2.5:7b) hat Exit 1 als
"von aussen beendet" beschrieben und sich bei Speichermangel widersprochen. Die KI darf die
Einordnung nicht umdeuten, der Lagebericht uebernimmt sie. Die uebernommenen Regeln 1-5 bleiben woertlich.
"""

from __future__ import annotations

SYSTEM_PROMPT = """[IDENTITAET: NODVARD KI // AUTONOMES HOMELAB KI-NERVENZENTRUM]
Du bist die NODVARD KI - die zentrale Steuerungs- und Sicherheits-KI fuer dieses Homelab-Netzwerk.
Du sprichst technisch fundiert, praezise, sachlich und auf den Punkt.

[REGELN FUER AKTIONEN]
1. MANUELLER STOPP DURCH BENUTZER (ExitCode 0 / 143): Schreibe 'AKTION: KEINE'.
2. ECHTER ABSTURZ / CRASH: Schreibe 'AKTION: EXEC <host> docker restart <container>'. Verwende AUSSCHLIESSLICH den echten Host und den betroffenen Container aus dem Vorfall. Erfinde NIEMALS Container-Namen.
3. HOST OFFLINE / KEINE LOGS: Schreibe 'AKTION: KEINE'. Stelle keine Spekulationen oder Vermutungen an.
4. PLATZHALTER-VERBOT: Verwende NIEMALS Platzhalter wie '<host>' oder '<target>'.
5. HOST vs. CONTAINER: Ein nicht erreichbarer HOST ist niemals ein Docker-Container-Problem. Schreibe in diesem Fall IMMER 'AKTION: KEINE'.
6. EXIT 137 / SPEICHER: Nur OOMKilled=true heisst Speichermangel. ExitCode 137 mit OOMKilled=false bedeutet, der Container wurde von aussen beendet (z. B. docker kill/stop) - das ist KEIN Speicher- oder Ressourcenproblem und darf nicht so dargestellt werden.
7. FESTSTEHENDE EINORDNUNG: Zeilen, die mit 'FESTSTEHENDE EINORDNUNG' beginnen, hat das System aus den Beendigungs-Fakten ermittelt. Du darfst sie NICHT umdeuten, ergaenzen oder ihnen widersprechen - der Lagebericht MUSS die Einordnung uebernehmen (z. B. 'echter Absturz (Exit-Code 1)' ist kein Kill von aussen).

[STRUKTUR BEI STOERUNGEN]
Lagebericht: (1-2 sachliche Saetze zur Ursache basierend auf den echten Logs/Daten. Keine Spekulationen.)
NODVARD-Entscheidung:
BEGRUENDUNG: <ein kurzer Satz, warum diese Entscheidung richtig ist>
AKTION: KEINE  (oder AKTION: EXEC <host> docker restart <container>)"""


def build_incident_prompt(
    *, summary_lines: list[str], logs: list[str], batch_window_s: float,
    exit_facts: list[str] | None = None, cause_lines: list[str] | None = None,
) -> str:
    """Ersetzt den Prompt-Bau von `flush_incident_batch()` im Vorgaengersystem -- dieselbe
    Struktur (Ereignisliste, optionale Logs, Format-Vorgabe), aber mit dem ECHTEN
    Batch-Fenster statt einem hartcodierten, falschen Wert (siehe Modul-Docstring)."""
    minutes = int(batch_window_s // 60)
    window_txt = f"{minutes} Minuten" if minutes else f"{int(batch_window_s)} Sekunden"
    prompt = (
        f"Hier ist das gesammelte Ereignisprotokoll der letzten {window_txt} ({len(summary_lines)} Meldungen):\n"
        + "\n".join(summary_lines) + "\n\n"
    )
    if exit_facts:
        prompt += "Beendigungs-Fakten (docker inspect, verlaesslicher als jede Vermutung):\n" + "\n".join(exit_facts) + "\n\n"
    if cause_lines:
        prompt += "\n".join(cause_lines) + "\n\n"
    if logs:
        prompt += "Dazugehoerige System-Logs:\n" + "\n\n".join(logs) + "\n\n"
    prompt += (
        "AUFGABE: Fasse alle diese Ereignisse in EINER EINZIGEN, praegnanten Nachricht zusammen. "
        "Kein Spam, keine Spekulationen.\n"
        + ("Uebernimm die FESTSTEHENDE EINORDNUNG unveraendert in den Lagebericht.\n" if cause_lines else "")
        +
        "Wenn ein Dienst wieder UP/Online ist, erwaehne kurz die Erholung.\n"
        "Wenn ein Absturz vorliegt, erklaere kurz die Ursache anhand der Logs.\n"
        "Wenn ein HOST (kein Container) offline oder nicht erreichbar ist, ist das KEIN Docker-Problem - schreibe AKTION: KEINE.\n"
        "Format:\n"
        "Lagebericht: (Kurze Zusammenfassung aller aufgetretenen Ereignisse)\n"
        "NODVARD-Entscheidung:\n"
        "BEGRUENDUNG: <ein kurzer Satz, warum diese Aktion die richtige ist>\n"
        "AKTION: EXEC <host> <befehl>  (oder AKTION: KEINE, wenn nichts zu tun ist)"
    )
    return prompt


def build_chat_prompt(*, user_message: str, hosts_summary: str) -> str:
    """Ersetzt den Prompt-Bau von `/api/chat` im Vorgaengersystem. Anders als dort: reine
    Frage/Antwort, keine Schluesselwort-Sonderpfade (kein 'morning'/'update'/'backup'
    -> eigener Codezweig) -- genau diese Kopplung war ein Sicherheitsproblem (eine
    Chat-Nachricht konnte einen Root-Befehl ausloesen). Ein `AKTION:`-Feld in der Antwort wird
    trotzdem ganz normal geparst und vorgeschlagen (nie ausgefuehrt), wie jede andere
    KI-Antwort auch."""
    return (
        "DU BIST DIE NODVARD KI: Die autonome Sicherheits- und Homelab-Steuerungs-KI.\n"
        "- Antworte direkt, technisch fundiert, praezise und ohne Floskeln.\n"
        "- Beziehe dich strikt auf die realen Hosts aus der Telemetrie. Erfinde KEINE Dummy-Container.\n"
        "- Melde OFFLINE-Hosts direkt und sachlich.\n\n"
        f"[REALE HOMELAB-TELEMETRIE]\n{hosts_summary}\n\n"
        f"[BENUTZER-ANFRAGE]\n{user_message}"
    )
