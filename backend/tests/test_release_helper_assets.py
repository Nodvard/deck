"""scripts/release_helper_assets.py: Compose-Datei mit dem Helfer per Digest und der Text fürs GitHub-Release."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("nodvard_deck_release_helper_assets", REPO_ROOT / "scripts" / "release_helper_assets.py")
assets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(assets)

IMAGE = "ghcr.io/nodvard/deck-updater"
DIGEST = "sha256:" + "ab" * 32
VERSION = "0.12.0"


def _yaml(text: str) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(text)


def test_only_the_image_line_of_the_helper_changes():
    original = assets.COMPOSE.read_text(encoding="utf-8")
    pinned = assets.pin_compose(original, IMAGE, DIGEST)
    changed = [(a, b) for a, b in zip(original.splitlines(), pinned.splitlines(), strict=True) if a != b]
    assert changed == [(f"    image: {IMAGE}:1", f"    image: {IMAGE}@{DIGEST}")]
    before, after = _yaml(original), _yaml(pinned)
    assert after["services"]["updater"].pop("image") == f"{IMAGE}@{DIGEST}"
    before["services"]["updater"].pop("image")
    assert after == before


def test_line_endings_and_indent_stay():
    pinned = assets.pin_compose(f"a: 1\r\n  image: {IMAGE}:1\r\nb: 2\r\n", IMAGE, DIGEST)
    assert pinned == f"a: 1\r\n  image: {IMAGE}@{DIGEST}\r\nb: 2\r\n"


@pytest.mark.parametrize("text", [
    "services:\n  updater:\n    image: other/helper:1\n",
    "a:\n    image: ghcr.io/nodvard/deck-updater:1\nb:\n    image: ghcr.io/nodvard/deck-updater:1\n",
    "    image: ghcr.io/nodvard/deck-updater:1.0\n",
], ids=["fehlt", "doppelt", "anderes-tag"])
def test_the_helper_line_must_be_there_exactly_once(text):
    with pytest.raises(assets.AssetError, match="genau einmal"):
        assets.pin_compose(text, IMAGE, DIGEST)


@pytest.mark.parametrize(("version", "image", "digest"), [
    ("0.12.0-rc1", IMAGE, DIGEST),
    ("v0.12.0", IMAGE, DIGEST),
    ("0.12", IMAGE, DIGEST),
    (VERSION, IMAGE, "sha256:" + "AB" * 32),
    (VERSION, IMAGE, "sha256:" + "ab" * 31),
    (VERSION, IMAGE, DIGEST + "\n"),
    (VERSION, IMAGE, ""),
    (VERSION, "ghcr.io/nodvard/deck-updater:1", DIGEST),
    (VERSION, "Ghcr.io/x", DIGEST),
    (VERSION, "ghcr.io/x y", DIGEST),
])
def test_bad_input_writes_nothing(tmp_path, version, image, digest):
    out = tmp_path / "out"
    with pytest.raises(assets.AssetError):
        assets.write_files(version, image, digest, out)
    assert not out.exists()


def test_files_for_a_version_with_release_notes(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / f"{VERSION}.md").write_text(
        f"# Nodvard Deck {VERSION} – Kurz gesagt\n\n*English version: below.*\n\n## Deutsch\n\nNeu.\n", encoding="utf-8"
    )
    title, body = assets.notes(VERSION, assets.section(VERSION, IMAGE, DIGEST), notes_dir)
    assert title == f"Nodvard Deck {VERSION} – Kurz gesagt"
    assert body.startswith("*English version: below.*\n\n## Deutsch\n\nNeu.\n\n" + assets.MARKER)
    assert f"`{IMAGE}@{DIGEST}` (Version {VERSION})" in body
    assert f"docker buildx imagetools inspect {IMAGE}:{VERSION}" in body


def test_files_for_a_version_without_release_notes(tmp_path):
    part = assets.section(VERSION, IMAGE, DIGEST)
    assert assets.notes(VERSION, part, tmp_path) == (f"Nodvard Deck {VERSION}", part)


def test_provenance_extends_the_helper_section_and_names_each_digest():
    images = (("deck", "ghcr.io/nodvard/deck", "sha256:" + "12" * 32),
              ("dock", "ghcr.io/nodvard/dock", "sha256:" + "34" * 32))
    original = assets.section(VERSION, IMAGE, DIGEST)
    part = assets.section(VERSION, IMAGE, DIGEST, images=images)
    assert part.startswith(original.removesuffix(assets.MARKER_END + "\n"))
    assert part.endswith(assets.MARKER_END + "\n")
    assert part.count(assets.MARKER) == part.count(assets.MARKER_END) == 1
    for name, source, digest in (*images, ("updater", IMAGE, DIGEST)):
        assert f"`{source}@{digest}`" in part
        assert f"https://github.com/nodvard/deck/releases/download/v{VERSION}/{name}.sigstore.json" in part
        assert f"docker buildx imagetools inspect {source}@{digest} --format '{{{{ json .Provenance }}}}'" in part
        assert f"gh attestation verify oci://{source}@{digest} --repo nodvard/deck" in part
    assert f"--source-ref refs/tags/v{VERSION}" in part
    assert assets.merge("Oben.\n\n" + original + "\nUnten.", part) == "Oben.\n\n" + part + "\nUnten."


def test_cli_adds_deck_dock_and_repository_to_the_section(tmp_path):
    assert assets.main(["files", "--version", VERSION, "--image", IMAGE, "--digest", DIGEST, "--out", str(tmp_path),
                        "--deck-image", "ghcr.io/example/deck", "--deck-digest", DIGEST,
                        "--dock-image", "ghcr.io/example/dock", "--dock-digest", DIGEST,
                        "--repository", "example/deck"]) == 0
    part = (tmp_path / assets.SECTION_NAME).read_text(encoding="utf-8")
    assert "ghcr.io/example/deck@" in part and "ghcr.io/example/dock@" in part
    assert "https://github.com/example/deck/releases/download/" in part
    assert "--signer-workflow example/deck/.github/workflows/release.yml" in part


@pytest.mark.parametrize("extra", [
    ["--deck-image", "ghcr.io/nodvard/deck"],
    ["--dock-digest", DIGEST],
    ["--deck-image", "ghcr.io/nodvard/deck", "--deck-digest", "sha256:falsch"],
    ["--dock-image", "ghcr.io/nodvard/dock:1", "--dock-digest", DIGEST],
    ["--repository", "nodvard/deck\nanderes"],
])
def test_incomplete_or_invalid_provenance_input_writes_nothing(tmp_path, extra):
    out = tmp_path / "out"
    assert assets.main(["files", "--version", VERSION, "--image", IMAGE, "--digest", DIGEST, "--out", str(out), *extra]) == 1
    assert not out.exists()


def test_main_writes_all_four_files(tmp_path):
    out = tmp_path / "release"
    assert assets.main(["files", "--version", VERSION, "--image", IMAGE, "--digest", DIGEST, "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [assets.COMPOSE_NAME, assets.SECTION_NAME, assets.NOTES_NAME, assets.TITLE_NAME]
    )
    assert f"image: {IMAGE}@{DIGEST}" in (out / assets.COMPOSE_NAME).read_text(encoding="utf-8")
    assert (out / assets.NOTES_NAME).read_text(encoding="utf-8").endswith((out / assets.SECTION_NAME).read_text(encoding="utf-8"))
    assert (out / assets.TITLE_NAME).read_text(encoding="utf-8").startswith("Nodvard Deck ")


def test_main_refuses_a_bad_digest(tmp_path, capsys):
    out = tmp_path / "release"
    assert assets.main(["files", "--version", VERSION, "--image", IMAGE, "--digest", "latest", "--out", str(out)]) == 1
    assert "Abgelehnt: Kein Digest" in capsys.readouterr().err
    assert not out.exists()


def test_merge_appends_to_a_release_written_by_hand():
    part = assets.section(VERSION, IMAGE, DIGEST)
    assert assets.merge("Von Hand.\n\n", part) == f"Von Hand.\n\n{part}"
    assert assets.merge("", part) == part


def test_merge_replaces_the_section_of_an_earlier_run():
    old = assets.section(VERSION, IMAGE, "sha256:" + "cd" * 32)
    new = assets.section(VERSION, IMAGE, DIGEST)
    merged = assets.merge(f"Von Hand.\n\n{old}", new)
    assert merged == f"Von Hand.\n\n{new}"
    assert "cd" * 32 not in merged


def test_merge_keeps_text_written_below_the_section():
    old = assets.section(VERSION, IMAGE, "sha256:" + "cd" * 32)
    new = assets.section(VERSION, IMAGE, DIGEST)
    merged = assets.merge(f"Oben.\n\n{old}\nUnten.\n", new)
    assert merged == f"Oben.\n\n{new}\nUnten.\n"
    assert assets.merge(merged, new) == merged


def test_merge_leaves_the_text_around_the_section_as_it_is():
    old = assets.section(VERSION, IMAGE, "sha256:" + "cd" * 32).rstrip("\n")
    new = assets.section(VERSION, IMAGE, DIGEST)
    existing = f"Oben  \r\n\r\n{old}\r\n\r\n  Unten\r\n\r\n"
    assert assets.merge(existing, new) == f"Oben  \r\n\r\n{new.rstrip()}\r\n\r\n  Unten\r\n\r\n"


@pytest.mark.parametrize("existing", [
    f"Oben.\n\n{assets.MARKER}\nalt\n\nVon Hand darunter.\n",
    f"Oben.\n\n{assets.MARKER_END}\nVon Hand.\n",
    f"{assets.MARKER_END}\nMitte\n{assets.MARKER}\n",
    assets.section(VERSION, IMAGE, DIGEST) * 2,
    f"{assets.section(VERSION, IMAGE, DIGEST)}{assets.MARKER_END}\n",
], ids=["ohne-ende", "nur-ende", "vertauscht", "doppelt", "ende-doppelt"])
def test_merge_refuses_a_broken_section(existing):
    with pytest.raises(assets.AssetError, match="nicht genau einmal vollständig"):
        assets.merge(existing, assets.section(VERSION, IMAGE, DIGEST))


def test_the_docs_point_to_the_file_of_the_release_job():
    latest = f"https://github.com/nodvard/deck/releases/latest/download/{assets.COMPOSE_NAME}"
    fallback = f"https://raw.githubusercontent.com/nodvard/deck/main/deploy/{assets.COMPOSE_NAME}"
    readme = (REPO_ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    header = assets.COMPOSE.read_text(encoding="utf-8").split("\nname:", 1)[0]
    for text in (readme, header):
        assert latest in text and fallback in text


def test_merge_on_the_command_line(tmp_path):
    part = tmp_path / "part.md"
    part.write_text(assets.section(VERSION, IMAGE, DIGEST), encoding="utf-8")
    existing = tmp_path / "current.md"
    existing.write_text("Von Hand.", encoding="utf-8")
    out = tmp_path / "notes.md"
    assert assets.main(["merge", "--existing", str(existing), "--section", str(part), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "Von Hand.\n\n" + part.read_text(encoding="utf-8")
    for broken in ("Irgendwas", assets.section(VERSION, IMAGE, DIGEST).replace(assets.MARKER_END, "")):
        part.write_text(broken, encoding="utf-8")
        assert assets.main(["merge", "--existing", str(existing), "--section", str(part), "--out", str(out)]) == 1


def test_merge_on_the_command_line_keeps_line_endings_and_drops_the_newline_of_gh(tmp_path):
    new = assets.section(VERSION, IMAGE, DIGEST)
    part = tmp_path / "part.md"
    part.write_text(new, encoding="utf-8")
    old = assets.section(VERSION, IMAGE, "sha256:" + "cd" * 32).rstrip("\n")
    existing = tmp_path / "current.md"
    existing.write_bytes(f"Oben\r\n\r\n{old}\r\nUnten\r\n\n".encode())
    out = tmp_path / "notes.md"
    assert assets.main(["merge", "--existing", str(existing), "--section", str(part), "--out", str(out)]) == 0
    assert out.read_bytes() == f"Oben\r\n\r\n{new.rstrip()}\r\nUnten\r\n".encode()
    existing.write_text(f"Oben\n{assets.MARKER}\nVon Hand.\n", encoding="utf-8")
    out.unlink()
    assert assets.main(["merge", "--existing", str(existing), "--section", str(part), "--out", str(out)]) == 1
    assert not out.exists()
