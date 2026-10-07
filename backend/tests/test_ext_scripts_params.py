"""`nodvard_deck_ext_scripts.params.substitute_params`."""

from __future__ import annotations

import itertools
import random
import re
import shlex
import sys
from pathlib import Path
from string import Template

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "scripts" / "src"))

from nodvard_deck_ext_scripts.params import ParamError, substitute_params  # noqa: E402


def test_substitutes_known_placeholder_with_shell_quoting():
    result = substitute_params("echo $greeting", {"greeting": {"type": "string"}}, {"greeting": "hi there"})
    assert result == "echo 'hi there'"


def test_falls_back_to_default_when_value_missing():
    result = substitute_params(
        "echo $level", {"level": {"type": "string", "default": "info"}}, {}
    )
    assert result == "echo info"


def test_missing_value_without_default_raises():
    with pytest.raises(ParamError, match="fehlt"):
        substitute_params("echo $level", {"level": {"type": "string"}}, {})


def test_unknown_value_key_is_rejected():
    """Sicherheitsrelevant: ein Wert fuer einen Namen, den das Skript gar nicht
    deklariert hat, darf nicht still durchgereicht werden."""
    with pytest.raises(ParamError, match="Unbekannte Parameter"):
        substitute_params("echo hi", {}, {"injected": "$(rm -rf /)"})


def test_shell_quoting_neutralizes_command_injection_attempt():
    result = substitute_params(
        "echo $name", {"name": {"type": "string"}}, {"name": "$(rm -rf /); echo pwned"}
    )
    assert "$(rm -rf /)" not in result or result.count("'") >= 2
    assert result == "echo '$(rm -rf /); echo pwned'"


def test_undeclared_placeholder_is_left_for_the_shell():
    """Frueher ein Fehler ("unbekannter Platzhalter"). Ein `$name`, das kein Parameter des
    Skripts ist, ist aber fast immer eine Shell-Variable -- sie bleibt stehen und die
    Shell setzt sie ein. Der Tippfehler-Schutz gilt weiter fuer die WERTE: ein Wert fuer
    einen nicht deklarierten Namen wird abgelehnt (`test_unknown_value_key_is_rejected`)."""
    result = substitute_params("echo $typo", {"typo_other": {"type": "string", "default": "x"}}, {})
    assert result == "echo $typo"


@pytest.mark.parametrize(
    "content",
    [
        "echo $HOME",
        'for f in /var/log/*.log; do gzip "$f"; done',
        "echo $(date +%F)",
        "echo $1 $2 $# $@ $* $? $! $- $0",
        "echo ${VAR:-standard} ${#VAR} ${VAR%.log}",
        "awk '{print $5}'",
        "echo kostet 5$",
        "echo $",
        "echo $ {x}",
        "printf '%s\\n' $'a\\tb'",
    ],
)
def test_normal_shell_syntax_passes_through_unchanged(content):
    """Jedes `$`, das kein deklarierter Parameter ist, gehoert der Shell. Frueher liess
    jedes davon den Lauf mit `ParamError` scheitern."""
    assert substitute_params(content, {"level": {"type": "string", "default": "info"}}, {}) == content


def test_declared_params_are_quoted_in_both_spellings_next_to_shell_variables():
    result = substitute_params(
        'for f in $dir/*.log ${dir}/alt/*.log; do gzip "$f"; done; echo ${dir}x $HOME',
        {"dir": {"type": "string"}},
        {"dir": "/var/log/mein dienst"},
    )
    assert result == (
        "for f in '/var/log/mein dienst'/*.log '/var/log/mein dienst'/alt/*.log; "
        "do gzip \"$f\"; done; echo '/var/log/mein dienst'x $HOME"
    )


def test_a_name_is_only_replaced_as_a_whole():
    """Wie bei `string.Template`: `$hostname` ist der Name `hostname`, nicht `host` plus
    "name". Wer den Parameter direkt vor Buchstaben braucht, schreibt `${host}name`.
    Gross-/Kleinschreibung zaehlt (`$HOST` ist nicht `host`)."""
    schema = {"host": {"type": "string", "default": "pve1"}}
    assert substitute_params("echo $hostname ${host}name $host_2 $HOST $host.lan $host", schema, {}) == (
        "echo $hostname pve1name $host_2 $HOST pve1.lan pve1"
    )


def test_double_dollar_still_means_one_dollar():
    """Bestehende Skripte schreiben ein `$` als `$$` -- das bleibt so, sonst gaebe ein heute
    funktionierendes Skript wie `echo $$HOME` ploetzlich etwas anderes an die Shell. Die
    Prozessnummer der Shell (`$$`) steht deshalb als `$$$$` im Skript."""
    schema = {"name": {"type": "string", "default": "welt"}}
    assert substitute_params("echo $$HOME $$name $$$name $$$$ $?", schema, {}) == "echo $HOME $name $welt $$ $?"


def test_escape_dollars_round_trips_any_text():
    """`escape_dollars` ist die Umkehrung: was es liefert, ergibt nach der Ersetzung genau
    den Ausgangstext, auch wenn ein Name darin ein deklarierter Parameter ist."""
    from nodvard_deck_ext_scripts.params import escape_dollars

    schema = {"name": {"type": "string", "default": "x"}, "HOME": {"type": "string", "default": "y"}}
    for text in ["", "$", "$$", "$$$", "echo $HOME $name ${name} $(date) $$ $?", "a$b$$c$$$d${e}"]:
        assert substitute_params(escape_dollars(text), schema, {}) == text


# --- Rueckwaertsvertraeglichkeit: alt gegen neu ---------------------------------------


def _old_substitute(content, params_schema, values):
    """Die Ersetzung bis einschliesslich 0.6 (`string.Template.substitute` auf dem ganzen
    Skript), woertlich -- Massstab fuer die Rueckwaertsvertraeglichkeit."""
    unknown = set(values) - set(params_schema)
    if unknown:
        raise ParamError(f"Unbekannte Parameter: {sorted(unknown)}")
    resolved = {}
    for name, spec in params_schema.items():
        if name in values:
            raw = values[name]
        elif "default" in spec:
            raw = spec["default"]
        else:
            raise ParamError(f"Parameter '{name}' fehlt (kein Default in params_schema).")
        resolved[name] = shlex.quote(str(raw))
    try:
        return Template(content).substitute(resolved)
    except KeyError as exc:
        raise ParamError(f"Skript verwendet unbekannten Platzhalter: {exc}") from exc
    except ValueError as exc:
        raise ParamError(f"Ungültiger Platzhalter im Skript: {exc}") from exc


_SCHEMA = {
    "a": {"type": "string", "default": "x y"},
    "ab": {"type": "string", "default": ""},
    "_1": {"type": "string", "default": "$a ${ab}"},
    "A": {"type": "string", "default": "it's"},
}
# Einzelzeichen fuer die vollstaendige Aufzaehlung: Klammern, Namen und Namensgrenzen
# (`-`), dazu das Kelvin-Zeichen, das bei Unicode-Gross-/Kleinschreibung als `k` gaelte.
_ALPHABET = ["$", "{", "}", "a", "b", "_", "1", "A", "K", "-"]
# Bausteine fuer zufaellige, laengere Skripte.
_TOKENS = [
    "$", "$$", "$$$$", "{", "}", "${", "a", "ab", "_1", "A", "b", "HOME", "x", "_", "1", "9", " ", "\n", '"', "'",
    "(", ")", ":-", "?", "#", "@", "*", "!", ".", "/", "-", "K", "ı", "ſ", "ä", "\t",
]


def _exhaustive_contents(max_len=5):
    for length in range(max_len + 1):
        for chars in itertools.product(_ALPHABET, repeat=length):
            yield "".join(chars)


# Bausteine, die schon die alte Ersetzung meist durchliess (Skripte im Stil von heute).
_OLD_STYLE_TOKENS = [
    "$$", "$$$$", "$a", "${a}", "$ab", "${ab}", "$_1", "${_1}", "$A", "${A}", "$$a", "$${", " ", "x", "b", "{", "}",
    "\n", "1", "_", "-", '"', "'", "(", ")", "\u212a", "\u00e4",
]


def _random_contents(count=20000, seed=20261003):
    rng = random.Random(seed)
    for tokens in (_TOKENS, _OLD_STYLE_TOKENS):
        for _ in range(count):
            yield "".join(rng.choice(tokens) for _ in range(rng.randint(1, 30)))


_REAL_SCRIPTS = [
    "#!/bin/sh\napt-get update && apt-get -y upgrade\n",
    "#!/bin/sh\nping -c 1 $a\n",
    'for f in /var/log/*.log; do gzip "$$f"; done\n',
    "df -h | awk '{print $$5}'\n",
    "echo $$HOME ${a}b $ab $$$a\n",
    "kill -0 $$$$ && echo ${_1}\n",
    "x=$${VAR:-standard}; echo $$x $A\n",
]


def test_every_script_that_ran_before_gives_the_same_command():
    """Fuer JEDES Skript, das die alte Ersetzung ohne Fehler durchliess, muss die neue Zeichen
    fuer Zeichen denselben Befehl liefern: vollstaendig alle kurzen Inhalte aus Sonderzeichen
    und Namen, dazu viele zufaellige laengere und ein paar echte Skripte. (Gruen mit altem
    und neuem Code: der Test vergleicht beide.)"""
    compared = 0
    contents = itertools.chain(_exhaustive_contents(), _random_contents(), _REAL_SCRIPTS)
    for content in contents:
        try:
            expected = _old_substitute(content, _SCHEMA, {})
        except ParamError:
            continue
        assert substitute_params(content, _SCHEMA, {}) == expected, repr(content)
        compared += 1
    # Nicht leer verglichen: genug Inhalte mit `$$`, `$a`, `${ab}` usw. liefen alt durch.
    assert compared > 80000


def test_only_double_dollars_change_when_no_parameter_is_written():
    """Ohne deklarierten Parameternamen im Skript aendert sich nur `$$` (zu `$`) -- jedes
    andere `$` bleibt, und kein Inhalt laesst die Ersetzung mehr scheitern."""
    schema = {"zzz": {"type": "string", "default": "nie benutzt"}}
    for content in itertools.chain(_exhaustive_contents(), _random_contents()):
        assert substitute_params(content, schema, {}) == re.sub(r"\$\$", "$", content), repr(content)
