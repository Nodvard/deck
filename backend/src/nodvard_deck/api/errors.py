"""Gemeinsame Fehlerantworten der API.

FastAPI haengt an jeden 422-Eintrag die komplette Eingabe (`input`) und bei eigenen
Pruefungen die Ausnahme (`ctx`). Bei Passwoertern, Tokens und privaten SSH-Schluesseln
darf nichts davon je in der Antwort (oder in einem Proxy-Log) zurueckkommen. Bleiben
`type`, `loc` und `msg` -- mehr braucht die Oberflaeche nicht.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_VALUE_ERROR_PREFIX = "Value error, "


def install_validation_error_handler(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for item in exc.errors():
            msg = str(item.get("msg", ""))
            # Eigene Pruefungen (ValueError) bekommen von pydantic ein englisches Praefix;
            # die deutschen Meldungen sollen unveraendert bei der Person ankommen.
            msg = msg.removeprefix(_VALUE_ERROR_PREFIX)
            errors.append({"type": item.get("type"), "loc": list(item.get("loc", ())), "msg": msg})
        return JSONResponse(status_code=422, content={"detail": errors})
