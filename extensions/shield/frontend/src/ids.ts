/** Kennung von Nodvard Shield; alle Aufrufe bauen ihre Adresse daraus. Der Server nimmt zusätzlich die alte Kennung
 * `nexus-soc` an, damit offene Seiten und gespeicherte Links weiter funktionieren. */
export const EXT_ID = "shield";

/** Adressen der Erweiterung unter `/api/v1` (`authedFetch` setzt den Anfang davor). */
export const EXT_API = `/ext/${EXT_ID}`;
