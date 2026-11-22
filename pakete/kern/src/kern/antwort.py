"""Antwortformat (Architektur 9.2), Anfrage-ID (ULID) und Anfragekontext."""

import re
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# 26 Zeichen, erstes hoechstens 7 (128 Bit)
_ULID_MUSTER = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")
KOPF_ANFRAGE_ID = "x-anfrage-id"

_anfrage_id: ContextVar[str | None] = ContextVar("anfrage_id", default=None)
_mandant: ContextVar[str | None] = ContextVar("mandant", default=None)
_lauf_id: ContextVar[str | None] = ContextVar("lauf_id", default=None)


def neue_ulid() -> str:
    wert = (time.time_ns() // 1_000_000) << 80 | secrets.randbits(80)
    zeichen = []
    for _ in range(26):
        zeichen.append(_CROCKFORD[wert & 0x1F])
        wert >>= 5
    return "".join(reversed(zeichen))


def ist_ulid(wert: str) -> bool:
    return _ULID_MUSTER.fullmatch(wert) is not None


def anfrage_id() -> str | None:
    return _anfrage_id.get()


def aktuelle_anfrage_id() -> str:
    """Anfrage-ID des Kontexts; ausserhalb einer Anfrage eine neue."""
    return _anfrage_id.get() or neue_ulid()


def kontext() -> dict[str, str]:
    werte = {"anfrage_id": _anfrage_id.get(), "mandant": _mandant.get(), "lauf_id": _lauf_id.get()}
    return {k: v for k, v in werte.items() if v is not None}


@contextmanager
def kontext_setzen(
    *, anfrage_id: str | None = None, mandant: str | None = None, lauf_id: str | None = None
) -> Iterator[None]:
    marken = []
    for variable, wert in ((_anfrage_id, anfrage_id), (_mandant, mandant), (_lauf_id, lauf_id)):
        if wert is not None:
            marken.append((variable, variable.set(wert)))
    try:
        yield
    finally:
        for variable, marke in reversed(marken):
            variable.reset(marke)


class Fundstelle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dok: str
    seite: int | None = None
    zitat: str | None = None


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anfrage_id: str
    paket: str | None = None
    version: str | None = None
    plan_hash: str | None = None
    modell_digest: str | None = None
    reife: str | None = None
    dauer_ms: int | None = None
    stufen: dict[str, int] | None = None


class Antwort[T](BaseModel):
    model_config = ConfigDict(extra="forbid")
    ergebnis: T
    fundstellen: list[Fundstelle] = Field(default_factory=list)
    meta: Meta


class AnfrageIdMiddleware:
    """Uebernimmt eine gueltige `X-Anfrage-Id` oder erzeugt eine; setzt Kontext und Antwortheader."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        kopf: dict[bytes, bytes] = dict(scope.get("headers", []))
        roh = kopf.get(KOPF_ANFRAGE_ID.encode(), b"").decode("latin-1")
        wert = roh if ist_ulid(roh) else neue_ulid()

        async def senden(nachricht: Message) -> None:
            if nachricht["type"] == "http.response.start":
                koepfe: list[Any] = [
                    k for k in nachricht.get("headers", []) if k[0].lower() != KOPF_ANFRAGE_ID.encode()
                ]
                koepfe.append((KOPF_ANFRAGE_ID.encode(), wert.encode()))
                nachricht["headers"] = koepfe
            await send(nachricht)

        marke = _anfrage_id.set(wert)
        try:
            await self.app(scope, receive, senden)
        finally:
            _anfrage_id.reset(marke)
