"""Antwortbeschreibungen für die API."""

from http import HTTPStatus

from fastapi import FastAPI


def error_responses(*codes: int) -> dict:
    responses = {}
    for code in codes:
        content = {"application/json": {"schema": {"type": "object"}}}
        if code == 500:
            content["text/plain"] = {"schema": {"type": "string"}}
        response = {"description": HTTPStatus(code).phrase, "content": content}
        if code == 405:
            response["headers"] = {"Allow": {"schema": {"type": "string"}, "description": "Erlaubte Methoden."}}
        responses[code] = response
    return responses


def file_responses(*media_types: str, ranges: bool = False) -> dict:
    content = {media: {"schema": {"type": "string", "format": "binary"}} for media in media_types}
    responses = {200: {"description": "OK", "content": content}}
    if ranges:
        responses[206] = {"description": "Teilinhalt.", "content": content}
        for code in (400, 416):
            responses[code] = {
                "description": HTTPStatus(code).phrase,
                "content": {"text/plain": {"schema": {"type": "string"}}},
            }
    return responses


API_RESPONSES = error_responses(405, 413, 500)


def describe_required_headers(app: FastAPI) -> None:
    original = app.openapi

    def openapi() -> dict:
        schema = original()
        for path in schema["paths"].values():
            for operation in path.values():
                if not isinstance(operation, dict):
                    continue
                for param in operation.get("parameters", []):
                    if param.get("in") == "header" and param["name"] in {"x-setup-code", "x-confirm-password"}:
                        param["required"] = True
        return schema

    app.openapi = openapi
