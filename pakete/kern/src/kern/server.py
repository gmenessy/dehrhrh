"""Start der Dienste mit Granian (ASGI, HTTP/1.1 und HTTP/2)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from granian import Granian
from granian.constants import HTTPModes, Interfaces
from granian.server.common import AbstractServer

from kern.config import lade

UMGEBUNGEN = frozenset({"dev", "test", "prod"})
# Im Container; Erreichbarkeit regeln Netze und Eingang
ALLE_SCHNITTSTELLEN = "0.0.0.0"  # noqa: S104


@dataclass(frozen=True)
class TlsEinstellung:
    zert: Path
    schluessel: Path
    ca: Path | None = None  # gesetzt: Client-Zertifikate werden verlangt (mTLS)


@dataclass(frozen=True)
class _Umgebung:
    umgebung: str


def umgebung() -> str:
    wert = lade(_Umgebung).umgebung
    if wert not in UMGEBUNGEN:
        raise SystemExit("Konfiguration ungueltig: UMGEBUNG")
    return wert


def baue(
    app_pfad: str, port: int, *, tls: TlsEinstellung | None = None, adresse: str = ALLE_SCHNITTSTELLEN
) -> AbstractServer[Any]:
    if umgebung() == "prod" and tls is None:
        raise SystemExit("TLS ist in prod Pflicht (fail-closed)")
    return Granian(
        app_pfad,
        address=adresse,
        port=port,
        interface=Interfaces.ASGI,
        http=HTTPModes.auto,
        log_access=False,
        ssl_cert=tls.zert if tls else None,
        ssl_key=tls.schluessel if tls else None,
        ssl_ca=tls.ca if tls else None,
        ssl_client_verify=bool(tls and tls.ca),
    )


def starte(app_pfad: str, port: int, *, tls: TlsEinstellung | None = None) -> None:
    baue(app_pfad, port, tls=tls).serve()
