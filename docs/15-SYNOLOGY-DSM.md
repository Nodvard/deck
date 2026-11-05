# Synology DSM in Nodvard Deck

> English version: [en/SYNOLOGY-DSM.md](en/SYNOLOGY-DSM.md)

Das Modul ist noch **Beta**. Die Seite „Synology DSM“ zeigt von einer oder mehreren Synology Modell, DSM-Version,
Auslastung, Temperatur, Speicherpools, Volumes, Festplatten (mit SMART), die Aufgaben von Hyper Backup und ob ein
DSM-Update bereitsteht. Nodvard Deck liest nur und ändert an der Synology nichts. Welche Version jeder Schnittstelle es
nimmt, fragt es DSM vorher; so laufen DSM 7.1 bis 7.4, auch nebeneinander. Erprobt ist das bisher mit nachgebauten
Antworten von DSM, Rückmeldungen von echten Geräten helfen.

Eingeschaltet wird es wie jedes Modul ([Erst-Einrichtung, Abschnitt 4](11-ERST-EINRICHTUNG.md#4-erweiterungen-einschalten)).

## Einrichten

**Konto:** Lege in DSM ein eigenes Konto nur für Nodvard Deck an (Systemsteuerung → Benutzer und Gruppe) und trag es
in den Einstellungen des Moduls ein, das Passwort kommt in den Tresor. Ehrlich gesagt: Speicher, Auslastung, Updates
und Hyper Backup gibt DSM nur an Konten in der Gruppe **„administrators“** heraus. Ein Konto ohne diese Gruppe
funktioniert, die Seite zeigt dann aber nur, was es lesen darf, und sagt bei jedem Teil, dass Rechte fehlen. Weil das
Konto damit Administrator ist, gib ihm ein langes, eigenes Passwort, das du sonst nirgends nutzt.

**Zwei-Faktor geht nicht:** Ein Konto mit Zwei-Faktor-Anmeldung kann Nodvard Deck nicht nutzen, es kennt den Code aus
der App nicht; die Seite sagt dann „Dieses Konto verlangt Zwei-Faktor“. Nimm darum das eigene Konto ohne Zwei-Faktor.
Verlangt DSM Zwei-Faktor für alle Administratoren, gilt das auch für dieses Konto; dann beschränke die Pflicht in den
Sicherheitseinstellungen von DSM auf bestimmte Konten.

**Zertifikat:** DSM hat oft ein selbst erstelltes Zertifikat. Dann im eigenen Netz „Selbstsigniertes Zertifikat
erlauben“ einschalten, oder besser unter „Erweitert“ den Fingerabdruck des Zertifikats eintragen (nur mit https://):
Dann spricht Nodvard Deck nur mit genau diesem Zertifikat.

## Mehrere Geräte

**Mehrere Geräte:** Trag unter Einstellungen → Erweiterungen → Synology DSM jedes Gerät einzeln ein (bis zu zehn): mit
Kurzname (z. B. „ds920“ oder „keller“), Adresse, eigenem Konto, eigenem Passwort im Tresor und eigenen Angaben zum
Zertifikat. Der Kurzname steht in Meldungen und auf den Kacheln, damit man die Geräte unterscheidet; ändere ihn nach dem
Einrichten nicht mehr, denn das Passwort gehört zum Namen und müsste neu eingetragen werden. Die Seite zeigt oben eine
Auswahl aller Geräte mit ihrem Zustand. Lässt sich ein Gerät nicht abfragen, steht bei ihm der Grund und „Erneut
versuchen“, die anderen bleiben davon unberührt. Auf der Startseite hat jedes Gerät seine drei Kacheln (Zustand,
Speicher, Hyper Backup), bei mehreren mit dem Kurznamen im Titel; neue Kacheln erscheinen unter „Widgets verwalten“. Auch
die Meldungen sind getrennt: Ihr Titel nennt bei mehreren Geräten den Kurznamen (bei einem einzigen das Modell), und
ein Problem oder eine Entwarnung des einen Geräts berührt das andere nicht. Kommt ein Kurzname doppelt vor (Groß- und
Kleinschreibung zählt nicht), fragt Nodvard Deck keinen dieser Einträge ab. Bei genau gleicher Schreibweise wird ein dazu
hinterlegtes Passwort beim Speichern gelöscht (nach Rückfrage), damit es nie bei der Adresse eines anderen Eintrags
landet, und ein neues nimmt Nodvard Deck erst an, wenn einer der Einträge umbenannt und gespeichert ist. Unterscheiden
sich die Namen nur in Groß- und Kleinschreibung, bleibt das Passwort beim bisherigen Eintrag stehen, wird aber nicht
benutzt, bis einer der Namen geändert ist.

**Von einem Gerät auf mehrere:** Wer das Modul schon mit einem Gerät eingerichtet hat, trägt nichts neu ein. Beim ersten
Start mit dieser Version wird das bisherige Gerät als Gerät „synology“ übernommen: Adresse, Konto, Angaben zum
Zertifikat, das Passwort (als Kopie im Tresor unter „synology-password:synology“), die Kacheln auf der Startseite und der
Stand der Meldungen. Schon gemeldete Probleme kommen darum nicht noch einmal. Die alten Einzelfelder und der alte
Tresor-Eintrag „synology-password“ bleiben unverändert stehen, damit ein Zurück auf die ältere Version das Gerät
findet. Solange das Gerät „synology“ in der Liste steht, bleibt der alte Eintrag im Tresor; er gehört dem Modul und
lässt sich dort nicht einzeln löschen. Entfernst du das Gerät „synology“ später oder benennst es um, löscht Nodvard Deck
den alten Eintrag gleich mit, danach führt kein Weg mehr zurück auf die ältere Version. Ein neues Passwort trägst du ab
jetzt beim Gerät „synology“ ein. Weitere Geräte kommen einfach dazu.

## Falsches Passwort

**Falsches Passwort:** DSM sperrt Adressen nach mehreren Fehlversuchen automatisch. Nach einer abgelehnten Anmeldung
fragt Nodvard Deck darum 30 Minuten lang nicht erneut, außer du drückst „Verbindung testen“ (das hebt die Pause aller
Geräte auf) oder änderst Adresse, Konto oder Zertifikat-Angaben dieses Geräts. Die Pause gilt nur für das Gerät, bei dem
die Anmeldung abgelehnt wurde; die anderen Geräte werden weiter abgefragt. Andere Einstellungen zu speichern oder ein
Gerät hinzuzufügen oder zu entfernen ändert nichts an der Pause. Hast du nur das Passwort im Tresor ersetzt, drücke
„Verbindung testen“.

## Meldungen

**Meldungen:** Alle fünf Minuten sieht Nodvard Deck bei jedem Gerät nach und meldet sich, wenn ein Speicherpool oder Volume nicht in
Ordnung ist (etwa „Eingeschränkt“ nach einem Plattenausfall), eine Festplatte warnt (SMART, defekte Sektoren,
Restlebensdauer), das Gerät oder ein Laufwerk zu warm wird oder Hyper Backup fehlschlägt oder überfällig ist. Die
Meldung steht unter „Braucht Aufmerksamkeit“ und kommt über die eingerichteten Kanäle aufs Handy; ist alles wieder gut,
kommt eine Entwarnung. Gemeldet wird nur ein Wechsel, nicht jede Prüfung, und noch einmal, wenn es schlimmer wird (etwa
von „Eingeschränkt“ zu „Ausgefallen“). Eine Temperatur erst, wenn sie 10 bis 15
Minuten anhält (Grenze für Laufwerke unter „Erweitert“, Vorgabe 55 °C für Festplatten, 65 °C für SSD, 70 °C für NVMe).
Reparieren, Erweitern oder Prüfen eines Pools oder Volumes gilt nicht als Problem, ebenso ein Zustand, den Nodvard Deck
nicht kennt; war er vorher gemeldet, kommt die Entwarnung aber erst, wenn er wieder „Normal“ ist. Solange ein Pool
nicht „Normal“ ist, melden seine Volumes nichts Eigenes. Fällt ein Volume aus, steht das in der Meldung seines Pools,
wenn der Pool selbst eine hat (etwa „Eingeschränkt“), und diese Meldung wird dringend; ohne Meldung des Pools (etwa
während einer Reparatur) meldet sich das Volume selbst. Ein neues DSM-Update kommt einmal je Version als
ruhiger Hinweis (Stufe „Info“, nicht unter „Braucht Aufmerksamkeit“). Beides lässt sich in den Einstellungen des Moduls
abschalten. Ist die Synology nicht erreichbar oder wird die Anmeldung abgelehnt, meldet der Wächter nichts Neues; das
zeigen die Seite und die Kachel.

## Hyper Backup

**Hyper Backup:** Je Aufgabe stehen Ergebnis, letzter und nächster Lauf da, in der Ortszeit der Synology. Überfällig
heißt: Ein geplanter Lauf ist seit mehr als sechs Stunden ausgeblieben, oder es gab seit zwei Abständen zwischen den
Läufen und sechs Stunden Puffer (bei täglicher Sicherung also seit 54 Stunden) keinen erfolgreichen mehr. Gezählt wird
mit der Uhr der Synology; liefert DSM sie nicht, kommen 14 Stunden Puffer für eine andere Zeitzone dazu. Läuft eine
Sicherung oder die Integritätsprüfung gerade, ist sie nicht überfällig; während der Prüfung steht „Integritätsprüfung
läuft“ da, ein Fehler des letzten Laufs bleibt aber sichtbar. Ist Hyper Backup nicht installiert, steht dort ein
ruhiger Hinweis, der Rest der Seite bleibt. Nodvard Deck fragt bis zu 64 Aufgaben ab; hat Hyper Backup mehr, sagt die
Seite das. Für Liste und Stände hat Hyper Backup zusammen 25 Sekunden, gerechnet ab Beginn der Abfrage. Kommt die
Liste nicht rechtzeitig, steht bei Hyper Backup „Hyper Backup hat nicht rechtzeitig geantwortet“; sind nicht alle
Stände da, stehen die übrigen Aufgaben mit „Stand unbekannt“ da, ohne Meldung und ohne Entwarnung. Der Rest der Seite
bleibt in beiden Fällen. Noch nicht an echten Geräten bestätigt (Beta): dass DSM eine Aufgabe, die gerade nicht
sichert, mit dem Zustand `status: none` meldet. Steht eine Aufgabe dauerhaft auf „Läuft gerade“, obwohl nichts läuft,
gib bitte Bescheid. Ebenso stammt aus einer Rückmeldung, nicht aus einer Beschreibung von Synology, dass der Zustand
`error_detect` die laufende Integritätsprüfung ist; steht eine Aufgabe tagelang auf „Integritätsprüfung läuft“, gib
bitte ebenfalls Bescheid.
