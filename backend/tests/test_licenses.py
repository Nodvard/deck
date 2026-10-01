"""Lizenz-Wächter und THIRD_PARTY_LICENSES (scripts/check_licenses.py,
scripts/third_party_licenses.py).

Warum kein Voll-Vergleich der Datei in jedem Testlauf: die Lizenztexte kommen aus den
INSTALLIERTEN Paketen (Python-dist-info, frontend/node_modules). Der CI-Backend-Job hat
kein node_modules, und Wheels anderer Plattformen bringen teils andere Zusatztexte mit.
Deshalb gilt:

* IMMER geprüft (nur aus constraints.txt + package-lock.json abgeleitet, also überall
  gleich): die eingecheckte Datei nennt genau die richtigen Pakete in den richtigen
  Versionen. Wer eine Abhängigkeit hebt/entfernt und die Datei nicht neu erzeugt, sieht
  hier rot.
* Voll verglichen (`python scripts/third_party_licenses.py --check`), wenn die Umgebung
  vollständig ist (Linux, alle Pakete wie gepinnt, node_modules da); sonst übersprungen.
"""

from __future__ import annotations

import base64
import email.message
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_licenses as guard
import third_party_licenses as tpl

LICENSES_FILE = REPO / "THIRD_PARTY_LICENSES"


def _pkg(name="demo", version="1.0", license_="MIT", ecosystem="python", problems=()):
    return tpl.Package(ecosystem, name, version, license_, "test", problems=list(problems))


# ---------------------------------------------------------------------------
# Bewertung von Lizenzangaben
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "declared",
    [
        "MIT", "MIT-0", "MIT License", "Apache-2.0", "Apache License 2.0", "Apache License, Version 2.0",
        "Apache Software License", "BSD-2-Clause", "BSD-3-Clause", "BSD-3-Clause-Clear", "BSD License",
        "0BSD", "ISC", "ISC License (ISCL)", "PSF-2.0", "Python-2.0", "Python Software Foundation License",
        "Unlicense", "The Unlicense (Unlicense)", "Zlib", "zlib/libpng license (Zlib)",
        "MIT OR Apache-2.0", "(MIT OR Apache-2.0)", "MIT AND PSF-2.0", "Apache-2.0 WITH LLVM-exception",
        "Apache-2.0 OR GPL-2.0-or-later",  # dulwich
        "GPL-3.0-only OR MIT", "(BSD-2-Clause OR GPL-2.0)", "MIT OR (GPL-2.0 AND LGPL-3.0)",
    ],
)
def test_allowed_licenses_pass(declared):
    assert guard.evaluate_license(declared).rank < guard.UNBEKANNT, declared


@pytest.mark.parametrize(
    "declared",
    [
        "GPL-3.0", "GPL-3.0-only", "GPL-2.0-or-later", "GPLv3", "AGPL-3.0", "AGPL-3.0-or-later",
        "LGPL-2.1", "LGPL-3.0-or-later", "SSPL-1.0", "GNU General Public License v3 (GPLv3)",
        "GNU Affero General Public License v3", "GNU Lesser General Public License v2 (LGPLv2)",
        "MIT AND GPL-3.0", "GPL-2.0 OR LGPL-3.0", "Apache-2.0 AND AGPL-3.0", "GPL-3.0 WITH Classpath-exception-2.0",
    ],
)
def test_copyleft_licenses_fail(declared):
    assert guard.evaluate_license(declared).rank == guard.COPYLEFT, declared


@pytest.mark.parametrize(
    "declared", ["", "   ", "UNKNOWN", "Proprietary", "SEE LICENSE IN LICENSE.txt", "Custom license", "EPL-2.0", "CC-BY-SA-4.0", "MIT AND Custom"]
)
def test_unknown_licenses_fail(declared):
    assert guard.evaluate_license(declared).rank == guard.UNBEKANNT, declared


def test_mpl_is_allowed_but_reported_as_note():
    verdict = guard.evaluate_license("MPL-2.0")
    assert verdict.rank == guard.HINWEIS
    assert "MPL-2.0" in verdict.hinweise
    assert guard.evaluate_license("Mozilla Public License 2.0 (MPL 2.0)").rank == guard.HINWEIS
    # Mit einer freien Alternative daneben gilt die, ohne Hinweis.
    assert guard.evaluate_license("MPL-2.0 OR MIT").hinweise == frozenset()


def test_or_needs_one_allowed_option_and_and_needs_all():
    assert guard.evaluate_license("GPL-3.0 OR Apache-2.0").rank == guard.OK
    assert guard.evaluate_license("GPL-3.0 OR LGPL-3.0").rank == guard.COPYLEFT
    assert guard.evaluate_license("MIT AND GPL-3.0").rank == guard.COPYLEFT
    assert guard.evaluate_license("BSD-3-Clause, Apache-2.0").rank == guard.OK  # Komma = UND
    assert guard.evaluate_license("BSD-3-Clause, Apache-2.0, dependency licenses").rank == guard.UNBEKANNT


# ---------------------------------------------------------------------------
# Prüfung von Paketlisten, Ausnahmen, Meldungen
# ---------------------------------------------------------------------------


def test_copyleft_package_is_a_violation_with_a_helpful_german_message():
    report = guard.check_packages([_pkg("boeser-baustein", "2.3.4", "GPL-3.0-only"), _pkg("gut", "1", "MIT")], exceptions={})
    assert [f.name for f in report.violations] == ["boeser-baustein"]
    message = guard.violation_message(report.violations[0])
    assert "boeser-baustein 2.3.4" in message
    assert "GPL-3.0-only" in message
    assert "Was tun" in message
    assert "ersetzen" in message


def test_unknown_license_message_points_to_the_exception_list():
    report = guard.check_packages([_pkg("raetsel", "0.1", "")], exceptions={})
    (finding,) = report.violations
    message = guard.violation_message(finding)
    assert "raetsel 0.1" in message and "unbekannt" in message and "EXCEPTIONS" in message


def test_exception_is_accepted_only_for_the_reviewed_version():
    exceptions = {("python", "raetsel"): guard.Ausnahme("0.1", "MIT", "Metadaten leer, LICENSE-Datei ist MIT")}
    ok = guard.check_packages([_pkg("raetsel", "0.1", "")], exceptions=exceptions)
    assert not ok.violations and ok.unused_exceptions == []

    bumped = guard.check_packages([_pkg("raetsel", "0.2", "")], exceptions=exceptions)
    (finding,) = bumped.violations
    assert "0.1" in guard.violation_message(finding) and "0.2" in guard.violation_message(finding)


def test_unused_exceptions_are_reported():
    exceptions = {("python", "weg"): guard.Ausnahme("1", "MIT", "x")}
    assert guard.check_packages([_pkg()], exceptions=exceptions).unused_exceptions == ["python:weg"]


def test_exception_names_are_normalized_like_pip():
    assert guard.exception_key("python", "Foo_Bar.baz") == ("python", "foo-bar-baz")
    assert guard.exception_key("npm", "@Scope/Name") == ("npm", "@scope/name")


def test_missing_package_is_an_environment_problem_not_a_pass():
    report = guard.check_packages([_pkg("fehlt", "1.0", "", problems=["nicht installiert (constraints verlangen 1.0)"])], exceptions={})
    assert report.env_problems and not report.findings


def test_package_not_for_this_platform_is_skipped_not_missing():
    pkg = _pkg("uvloop", "0.22.1", "")
    pkg.skipped = 'nicht für diese Plattform: uvicorn: extra == "standard" and sys_platform != "win32"'
    report = guard.check_packages([pkg, _pkg("demo")], exceptions={})
    assert not report.env_problems and not report.violations
    assert [f.name for f in report.findings] == ["demo"]
    assert len(report.skipped) == 1 and "übersprungen" in report.skipped[0] and "uvloop" in report.skipped[0]


def test_every_exception_has_a_reason_and_a_version():
    for (ecosystem, name), exc in guard.EXCEPTIONS.items():
        assert ecosystem in ("python", "npm")
        assert name == name.lower()
        assert exc.version and len(exc.grund) > 40 and exc.bewertet_als


def test_real_dependencies_pass_the_guard():
    """Kernstück: Python (constraints) und ausgelieferte npm-Pakete sind sauber."""
    report = guard.check_packages(guard.collect_packages())
    assert not report.env_problems, (
        "Umgebung passt nicht zu deploy/constraints.txt / package-lock.json:\n  " + "\n  ".join(report.env_problems)
    )
    assert not report.violations, "Lizenzproblem(e):\n" + "\n".join(guard.violation_message(f) for f in report.violations)
    assert not report.unused_exceptions, f"Ausnahmen nicht mehr nötig: {report.unused_exceptions}"
    assert len(report.findings) > 50


def test_dulwich_is_accepted_through_its_apache_option():
    report = guard.check_packages(guard.collect_packages())
    (dulwich,) = [f for f in report.findings if f.name.lower() == "dulwich"]
    assert "GPL" in dulwich.license and not dulwich.failed and dulwich.exception is None


def test_cli_exit_codes(capsys):
    assert guard.main([]) == 0
    assert "Lizenzprüfung bestanden" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Lizenzangaben aus Python-Metadaten
# ---------------------------------------------------------------------------


def _meta(**fields) -> email.message.Message:
    msg = email.message.Message()
    for key, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            msg[key.replace("_", "-")] = item
    return msg


def test_license_expression_beats_everything():
    meta = _meta(License_Expression="Apache-2.0 OR BSD-3-Clause", License="MIT", Classifier=["License :: OSI Approved :: MIT License"])
    assert tpl.python_declared_license(meta) == ("Apache-2.0 OR BSD-3-Clause", "License-Expression")


def test_short_license_field_then_classifiers():
    assert tpl.python_declared_license(_meta(License="BSD-3-Clause")) == ("BSD-3-Clause", "License")
    embedded_text = "MIT License\n\nCopyright (c) someone\n" * 10
    meta = _meta(License=embedded_text, Classifier=["License :: OSI Approved :: MIT License"])
    assert tpl.python_declared_license(meta) == ("MIT License", "Classifier")
    both = _meta(Classifier=["License :: OSI Approved :: MIT License", "License :: OSI Approved :: Apache Software License"])
    assert tpl.python_declared_license(both)[0] == "MIT License AND Apache Software License"
    assert tpl.python_declared_license(_meta(Classifier=["License :: OSI Approved"])) == ("", "keine Angabe")
    assert tpl.python_declared_license(_meta(License="UNKNOWN")) == ("", "keine Angabe")


# ---------------------------------------------------------------------------
# npm: nur Production, transitiv
# ---------------------------------------------------------------------------


def _fake_lock() -> dict:
    return {
        "": {"dependencies": {"app-dep": "^1"}},
        "node_modules/app-dep": {"version": "1.0.0", "license": "MIT", "dependencies": {"shared": "^1", "nested": "^2"}},
        "node_modules/shared": {"version": "1.5.0", "license": "ISC", "optionalDependencies": {"absent": "^1"}},
        "node_modules/app-dep/node_modules/nested": {"version": "2.0.0", "license": "BSD-2-Clause", "dependencies": {"shared": "^1"}},
        "node_modules/nested": {"version": "9.9.9", "license": "GPL-3.0", "dev": True},
        "node_modules/dev-only": {"version": "1.0.0", "license": "GPL-3.0", "dev": True},
    }


def test_npm_closure_follows_node_resolution_and_skips_dev_and_missing_optionals():
    paths, problems = tpl.npm_closure(_fake_lock(), {"app-dep"})
    assert problems == []
    assert paths == ["node_modules/app-dep", "node_modules/app-dep/node_modules/nested", "node_modules/shared"]


def test_npm_closure_reports_roots_missing_from_the_lock():
    _, problems = tpl.npm_closure(_fake_lock(), {"gibt-es-nicht"})
    assert problems and "gibt-es-nicht" in problems[0]


def test_real_npm_set_has_production_packages_only():
    names = {p.name for p in tpl.collect_npm(REPO, with_texts=False)}
    assert {"react", "react-dom", "@novnc/novnc", "@xterm/xterm", "markdown-it", "lucide-react"} <= names
    assert not names & {"vite", "vitest", "typescript", "esbuild", "jsdom", "postcss", "@vitejs/plugin-react"}


def test_build_tools_whose_code_is_shipped_are_listed_without_their_dependencies():
    """Tailwinds Grundstile stehen im ausgelieferten CSS -- Tailwind selbst gehoert in die Liste,
    seine Abhaengigkeiten (postcss, chokidar ...) laufen nur beim Bauen und gehoeren nicht hinein."""
    packages = {p.name: p for p in tpl.collect_npm(REPO, with_texts=False)}
    assert "tailwindcss" in packages and "Preflight" in packages["tailwindcss"].note
    assert not {"postcss", "chokidar", "sucrase", "jiti"} & set(packages)
    assert all(not p.note for name, p in packages.items() if name not in tpl._NPM_BUILD_OUTPUT)


def test_bundle_scan_finds_bare_imports_and_bundled_node_modules(tmp_path):
    dist = tmp_path / "extensions" / "demo" / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.js").write_text(
        '// ../../../frontend/node_modules/some-lib/dist/index.js\n'
        '// ../../../frontend/node_modules/@sc/pkg/lib/a.js\n'
        '// src/Own.tsx\n'
        'import { jsx } from "react/jsx-runtime";\n'
        'import { useState } from "react";\n'
        'import x from "./lokal.js";\n'
        'import y from "@scope/thing/sub";\n',
        encoding="utf-8",
    )
    assert tpl.bundle_npm_roots(tmp_path) == {"some-lib", "@sc/pkg", "react", "@scope/thing"}


# ---------------------------------------------------------------------------
# Erzeugung: deterministisch, vollständig
# ---------------------------------------------------------------------------


def test_render_is_deterministic_and_independent_of_input_order():
    a = tpl.Package("python", "Zeta", "1", "MIT", "t", texts=[("LICENSE", "zeta text")])
    b = tpl.Package("python", "alpha", "2", "ISC", "t", texts=[("LICENSE", "alpha text")])
    c = tpl.Package("npm", "@x/y", "3", "MIT", "t", texts=[("LICENSE", "xy text")])
    first = tpl.render([a, b], [c])
    assert first == tpl.render([b, a], [c])
    assert first.index("alpha 2\nLizenz: ISC") < first.index("Zeta 1\nLizenz: MIT")
    assert "\r" not in first and first.endswith("\n") and not first.endswith("\n\n")


def test_normalize_text_makes_crlf_and_trailing_space_irrelevant():
    assert tpl.normalize_text("a  \r\nb\t\r\n\r\n") == "a\nb"


def test_dedupe_keeps_each_text_once():
    files = [("b/x.txt", "T"), ("a/x.txt", "T"), ("c.txt", "U")]
    assert tpl._dedupe(files) == [("a/x.txt", "T"), ("c.txt", "U")]


# ---------------------------------------------------------------------------
# Lizenztexte: Kodierung, SBOM, mitgelieferter Fremdcode
# ---------------------------------------------------------------------------


def test_license_files_that_are_not_utf8_are_read_as_latin1(tmp_path):
    """pypdfium2 liefert die FreeType-Lizenz als Latin-1 ("copyright \\xa9 <year>") -- ohne
    Rueckfall stuende dort ein Ersatzzeichen statt des (c)-Zeichens."""
    path = tmp_path / "freetype.txt"
    path.write_bytes(b"Portions of this software are copyright \xa9 <year> The FreeType\r\nProject.\r\n")
    assert tpl._read_text(path) == "Portions of this software are copyright © <year> The FreeType\nProject."
    assert tpl.decode_text("Grüße ©".encode()) == "Grüße ©"  # gueltiges UTF-8 bleibt UTF-8


def test_leading_comments_take_the_license_header_only():
    source = (
        "/*\n * Copyright (C) 1996 by Jef Poskanzer.\n */\n"
        "/*---\n * Copyright (c) Microsoft Corporation.\n *---*/\n"
        "\n/* eslint-disable comma-spacing */\nconst x = 1;\n"
    )
    header = tpl.leading_comments(source)
    assert header.startswith("/*\n * Copyright (C) 1996 by Jef Poskanzer.") and header.endswith(" *---*/")
    assert "Microsoft" in header and "eslint" not in header and "const" not in header
    assert tpl.leading_comments("// QR Code\n//\n// Copyright (c) 2009 Kazuhiko Arase\n\nexport {};") == (
        "// QR Code\n//\n// Copyright (c) 2009 Kazuhiko Arase"
    )
    assert tpl.leading_comments("'use strict';\n/* spaeter */") == ""


def _sbom(*components, **extra) -> dict:
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": list(components), **extra}


def test_sbom_components_are_listed_sorted_with_license_and_author_without_emails():
    unicode_text = base64.b64encode("UNICODE LICENSE V3\n\nCopyright © 2020-2023 Unicode, Inc.\n".encode()).decode()
    sbom = _sbom(
        {"name": "zeta", "version": "1.0", "author": "Jane Doe <jane@example.org>, Max <m@x.de>", "licenses": [{"expression": "MIT OR Apache-2.0"}]},
        {"name": "Alpha", "version": "0.2.1", "licenses": [{"license": {"id": "IJG"}}, {"license": {"id": "BSD-3-Clause"}}]},
        {"name": "tinystr", "version": "0.7.5", "licenses": [{"license": {"name": "Unknown", "text": {"encoding": "base64", "content": unicode_text}}}]},
        {"name": "ohne", "version": "2"},
        {"name": "openssl", "version": "4.0.2", "externalReferences": [{"type": "distribution", "url": "https://example.org/openssl-4.0.2.tar.gz"}]},
        {"name": "openssl", "version": "1.1.1"},
        metadata={"timestamp": "2026-08-25T18:07:43Z", "component": {"name": "self"}},
    )
    blocks = tpl.sbom_texts(sbom, "dist-info/sboms/demo.json")
    (label, listing), (text_label, text) = blocks
    assert label == "dist-info/sboms/demo.json: 6 einkompilierte Bestandteile"
    rows = [line for line in listing.splitlines() if line.startswith("  ")]
    assert [row.split()[0] for row in rows] == ["Alpha", "ohne", "openssl", "openssl", "tinystr", "zeta"]
    assert "IJG AND BSD-3-Clause" in rows[0]
    assert "(keine Angabe in der SBOM)" in rows[1]
    assert "openssl 1.1.1" in rows[2] and "(keine Angabe in der SBOM)" in rows[2]  # alte Version: nicht raten
    assert "openssl 4.0.2" in rows[3] and "Apache-2.0" in rows[3] and "Quelle: https://example.org/openssl-4.0.2.tar.gz" in rows[3]
    assert "Lizenztext siehe unten" in rows[4]
    assert "MIT OR Apache-2.0" in rows[5] and "Urheber: Jane Doe, Max" in rows[5] and "@" not in rows[5]
    assert "2026-08-25" not in listing  # kein Zeitstempel: die Datei soll bei jedem Lauf gleich sein
    assert text_label == "Lizenztext aus dist-info/sboms/demo.json: tinystr 0.7.5"
    assert text.startswith("UNICODE LICENSE V3") and "Copyright © 2020-2023 Unicode" in text
    assert tpl.sbom_texts(_sbom(), "leer.json") == []
    assert tpl.sbom_texts(_sbom({"name": "x", "version": "1", "licenses": [{"expression": "MIT"}]}), "s.json")[0][0] == (
        "s.json: 1 einkompilierter Bestandteil"
    )


def test_sbom_problems_are_reported_instead_of_writing_garbage(tmp_path):
    """Kaputtes JSON, falsches Format oder ein Lizenztext, der kein Base64 ist: laut melden, nie still
    etwas auslassen oder den Base64-Text als Lizenztext in die Datei schreiben."""
    import importlib.metadata as md

    info = tmp_path / "demo-1.0.dist-info"
    (info / "sboms").mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.4\nName: demo\nVersion: 1.0\n", encoding="utf-8")
    bad_text = {"license": {"name": "Custom", "text": {"encoding": "base64", "content": "!!kein base64!!"}}}
    sboms = {
        "a.json": '{"bomFormat": "CycloneDX", "components": [',
        "b.json": '{"bomFormat": "SPDX"}',
        "c.json": json.dumps(_sbom({"name": "x", "version": "1", "licenses": [bad_text]})),
    }
    for name, content in sboms.items():
        (info / "sboms" / name).write_text(content, encoding="utf-8")
    record = [f"demo-1.0.dist-info/{rel},," for rel in ("METADATA", *(f"sboms/{n}" for n in sboms), "RECORD")]
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    texts, problems = tpl._python_sbom_texts(md.PathDistribution(info), "demo")
    assert texts == []
    assert [p.split(":")[0] for p in problems] == [
        "SBOM dist-info/sboms/a.json nicht lesbar",
        "SBOM dist-info/sboms/b.json ist kein CycloneDX-Dokument",
        "SBOM dist-info/sboms/c.json",
    ]
    assert "kein gültiges Base64" in problems[2]


def test_python_sbom_of_pyrage_lists_the_rust_crates():
    import importlib.metadata as md

    try:
        version = md.version("pyrage")
    except md.PackageNotFoundError:
        pytest.skip("pyrage ist hier nicht installiert")
    (pkg,) = tpl.collect_python([("pyrage", version)])
    labels = [label for label, _ in pkg.texts]
    sbom_blocks = [text for label, text in pkg.texts if "sboms/" in label and "einkompilierte" in label]
    if not sbom_blocks:
        pytest.skip("dieses pyrage-Wheel bringt keine SBOM mit")
    assert labels[0] == "LICENSE" and not pkg.text_problems
    listing = sbom_blocks[0]
    assert re.search(r"^  age \S+\s+MIT OR Apache-2\.0", listing, re.MULTILINE)
    assert re.search(r"^  age-core \S+", listing, re.MULTILINE)


def test_pillow_sbom_is_skipped_because_its_license_file_covers_the_bundled_libraries():
    import importlib.metadata as md

    try:
        version = md.version("pillow")
    except md.PackageNotFoundError:
        pytest.skip("Pillow ist hier nicht installiert")
    (pkg,) = tpl.collect_python([("pillow", version)])
    assert pkg.texts and not [label for label, _ in pkg.texts if "sboms/" in label]
    assert not [label for label, text in pkg.texts if "libimagequant" in text]  # nur in Pillows SBOM, nicht im Wheel
    assert set(tpl._SBOM_SKIP) <= {tpl.pep503(name) for name, _ in tpl.read_constraints()}


def _fake_frontend(tmp_path: Path, files: dict[str, str]) -> Path:
    """Minimales Repo: ein Frontend mit @novnc/novnc als Abhaengigkeit, dazu tailwindcss (Build-Werkzeug)."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (tmp_path / "extensions").mkdir()
    (frontend / "package.json").write_text('{"dependencies": {"@novnc/novnc": "^1"}}', encoding="utf-8")
    lock = {
        "packages": {
            "": {"dependencies": {"@novnc/novnc": "^1"}},
            "node_modules/@novnc/novnc": {
                "version": "1.7.0", "license": "MPL-2.0",
                "resolved": "https://registry.npmjs.org/@novnc/novnc/-/novnc-1.7.0.tgz",
            },
            "node_modules/tailwindcss": {"version": "3.4.19", "license": "MIT", "dev": True, "dependencies": {"postcss": "^8"}},
            "node_modules/postcss": {"version": "8.0.0", "license": "MIT", "dev": True},
        }
    }
    (frontend / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    for rel, content in files.items():
        path = frontend / "node_modules" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def _novnc_files() -> dict[str, str]:
    return {
        "@novnc/novnc/package.json": '{"name": "@novnc/novnc", "version": "1.7.0", "repository": {"url": "git+https://github.com/novnc/noVNC.git"}}',
        "@novnc/novnc/LICENSE.txt": "noVNC is Copyright (C) 2022 The noVNC authors",
        "@novnc/novnc/docs/LICENSE.MPL-2.0": "Mozilla Public License Version 2.0",
        "@novnc/novnc/docs/LICENSE.BSD-2-Clause": "BSD 2-Clause text",
        "@novnc/novnc/vendor/pako/LICENSE": "(The MIT License)\n\nCopyright (C) 2014-2016 by Vitaly Puzrin",
        "@novnc/novnc/vendor/pako/lib/zlib/inflate.js": "export default 1;",
        "@novnc/novnc/core/icons/copyright.mjs": "export const copyright = 1;",  # Code, keine Lizenzdatei
        "@novnc/novnc/core/crypto/des.js": "/*\n * Copyright (C) 1996 by Jef Poskanzer <jef@acme.com>.\n */\n\nconst PC2 = [];\n",
        "@novnc/novnc/core/decoders/tight.js": "/*\n * (c) 2012 Michael Tinglof, Joe Balaz, Les Piech (Mercuri.ca)\n */\nexport {};\n",
        "tailwindcss/package.json": '{"name": "tailwindcss", "version": "3.4.19"}',
        "tailwindcss/LICENSE": "MIT License\n\nCopyright (c) Tailwind Labs, Inc.",
        "tailwindcss/lib/css/LICENSE": "MIT License\n\nCopyright (c) Sindre Sorhus",
    }


def test_npm_vendored_license_files_and_file_headers_are_appended(tmp_path):
    repo = _fake_frontend(tmp_path, _novnc_files())
    packages = {p.name: p for p in tpl.collect_npm(repo)}
    assert set(packages) == {"@novnc/novnc", "tailwindcss"}  # postcss nicht: Abhaengigkeit des Build-Werkzeugs
    novnc = packages["@novnc/novnc"]
    assert not novnc.problems and not novnc.text_problems
    labels = [label for label, _ in novnc.texts]
    assert labels == [
        "LICENSE.txt",  # die Hauptlizenz zuerst
        "docs/LICENSE.BSD-2-Clause",
        "docs/LICENSE.MPL-2.0",
        "vendor/pako/LICENSE",
        "Lizenzkopf aus core/crypto/des.js",
        "Lizenzkopf aus core/decoders/tight.js",
    ]
    texts = dict(novnc.texts)
    assert "Vitaly Puzrin" in texts["vendor/pako/LICENSE"]
    assert texts["Lizenzkopf aus core/crypto/des.js"] == "/*\n * Copyright (C) 1996 by Jef Poskanzer <jef@acme.com>.\n */"
    assert novnc.download_url == "https://registry.npmjs.org/@novnc/novnc/-/novnc-1.7.0.tgz"
    tailwind = packages["tailwindcss"]
    assert tailwind.note and [label for label, _ in tailwind.texts] == ["LICENSE", "lib/css/LICENSE"]


def test_missing_file_with_license_header_is_a_loud_problem(tmp_path):
    files = _novnc_files()
    del files["@novnc/novnc/core/crypto/des.js"]
    files["@novnc/novnc/core/decoders/tight.js"] = "export {};\n"  # Kopf fehlt
    repo = _fake_frontend(tmp_path, files)
    (novnc,) = [p for p in tpl.collect_npm(repo) if p.name == "@novnc/novnc"]
    assert any("core/crypto/des.js fehlt" in problem for problem in novnc.text_problems)
    assert any("core/decoders/tight.js beginnt nicht mit einem Lizenzkopf" in problem for problem in novnc.text_problems)


def test_mpl_note_names_the_exact_source_version_and_where_it_is_shipped():
    novnc = tpl.Package("npm", "@novnc/novnc", "1.7.0", "MPL-2.0", "t", source_url="https://github.com/novnc/noVNC",
                        download_url="https://registry.npmjs.org/@novnc/novnc/-/novnc-1.7.0.tgz")
    certifi = tpl.Package("python", "certifi", "2026.7.22", "MPL-2.0", "t", source_url="https://github.com/certifi/python-certifi")
    novnc_lines = "\n".join(tpl.mpl_lines(novnc))
    assert novnc_lines.startswith("  @novnc/novnc 1.7.0 (npm): https://github.com/novnc/noVNC\n")
    assert "Quelltext genau dieser Version: https://github.com/novnc/noVNC/tree/v1.7.0" in novnc_lines
    assert "Paket genau dieser Version: https://registry.npmjs.org/@novnc/novnc/-/novnc-1.7.0.tgz" in novnc_lines
    assert "rfb-*.js" in novnc_lines and "unverändert" in novnc_lines
    certifi_lines = "\n".join(tpl.mpl_lines(certifi))
    assert "https://pypi.org/project/certifi/2026.7.22/" in certifi_lines and "Quelltext genau" not in certifi_lines


def test_license_files_of_an_installed_package_are_read():
    (pkg,) = tpl.collect_python([("dulwich", _installed_version("dulwich"))])
    assert pkg.texts and not pkg.problems and "Apache" in " ".join(text for _, text in pkg.texts)


def _installed_version(name: str) -> str:
    import importlib.metadata as md

    return md.version(name)


def test_python_version_mismatch_and_missing_packages_are_reported_loudly():
    wrong, missing = tpl.collect_python([("dulwich", "0.0.1"), ("gibt-es-nicht-xyz", "1.0")])
    assert "0.0.1" in wrong.problems[0]
    assert "nicht installiert" in missing.problems[0]


def test_platform_marker_decides_between_skipped_and_missing():
    """uvloop (uvicorn[standard], Marker sys_platform != "win32"): unter simuliertem Windows
    uebersprungen, unter Linux dagegen ein echtes "nicht installiert" (falls es fehlt)."""
    if not any(True for _ in _dists_requiring("uvloop")):
        pytest.skip("uvicorn[standard] ist hier nicht installiert")
    assert "nicht für diese Plattform" in tpl.platform_skip_reason("uvloop", {"sys_platform": "win32"})
    assert tpl.platform_skip_reason("uvloop", {"sys_platform": "linux"}) == ""
    assert tpl.platform_skip_reason("uvloop", {"sys_platform": "win32"}) == tpl.platform_skip_reason("UVLoop", {"sys_platform": "win32"})
    # Niemand verlangt das Paket -> nie "uebersprungen", sondern ein echtes Problem.
    assert tpl.platform_skip_reason("gibt-es-nicht-xyz", {"sys_platform": "win32"}) == ""
    # Ein Paket mit Anforderung OHNE Marker ist nie plattformgebunden.
    assert tpl.platform_skip_reason("pydantic", {"sys_platform": "win32"}) == ""


def _dists_requiring(name: str):
    import importlib.metadata as md

    for dist in md.distributions():
        for raw in dist.requires or []:
            if raw.lower().startswith(name):
                yield dist


def test_missing_platform_package_is_skipped_in_collect_python(monkeypatch):
    import importlib.metadata as md

    real = md.distribution

    def fake(name):
        if name == "uvloop":
            raise md.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(tpl.importlib_metadata, "distribution", fake)
    if not list(_dists_requiring("uvloop")):
        pytest.skip("uvicorn[standard] ist hier nicht installiert")
    (win,) = tpl.collect_python([("uvloop", "0.22.1")], {"sys_platform": "win32"})
    assert win.skipped and not win.problems
    (linux,) = tpl.collect_python([("uvloop", "0.22.1")], {"sys_platform": "linux"})
    assert not linux.skipped and "nicht installiert" in linux.problems[0]


def test_third_party_cli_does_not_write_a_partial_file_when_packages_are_skipped(tmp_path, monkeypatch, capsys):
    skipped = tpl.Package("python", "uvloop", "0.22.1", "", "", skipped="nicht für diese Plattform: x")
    monkeypatch.setattr(tpl, "collect_all", lambda repo=tpl.REPO_ROOT: ([skipped], []))
    out = tmp_path / "THIRD_PARTY_LICENSES"
    assert tpl.main(["--output", str(out)]) == 2
    assert not out.exists()
    assert "nicht für diese Plattform" in capsys.readouterr().out
    # --check: prueft nur die Paketliste gegen constraints/Lockfile (hier: echte Datei -> ok).
    assert tpl.main(["--check"]) == 0
    stale = tmp_path / "alt"
    stale.write_text(LICENSES_FILE.read_text(encoding="utf-8").replace("  httptools ", "  httptoolz ", 1), encoding="utf-8")
    assert tpl.main(["--check", "--output", str(stale)]) == 1


def test_constraints_must_be_pinned(tmp_path):
    bad = tmp_path / "constraints.txt"
    bad.write_text("foo>=1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        tpl.read_constraints(bad)


# ---------------------------------------------------------------------------
# Die eingecheckte Datei
# ---------------------------------------------------------------------------


def test_checked_in_file_lists_exactly_the_pinned_packages():
    assert LICENSES_FILE.is_file(), "THIRD_PARTY_LICENSES fehlt: python scripts/third_party_licenses.py"
    text = LICENSES_FILE.read_text(encoding="utf-8")
    actual = tpl.parse_index(text)
    expected = tpl.expected_index(REPO)
    for section in ("Python", "npm"):
        assert sorted(actual.get(section, [])) == sorted(expected[section]), (
            f"THIRD_PARTY_LICENSES ({section}) ist veraltet -- neu erzeugen mit: python scripts/third_party_licenses.py"
        )


def test_checked_in_file_has_a_license_text_section_for_every_package():
    text = LICENSES_FILE.read_text(encoding="utf-8")
    expected = tpl.expected_index(REPO)
    for name, version in expected["Python"] + expected["npm"]:
        pattern = rf"^{re.escape(name)} {re.escape(version)}\nLizenz: .+\n-{{10,}}\n\s*[^\s-]"
        assert re.search(pattern, text, re.MULTILINE), f"{name} {version}: kein Lizenztext in THIRD_PARTY_LICENSES"


def test_checked_in_file_is_clean_text():
    raw = LICENSES_FILE.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    assert "\ufffd" not in raw.decode("utf-8"), "Ersatzzeichen: eine Lizenzdatei wurde mit falscher Kodierung gelesen"


def test_checked_in_file_matches_a_fresh_full_run_when_the_environment_is_complete():
    if not sys.platform.startswith("linux"):
        pytest.skip("Wheels anderer Plattformen bringen teils andere Zusatztexte mit; Voll-Vergleich nur unter Linux.")
    python_packages, npm_packages = tpl.collect_all(REPO)
    if any(p.problems for p in python_packages + npm_packages):
        pytest.skip("Umgebung nicht wie gepinnt (Paket fehlt/andere Version/node_modules fehlt): siehe `scripts/third_party_licenses.py --check`.")
    fresh = tpl.render(python_packages, npm_packages)
    current = LICENSES_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert current == fresh, "THIRD_PARTY_LICENSES ist veraltet -- neu erzeugen mit: python scripts/third_party_licenses.py"


def test_mpl_components_are_listed_with_source_note():
    text = LICENSES_FILE.read_text(encoding="utf-8")
    assert "Hinweis zu MPL-2.0-Komponenten" in text
    version = tpl.load_lock(REPO / "frontend" / "package-lock.json")["node_modules/@novnc/novnc"]["version"]
    assert re.search(rf"@novnc/novnc {re.escape(version)} \(npm\): https://github\.com/novnc/noVNC\n", text)
    assert f"Quelltext genau dieser Version: https://github.com/novnc/noVNC/tree/v{version}\n" in text
    assert f"Paket genau dieser Version: https://registry.npmjs.org/@novnc/novnc/-/novnc-{version}.tgz\n" in text


def _section(text: str, name: str) -> str:
    """Der Abschnitt eines Pakets in THIRD_PARTY_LICENSES (bis zum naechsten Paket)."""
    match = re.search(rf"^{re.escape(name)} \S+\nLizenz: .*?(?=^-{{78}}\n\S+ \S+\nLizenz: |^={{78}}|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name}: kein Abschnitt in THIRD_PARTY_LICENSES"
    return match.group(0)


def test_checked_in_file_has_the_vendored_notices_of_the_bundles():
    """Was die Pruefung des oeffentlichen Repos vermisst hat: noVNC bringt pako (MIT), DES mit eigenem
    Kopf und weitere Lizenztexte mit; xterm enthaelt Code aus VS Code; pyrage enthaelt Rust-Crates."""
    text = LICENSES_FILE.read_text(encoding="utf-8")
    novnc = _section(text, "@novnc/novnc")
    for label in ("[vendor/pako/LICENSE]", "[docs/LICENSE.MPL-2.0]", "[docs/LICENSE.BSD-2-Clause]", "[docs/LICENSE.BSD-3-Clause]",
                  "[docs/LICENSE.OFL-1.1]", "[Lizenzkopf aus core/crypto/des.js]"):
        assert label in novnc, label
    assert "Copyright (C) 2014-2016 by Vitaly Puzrin" in novnc and "Jef Poskanzer" in novnc and "Widget Workshop" in novnc
    assert "Copyright (c) Microsoft Corporation" in _section(text, "@xterm/xterm")
    assert "Modernizr" in _section(text, "react-dom")
    assert "Kazuhiko Arase" in _section(text, "qrcode-generator")
    assert "Sindre Sorhus" in _section(text, "tailwindcss")
    pyrage = _section(text, "pyrage")
    assert "[dist-info/sboms/pyrage.cyclonedx.json:" in pyrage
    assert re.search(r"^  age \S+\s+MIT OR Apache-2\.0", pyrage, re.MULTILINE)
    assert "UNICODE LICENSE V3" in pyrage  # Lizenztext, den die SBOM selbst mitbringt
    assert "pillow.cdx" not in text and "libimagequant" not in text


def test_docker_image_ships_the_license_file():
    dockerfile = (REPO / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^COPY\s+THIRD_PARTY_LICENSES\s+/app/THIRD_PARTY_LICENSES\s*$", dockerfile, re.MULTILINE)


# ---------------------------------------------------------------------------
# PyMuPDF ist raus (AGPL)
# ---------------------------------------------------------------------------


def test_pymupdf_is_gone_from_code_and_dependency_files():
    skip_dirs = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist"}
    import_re = re.compile(r"^\s*(import|from)\s+(fitz|pymupdf)\b", re.MULTILINE)
    offenders = []
    for path in REPO.rglob("*.py"):
        if skip_dirs & set(path.relative_to(REPO).parts):
            continue
        if import_re.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"PyMuPDF/fitz wird noch importiert (AGPL): {offenders}"
    for rel in ("deploy/constraints.txt", "backend/pyproject.toml", "sdk/python/pyproject.toml"):
        lines = [ln for ln in (REPO / rel).read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
        assert not [ln for ln in lines if re.search(r"pymupdf|fitz", ln, re.IGNORECASE)], rel


def test_pypdfium2_is_declared_and_pinned():
    constraints = (REPO / "deploy" / "constraints.txt").read_text(encoding="utf-8")
    assert re.search(r"^pypdfium2==\d+\.\d+\.\d+$", constraints, re.MULTILINE)
    assert "pypdfium2" in (REPO / "backend" / "pyproject.toml").read_text(encoding="utf-8")
