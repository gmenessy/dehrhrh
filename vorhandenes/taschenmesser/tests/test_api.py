import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import IBAN_OK, FakeModelle, pdf_bytes

ANTRAG = [
    "Gewerbeanzeige (Anmeldung)\nName: Erika Musterfrau\nE-Mail: erika@example.org\nBetriebsbeginn: 01.03.2026",
    f"Taetigkeit: Einzelhandel mit Fahrraedern\nBankverbindung IBAN {IBAN_OK}\nAnlage: Personalausweis liegt bei",
]


def _client(fake, cfg, monkeypatch=None, keys=None):
    if keys is not None and monkeypatch:
        monkeypatch.setenv("API_KEYS", keys)
        monkeypatch.setenv("DEV", "0")
        from app.config import laden
        cfg = laden()
    app.state.cfg = cfg
    app.state.modelle = fake
    return TestClient(app)


@pytest.fixture(autouse=True)
def _aufraeumen():
    yield
    for k in ("cfg", "modelle"):
        if hasattr(app.state, k):
            delattr(app.state, k)


def test_auth(cfg, monkeypatch):
    c = _client(FakeModelle(), cfg, monkeypatch, keys="musterhausen:geheim")
    assert c.post("/v1/text", data={"text": "x"}).status_code == 401
    r = c.post("/v1/text", data={"text": "x"}, headers={"X-API-Key": "geheim"})
    assert r.status_code == 200 and r.json()["meta"]["mandant"] == "musterhausen"


def test_text_mit_ocr(cfg):
    pdf = pdf_bytes(ANTRAG, scan_text="Nachweis Gewerbeerlaubnis\nAktenzeichen 32.1-2026/17")
    with _client(FakeModelle(), cfg) as c:
        r = c.post("/v1/text", files={"dateien": ("antrag.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    d = r.json()["dokumente"][0]
    assert d["seiten"][0]["quelle"] == "textebene" and "Erika Musterfrau" in d["seiten"][0]["text"]
    assert d["seiten"][2]["quelle"] == "ocr" and "Gewerbeerlaubnis" in d["seiten"][2]["text"]
    assert d["ocr_seiten"] == 1


def test_schwaerzen(cfg):
    with _client(FakeModelle(), cfg) as c:
        r = c.post("/v1/schwaerzen", data={"text": ANTRAG[0] + "\n" + ANTRAG[1]})
    body = r.json()
    assert "[EMAIL_1]" in body["seiten"][0]["text"] and "[IBAN_1]" in body["seiten"][0]["text"]
    assert "erika@example.org" not in json.dumps(body)


SCHEMA = {"type": "object", "required": ["name", "betriebsbeginn", "iban", "taetigkeit", "steuernummer"], "properties": {
    "name": {"type": "string", "description": "Name der anzeigenden Person"},
    "betriebsbeginn": {"type": "string", "format": "date", "description": "Beginn des Betriebs"},
    "iban": {"type": "string", "format": "iban", "description": "IBAN"},
    "email": {"type": "string", "format": "email", "description": "E-Mail"},
    "taetigkeit": {"type": "string", "description": "angemeldete Tätigkeit"},
    "steuernummer": {"type": "string", "description": "Steuernummer"}}}


def _extraktion_fake():
    def json_fn(nutzer, schema):
        assert "erika@example.org" not in nutzer and IBAN_OK not in nutzer  # Modell sieht nur redigierte Fassung
        felder = schema["properties"]
        out = {}
        for n in felder:
            out[n] = {"wert": None, "seite": None, "zitat": None}
        if "name" in felder:
            out["name"] = {"wert": "Erika Musterfrau", "seite": 1, "zitat": "Name: Erika Musterfrau"}
        if "betriebsbeginn" in felder:
            out["betriebsbeginn"] = {"wert": "01.03.2026", "seite": 1, "zitat": "Betriebsbeginn: 01.03.2026"}
        if "taetigkeit" in felder:  # erfundenes Zitat -> darf nicht übernommen werden
            out["taetigkeit"] = {"wert": "Fahrradverleih", "seite": 2, "zitat": "Taetigkeit: Fahrradverleih"}
        return out
    return FakeModelle(json_fn=json_fn)


def test_extrahieren(cfg):
    fake = _extraktion_fake()
    with _client(fake, cfg) as c:
        r = c.post("/v1/extrahieren", files={"dateien": ("antrag.pdf", pdf_bytes(ANTRAG), "application/pdf")},
                   data={"schema": json.dumps(SCHEMA)})
    assert r.status_code == 200, r.text
    b = r.json()
    d = b["daten"]
    assert d["iban"] == {**d["iban"], "wert": IBAN_OK.replace(" ", ""), "status": "ok", "stufe": 0, "seite": 2}
    assert d["email"]["status"] == "ok" and d["email"]["stufe"] == 0
    assert d["name"]["status"] == "ok" and d["name"]["seite"] == 1 and d["name"]["dokument"] == "antrag.pdf"
    assert d["betriebsbeginn"]["wert"] == "2026-03-01"
    assert d["taetigkeit"]["status"] == "ohne_fundstelle" and d["taetigkeit"]["wert"] is None
    assert d["steuernummer"]["status"] == "fehlt"
    assert b["vollstaendig"] is False and set(b["fehlende_pflichtfelder"]) == {"taetigkeit", "steuernummer"}
    assert b["meta"]["modellaufrufe"]["llm"] == 1
    assert "iban" not in json.dumps(fake.json_calls[0]).split("Text:")[0]  # Stufe-0-Felder gehen nicht ans Modell


def test_extrahieren_platzhalter_rueck(cfg):
    text = "Kontakt: [x] info@a.de und zweite Adresse post@b.de, zuständig ist post@b.de"
    schema = {"type": "object", "properties": {"kontakt": {"type": "string", "format": "email", "description": "Kontaktadresse"}}}

    def json_fn(nutzer, schema):
        z = re.search(r"zuständig ist \[EMAIL_\d\]", nutzer).group(0)
        return {"kontakt": {"wert": z.split()[-1], "seite": 1, "zitat": z}}
    with _client(FakeModelle(json_fn=json_fn), cfg) as c:
        r = c.post("/v1/extrahieren", data={"text": text, "schema": json.dumps(schema)})
    d = r.json()["daten"]["kontakt"]
    assert d == {**d, "wert": "post@b.de", "status": "ok", "stufe": 2, "zitat": "zuständig ist post@b.de"}


def test_extrahieren_fail_closed(cfg):
    with _client(FakeModelle(fehler=True), cfg) as c:
        r = c.post("/v1/extrahieren", data={"text": "Name: X", "schema": json.dumps({"type": "object", "properties": {"name": {"type": "string"}}})})
    assert r.status_code == 503 and r.json()["fail_closed"] is True


def test_schema_fehler(cfg):
    with _client(FakeModelle(), cfg) as c:
        r = c.post("/v1/extrahieren", data={"text": "x", "schema": json.dumps({"type": "object", "properties": {"a": {"type": "array"}}})})
    assert r.status_code == 422


def test_zusammenfassen(cfg):
    def json_fn(nutzer, schema):
        assert "[EMAIL_1]" in nutzer
        return {"zusammenfassung": "Gewerbeanzeige von Erika Musterfrau, Kontakt [EMAIL_1].",
                "kernpunkte": [{"text": "Betrieb ab März", "seite": 1, "zitat": "Betriebsbeginn: 01.03.2026"},
                               {"text": "erfunden", "seite": 2, "zitat": "Gaststätte mit Alkoholausschank"}]}
    with _client(FakeModelle(json_fn=json_fn), cfg) as c:
        r = c.post("/v1/zusammenfassen", files={"dateien": ("a.pdf", pdf_bytes(ANTRAG), "application/pdf")}, data={"laenge": "kurz"})
    b = r.json()
    assert "erika@example.org" in b["zusammenfassung"]
    assert b["kernpunkte"][0]["belegt"] and b["kernpunkte"][0]["seite"] == 1
    assert not b["kernpunkte"][1]["belegt"] and b["unbelegte_kernpunkte"] == 1


def test_zusammenfassen_lang_map_reduce(cfg, monkeypatch):
    monkeypatch.setenv("KONTEXT_ZEICHEN", "120")
    from app.config import laden
    c2 = laden()
    calls = []

    def json_fn(nutzer, schema):
        calls.append(nutzer)
        if "kernpunkte" in schema["properties"]:
            return {"zusammenfassung": "Teil", "kernpunkte": []}
        return {"zusammenfassung": "Gesamt"}
    with _client(FakeModelle(json_fn=json_fn), c2) as c:
        r = c.post("/v1/zusammenfassen", files={"dateien": ("a.pdf", pdf_bytes(ANTRAG), "application/pdf")})
    assert r.json()["zusammenfassung"] == "Gesamt" and len(calls) >= 3


def test_klassifizieren(cfg):
    opt = [{"id": "gewerbeanzeige", "beschreibung": "Gewerbe-An-, Um- oder Abmeldung"}, {"id": "rechnung", "beschreibung": "Rechnung"}]

    def sicher(n, o):
        return {o[0]: 0.93, o[1]: 0.05, o[2]: 0.02}

    def unsicher(n, o):
        return {o[0]: 0.55, o[1]: 0.40, o[2]: 0.05}
    with _client(FakeModelle(waehle_fn=sicher), cfg) as c:
        b = c.post("/v1/klassifizieren", data={"text": ANTRAG[0], "optionen": json.dumps(opt)}).json()
    assert b["wert"] == "gewerbeanzeige" and b["pfad"] == "auto"
    with _client(FakeModelle(waehle_fn=unsicher), cfg) as c:
        b = c.post("/v1/klassifizieren", data={"text": ANTRAG[0], "optionen": json.dumps(opt)}).json()
    assert b["pfad"] == "mensch" and b["grund"] == "unter der Schwelle"
    with _client(FakeModelle(fehler=True), cfg) as c:
        b = c.post("/v1/klassifizieren", data={"text": ANTRAG[0], "optionen": json.dumps(opt)}).json()
    assert b["pfad"] == "mensch" and b["fail_closed"] is True


def test_pruefen(cfg):
    def waehle_fn(nutzer, o):
        if "Personalausweis" in nutzer.split("Aussage:")[1]:
            return {o[0]: 0.95, o[1]: 0.05}
        if "Handelsregister" in nutzer.split("Aussage:")[1]:
            return {o[0]: 0.04, o[1]: 0.96}
        return {o[0]: 0.50, o[1]: 0.50}
    cl = {"nachweise": [{"id": "ausweis", "aussage": "Eine Kopie des Personalausweises liegt bei."},
                        {"id": "hr", "aussage": "Ein Handelsregisterauszug liegt bei."}],
          "pflichtfelder": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "description": "Name"}}}}
    fake = FakeModelle(waehle_fn=waehle_fn, json_fn=lambda n, s: {"name": {"wert": "Erika Musterfrau", "seite": 1, "zitat": "Erika Musterfrau"}})
    with _client(fake, cfg) as c:
        b = c.post("/v1/pruefen", files={"dateien": ("a.pdf", pdf_bytes(ANTRAG), "application/pdf")}, data={"checkliste": json.dumps(cl)}).json()
    st = {n["id"]: n["status"] for n in b["nachweise"]}
    assert st == {"ausweis": "vorhanden", "hr": "fehlt"}
    assert b["ergebnis"] == "unvollstaendig" and b["nachforderung"] == ["Ein Handelsregisterauszug liegt bei."]
    cl["nachweise"].append({"id": "erlaubnis", "aussage": "Eine Erlaubnis nach § 34c GewO liegt bei."})
    with _client(fake, cfg) as c:
        b = c.post("/v1/pruefen", files={"dateien": ("a.pdf", pdf_bytes(ANTRAG), "application/pdf")}, data={"checkliste": json.dumps(cl)}).json()
    assert b["ergebnis"] == "pruefen" and b["zu_pruefen"] == ["erlaubnis"]


def test_grenzen(cfg, monkeypatch):
    monkeypatch.setenv("MAX_SEITEN", "1")
    from app.config import laden
    with _client(FakeModelle(), laden()) as c:
        r = c.post("/v1/text", files={"dateien": ("a.pdf", pdf_bytes(ANTRAG), "application/pdf")})
        assert r.status_code == 413
        r = c.post("/v1/text", files={"dateien": ("x.bin", b"\xff\xfe\x00\x01bin\xe4r", "application/octet-stream")})
        assert r.status_code == 415
