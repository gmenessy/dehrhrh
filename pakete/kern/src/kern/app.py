"""App-Fabrik: jeder Dienst startet mit denselben Bausteinen."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kern.antwort import AnfrageIdMiddleware
from kern.fehler import FehlerMiddleware, registriere_handler
from kern.log import richte_ein
from kern.metrik import MetrikMiddleware, registriere_metriken
from kern.server import umgebung

type Bereitschaft = Callable[[], Awaitable[bool]]


class SicherheitsMiddleware:
    """Entfernt `Server`, setzt `X-Content-Type-Options`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def senden(nachricht: Message) -> None:
            if nachricht["type"] == "http.response.start":
                koepfe = [k for k in nachricht.get("headers", []) if k[0].lower() != b"server"]
                koepfe.append((b"x-content-type-options", b"nosniff"))
                nachricht["headers"] = koepfe
            await send(nachricht)

        await self.app(scope, receive, senden if scope["type"] == "http" else send)


async def _immer_bereit() -> bool:
    return True


def neue_app(dienst: str, version: str, *, bereit: Bereitschaft | None = None) -> FastAPI:
    produktiv = umgebung() == "prod"
    richte_ein(dienst)
    app = FastAPI(
        title=dienst,
        version=version,
        docs_url=None if produktiv else "/docs",
        redoc_url=None,
        openapi_url=None if produktiv else "/openapi.json",
    )
    registriere_handler(app)
    registriere_metriken(app)
    pruefe_bereit = bereit or _immer_bereit

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        if await pruefe_bereit():
            return JSONResponse({"status": "bereit"})
        return JSONResponse({"status": "nicht_bereit"}, status_code=503)

    # zuletzt hinzugefuegt = aussen: AnfrageId > Metrik > Sicherheit > Fehler > Router
    app.add_middleware(FehlerMiddleware)
    app.add_middleware(SicherheitsMiddleware)
    app.add_middleware(MetrikMiddleware, dienst=dienst)
    app.add_middleware(AnfrageIdMiddleware)
    return app
