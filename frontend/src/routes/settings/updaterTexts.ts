/**
 * Texte zum Update-Helfer (Einstellungen → System, Karten „Updates“ und „Kopien vor Updates“).
 *
 * Die API liefert nur feste Codes, nie Freitext: Gründe für „nicht bereit“ (Vorprüfung), Ablehnungen eines Auftrags,
 * Fehler im Ablauf, Schritte und Ergebnisse. Die Sätze dazu stehen hier, in der Du-Form: was los ist und in einem Satz,
 * was du tun kannst. Quelle der Codes ist der Helfer selbst (`deploy/updater/nodvard_deck_updater/policy.py`, gleich
 * `deploy/updater/tests/vectors/protocol.json`), dazu die wenigen der Ansicht im Dashboard
 * (`backend/src/nodvard_deck/services/update_helper.py`, `core/updater_client.py`).
 *
 * Kommt im Helfer ein Code dazu, schlagen `updaterTexts.test.ts` (liest protocol.json und status.json) und
 * `backend/tests/test_updater_texts.py` (liest policy.py) fehl, bis er hier einen Text hat. Ein unbekannter Code eines
 * neueren Helfers bekommt bis dahin `fallbackText`.
 */

export type HelperOutcome = "applied" | "reverted" | "rolled_back" | "refused" | "aborted" | "failed_manual" | "external_change";
export type HelperAction = "update" | "rollback";
export type PresenceReason = "missing" | "stale" | "unsafe" | "proto" | "invalid";

/** Wo die Einrichtung des Helfers beschrieben ist (Abschnitt „Update-Helfer“ in deploy/README.md). */
export const HELPER_GUIDE_URL = "https://github.com/nodvard/deck/blob/main/deploy/README.md#update-helfer";
export const HELPER_GUIDE_LABEL = "Anleitung: deploy/README.md, Abschnitt „Update-Helfer“";
/** Kurzer Satz in der Zeile „Update-Helfer: nicht eingerichtet.“, dahinter steht der Link zur Anleitung. */
export const HELPER_MISSING_LEAD = "Mit ihm spielst du Updates hier per Knopf ein.";

/**
 * Warum der Helfer gar nicht da ist (`reason` in `GET /system/updates/helper`, `present: false`). Die Oberfläche nennt
 * davor schon den Zustand („Update-Helfer: antwortet nicht.“), der Text erklärt nur noch und sagt, was hilft.
 */
export const PRESENCE_TEXTS: Record<PresenceReason, string> = {
  missing: "Wie du ihn einrichtest, steht in deploy/README.md im Abschnitt „Update-Helfer“.",
  stale:
    "Sein letztes Lebenszeichen ist älter als 90 Sekunden. Sieh auf dem Server mit „docker compose ps“ nach, ob der Dienst „updater“ läuft, und starte ihn sonst mit „docker compose up -d updater“.",
  unsafe:
    "Besitzer oder Rechte des Ordners, über den Dashboard und Update-Helfer sprechen, stimmen nicht. Nimm dafür den Datenspeicher „nodvard-deck-updater“ aus der Compose-Datei der Anleitung, keinen eigenen Ordner.",
  proto:
    "Hol die passende Version des Helfers: „docker compose pull updater“ und danach „docker compose up -d updater“.",
  invalid:
    "Starte ihn neu („docker compose restart updater“); hilft das nicht, zeigt „docker compose logs updater“ den Grund.",
};

/** Gründe, die nur die Ansicht im Dashboard kennt (`ready_reason`). */
export const VIEW_REASON_TEXTS: Record<string, string> = {
  finishing: "Der Update-Helfer räumt nach dem letzten Vorgang noch auf. Gleich ist er wieder bereit.",
};

/**
 * Alle Codes des Helfers (`policy.CODES`): Einrichtung, Ziel (Vorprüfung), Auftrag (Ablehnung), Ablauf. Sie kommen als
 * `ready_reason`, als `code` eines Ergebnisses und als `helper_reason` einer Ablehnung des Dashboards.
 */
export const HELPER_CODE_TEXTS: Record<string, string> = {
  // Einrichtung
  channel_unsafe:
    "Der Update-Helfer hält den Ordner, über den er mit dem Dashboard spricht, für unsicher und nimmt keine Aufträge an. Nimm dafür den Datenspeicher „nodvard-deck-updater“ aus der Compose-Datei der Anleitung und starte den Helfer neu: „docker compose up -d updater“.",
  channel_cluttered:
    "Im Auftragsordner des Update-Helfers liegen Ordner, die dort nicht hingehören. Er arbeitet trotzdem; entferne sie bei Gelegenheit aus dem Datenspeicher „nodvard-deck-updater“ (Ordner „requests“).",
  state_unsafe:
    "Der eigene Speicher des Update-Helfers war beschädigt oder ist nicht sicher eingerichtet; zur Sicherheit nimmt er bis zu 24 Stunden keine Aufträge an. Geht es danach nicht wieder, zeigt „docker compose logs updater“ den Grund.",
  second_instance:
    "Es läuft ein zweiter Update-Helfer mit demselben Speicher, dieser hier wartet. Lass nur einen laufen („docker compose ps“ zeigt beide).",
  self_unknown:
    "Der Update-Helfer erkennt seinen eigenen Container nicht. Starte ihn mit der Compose-Datei aus der Anleitung neu: „docker compose up -d updater“.",
  not_compose:
    "Der Update-Helfer läuft nicht über Docker Compose. Er arbeitet nur als Dienst „updater“ in derselben Compose-Datei wie Nodvard Deck.",
  engine_unreachable:
    "Der Update-Helfer erreicht Docker nicht. Prüf, ob Docker läuft und der Docker-Socket wie in der Anleitung beim Helfer eingehängt ist.",
  engine_unsupported:
    "Der Update-Helfer versteht die Antworten von Docker nicht. Aktualisiere Docker und den Helfer („docker compose pull updater“).",
  api_too_old: "Docker ist für den Update-Helfer zu alt. Aktualisiere Docker auf eine aktuelle Version.",

  // Ziel: der Container von Nodvard Deck (Vorprüfung)
  no_target:
    "Der Update-Helfer findet den Container von Nodvard Deck nicht. Beide müssen in derselben Compose-Datei stehen, der Dienst heißt „nodvard-deck“ (sonst beim Helfer NODVARD_DECK_UPDATER_SERVICE setzen).",
  multiple_targets:
    "Es gibt mehr als einen Container für Nodvard Deck, etwa einen übrig gebliebenen von einem früheren Update. „docker compose ps -a“ zeigt alle; entferne den überzähligen.",
  target_not_running:
    "Nodvard Deck läuft gerade nicht richtig (gestoppt oder startet immer wieder neu). Der Update-Helfer wartet, bis es wieder läuft.",
  target_unhealthy:
    "Nodvard Deck meldet sich gerade nicht als gesund. Der Update-Helfer wartet; bleibt es so, zeigt „docker compose logs nodvard-deck“ den Grund.",
  no_healthcheck:
    "Für Nodvard Deck ist keine Gesundheitsprüfung eingerichtet. Übernimm den Abschnitt „healthcheck“ aus der Compose-Datei der Anleitung.",
  swarm:
    "Nodvard Deck läuft als Swarm-Dienst. Der Update-Helfer kann nur Docker Compose; aktualisiere mit „docker service update“.",
  foreign_image:
    "Nodvard Deck läuft nicht mit dem offiziellen Image ghcr.io/nodvard/deck. Der Update-Helfer aktualisiert nur dieses; nimm sonst die Anleitung unten.",
  pinned_version:
    "In der Compose-Datei steht eine feste Version von Nodvard Deck. Trag bei „image:“ ghcr.io/nodvard/deck:latest ein und führ „docker compose up -d“ aus, dann geht das Update per Knopf.",
  not_from_registry:
    "Das laufende Image von Nodvard Deck hat keinen Namen mehr unter ghcr.io/nodvard/deck, meist nach „docker compose pull“ ohne anschließendes „docker compose up -d“. Führ „docker compose up -d“ aus, danach ist der Update-Helfer wieder bereit.",
  no_version_label:
    "Das laufende Image nennt keine fertige Versionsnummer (etwa eine Vorabversion). Per Knopf geht es erst ab einer fertigen Version; bis dahin hilft die Anleitung unten.",
  version_too_old:
    "Die Version ist zu alt für den Update-Helfer (er braucht mindestens 0.7.0). Wechsle dieses eine Mal von Hand nach der Anleitung unten.",
  image_config_missing:
    "Docker liefert die Angaben zum Image von Nodvard Deck nicht vollständig. Aktualisiere Docker; bis dahin hilft die Anleitung unten.",
  auto_remove:
    "Der Container von Nodvard Deck wird beim Stoppen automatisch gelöscht („auto_remove“ bzw. „--rm“). Nimm das aus der Compose-Datei, sonst ginge beim Umschalten der alte Stand verloren.",
  custom_entrypoint:
    "In der Compose-Datei steht für Nodvard Deck ein eigener Startbefehl („entrypoint“ oder „command“). Nimm ihn heraus, sonst liefe beim Start die Kopie der Datenbank nicht.",
  unsafe_target:
    "Bei Nodvard Deck sind der Docker-Socket oder der Speicher des Update-Helfers eingehängt. Nimm diese Zeilen bei „nodvard-deck“ aus der Compose-Datei, sie gehören nur zum Helfer.",
  channel_missing_in_target:
    "Bei Nodvard Deck fehlt der Datenspeicher des Update-Helfers (/app/updater). Übernimm die Zeile aus der Compose-Datei der Anleitung und führ „docker compose up -d“ aus.",
  dependent_containers:
    "Ein anderer Container hängt direkt an Nodvard Deck („network_mode: container:…“ o. Ä.). Den kann der Update-Helfer nicht mit umziehen; aktualisiere von Hand nach der Anleitung unten.",
  macvlan:
    "Nodvard Deck hängt an einem macvlan- oder ipvlan-Netz. Das kann der Update-Helfer nicht übernehmen; aktualisiere von Hand nach der Anleitung unten.",
  network_unclear:
    "Die Netzwerk-Einstellungen von Nodvard Deck sind für den Update-Helfer nicht eindeutig. Aktualisiere von Hand nach der Anleitung unten; Einzelheiten zeigt „docker compose logs updater“.",
  unknown_field:
    "Der Container von Nodvard Deck hat eine Einstellung, die der Update-Helfer nicht sicher übernehmen kann. Aktualisiere von Hand nach der Anleitung unten; welche es ist, zeigt „docker compose logs updater“.",
  name_taken:
    "Es gibt schon einen Container mit „-previous“ am Namen von Nodvard Deck, wohl ein Rest eines früheren Updates. Prüf ihn mit „docker ps -a“ und entferne ihn, wenn Nodvard Deck richtig läuft.",

  // Auftrag (Ablehnung)
  bad_request:
    "Der Update-Helfer konnte den Auftrag nicht lesen. Versuch es noch einmal; klappt es wieder nicht, hol die aktuelle Version des Helfers („docker compose pull updater“).",
  expired: "Der Auftrag war zu alt, als der Update-Helfer ihn gelesen hat (er lief wohl gerade nicht). Versuch es noch einmal.",
  replay: "Diesen Auftrag hatte der Update-Helfer schon einmal und hat ihn nicht noch einmal ausgeführt. Versuch es mit einem neuen Klick.",
  busy: "Der Update-Helfer ist gerade mit einem anderen Vorgang beschäftigt. Warte, bis er fertig ist, und versuch es dann noch einmal.",
  rate_limited: "Der Update-Helfer nimmt höchstens einen Auftrag alle 10 Minuten an. Versuch es in ein paar Minuten noch einmal.",
  blocked_version:
    "Von dieser Version bist du vor Kurzem zurückgegangen. Der Update-Helfer spielt sie deshalb 24 Stunden lang nicht wieder ein.",
  not_newer: "Die gewünschte Version ist nicht neuer als die laufende. Such zuerst nach Updates und versuch es dann noch einmal.",
  tag_not_on_version:
    "Das Tag in der Compose-Datei zeigt nicht auf diese Version (noch nicht veröffentlicht, oder eine Reihe wie „:0.7“). Warte ein paar Minuten oder trag „:latest“ ein und versuch es noch einmal.",
  platform_mismatch:
    "Die neue Version gibt es nicht für die Bauart dieses Rechners (z. B. arm64 beim Raspberry Pi). Warte auf die nächste Version.",
  pull_failed:
    "Die neue Version ließ sich nicht herunterladen. Prüf die Internetverbindung und den freien Speicher des Servers und versuch es noch einmal.",
  no_previous:
    "Es gibt keine Version mehr, auf die der Update-Helfer zurückgehen kann (der Rückweg gilt 7 Tage). Wechsle von Hand nach der Anleitung unter „Updates“.",
  previous_mismatch:
    "Der Container wurde seit dem Update verändert (etwa von Hand neu erstellt). Den Rückweg per Knopf gibt es deshalb nicht mehr; wechsle von Hand nach der Anleitung unter „Updates“.",
  not_implemented:
    "Diese Aktion kennt der Update-Helfer in seiner Version noch nicht. Hol die aktuelle Version: „docker compose pull updater“ und „docker compose up -d updater“.",

  // Ablauf
  create_failed: "Der Container für die neue Version ließ sich nicht anlegen. Einzelheiten zeigt „docker compose logs updater“.",
  clone_mismatch:
    "Der neue Container hätte andere Einstellungen bekommen als der alte, deshalb hat der Update-Helfer abgebrochen. Einzelheiten zeigt „docker compose logs updater“.",
  stop_failed: "Die bisherige Version ließ sich nicht anhalten. Sieh mit „docker compose ps“ nach, ob Docker hängt, und versuch es dann noch einmal.",
  start_failed: "Die neue Version ließ sich nicht starten. Einzelheiten zeigt „docker compose logs updater“.",
  exited: "Die neue Version hat sich gleich nach dem Start wieder beendet. Versuch es mit der nächsten Version noch einmal.",
  restart_loop: "Die neue Version ist immer wieder neu gestartet. Versuch es mit der nächsten Version noch einmal.",
  rescue_page:
    "Die neue Version hat beim Start nur die Notseite gezeigt (etwa weil der Umbau der Datenbank scheiterte). Versuch es mit der nächsten Version noch einmal.",
  timeout:
    "Die neue Version ist in 15 Minuten nicht bereit geworden. Ist der Rechner gerade sehr ausgelastet, versuch es zu einer ruhigeren Zeit noch einmal.",
  external_change:
    "Während des Vorgangs wurde von außen etwas an den Containern geändert (etwa mit „docker compose up“). Prüf mit „docker compose ps“, welche Version jetzt läuft.",
  rollback_failed: "Die Version, die vorher lief, ist nach dem Zurückschalten nicht wieder gesund geworden.",
};

/** Für einen Code, den diese Oberfläche noch nicht kennt (neuerer Helfer). */
export function fallbackText(code: string): string {
  return `Der Update-Helfer meldet „${code}“. Was das genau heißt, zeigt „docker compose logs updater“.`;
}

/** Text zu einem Grund oder Code; Ansicht vor Helfer, unbekannte Codes allgemein. */
export function codeText(code: string | null | undefined): string | null {
  if (!code) return null;
  return VIEW_REASON_TEXTS[code] ?? HELPER_CODE_TEXTS[code] ?? fallbackText(code);
}

// ---------------------------------------------------------------------------
// Schritte (`busy.step`) und Fortschritt
// ---------------------------------------------------------------------------

/** Was gerade geschieht, je Schritt des Helfers (`policy.STEPS`). */
export const STEP_TEXTS: Record<string, string> = {
  begin: "Die neue Version wird heruntergeladen …",
  pulled: "Heruntergeladen. Der neue Container wird vorbereitet …",
  protected: "Die bisherige Version wird für den Rückweg gesichert …",
  renamed: "Der bisherige Container wird beiseitegestellt …",
  tagged: "Der neue Container wird vorbereitet …",
  creating: "Der neue Container wird angelegt …",
  created: "Der neue Container ist angelegt …",
  old_stopped: "Die bisherige Version wird angehalten …",
  started: "Die neue Version startet. Muss sie die Datenbank umbauen, legt sie vorher eine Kopie an; das kann ein paar Minuten dauern …",
  committed: "Geschafft. Der Update-Helfer räumt noch auf …",
};

/** Beim Rückweg ist die „neue“ Version die vorige: eigene Sätze, wo das einen Unterschied macht. */
const ROLLBACK_STEP_TEXTS: Record<string, string> = {
  begin: "Die vorige Version wird bereitgestellt …",
  protected: "Die Versionen werden gesichert …",
  started: "Die vorige Version startet …",
};

export function stepText(step: string | null | undefined, action: HelperAction): string {
  if (!step) return "Der Update-Helfer übernimmt den Auftrag …";
  if (action === "rollback" && ROLLBACK_STEP_TEXTS[step]) return ROLLBACK_STEP_TEXTS[step];
  return STEP_TEXTS[step] ?? "Der Update-Helfer arbeitet …";
}

/** Grobe Abschnitte für die Anzeige; jeder Schritt gehört zu genau einem. */
export const STAGES: { label: string; steps: string[] }[] = [
  { label: "Herunterladen", steps: ["begin"] },
  { label: "Vorbereiten", steps: ["pulled", "protected", "renamed", "tagged", "creating", "created"] },
  { label: "Umschalten", steps: ["old_stopped"] },
  { label: "Starten und prüfen", steps: ["started", "committed"] },
];

export function stageOf(step: string | null | undefined): number {
  if (!step) return -1;
  return STAGES.findIndex((stage) => stage.steps.includes(step));
}

// ---------------------------------------------------------------------------
// Ergebnisse
// ---------------------------------------------------------------------------

export interface ResultLike {
  action: HelperAction;
  from: string | null;
  to: string | null;
  outcome: HelperOutcome | string;
  code: string | null;
}

export type ResultTone = "good" | "warn" | "bad";

/** Kurzer Titel je Ergebnis (`policy.OUTCOMES`). */
export const OUTCOME_TITLES: Record<HelperOutcome, string> = {
  applied: "Update eingespielt",
  reverted: "Zurück auf die vorige Version",
  rolled_back: "Hat nicht geklappt, zurückgeschaltet",
  refused: "Vom Update-Helfer abgelehnt",
  aborted: "Abgebrochen, nichts verändert",
  failed_manual: "Bitte von Hand nachsehen",
  external_change: "Abgebrochen: von außen geändert",
};

export const OUTCOME_TONES: Record<HelperOutcome, ResultTone> = {
  applied: "good",
  reverted: "good",
  rolled_back: "warn",
  refused: "warn",
  aborted: "warn",
  failed_manual: "bad",
  external_change: "bad",
};

/** Was du nach `failed_manual` auf dem Server tun kannst (die Meldung des Dashboards verweist hierher). */
export const FAILED_MANUAL_STEPS: string[] = [
  "Öffne auf dem Server den Ordner mit der Compose-Datei.",
  "„docker compose ps“ zeigt, ob Nodvard Deck läuft und gesund ist („healthy“). Läuft es nicht, starte es mit „docker compose up -d“.",
  "Was genau schiefging, zeigt „docker compose logs updater“.",
  "Danach ist der Update-Helfer von selbst wieder bereit. Ist er es nicht, steht hier unter „Updates“, was noch fehlt.",
];

const version = (v: string | null | undefined, fallback: string) => (v ? `Version ${v}` : fallback);

/**
 * Nach einem gescheiterten Rückweg mit Daten: Ob die Datenbank schon zurückgesetzt war, weiß nur die Meldung des
 * Dashboards (`_DATA_TEXTS` in services/update_helper.py, nur bei „zurückgesetzt“ und „unklar“).
 */
export const ROLLBACK_DATA_HINT =
  "Ob deine Daten dabei schon auf den Stand vor dem Update zurückgesetzt wurden, steht in der Meldung dazu (Glocke); steht dort nichts dazu, sind sie unverändert.";

/**
 * Der ganze Satz zu einem Ergebnis, mit Versionen und dem Text zum Code. `dataRevert`: Der Rückweg sollte die Daten
 * mitnehmen (nur dann sagt die Meldung zu einem gescheiterten Rückweg etwas über die Daten).
 */
export function outcomeText(result: ResultLike, { dataRevert = false }: { dataRevert?: boolean } = {}): string {
  const rollback = result.action === "rollback";
  const reason = result.code ? ` ${codeText(result.code)}` : "";
  const to = result.to ?? "?";
  switch (result.outcome) {
    case "applied":
      return `${version(result.to, "Die neue Version")} läuft jetzt${result.from ? ` (vorher ${result.from})` : ""}.`;
    case "reverted":
      return `${version(result.to, "Die vorige Version")} läuft wieder${result.from ? ` (vorher ${result.from})` : ""}.`;
    case "rolled_back":
      return rollback
        ? `Der Rückweg auf ${to} hat nicht geklappt, ${version(result.from, "die bisherige Version")} läuft weiter.${reason}${dataRevert ? ` ${ROLLBACK_DATA_HINT}` : ""}`
        : `Das Update auf ${to} hat nicht geklappt, der Update-Helfer hat zurückgeschaltet: ${version(result.from, "die bisherige Version")} läuft wieder.${reason}`;
    case "refused":
      return `Der Update-Helfer hat ${rollback ? "den Rückweg" : "das Update"} abgelehnt, es wurde nichts verändert.${reason || " Versuch es noch einmal."}`;
    case "aborted":
      return `Der Update-Helfer hat abgebrochen, bevor ${rollback ? "die vorige" : "die neue"} Version gestartet wurde. Es ist alles wie vorher.${reason || " Versuch es noch einmal."}`;
    case "failed_manual":
      return `Der Update-Helfer konnte ${version(result.from, "die Version")}, die vorher lief, nicht wieder starten und hat danach nichts mehr verändert.`;
    case "external_change":
      return `Während des Vorgangs wurde von außen etwas an den Containern geändert. Der Update-Helfer hat deshalb nichts weiter getan. Prüf mit „docker compose ps“, welche Version jetzt läuft.`;
    default:
      return `Der Update-Helfer meldet „${result.outcome}“.${reason}`;
  }
}

export function outcomeTitle(outcome: string): string {
  return OUTCOME_TITLES[outcome as HelperOutcome] ?? "Vorgang beendet";
}

export function outcomeTone(outcome: string): ResultTone {
  return OUTCOME_TONES[outcome as HelperOutcome] ?? "warn";
}
