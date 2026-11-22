from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from kern.app import neue_app
from kern.fehler import Fehlercode, PlattformFehler

METRIK_TOKEN = "scrape-token-test"


class Eingabe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zahl: int


@pytest.fixture
def metrik_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    datei = tmp_path / "metrik.token"
    datei.write_text(METRIK_TOKEN + "\n", encoding="utf-8")
    monkeypatch.setenv("METRIK_TOKEN_DATEI", str(datei))
    monkeypatch.setenv("UMGEBUNG", "test")
    return METRIK_TOKEN


@pytest.fixture
def app(metrik_token: str) -> FastAPI:
    app = neue_app("testdienst", "0.1.0")

    @app.get("/gate")
    async def gate() -> None:
        raise PlattformFehler(Fehlercode.GATE_VERSTOSS, "Zitat nicht in Quelle", grund="VAL-004")

    @app.get("/kontingent")
    async def kontingent() -> None:
        raise PlattformFehler(Fehlercode.KONTINGENT, "Quote erschoepft", retry_after=30)

    @app.get("/kaputt")
    async def kaputt() -> None:
        raise RuntimeError("Klartext Max Mustermann im Fehler")

    @app.post("/eingabe")
    async def eingabe(daten: Eingabe) -> dict[str, int]:
        return {"zahl": daten.zahl}

    @app.get("/teile/{nr}")
    async def teile(nr: int) -> dict[str, int]:
        return {"nr": nr}

    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
