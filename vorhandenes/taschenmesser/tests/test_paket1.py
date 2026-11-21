import datetime as dt
import io
import json
import re
import zipfile

import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from fastapi.testclient import TestClient

from app import werkzeuge
from app.main import app
from app.markieren import _suche
from conftest import FakeModelle, pdf_bytes


@pytest.fixture(autouse=True)
def _aufraeumen():
    yield
    for k in ("cfg", "modelle"):
        if hasattr(app.state, k):
            delattr(app.state, k)


def _client(cfg, fake=None):
    app.state.cfg, app.state.modelle = cfg, fake or FakeModelle()
    return TestClient(app)


def _pdf(name, seiten, **kw):
    return ("dateien", (name, pdf_bytes(seiten, **kw), "application/pdf"))


# ---------------------------------------------------------------- abgleichen
ANTRAG = ["Elterngeldantrag\nName: Erika Musterfrau\nGeburtsdatum: 12.04.1985\nBruttoeinkommen: 3.200,00 EUR"]
GEHALT = ["Lohnabrechnung Maerz 2026\nArbeitnehmer: Musterfrau, Erika\nBrutto: 3.450,00 EUR"]
SCHEMA_EINK = {"type": "object", "properties": {
    "name": {"type": "string", "description": "Name der Person"},
    "geburtsdatum": {"type": "string", "format": "date", "description": "Geburtsdatum"},
    "einkommen": {"type": "number", "format": "betrag", "description": "monatliches Bruttoeinkommen", "toleranz": 0}}}
MUSTER = {"name": r"(?:Name|Arbeitnehmer): ([^\n]+)", "geburtsdatum": r"Geburtsdatum: (\S+)",
          "einkommen": r"Brutto(?:einkommen)?: ([\d.,]+)"}


def _lese_fake():
    def json_fn(nutzer, schema):
        assert "Erika" not in nutzer and "Musterfrau" not in nutzer  # auch im zweiten Dokument geschwärzt
        text = nutzer.split("Text:", 1)[1]
        out = {}
        for n in schema["properties"]:
            m = re.search(MUSTER[n], text)
            out[n] = {"wert": m.group(1).strip(), "seite": 1, "zitat": m.group(0).strip()} if m else {"wert": None, "seite": None, "zitat": None}
        return out
    return FakeModelle(json_fn=json_fn)


def test_abgleichen_findet_abweichung(cfg):
    with _client(cfg, _lese_fake()) as c:
        b = c.post("/v1/abgleichen", files=[_pdf("antrag.pdf", ANTRAG), _pdf("gehalt.pdf", GEHALT)],
                   data={"schema": json.dumps(SCHEMA_EINK)}).json()
    f = b["felder"]
    assert f["name"]["status"] == "gleich"                      # "Musterfrau, Erika" == "Erika Musterfrau"
    assert f["einkommen"]["status"] == "abweichung" and b["abweichungen"] == ["einkommen"]
    assert {w["dokument"]: w["wert"] for w in f["einkommen"]["werte"]} == {"antrag.pdf": 3200.0, "gehalt.pdf": 3450.0}
    assert f["geburtsdatum"]["status"] == "einmal" and f["geburtsdatum"]["werte"][0]["wert"] == "1985-04-12"
    assert b["ergebnis"] == "abweichung" and b["meta"]["modellaufrufe"]["llm"] == 2


def test_abgleichen_toleranz_und_gleiche_dateinamen(cfg):
    schema = json.loads(json.dumps(SCHEMA_EINK))
    schema["properties"]["einkommen"]["toleranz"] = 300
    with _client(cfg, _lese_fake()) as c:
        b = c.post("/v1/abgleichen", files=[_pdf("scan.pdf", ANTRAG), _pdf("scan.pdf", GEHALT)],
                   data={"schema": json.dumps(schema)}).json()
    assert b["ergebnis"] == "stimmig"
    assert [w["dokument"] for w in b["felder"]["einkommen"]["werte"]] == ["scan.pdf", "scan.pdf (2)"]


def test_abgleichen_braucht_zwei(cfg):
    with _client(cfg) as c:
        r = c.post("/v1/abgleichen", data={"text": "x", "schema": json.dumps(SCHEMA_EINK)})
    assert r.status_code == 422


# ---------------------------------------------------------------- ampel
PLAN = {"schwellen": {"gelb": 1, "rot": 3}, "regeln": [
    {"id": "umsatz_hoch", "wenn": {"feld": "umsatz", "op": ">", "wert": 100000}, "gewicht": 2, "hinweis": "Umsatz über 100.000 €"},
    {"id": "beginn_alt", "wenn": {"feld": "betriebsbeginn", "op": "<", "wert": "heute-365"}, "gewicht": 1,
     "hinweis": "Betriebsbeginn liegt über ein Jahr zurück"},
    {"id": "branche_ko", "wenn": {"feld": "branche", "op": "in", "wert": ["Spielhalle", "Pfandleihe"]}, "ko": True,
     "hinweis": "erlaubnispflichtige Branche"},
    {"id": "summe", "wenn": {"feld": "summe_positionen", "op": "!=", "feld2": "gesamtsumme", "toleranz": 0.01}, "gewicht": 3,
     "hinweis": "Positionen ergeben nicht die Gesamtsumme"}]}
DATEN = {"umsatz": 150000, "betriebsbeginn": "2020-01-01", "branche": "Einzelhandel", "summe_positionen": 999.99,
         "gesamtsumme": 1000}


def _ampel(c, daten, plan=PLAN, **kw):
    return c.post("/v1/ampel", data={"pruefplan": json.dumps(plan), "daten": json.dumps(daten)}, **kw)


def test_ampel_nur_daten_ohne_modell(cfg):
    fake = FakeModelle()
    with _client(cfg, fake) as c:
        b = _ampel(c, DATEN).json()
        assert b["korb"] == "rot" and b["punkte"] == 3
        assert {r["id"]: r["ergebnis"] for r in b["regeln"]} == {
            "umsatz_hoch": "ausgeloest", "beginn_alt": "ausgeloest", "branche_ko": "nicht_ausgeloest", "summe": "nicht_ausgeloest"}
        assert b["regeln"][0]["belege"][0] == {"feld": "umsatz", "wert": 150000, "status": "ok", "dokument": None,
                                               "seite": None, "zitat": None, "quelle": "fachverfahren"}
        assert _ampel(c, {**DATEN, "umsatz": 10, "betriebsbeginn": dt.date.today().isoformat()}).json()["korb"] == "gruen"
        assert _ampel(c, {**DATEN, "umsatz": 10, "branche": "Spielhalle"}).json()["korb"] == "rot"   # KO-Regel
        assert _ampel(c, {**DATEN, "umsatz": 10, "gesamtsumme": 1200}).json()["korb"] == "rot"
    assert fake.json_calls == [] and fake.waehle_calls == []


def test_ampel_unklares_ist_nie_gruen(cfg):
    daten = {k: v for k, v in DATEN.items() if k != "umsatz"}
    daten["betriebsbeginn"] = dt.date.today().isoformat()
    with _client(cfg) as c:
        b = _ampel(c, daten).json()
    assert b["punkte"] == 0 and b["korb"] == "gelb" and b["nicht_pruefbar"] == ["umsatz_hoch"]


def test_ampel_syntaxfehler(cfg):
    with _client(cfg) as c:
        assert _ampel(c, DATEN, {"regeln": [{"id": "x", "wenn": {"feld": "a", "op": "ungefaehr"}}]}).status_code == 422
        assert _ampel(c, DATEN, {"regeln": [{"id": "x", "wenn": {"alle": []}}]}).status_code == 422
        assert _ampel(c, DATEN, {"regeln": []}).status_code == 422
        tippfehler = {"nachweise": [{"id": "hr", "aussage": "x"}], "regeln": [{"id": "x", "wenn": {"nachweis": "h_r", "status": "fehlt"}}]}
        assert "nicht definiert" in _ampel(c, DATEN, tippfehler).json()["fehler"]


def test_ampel_mit_dokument_und_nachweis(cfg):
    plan = {"felder": {"type": "object", "properties": {"umsatz": {"type": "number", "format": "betrag", "description": "Umsatz"}}},
            "nachweise": [{"id": "hr", "aussage": "Ein Handelsregisterauszug liegt bei."}],
            "regeln": [{"id": "hr_fehlt", "wenn": {"nachweis": "hr", "status": "fehlt"}, "gewicht": 2, "hinweis": "HR-Auszug fehlt"},
                       {"id": "umsatz_hoch", "wenn": {"feld": "umsatz", "op": ">", "wert": 100000}, "gewicht": 2}]}
    fake = FakeModelle(json_fn=lambda n, s: {"umsatz": {"wert": "250.000,00", "seite": 1, "zitat": "Umsatz: 250.000,00 EUR"}},
                       waehle_fn=lambda n, o: {o[0]: 0.05, o[1]: 0.95})
    with _client(cfg, fake) as c:
        b = c.post("/v1/ampel", files=[_pdf("antrag.pdf", ["Gewerbeanzeige\nUmsatz: 250.000,00 EUR"])],
                   data={"pruefplan": json.dumps(plan), "daten": json.dumps({"branche": "Einzelhandel"})}).json()
    assert b["korb"] == "rot" and b["punkte"] == 4
    beleg = b["regeln"][1]["belege"][0]
    assert beleg["wert"] == 250000.0 and beleg["seite"] == 1 and beleg["quelle"] == "dokument"


# ---------------------------------------------------------------- zerlegen
STAPEL = ["Gewerbeanzeige (Anmeldung)\nName: Erika Musterfrau\nSeite 1 von 2",
          "Angaben zur Taetigkeit: Einzelhandel\nSeite 2 von 2",
          "",
          "Lohnabrechnung Maerz 2026\nBrutto: 3.450,00 EUR",
          "Lohnabrechnung April 2026\nBrutto: 3.450,00 EUR"]
TYPEN = [{"id": "gewerbeanzeige", "beschreibung": "Gewerbeanzeige"}, {"id": "lohn", "beschreibung": "Lohn- oder Gehaltsabrechnung"},
         {"id": "ausweis", "beschreibung": "Personalausweis oder Reisepass"}]


def _zerleg_fake():
    def waehle_fn(nutzer, opts):
        seite = nutzer.split("Aktuelle Seite:", 1)[1]
        ziel = "gewerbeanzeige" if "Gewerbeanzeige" in seite else "lohn" if "Lohnabrechnung" in seite else None
        p = {o: 0.01 for o in opts}
        treffer = next((o for o in opts if ziel and o.startswith(ziel + ":")), opts[-1])
        p[treffer] = 1 - 0.01 * (len(opts) - 1)
        return p
    return FakeModelle(waehle_fn=waehle_fn)


def test_zerlegen(cfg):
    fake = _zerleg_fake()
    with _client(cfg, fake) as c:
        b = c.post("/v1/zerlegen", files=[_pdf("stapel.pdf", STAPEL)], data={"typen": json.dumps(TYPEN)}).json()
    d = b["dokumente"]
    assert [(x["typ"], [s["seite"] for s in x["seiten"]]) for x in d] == [("gewerbeanzeige", [1, 2, 3]), ("lohn", [4]), ("lohn", [5])]
    assert d[0]["seiten"][1]["stufe"] == 0 and d[0]["seiten"][1]["regel"] == "Seite 2 von 2"   # ohne Modell
    assert d[0]["leerseiten"] == 1 and b["pfad"] == "auto"
    assert len(fake.waehle_calls) == 3                                                       # Seite 2 und Leerseite ohne Modell


def test_zerlegen_als_zip(cfg):
    with _client(cfg, _zerleg_fake()) as c:
        r = c.post("/v1/zerlegen", files=[_pdf("stapel.pdf", STAPEL)], data={"typen": json.dumps(TYPEN), "als_zip": "true"})
    assert r.headers["content-type"] == "application/zip" and r.headers["x-pfad"] == "auto"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["01_gewerbeanzeige.pdf", "02_lohn.pdf", "03_lohn.pdf", "zerlegung.json"]
    assert len(pdfium.PdfDocument(z.read("01_gewerbeanzeige.pdf"))) == 3


def test_zerlegen_ohne_modell_fail_closed(cfg):
    with _client(cfg, FakeModelle(fehler=True)) as c:
        r = c.post("/v1/zerlegen", files=[_pdf("stapel.pdf", STAPEL[:2])], data={"typen": json.dumps(TYPEN)})
    assert r.status_code == 503


# ---------------------------------------------------------------- nachforderung
def test_fristende_wochenende():
    freitag = dt.date(2026, 11, 20)
    assert werkzeuge.fristende(1, freitag) == dt.date(2026, 11, 23)   # Samstag -> Montag
    assert werkzeuge.fristende(14, freitag) == dt.date(2026, 12, 4)


def test_nachforderung(cfg):
    def json_fn(nutzer, schema):
        assert "Musterfrau" not in nutzer and "32.1-2026" not in nutzer
        return {"punkte": ["Bitte senden Sie uns einen aktuellen Auszug aus dem Handelsregister.",
                           "Bitte senden Sie uns eine Kopie Ihres Personalausweises, nicht älter als 3 Monate."]}
    fake = FakeModelle(json_fn=json_fn)
    with _client(cfg, fake) as c:
        b = c.post("/v1/nachforderung", data={
            "punkte": json.dumps(["Ein Handelsregisterauszug liegt bei.", "Kopie des Personalausweises"]),
            "anrede": "Sehr geehrte Frau Musterfrau", "aktenzeichen": "32.1-2026/17", "verfahren": "Gewerbeanzeige"}).json()
    assert b["punkte"][0]["formuliert"].startswith("Bitte senden Sie uns einen aktuellen Auszug")
    assert b["punkte"][1]["formuliert"] == "Kopie des Personalausweises"    # erfundene Frist "3 Monate" verworfen
    assert b["text"].startswith("Sehr geehrte Frau Musterfrau,") and "Aktenzeichen 32.1-2026/17 an" in b["text"]
    assert b["betreff"] == "Gewerbeanzeige – Aktenzeichen 32.1-2026/17 – Nachforderung von Unterlagen"
    assert dt.date.fromisoformat(b["frist"]).weekday() < 5 and b["stufe"] == 2


def test_nachforderung_aus_pruefergebnis_ohne_modell(cfg):
    pe = {"ergebnis": "unvollstaendig", "nachforderung": ["Kopie des Personalausweises"]}
    with _client(cfg, FakeModelle(fehler=True)) as c:
        b = c.post("/v1/nachforderung", data={"pruefergebnis": json.dumps(pe), "art": "anhoerung",
                                              "rechtsgrundlage": "§ 28 LVwVfG"}).json()
    assert b["stufe"] == 0 and "grund" in b and "1. Kopie des Personalausweises" in b["text"]
    assert "zu äußern (§ 28 LVwVfG):" in b["text"] and b["text"].startswith("Sehr geehrte Damen und Herren,")


def test_nachforderung_fehler(cfg):
    with _client(cfg) as c:
        assert c.post("/v1/nachforderung", data={"punkte": "[]"}).status_code == 422
        assert c.post("/v1/nachforderung", data={}).status_code == 422
        assert c.post("/v1/nachforderung", data={"punkte": '["x"]', "art": "mahnung", "formulieren": "false"}).status_code == 422


# ---------------------------------------------------------------- markieren
def _annots(pdf_data):
    pdf = pdfium.PdfDocument(pdf_data)
    return [raw.FPDFPage_GetAnnotCount(pdf[i].raw) for i in range(len(pdf))]


def test_suche_normalisiert():
    text = "Die Gewerbe-\r\nanmeldung erfolgt  „schriftlich“."
    s, e = _suche('Gewerbeanmeldung erfolgt "schriftlich"', text)
    assert text[s:e] == "Gewerbe-\r\nanmeldung erfolgt  „schriftlich“"
    assert _suche("mündlich", text) is None


def test_markieren_textebene_und_ocr(cfg):
    seiten = ["Name: Erika Musterfrau\nBetriebsbeginn: 01.03.2026", "Taetigkeit: Einzelhandel"]
    extraktion = {"daten": {
        "name": {"wert": "Erika Musterfrau", "status": "ok", "seite": 1, "zitat": "Name: Erika Musterfrau"},
        "taetigkeit": {"wert": "Einzelhandel", "status": "ok", "seite": 1, "zitat": "Taetigkeit: Einzelhandel"},  # Seite falsch
        "erlaubnis": {"wert": "ja", "status": "ok", "seite": 3, "zitat": "Nachweis Gewerbeerlaubnis"},              # nur per OCR
        "steuer": {"wert": "x", "status": "ok", "seite": 1, "zitat": "Steuernummer 99/999"},                       # steht nicht drin
        "plz": {"wert": None, "status": "fehlt", "seite": None, "zitat": None}}}
    with _client(cfg) as c:
        r = c.post("/v1/markieren", files=[_pdf("antrag.pdf", seiten, scan_text="Nachweis Gewerbeerlaubnis\nliegt vor")],
                   data={"fundstellen": json.dumps(extraktion)})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.headers["x-markiert"] == "3" and r.headers["x-nicht-gefunden"] == "1"
    assert _annots(r.content) == [1, 1, 1]
    assert "antrag_markiert.pdf" in r.headers["content-disposition"]


def test_markieren_nur_eine_datei(cfg):
    with _client(cfg) as c:
        r = c.post("/v1/markieren", files=[_pdf("a.pdf", ["x" * 30]), _pdf("b.pdf", ["y" * 30])], data={"fundstellen": "[]"})
        assert r.status_code == 422
        r = c.post("/v1/markieren", files=[_pdf("a.pdf", ["x" * 30])], data={"fundstellen": '"kaputt"'})
        assert r.status_code == 422


def test_pruefen_nutzt_vorformulierten_nachforderungstext(cfg):
    cl = {"nachweise": [{"id": "hr", "aussage": "Ein Handelsregisterauszug liegt bei.",
                         "nachforderung": "aktueller Auszug aus dem Handelsregister"}]}
    with _client(cfg, FakeModelle(waehle_fn=lambda n, o: {o[0]: 0.02, o[1]: 0.98})) as c:
        b = c.post("/v1/pruefen", data={"text": "Gewerbeanzeige", "checkliste": json.dumps(cl)}).json()
    assert b["nachforderung"] == ["aktueller Auszug aus dem Handelsregister"]
