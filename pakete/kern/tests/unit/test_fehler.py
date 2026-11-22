from fastapi.testclient import TestClient

from kern.antwort import ist_ulid
from kern.fehler import Fehlercode, PlattformFehler


def test_gate_verstoss_format(client: TestClient) -> None:
    antwort = client.get("/gate")
    assert antwort.status_code == 422
    daten = antwort.json()
    assert daten["fehler"] == {
        "code": "GATE_VERSTOSS",
        "grund": "VAL-004",
        "nachricht": "Zitat nicht in Quelle",
        "fail_closed": True,
    }
    assert ist_ulid(daten["meta"]["anfrage_id"])
    assert daten["meta"]["anfrage_id"] == antwort.headers["x-anfrage-id"]


def test_unerwartete_ausnahme_ohne_details(client: TestClient) -> None:
    antwort = client.get("/kaputt")
    assert antwort.status_code == 500
    assert antwort.json()["fehler"]["code"] == "INTERN"
    text = antwort.text
    assert "Mustermann" not in text
    assert "RuntimeError" not in text
    assert "Traceback" not in text


def test_validierung_ohne_werte(client: TestClient) -> None:
    antwort = client.post("/eingabe", json={"zahl": "geheimer-wert", "extra": "noch-geheimer"})
    assert antwort.status_code == 422
    daten = antwort.json()
    assert daten["fehler"]["code"] == "EINGABE_UNGUELTIG"
    assert "geheimer-wert" not in antwort.text
    assert "noch-geheimer" not in antwort.text
    pfade = {tuple(f["pfad"]) for f in daten["fehler"]["felder"]}
    assert ("body", "zahl") in pfade
    assert ("body", "extra") in pfade


def test_kontingent_mit_retry_after(client: TestClient) -> None:
    antwort = client.get("/kontingent")
    assert antwort.status_code == 429
    assert antwort.headers["retry-after"] == "30"


def test_nicht_gefunden_im_plattformformat(client: TestClient) -> None:
    antwort = client.get("/gibt-es-nicht")
    assert antwort.status_code == 404
    assert antwort.json()["fehler"]["code"] == "NICHT_GEFUNDEN"


def test_alle_codes_mit_status() -> None:
    erwartet = {
        "NICHT_ANGEMELDET": 401,
        "KEIN_RECHT": 403,
        "NICHT_GEFUNDEN": 404,
        "EINGABE_UNGUELTIG": 422,
        "ZU_GROSS": 413,
        "VIRUS": 422,
        "VIRENPRUEFUNG_FEHLT": 503,
        "KONTINGENT": 429,
        "PAKET_UNGUELTIG": 422,
        "SIGNATUR_UNGUELTIG": 422,
        "REGISTER_ABWEICHUNG": 503,
        "LAUFBEDINGUNG": 403,
        "BUDGET": 422,
        "GATE_VERSTOSS": 422,
        "AN_MENSCH": 200,
        "ZONENVERLETZUNG": 500,
        "FREIGABE_ABGELAUFEN": 409,
        "HASH_ABWEICHUNG": 409,
        "ZIEL_FEHLER": 502,
        "MODELL_FEHLER": 503,
        "INTERN": 500,
    }
    assert {c.name: c.status for c in Fehlercode} == erwartet


def test_status_ueberschreibbar() -> None:
    fehler = PlattformFehler(Fehlercode.INTERN, "x", status=503, fail_closed=False)
    assert fehler.status == 503
    assert fehler.fail_closed is False
    assert fehler.code is Fehlercode.INTERN
