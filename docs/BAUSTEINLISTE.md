# Bausteinliste und Herkunft prüfen

[English](en/SBOM.md)

Am [GitHub-Release](https://github.com/nodvard/deck/releases) stehen die Digests von Deck, Dock und dem
Update-Helfer. Unter „Assets“ findest du ihre Bausteinlisten (SBOM) und signierten Herkunftsnachweise.

| Image | Dateianfang |
|---|---|
| `ghcr.io/nodvard/deck` | `deck` |
| `ghcr.io/nodvard/dock` | `dock` |
| `ghcr.io/nodvard/deck-updater` | `updater` |

Wähle die Architektur deiner Installation: `amd64` für übliche PCs und NAS, `arm64` etwa für einen Raspberry Pi
mit 64-Bit-System. Für jede Architektur gibt es zwei gleichwertige Formate, zum Beispiel
`deck-linux-amd64.spdx.json` (SPDX-JSON) und `deck-linux-amd64.cdx.json` (CycloneDX-JSON).

## Bausteinliste lesen

Die Dateien lassen sich in einem Texteditor öffnen. SPDX nennt die Pakete unter `packages`: `name`,
`versionInfo` und `licenseConcluded` oder `licenseDeclared`. CycloneDX führt sie unter `components` mit `name`,
`version` und `licenses`. `NOASSERTION` heißt, dass die Lizenz nicht ermittelt wurde. Mit `jq` geht es übersichtlicher:

```bash
jq -r '.packages[] | [.name, .versionInfo, .licenseDeclared] | @tsv' deck-linux-amd64.spdx.json
jq -r '.components[] | [.name, .version] | @tsv' deck-linux-amd64.cdx.json
```

Die Liste beschreibt die erkannten Pakete des ausgelieferten Images. Sie ist kein Bericht über Sicherheitslücken
und kann eingebettete oder gebündelte Bibliotheken übersehen. Die Lizenz von Nodvard Deck bleibt PolyForm
Noncommercial; Lizenzangaben einzelner Pakete stehen für diese Abhängigkeiten.

## Digest vergleichen

`<version>` durch die Versionsnummer des Releases ohne `v` ersetzen:

```bash
docker buildx imagetools inspect ghcr.io/nodvard/deck:<version>
```

Vergleiche die oberste Zeile `Digest:` vollständig mit dem Deck-Digest im Release-Text. Das ist der Digest des
Image-Index für beide Architekturen. Die Einträge unter `Manifests` haben eigene Plattform-Digests und sind andere
Werte. Für Dock oder den Helfer den Image-Namen aus der Tabelle nehmen. Ein Digest beginnt mit `sha256:` und hat
danach 64 Hex-Zeichen. Bei einer Abweichung zuerst Version und Image-Namen prüfen, dann das Release melden.

## Herkunft prüfen

Den Digest aus dem Release einsetzen, einschließlich `sha256:`. Die folgenden Befehle lesen die von Buildx
am Image abgelegten Bausteinlisten und SLSA-Bauangaben für beide Plattformen:

```bash
docker buildx imagetools inspect ghcr.io/nodvard/deck@<digest> --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/nodvard/deck@<digest> --format '{{ json .Provenance }}'
```

Zum Prüfen der signierten Herkunft brauchst du eine aktuelle GitHub CLI (`gh`) und eine Anmeldung bei GitHub
und der Registry. Der Befehl bindet den Digest an das Repository, den Release-Workflow und das Tag:

```bash
gh attestation verify oci://ghcr.io/nodvard/deck@<digest> --repo nodvard/deck --signer-workflow nodvard/deck/.github/workflows/release.yml --source-ref refs/tags/v<version>
```

Für Dock und den Helfer gilt derselbe Befehl mit ihrem Image und Digest. Die signierten Nachweise liegen außerdem
als `deck.sigstore.json`, `dock.sigstore.json` und `updater.sigstore.json` am Release. Mit `--bundle deck.sigstore.json`
kann `gh attestation verify` den heruntergeladenen Nachweis verwenden. Ein erfolgreiches Ergebnis bestätigt die
Herkunft des Images. Die Buildx-Bauangaben allein sind kein Signaturbeweis; die manuelle Update-Freigabe mit
`freigabe.json` und `freigabe.json.sig` wird weiterhin getrennt geprüft.
