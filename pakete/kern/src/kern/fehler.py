"""Fehlermodell der Plattform (Implementierung 6) und FastAPI-Handler."""

import logging
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kern.antwort import Meta, aktuelle_anfrage_id

_log = logging.getLogger("kern.fehler")


class Fehlercode(StrEnum):
    NICHT_ANGEMELDET = "NICHT_ANGEMELDET"
    KEIN_RECHT = "KEIN_RECHT"
    NICHT_GEFUNDEN = "NICHT_GEFUNDEN"
    EINGABE_UNGUELTIG = "EINGABE_UNGUELTIG"
    ZU_GROSS = "ZU_GROSS"
    VIRUS = "VIRUS"
    VIRENPRUEFUNG_FEHLT = "VIRENPRUEFUNG_FEHLT"
    KONTINGENT = "KONTINGENT"
    PAKET_UNGUELTIG = "PAKET_UNGUELTIG"
    SIGNATUR_UNGUELTIG = "SIGNATUR_UNGUELTIG"
    REGISTER_ABWEICHUNG = "REGISTER_ABWEICHUNG"
    LAUFBEDINGUNG = "LAUFBEDINGUNG"
    BUDGET = "BUDGET"
    GATE_VERSTOSS = "GATE_VERSTOSS"
    AN_MENSCH = "AN_MENSCH"
    ZONENVERLETZUNG = "ZONENVERLETZUNG"
    FREIGABE_ABGELAUFEN = "FREIGABE_ABGELAUFEN"
    HASH_ABWEICHUNG = "HASH_ABWEICHUNG"
    ZIEL_FEHLER = "ZIEL_FEHLER"
    MODELL_FEHLER = "MODELL_FEHLER"
    INTERN = "INTERN"

    @property
    def status(self) -> int:
        return _STATUS[self]


_STATUS: dict[Fehlercode, int] = {
    Fehlercode.NICHT_ANGEMELDET: 401,
    Fehlercode.KEIN_RECHT: 403,
    Fehlercode.NICHT_GEFUNDEN: 404,
    Fehlercode.EINGABE_UNGUELTIG: 422,
    Fehlercode.ZU_GROSS: 413,
    Fehlercode.VIRUS: 422,
    Fehlercode.VIRENPRUEFUNG_FEHLT: 503,
    Fehlercode.KONTINGENT: 429,
    Fehlercode.PAKET_UNGUELTIG: 422,
    Fehlercode.SIGNATUR_UNGUELTIG: 422,
    Fehlercode.REGISTER_ABWEICHUNG: 503,
    Fehlercode.LAUFBEDINGUNG: 403,
    Fehlercode.BUDGET: 422,
    Fehlercode.GATE_VERSTOSS: 422,
    Fehlercode.AN_MENSCH: 200,
    Fehlercode.ZONENVERLETZUNG: 500,
    Fehlercode.FREIGABE_ABGELAUFEN: 409,
    Fehlercode.HASH_ABWEICHUNG: 409,
    Fehlercode.ZIEL_FEHLER: 502,
    Fehlercode.MODELL_FEHLER: 503,
    Fehlercode.INTERN: 500,
}

_STATUS_ZU_CODE = {
    401: Fehlercode.NICHT_ANGEMELDET,
    403: Fehlercode.KEIN_RECHT,
    404: Fehlercode.NICHT_GEFUNDEN,
    405: Fehlercode.NICHT_GEFUNDEN,
    413: Fehlercode.ZU_GROSS,
    429: Fehlercode.KONTINGENT,
}


class PlattformFehler(Exception):
    def __init__(  # noqa: PLR0913 - Felder aus S02, alle ausser code/nachricht nur per Schluesselwort
        self,
        code: Fehlercode,
        nachricht: str,
        *,
        grund: str | None = None,
        status: int | None = None,
        fail_closed: bool = True,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(code.name)
        self.code = code
        self.nachricht = nachricht
        self.grund = grund
        self.status = status if status is not None else code.status
        self.fail_closed = fail_closed
        self.retry_after = retry_after


def fehler_antwort(fehler: PlattformFehler, *, felder: list[dict[str, object]] | None = None) -> JSONResponse:
    inhalt: dict[str, object] = {
        "code": fehler.code.name,
        "nachricht": fehler.nachricht,
        "fail_closed": fehler.fail_closed,
    }
    if fehler.grund is not None:
        inhalt["grund"] = fehler.grund
    if felder is not None:
        inhalt["felder"] = felder
    meta = Meta(anfrage_id=aktuelle_anfrage_id())
    kopf = {"Retry-After": str(fehler.retry_after)} if fehler.retry_after is not None else None
    return JSONResponse(
        {"fehler": inhalt, "meta": meta.model_dump(exclude_none=True)}, status_code=fehler.status, headers=kopf
    )


def _intern() -> JSONResponse:
    return fehler_antwort(PlattformFehler(Fehlercode.INTERN, "Interner Fehler"))


async def _plattform(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, PlattformFehler):
        return _intern()
    return fehler_antwort(exc)


async def _validierung(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        return _intern()
    # nur Pfad und Art, nie Eingabewerte
    felder: list[dict[str, object]] = [
        {"pfad": [str(teil) for teil in fehler.get("loc", ())], "art": str(fehler.get("type", ""))}
        for fehler in exc.errors()
    ]
    return fehler_antwort(PlattformFehler(Fehlercode.EINGABE_UNGUELTIG, "Eingabe verletzt das Schema"), felder=felder)


async def _http(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        return _intern()
    code = _STATUS_ZU_CODE.get(exc.status_code, Fehlercode.INTERN)
    return fehler_antwort(PlattformFehler(code, code.name.replace("_", " ").lower(), status=exc.status_code))


class FehlerMiddleware:
    """Faengt unerwartete Ausnahmen ab: Antwort `INTERN` ohne Details, Log nur mit Typname, kein erneutes Werfen.

    Ersetzt den Exception-Handler von Starlette, der die Ausnahme samt Text an den Server weiterreicht.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        begonnen = False

        async def senden(nachricht: Message) -> None:
            nonlocal begonnen
            if nachricht["type"] == "http.response.start":
                begonnen = True
            await send(nachricht)

        try:
            await self.app(scope, receive, senden)
        except Exception as exc:  # noqa: BLE001 - bewusst: alles wird INTERN
            _log.error("unerwarteter_fehler", extra={"fehler_code": type(exc).__name__})
            if not begonnen:
                await _intern()(scope, receive, send)


def registriere_handler(app: FastAPI) -> None:
    app.add_exception_handler(PlattformFehler, _plattform)
    app.add_exception_handler(RequestValidationError, _validierung)
    app.add_exception_handler(StarletteHTTPException, _http)
