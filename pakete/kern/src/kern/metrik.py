"""Metriken: eigene Registry, Standardmetriken, Anfrage-Middleware, `/metrics` mit Scrape-Token."""

import hmac
import time
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kern.config import lade
from kern.fehler import Fehlercode, PlattformFehler, fehler_antwort

REGISTRY = CollectorRegistry(auto_describe=True)

ANFRAGEN = Counter(
    "plattform_anfragen_total", "Anfragen je Route und Status", ["dienst", "route", "status"], registry=REGISTRY
)
DAUER = Histogram("plattform_dauer_sekunden", "Dauer je Route", ["dienst", "route"], registry=REGISTRY)
MODELL_TOKENS = Counter(
    "plattform_modell_tokens_total", "Modell-Tokens", ["dienst", "modell", "richtung"], registry=REGISTRY
)
GATE_VERSTOSS = Counter(
    "plattform_gate_verstoss_total", "Gate-Verstoesse je Regel", ["dienst", "regel"], registry=REGISTRY
)
LOG_FELD_VERWORFEN = Counter(
    "plattform_log_feld_verworfen_total", "Aus Logs entfernte Felder", ["feld"], registry=REGISTRY
)

ROUTE_UNBEKANNT = "unbekannt"
PFAD_METRIKEN = "/metrics"


@dataclass(frozen=True)
class _MetrikEinstellung:
    metrik_token_datei: str | None = None


class MetrikMiddleware:
    """Zaehlt Anfragen und Dauer; Route als Muster, nie als konkreter Pfad."""

    def __init__(self, app: ASGIApp, dienst: str) -> None:
        self.app = app
        self.dienst = dienst

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 500
        beginn = time.perf_counter()

        async def senden(nachricht: Message) -> None:
            nonlocal status
            if nachricht["type"] == "http.response.start":
                status = nachricht["status"]
            await send(nachricht)

        try:
            await self.app(scope, receive, senden)
        finally:
            route = getattr(scope.get("route"), "path", ROUTE_UNBEKANNT)
            ANFRAGEN.labels(self.dienst, route, str(status)).inc()
            DAUER.labels(self.dienst, route).observe(time.perf_counter() - beginn)


def registriere_metriken(app: FastAPI) -> None:
    """`/metrics` nur mit Bearer-Token aus `METRIK_TOKEN_DATEI`; ohne Datei immer gesperrt."""
    token = lade(_MetrikEinstellung).metrik_token_datei
    erwartet = token.encode() if token else None

    @app.get(PFAD_METRIKEN, include_in_schema=False)
    async def metriken(request: Request) -> Response:
        kopf = request.headers.get("authorization", "")
        art, _, wert = kopf.partition(" ")
        if erwartet is None or art.lower() != "bearer" or not hmac.compare_digest(wert.encode(), erwartet):
            return fehler_antwort(PlattformFehler(Fehlercode.NICHT_ANGEMELDET, "Scrape-Token fehlt oder ist falsch"))
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
