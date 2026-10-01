/**
 * Mitgelieferte Fremdsoftware, auf die "Über Nodvard Deck" verweist. Die vollständige Liste mit
 * Lizenztexten ist THIRD_PARTY_LICENSES (erzeugt von scripts/third_party_licenses.py, liegt im
 * Image unter /app). Hier steht nur, was die Oberfläche selbst nennt.
 */

/** THIRD_PARTY_LICENSES im öffentlichen Quellcode. */
export const THIRD_PARTY_LICENSES_URL = "https://github.com/nodvard/deck/blob/main/THIRD_PARTY_LICENSES";

/** Wo dieselbe Datei im Docker-Image liegt (deploy/Dockerfile). */
export const THIRD_PARTY_LICENSES_IMAGE_PATH = "/app/THIRD_PARTY_LICENSES";

/**
 * noVNC (grafische Konsole) steht unter der MPL-2.0. Wer es weitergibt, muss sagen, wo es den
 * Quellcode genau dieser Version gibt. Muss zur Version in frontend/package-lock.json passen
 * (thirdParty.test.ts prüft das).
 */
export const NOVNC_VERSION = "1.7.0";

/** Quellcode von noVNC genau in der mitgelieferten Version (Git-Tag des Projekts). */
export const NOVNC_SOURCE_URL = `https://github.com/novnc/noVNC/tree/v${NOVNC_VERSION}`;
