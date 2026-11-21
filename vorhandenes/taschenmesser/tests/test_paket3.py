import csv
import io
import json
import re

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient

from app import goldset, herausgabe
from app.config import laden
from app.dokument import ocr_bild
from app.main import app
from app.paket3 import aktenplan_baum, gleichlautend
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


# ---------------------------------------------------------------- Aktenplan
PLAN = ([{"az": "0", "titel": "Allgemeine Verwaltung"}, {"az": "02", "titel": "Organisation"},
         {"az": "022", "titel": "Geschäftsverteilung"}, {"az": "022.3", "titel": "Vertretungsregeln"},
         {"az": "1", "titel": "Öffentliche Sicherheit und Ordnung"}, {"az": "12", "titel": "Gewerbe"},
         {"az": "122", "titel": "Gewerbeanzeigen", "beschreibung": "An-, Um-, Abmeldungen"}, {"az": "123", "titel": "Gaststätten"}]
        + [{"az": f"9{i:02d}", "titel": f"Sonstiges {i}"} for i in range(25)])   # > 18 Wurzeln -> Turnier


def test_aktenplan_baum():
    baum = aktenplan_baum(PLAN)
    assert len(baum) == 27
    null = next(b for b in baum if b["az"] == "0")
    assert null["kinder"][0]["kinder"][0]["kinder"][0]["az"] == "022.3"
    with pytest.raises(ValueError):
        aktenplan_baum([{"az": "1", "titel": "a"}, {"az": "1", "titel": "b"}])


def _plan_fake(ziel: set[str], p: float = 0.92):
    def waehle_fn(n, opts):
        out = {o: 0.0 for o in opts}
        treffer = [o for o in opts if o.split(" ", 1)[0] in ziel]
        if treffer:
            out[treffer[0]] = p
            rest = (1 - p) / (len(opts) - 1)
            for o in opts:
                if o != treffer[0]:
                    out[o] = rest
        else:
            out[opts[-1]] = 0.9
            for o in opts[:-1]:
                out[o] = 0.1 / (len(opts) - 1)
        return out
    return FakeModelle(waehle_fn=waehle_fn)


def test_aktenplan_stufenweise(cfg):
    fake = _plan_fake({"1", "12", "122"})
    with _client(cfg, fake) as c:
        b = c.post("/v1/aktenplan", data={"text": "Gewerbeanmeldung Fahrradhandel", "aktenplan": json.dumps(PLAN)}).json()
    assert b["az"] == "122" and [s["az"] for s in b["pfad"]] == ["1", "12", "122"] and b["pfad_status"] == "auto"
    assert len(fake.waehle_calls) == 2 + 1 + 1 + 1        # Ebene 1: zwei Gruppen + Finale, dann je ein Aufruf
    with _client(cfg, _plan_fake({"1"})) as c:            # Ebene 2: keine passt
        b = c.post("/v1/aktenplan", data={"text": "x", "aktenplan": json.dumps(PLAN)}).json()
    assert b["az"] == "1" and b["vollstaendig"] is False and b["pfad_status"] == "mensch" and "Ebene 2" in b["grund"]
    with _client(cfg, _plan_fake({"1", "12", "122"}, p=0.7)) as c:
        b = c.post("/v1/aktenplan", data={"text": "x", "aktenplan": json.dumps(PLAN)}).json()
    assert b["az"] == "122" and b["pfad_status"] == "mensch" and b["grund"] == "unter der Schwelle"


# ---------------------------------------------------------------- Herausgabe
SEITEN = ["Antrag auf Akteneinsicht\nName: Erika Musterfrau\nE-Mail: erika@example.org\nIBAN DE89 3704 0044 0532 0130 00\n"
          "Bearbeitet von Buergermeister Klein",
          "Allgemeine Hinweise zur Gebuehrenordnung der Stadt."]


def _ocr(pdf_data: bytes, seite: int) -> str:
    cfg = laden()
    pdf = pdfium.PdfDocument(pdf_data)
    return ocr_bild(pdf[seite].render(scale=cfg.ocr_dpi / 72).to_pil(), cfg)


def test_herausgabe_vorschau_und_pdf(cfg):
    datei = pdf_bytes(SEITEN, scan_text="Unterschrift Erika Musterfrau")
    with _client(cfg) as c:
        v = c.post("/v1/herausgabe", files=[("dateien", ("akte.pdf", datei, "application/pdf"))],
                   data={"vorschau": "true", "ausnahmen": json.dumps(["Klein"])}).json()
        texte = {f["text"]: f for f in v["funde"]}
        assert {"Erika Musterfrau", "erika@example.org", "DE89 3704 0044 0532 0130 00"} <= set(texte)
        assert "Klein" not in texte and texte["Musterfrau"]["seiten"] == [1, 3]
        r = c.post("/v1/herausgabe", files=[("dateien", ("akte.pdf", datei, "application/pdf"))],
                   data={"ausnahmen": json.dumps(["Klein"]), "zusatz": json.dumps(["Akteneinsicht"])})
    assert r.status_code == 200, r.text
    assert r.headers["x-gerasterte-seiten"] == "1,3" and "NAME=" in r.headers["x-geschwaerzt"]
    out = pdfium.PdfDocument(r.content)
    assert len(out) == 3 and out[0].get_textpage().get_text_range().strip() == ""       # Textebene entfernt
    assert "Gebuehrenordnung" in out[1].get_textpage().get_text_range()                  # unbetroffene Seite unverändert
    s1, s3 = _ocr(r.content, 0), _ocr(r.content, 2)
    assert "Musterfrau" not in s1 and "example.org" not in s1 and "Akteneinsicht" not in s1 and "Klein" in s1
    assert "Musterfrau" not in s3 and "Unterschrift" in s3


def test_herausgabe_kontrolle_fail_closed(cfg, monkeypatch):
    monkeypatch.setattr(herausgabe, "ocr_bild", lambda bild, cfg: "noch lesbar: Erika Musterfrau")
    with _client(cfg) as c:
        r = c.post("/v1/herausgabe", files=[("dateien", ("akte.pdf", pdf_bytes(SEITEN), "application/pdf"))])
    assert r.status_code == 409 and r.json()["fail_closed"] is True and r.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------- Einwendungen
FORM = "Einwendung zum Bebauungsplan Lindenstrasse\nIch wende mich gegen den zusaetzlichen Verkehr in der Lindenstrasse und den Verlust der Baeume.\nName: {}"
EINW = [FORM.format("Anna Schmidt"), FORM.format("Peter Weber"),
        "Einwendung\nDie geplante Hoehe von 15 Metern verschattet mein Grundstueck.\nName: Hans Bauer",
        "Hinweis: Das Plangebiet liegt im Ueberschwemmungsgebiet.\nName: Rita Klein"]
THEMEN = [{"id": "verkehr", "beschreibung": "Verkehr, Lärm"}, {"id": "natur", "beschreibung": "Bäume, Natur"},
          {"id": "bauhoehe", "beschreibung": "Höhe, Verschattung"}]


def _einw_fake():
    def json_fn(n, s):
        if "zusaetzlichen Verkehr" in n:
            return {"kernpunkte": [
                {"thema": "verkehr", "kernpunkt": "Mehr Verkehr in der Lindenstraße", "seite": 1, "zitat": "gegen den zusaetzlichen Verkehr in der Lindenstrasse"},
                {"thema": "natur", "kernpunkt": "Verlust von Bäumen", "seite": 1, "zitat": "den Verlust der Baeume"}]}
        if "Hoehe von 15 Metern" in n:
            return {"kernpunkte": [{"thema": "bauhoehe", "kernpunkt": "Verschattung durch 15 m Höhe", "seite": 1,
                                    "zitat": "Die geplante Hoehe von 15 Metern verschattet mein Grundstueck"}]}
        return {"kernpunkte": [{"thema": "sonstiges", "kernpunkt": "Überschwemmungsgebiet", "seite": 1, "zitat": "liegt im Ueberschwemmungsgebiet"}]}
    return FakeModelle(json_fn=json_fn)


def test_gleichlautend():
    assert gleichlautend(EINW) == [[0, 1]]


def test_einwendungen(cfg):
    fake = _einw_fake()
    dateien = [_pdf(f"e{i + 1}.pdf", [t]) for i, t in enumerate(EINW)]
    with _client(cfg, fake) as c:
        b = c.post("/v1/einwendungen", files=dateien, data={"themen": json.dumps(THEMEN)}).json()
        r = c.post("/v1/einwendungen", files=dateien, data={"themen": json.dumps(THEMEN), "format": "csv"})
    assert len(fake.json_calls) == 3 * 2                                   # Formbrief nur einmal gelesen (zweimal: json + csv)
    assert b["gleichlautende_gruppen"] == [[1, 2]] and b["einwendungen"][1]["gleichlautend_mit"] == 1
    t = {x["thema"]: x for x in b["themen"]}
    assert t["verkehr"]["einwender"] == 2 and t["verkehr"]["eintraege"][0]["anzahl_gleichlautend"] == 2
    assert b["themen"][0]["thema"] in ("verkehr", "natur") and t["sonstiges"]["kernpunkte"] == 1 and b["unbelegt"] == 0
    assert "Anna" not in json.dumps(fake.json_calls) and "Hans" not in json.dumps(fake.json_calls)
    zeilen = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert zeilen[0][:4] == ["Thema", "Einwender Nr.", "gleichlautend (Anzahl)", "Kernpunkt"] and len(zeilen) == 5


# ---------------------------------------------------------------- Kriterien
ANTRAG = ["Projektantrag Jugendtreff\nDas Projekt richtet sich an Jugendliche von 12 bis 18 Jahren im Stadtteil.\n"
          "Die Gesamtkosten betragen 40.000 EUR, der Eigenanteil 10 Prozent."]
KRIT = [{"id": "zielgruppe", "kriterium": "Das Vorhaben richtet sich an Kinder oder Jugendliche."},
        {"id": "eigenanteil", "kriterium": "Der Antragsteller trägt einen Eigenanteil von mindestens 20 Prozent."},
        {"id": "nachhaltig", "kriterium": "Das Vorhaben ist auf mindestens drei Jahre angelegt."}]


def _krit_fake():
    def waehle_fn(n, o):
        aussage = n.split("Aussage:", 1)[1]
        p = 0.95 if "Jugendliche" in aussage else 0.6 if "Eigenanteil" in aussage else 0.05
        return {o[0]: p, o[1]: 1 - p}

    def json_fn(n, s):
        if "anforderungen" in s["properties"]:
            return {"anforderungen": [{"kriterium": "Zielgruppe Jugendliche", "seite": 1, "zitat": "Gefördert werden Vorhaben für Jugendliche"}]}
        return {"zielgruppe": {"zitat": "richtet sich an Jugendliche von 12 bis 18 Jahren", "seite": 1},
                "eigenanteil": {"zitat": "der Eigenanteil 10 Prozent", "seite": 1},
                "nachhaltig": {"zitat": "Laufzeit fünf Jahre", "seite": 1}}          # steht nicht im Antrag
    return FakeModelle(waehle_fn=waehle_fn, json_fn=json_fn)


def test_kriterien(cfg):
    with _client(cfg, _krit_fake()) as c:
        b = c.post("/v1/kriterien", files=[_pdf("antrag.pdf", ANTRAG)], data={"kriterien": json.dumps(KRIT)}).json()
    st = {x["id"]: x for x in b["kriterien"]}
    assert st["zielgruppe"]["status"] == "belegt" and st["zielgruppe"]["beleg"]["seite"] == 1
    assert st["eigenanteil"]["status"] == "pruefen"                        # unsicher (p=0.6), Zitat vorhanden
    assert st["nachhaltig"]["status"] == "nicht_belegt" and "beleg" not in st["nachhaltig"]   # erfundenes Zitat verworfen
    assert b["zusammenfassung"] == {"belegt": 1, "nicht_belegt": 1, "pruefen": 1}


def test_kriterien_aus_richtlinie(cfg):
    rl = pdf_bytes(["Foerderrichtlinie Jugend\nGefördert werden Vorhaben für Jugendliche im Stadtgebiet."])
    with _client(cfg, _krit_fake()) as c:
        b = c.post("/v1/kriterien", files=[_pdf("antrag.pdf", ANTRAG), ("richtlinie", ("rl.pdf", rl, "application/pdf"))]).json()
    assert b["kriterien"][0]["id"] == "K1" and b["kriterien"][0]["quelle"]["belegt"] is True


# ---------------------------------------------------------------- Goldset
def test_goldset(cfg, tmp_path):
    opts = [{"id": "a", "beschreibung": "Typ A"}, {"id": "b", "beschreibung": "Typ B"}]

    def waehle_fn(n, o):
        p = float(re.search(r"p=([\d.]+)", n).group(1))
        typ = re.search(r"typ=(\w)", n).group(1)
        ziel = o[0] if typ == "a" else o[1]
        return {x: (p if x == ziel else (1 - p) / (len(o) - 1)) for x in o}

    def json_fn(n, s):
        return {"betrag": {"wert": "12,50", "seite": 1, "zitat": "Betrag 12,50"}}
    faelle = []
    for i in range(40):   # hohe p: korrekt, mittlere p: teils falsch
        p = 0.99 if i < 25 else 0.7
        falsch = i >= 25 and i % 2 == 0
        faelle.append({"id": i, "route": "klassifizieren", "text": f"FALL p={p} typ=a",
                       "parameter": {"optionen": opts}, "erwartet": "b" if falsch else "a"})
    faelle.append({"route": "extrahieren", "text": "Betrag 12,50", "erwartet": {"betrag": 12.5},
                   "parameter": {"schema": {"type": "object", "properties": {"betrag": {"type": "number", "format": "betrag"}}}}})
    with _client(cfg, FakeModelle(waehle_fn=waehle_fn, json_fn=json_fn)) as c:
        erg = goldset.messen(faelle, c, tmp_path, None, 0.98)
    assert erg["auto_ab"]["n"] == 40 and erg["auto_ab"]["empfehlung"] == 0.71       # erst oberhalb von 0,70 nur korrekte Fälle
    assert erg["felder"]["betrag"]["richtig"] == 1 and erg["fehler"] == []
    assert "Empfohlene AUTO_AB: **0.71**" in goldset.bericht(erg)
