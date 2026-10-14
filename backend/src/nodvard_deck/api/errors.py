"""Gemeinsame Fehlerantworten der API.

Bei 422 bleiben nur `type`, `loc`, `msg` (deutsch); `input` und `ctx` könnten Passwörter, Tokens, Schlüssel enthalten."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_VALUE_ERROR_PREFIX = "Value error, "


def _german_message(error_type: str, ctx: Mapping[str, Any], fallback: str) -> str:
    """Deutscher Satz zu den pydantic-Standardprüfungen, gebaut aus `type` und `ctx` (`ctx` selbst kommt nie in die Antwort).

    Eigene Prüfungen (`value_error`) tragen schon einen deutschen Text und bleiben unverändert, ebenso alles Unbekannte."""
    if error_type == "missing":
        return "Pflichtangabe fehlt."
    if error_type == "string_too_short":
        minimum = ctx.get("min_length")
        return "Darf nicht leer sein." if minimum == 1 else f"Mindestens {minimum} Zeichen."
    if error_type == "string_too_long":
        return f"Höchstens {ctx.get('max_length')} Zeichen."
    if error_type == "too_short":
        minimum = ctx.get("min_length")
        return "Mindestens 1 Eintrag." if minimum == 1 else f"Mindestens {minimum} Einträge."
    if error_type == "too_long":
        return f"Höchstens {ctx.get('max_length')} Einträge."
    if error_type == "greater_than_equal":
        return f"Mindestens {ctx.get('ge')}."
    if error_type == "less_than_equal":
        return f"Höchstens {ctx.get('le')}."
    if error_type == "greater_than":
        return f"Muss größer als {ctx.get('gt')} sein."
    if error_type == "less_than":
        return f"Muss kleiner als {ctx.get('lt')} sein."
    if error_type in {"int_parsing", "int_type", "int_from_float"}:
        return "Muss eine ganze Zahl sein."
    if error_type in {"float_parsing", "float_type"}:
        return "Muss eine Zahl sein."
    if error_type in {"bool_parsing", "bool_type"}:
        return "Muss „ja“ oder „nein“ sein."
    if error_type == "string_type":
        return "Muss ein Text sein."
    if error_type == "literal_error":
        expected = ctx.get("expected")
        allowed = str(expected).replace("'", "").replace(" or ", " oder ")
        return f"Erlaubt ist nur: {allowed}." if expected else "Ungültiger Wert."
    if error_type == "json_invalid":
        return "Das ist kein gültiges JSON."
    if error_type == "extra_forbidden":
        return "Unbekannte Angabe."
    return fallback


def install_validation_error_handler(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for item in exc.errors():
            msg = str(item.get("msg", ""))
            # Eigene Prüfungen (ValueError) bekommen von pydantic ein englisches Präfix;
            # die deutschen Meldungen sollen unverändert bei der Person ankommen.
            msg = msg.removeprefix(_VALUE_ERROR_PREFIX)
            msg = _german_message(str(item.get("type", "")), item.get("ctx") or {}, msg)
            errors.append({"type": item.get("type"), "loc": list(item.get("loc", ())), "msg": msg})
        return JSONResponse(status_code=422, content={"detail": errors})


class CodedHTTPException(HTTPException):
    """HTTP-Fehler mit festem Bezeichner: Die Antwort ist `{detail, code}`, damit Oberfläche und Apps ihn ohne Textvergleich
    erkennen. `extra` hängt weitere Felder an (z. B. den Fingerabdruck eines Server-Schlüssels), überschreibt aber nie
    `detail` und `code`."""

    def __init__(
        self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.extra = dict(extra or {})

    def response(self) -> JSONResponse:
        return JSONResponse(
            {**self.extra, "detail": self.detail, "code": self.code}, status_code=self.status_code, headers=self.headers
        )


def install_coded_error_handler(app: FastAPI) -> None:
    @app.exception_handler(CodedHTTPException)
    async def _coded_error(_request: Request, exc: CodedHTTPException) -> JSONResponse:
        return exc.response()
