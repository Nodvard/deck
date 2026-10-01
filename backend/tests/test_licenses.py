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

import email.message
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
    assert not names & {"vite", "vitest", "typescript", "tailwindcss", "esbuild", "jsdom", "postcss", "@vitejs/plugin-react"}


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
    raw.decode("utf-8")


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
    assert re.search(r"@novnc/novnc 1\.7\.0 \(npm\): https://github\.com/novnc/noVNC", text)


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
