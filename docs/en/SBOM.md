# Check components and provenance

[Deutsch](../BAUSTEINLISTE.md)

Each [GitHub release](https://github.com/nodvard/deck/releases) lists the digests for Deck, Dock and the update
helper. Its assets include component lists (SBOMs) and signed provenance bundles.

| Image | File prefix |
|---|---|
| `ghcr.io/nodvard/deck` | `deck` |
| `ghcr.io/nodvard/dock` | `dock` |
| `ghcr.io/nodvard/deck-updater` | `updater` |

Choose your installation's architecture: `amd64` for typical PCs and NAS devices, `arm64` for devices such as
a Raspberry Pi running a 64-bit system. Both formats describe the same scan, for example
`deck-linux-amd64.spdx.json` (SPDX-JSON) and `deck-linux-amd64.cdx.json` (CycloneDX-JSON).

## Read the component list

Open either file in a text editor. SPDX lists `packages` with `name`, `versionInfo` and `licenseConcluded` or
`licenseDeclared`. CycloneDX lists `components` with `name`, `version` and `licenses`. `NOASSERTION` means the
license was not determined. With `jq`, you can list package names and versions:

```bash
jq -r '.packages[] | [.name, .versionInfo, .licenseDeclared] | @tsv' deck-linux-amd64.spdx.json
jq -r '.components[] | [.name, .version] | @tsv' deck-linux-amd64.cdx.json
```

The list records detected packages in the shipped image. It is not a vulnerability report and may miss embedded
or bundled libraries. Nodvard Deck remains licensed under PolyForm Noncommercial; individual package licenses
apply to those dependencies.

## Compare the digest

Replace `<version>` with the release version without the leading `v`:

```bash
docker buildx imagetools inspect ghcr.io/nodvard/deck:<version>
```

Compare the entire top-level `Digest:` value with the Deck digest in the release notes. This identifies the image
index covering both architectures. Entries under `Manifests` have separate platform digests and are different
values. For Dock or the helper, use its image name from the table. A digest starts with `sha256:` followed by
64 hexadecimal characters. If it differs, check the version and image name first, then report the release.

## Verify provenance

Use the digest from the release, including `sha256:`. These commands read the SBOM and SLSA build information
attached by Buildx for both platforms:

```bash
docker buildx imagetools inspect ghcr.io/nodvard/deck@<digest> --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/nodvard/deck@<digest> --format '{{ json .Provenance }}'
```

To verify signed provenance, use a current GitHub CLI (`gh`) with GitHub and registry authentication configured.
This binds the digest to the repository, release workflow and tag:

```bash
gh attestation verify oci://ghcr.io/nodvard/deck@<digest> --repo nodvard/deck --signer-workflow nodvard/deck/.github/workflows/release.yml --source-ref refs/tags/v<version>
```

For Dock and the helper, substitute their image names and digests. The release also includes signed bundles:
`deck.sigstore.json`, `dock.sigstore.json` and `updater.sigstore.json`. Add `--bundle deck.sigstore.json` to
`gh attestation verify` to use the downloaded bundle. Successful verification confirms the image's provenance.
Buildx build information alone does not prove a signature. The manual update grant in `freigabe.json` and
`freigabe.json.sig` is still checked separately.
