import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schwaerzen import Schwaerzung, steuerid_gueltig
from conftest import FakeModelle

BRIEF = """Landratsamt Esslingen - Gewerbeamt
Name, Vorname: Musterfrau, Erika
Geburtsdatum: 12.04.1985
Anschrift: Hauptstraße 12a, 73728 Esslingen
Betriebsstätte: Stuttgarter Str. 5 / 7, 70173 Stuttgart
Steuer-ID: 86 095 742 719   SV-Nr.: 65 170839 J 00 3
Personalausweisnummer: L01X00T47

Sehr geehrte Frau Dr. Musterfrau,
Sie haben bei Herrn Bürgermeister Klein vorgesprochen. Ansprechpartnerin: Petra Klein-Schmidt
Gemäß § 14 GewO ist die Anzeige vollständig. Erika wohnt Im Wiesengrund 4.

Mit freundlichen Grüßen

Hans-Peter von Weber
Sachbearbeiter"""


def _schwaerzen(t):
    sw = Schwaerzung()
    sw.lernen([t])
    return sw, sw.anwenden(t)


def test_regeln_decken_personenbezug_ab():
    sw, r = _schwaerzen(BRIEF)
    for klar in ["Musterfrau", "Erika", "12.04.1985", "Hauptstraße 12a", "Stuttgarter Str. 5 / 7", "86 095 742 719",
                 "65 170839 J 00 3", "L01X00T47", "Klein", "Petra Klein-Schmidt", "Im Wiesengrund 4", "Hans-Peter von Weber"]:
        assert klar not in r, klar
    # Auslöser, PLZ/Ort, Behörde und Norm bleiben lesbar
    for bleibt in ["Geburtsdatum: [GEBDATUM_1]", "73728 Esslingen", "70173 Stuttgart", "Landratsamt Esslingen", "§ 14 GewO",
                   "Bürgermeister [NAME_", "Sachbearbeiter"]:
        assert bleibt in r, bleibt
    assert set(sw.zaehlung()) == {"NAME", "ADRESSE", "GEBDATUM", "STEUERID", "SVNR", "AUSWEIS"}


def test_rueckabbildung_ist_exakt():
    sw, r = _schwaerzen(BRIEF)
    assert sw.rueck(r) == BRIEF


def test_name_wird_ueber_seiten_abgeglichen():
    sw = Schwaerzung()
    seiten = ["Antragsteller: Erika Musterfrau", "Rückfrage: Musterfrau hat am Telefon bestätigt."]
    sw.lernen(seiten)
    assert sw.anwenden(seiten[1]).startswith("Rückfrage: [NAME_")


@pytest.mark.parametrize("text", [
    "Sehr geehrte Damen und Herren,",
    "Im Jahr 2026 am Montag 12 Uhr",
    "auf Platz 1 der Liste",
    "Antragsteller: Fahrrad Huber GmbH",
    "Frau\nName des Betriebs: Radhaus",
])
def test_keine_fehlgriffe(text):
    _, r = _schwaerzen(text)
    assert r == text


def test_steuerid_pruefziffer():
    assert steuerid_gueltig("86095742719") and not steuerid_gueltig("86095742718")
    _, r = _schwaerzen("Aktenzeichen 86095742718")
    assert "86095742718" in r


# ---------------------------------------------------------------- Personenerkennung per LLM
FLIESSTEXT = ("Widerspruch. Ich lege für meine Mutter Anna Schmidt Widerspruch ein. "
              "Sie wohnt Hohenheimer Höhe 3, 70599 Stuttgart. Anna ist 84 Jahre alt.")


def _client(cfg, fake):
    app.state.cfg, app.state.modelle = cfg, fake
    return TestClient(app)


@pytest.fixture(autouse=True)
def _aufraeumen():
    yield
    for k in ("cfg", "modelle"):
        if hasattr(app.state, k):
            delattr(app.state, k)


@pytest.fixture
def cfg_llm(cfg, monkeypatch):
    monkeypatch.setenv("SCHWAERZEN_NER", "llm")
    from app.config import laden
    return laden()


def test_llm_ner_nur_wortlich_belegte_treffer(cfg_llm):
    ner = lambda text: {"personen": ["Anna Schmidt", "Max Mustermann"],  # noqa: E731  zweiter Name ist erfunden
                        "anschriften": ["Hohenheimer Höhe 3, 70599 Stuttgart"]}
    fake = FakeModelle(ner_fn=ner)
    with _client(cfg_llm, fake) as c:
        b = c.post("/v1/schwaerzen", data={"text": FLIESSTEXT}).json()
    t = b["seiten"][0]["text"]
    assert "Anna" not in t and "Schmidt" not in t and "Hohenheimer Höhe 3" not in t
    assert "70599 Stuttgart" in t                      # PLZ und Ort bleiben stehen
    assert "Max Mustermann" not in json.dumps(b) and b["funde"] == {"NAME": 2, "ADRESSE": 1}
    assert len(fake.ner_calls) == 1 and b["meta"]["modellaufrufe"]["llm"] == 1


def test_llm_ner_ohne_modell_fail_closed(cfg_llm):
    with _client(cfg_llm, FakeModelle(fehler=True)) as c:
        r = c.post("/v1/schwaerzen", data={"text": FLIESSTEXT})
    assert r.status_code == 503 and r.json()["fail_closed"] is True


def test_extraktion_sieht_nur_platzhalter(cfg_llm):
    schema = {"type": "object", "required": ["betroffene", "anschrift"], "properties": {
        "betroffene": {"type": "string", "description": "Name der betroffenen Person"},
        "anschrift": {"type": "string", "description": "Straße und Hausnummer"},
        "plz": {"type": "string", "format": "plz", "description": "Postleitzahl"}}}

    def json_fn(nutzer, s):
        assert "Anna" not in nutzer and "Hohenheimer" not in nutzer
        return {"betroffene": {"wert": "[NAME_1]", "seite": 1, "zitat": "meine Mutter [NAME_1] Widerspruch"},
                "anschrift": {"wert": "[ADRESSE_1]", "seite": 1, "zitat": "Sie wohnt [ADRESSE_1]"},
                "plz": {"wert": "70599", "seite": 1, "zitat": "70599 Stuttgart"}}
    fake = FakeModelle(json_fn=json_fn, ner_fn=lambda t: {"personen": ["Anna Schmidt"], "anschriften": ["Hohenheimer Höhe 3"]})
    with _client(cfg_llm, fake) as c:
        d = c.post("/v1/extrahieren", data={"text": FLIESSTEXT, "schema": json.dumps(schema)}).json()["daten"]
    assert d["betroffene"]["wert"] == "Anna Schmidt" and d["betroffene"]["status"] == "ok"
    assert d["anschrift"]["wert"] == "Hohenheimer Höhe 3" and d["anschrift"]["zitat"] == "Sie wohnt Hohenheimer Höhe 3"
    assert d["plz"]["wert"] == "70599"
