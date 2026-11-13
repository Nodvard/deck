"""Methodenprüfung vor dem Frontend-Fallback."""

from fastapi import routing
from starlette.responses import JSONResponse
from starlette.routing import Match, Router
from starlette.types import ASGIApp, Receive, Scope, Send


class ApiMethodsMiddleware:
    def __init__(self, app: ASGIApp, router: Router) -> None:
        self.app = app
        self.router = router

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            allowed: set[str] = set()
            iter_routes = getattr(routing, "iter_route_contexts", iter)
            for route in iter_routes(self.router.routes):
                if not isinstance(getattr(route, "original_route", route), routing.APIRoute):
                    continue
                match, _ = route.matches(scope)
                if match == Match.FULL:
                    break
                if match == Match.PARTIAL:
                    allowed.update(route.methods)
            else:
                if allowed:
                    response = JSONResponse(
                        {"detail": "Methode nicht erlaubt."}, status_code=405,
                        headers={"Allow": ", ".join(sorted(allowed))},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
