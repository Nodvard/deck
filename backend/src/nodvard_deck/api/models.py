"""Eingaben der Kern-API."""

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
