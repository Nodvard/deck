"""Berichte enthalten ausschließlich feste Werte, Klassen, Zähler und eine zufällige UUID."""

from __future__ import annotations

import json
from enum import StrEnum
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4

from ..version import __version__
from .codes import ErrorCode

VOCABULARY = json.loads(files(__package__).joinpath("vocabulary.json").read_text(encoding="utf-8"))
Installation = StrEnum("Installation", {value: value for value in VOCABULARY["installation"]})
ModuleId = StrEnum("ModuleId", {value: value for value in VOCABULARY["module"]})


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Choices(ClosedModel):
    basis: bool = Field(default=False, strict=True)
    nutzung: bool = Field(default=False, strict=True)
    fehler: bool = Field(default=False, strict=True)


class Basis(ClosedModel):
    version: Literal[__version__] = __version__
    produkt: Literal["deck", "dock"]
    installation: Installation
    arch: Literal["amd64", "arm64"]
    sprache: Literal["de", "en"]


class Usage(ClosedModel):
    module: list[ModuleId] = Field(max_length=len(VOCABULARY["module"]))
    fremd: Literal["0", "1", "2-5", "6+"]
    server: Literal["0", "1", "2-5", "6-20", "21+"]
    benutzer: Literal["1", "2-5", "6+"]
    zwei_faktor: bool
    sicherungen_aktiv: bool


class Errors(ClosedModel):
    arten: dict[ErrorCode, Annotated[int, Field(strict=True, ge=0, le=99)]]


class Report(ClosedModel):
    schema_version: Literal[1] = Field(default=1, alias="schema")
    id: UUID4
    basis: Basis | None = None
    nutzung: Usage | None = None
    fehler: Errors | None = None
