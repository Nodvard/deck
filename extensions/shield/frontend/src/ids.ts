/** Kennung von Nodvard Shield (bis 0.6 `nexus-soc`). Alle Aufrufe der Erweiterung bauen ihre Adresse daraus; die alte
 * Kennung nimmt der Server nur noch an, damit offene Seiten und gespeicherte Links weiter funktionieren. */
export const EXT_ID = "shield";

/** Adressen der Erweiterung unter `/api/v1` (`authedFetch` setzt den Anfang davor). */
export const EXT_API = `/ext/${EXT_ID}`;
