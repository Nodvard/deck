"""Werte für neue Stacks, ohne Geheimnisse in Kommandozeilen oder Vorlagen."""

from __future__ import annotations

import json
import re
import secrets
import shlex
from typing import Any

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def env_file(values: dict[str, str]) -> bytes:
    from .stacks import MAX_FILE_BYTES

    if not isinstance(values, dict) or not all(isinstance(name, str) and isinstance(value, str) for name, value in values.items()):
        raise ValueError("Variablen brauchen einen Namen und einen Textwert.")
    if len(values) > 100:
        raise ValueError("Höchstens 100 Variablen sind erlaubt.")
    lines = []
    for name, value in values.items():
        if not _NAME.fullmatch(name):
            raise ValueError("Variablennamen dürfen nur Buchstaben ohne Umlaute, Ziffern und „_“ enthalten und nicht mit einer Ziffer beginnen.")
        if any(ord(c) < 32 and c not in "\n\r\t" or ord(c) == 127 for c in value):
            raise ValueError(f"Die Variable {name} enthält ein unzulässiges Steuerzeichen.")
        lines.append(f"{name}={json.dumps(value, ensure_ascii=False).replace('$', '$$')}\n")
    content = "".join(lines).encode()
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"Die Variablen sind zusammen größer als {MAX_FILE_BYTES // 1024} KB.")
    return content


def valid_env_names(names: Any) -> bool:
    return isinstance(names, (list, tuple)) and len(names) <= 100 and all(isinstance(name, str) and _NAME.fullmatch(name) for name in names)


def with_saved_env(argv: list[str], names: tuple[str, ...]) -> list[str]:
    if not valid_env_names(names):
        raise ValueError("Ungültige Variablennamen.")
    if not names:
        return argv
    remove = " ".join(f"-u {shlex.quote(name)}" for name in names)
    script = f'docker_bin=$(command -v docker) || exit 1; exec env {remove} "$docker_bin" "$@"'
    return ["sh", "-c", script, "sh", *argv[1:]]


def cmd_with_env(argv: list[str], values: dict[str, str] | None) -> list[str]:
    if not values:
        return argv
    index = argv.index("docker")
    return [*argv[:index], "sh", "-s", "--", *argv[index + 1:]]


def env_stdin(values: dict[str, str] | None, content: bytes | None = None, *, saved: bool = False, pwd: str | None = None) -> bytes | None:
    """Compose liest die Werte aus einer privaten Prüfdatei; sie werden nie zur Umgebung der Shell."""
    if not values:
        return content
    script = 'docker_bin=$(command -v docker) || exit 1\n'
    marker = "NODVARD_" + secrets.token_hex(16)
    if not saved:
        data = env_file(values).decode()
        script += 'umask 077\nenv_path=$(mktemp) || exit 1\n'
        script += 'trap \'rm -f -- "$env_path"\' EXIT\ntrap \'exit 1\' HUP INT TERM\n'
        script += f"cat >\"$env_path\" <<'{marker}' || exit 1\n{data}{marker}\n"
        script += 'verb=$1; shift\n'
    remove = " ".join(f"-u {shlex.quote(name)}" for name in values)
    # Compose nimmt `${PWD}` nur aus der Umgebung, nie aus einer Variablendatei; von Hand gestartet ist es der
    # Stack-Ordner. Eine eingegebene Variable `PWD` ändert daran nichts, die Prüfung also auch nicht.
    directory = f" PWD={shlex.quote(pwd)}" if pwd is not None else ""
    command = '"$@"' if saved else '"$verb" --env-file "$env_path" "$@"'
    script += f'env {remove}{directory} "$docker_bin" {command}'
    if content is not None:
        script += f" <<'{marker}_COMPOSE'\n{content.decode()}\n{marker}_COMPOSE"
    script += '\nstatus=$?\nexit "$status"\n'
    return script.encode()


def hide_values(text: str | None, values: dict[str, str]) -> str | None:
    if text is None:
        return None
    hidden = {part for value in values.values() for part in (value, value.replace("$", "$$"), json.dumps(value)[1:-1], *value.splitlines()) if part}
    for value in sorted(hidden, key=len, reverse=True):
        text = text.replace(value, "…")
        for size in range(min(len(value) - 1, len(text)), 3, -1):
            if text.endswith(value[:size]):
                text = text[:-size] + "…"
                break
    return text


def hide_detail(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        return hide_values(value, values)
    if isinstance(value, dict):
        return {key: hide_detail(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [hide_detail(item, values) for item in value]
    return value
