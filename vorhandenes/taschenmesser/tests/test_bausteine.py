import json
import math

import httpx
import pytest

from app.llm import ModellFehler, Modelle
from app.schema import SchemaFehler, modell_schema, normalisiere, pruefe_schema
from app.schwaerzen import Schwaerzung, iban_gueltig
from app.zitat import belegt, finde_seite
from conftest import IBAN_OK


def test_iban_pruefziffer():
    assert iban_gueltig(IBAN_OK)
    assert not iban_gueltig("DE89 3704 0044 0532 0130 01")


def test_schwaerzen_und_rueckabbildung():
    sw = Schwaerzung()
    t = sw.anwenden(f"Mail a@b.de, IBAN {IBAN_OK}, Tel. 0711 123456, nochmal a@b.de, falsch DE00 1234 5678 9012 3456 78")
    assert "[EMAIL_1]" in t and t.count("[EMAIL_1]") == 2
    assert "[IBAN_1]" in t and "DE00 1234" in t  # ungültige IBAN bleibt stehen
    assert "[TELEFON_1]" in t
    assert sw.rueck("Konto [IBAN_1]") == f"Konto {IBAN_OK}"
    assert sw.rueck({"a": ["[EMAIL_1]"]}) == {"a": ["a@b.de"]}
    assert Schwaerzung(aktiv=False).anwenden("a@b.de") == "a@b.de"


def test_zitat_normalisierung():
    text = "Die Gewerbe-\nanmeldung erfolgt „schriftlich“   bei der Behörde."
    assert belegt('Gewerbeanmeldung erfolgt "schriftlich" bei', text)
    assert not belegt("mündlich", text)
    assert not belegt("", text)
    assert finde_seite("bei der Behörde", [(1, "x"), (2, text)], 1) == 2
    assert belegt("Gewerbeanmeldung erfolgt", "Die Gewerbe\ufffeanmeldung erfolgt")  # PDFium-Trennstelle


def test_schema_pruefung():
    felder = pruefe_schema({"type": "object", "properties": {"iban": {"type": "string", "format": "iban"}}, "required": ["iban"]})
    assert "iban" in felder
    with pytest.raises(SchemaFehler):
        pruefe_schema({"type": "object", "properties": {"x": {"type": "array"}}})
    with pytest.raises(SchemaFehler):
        pruefe_schema({"type": "object", "properties": {"x": {}}, "required": ["y"]})
    ms = modell_schema(felder)
    assert ms["properties"]["iban"]["required"] == ["wert", "seite", "zitat"]


@pytest.mark.parametrize("wert,f,erwartet,fehler", [
    ("01.12.2026", {"format": "date"}, "2026-12-01", None),
    ("1.234,56 €", {"format": "betrag"}, 1234.56, None),
    ("12,5", {"type": "number"}, 12.5, None),
    ("ja", {"type": "boolean"}, True, None),
    ("7000", {"format": "plz"}, "7000", "PLZ muss fünfstellig sein"),
    ("DE89370400440532013001", {"format": "iban"}, "DE89370400440532013001", "IBAN-Prüfziffer falsch"),
    ("Gaststätte", {"enum": ["Einzelhandel"]}, "Gaststätte", "Wert nicht in enum"),
    (3, {"type": "integer", "minimum": 5}, 3, "minimum 5 verletzt"),
])
def test_normalisieren(wert, f, erwartet, fehler):
    assert normalisiere(wert, f) == (erwartet, fehler)


def _antwort_logprobs(top):
    return {"choices": [{"logprobs": {"content": [{"token": top[0][0], "top_logprobs": [{"token": t, "logprob": lp} for t, lp in top]}]}}]}


async def test_waehle_logprobs_und_parameter(cfg):
    gesehen = {}

    def handler(req: httpx.Request):
        gesehen.update(json.loads(req.content))
        return httpx.Response(200, json=_antwort_logprobs([("A", math.log(0.7)), ("B", math.log(0.2)), (" C", math.log(0.1))]))

    m = Modelle(cfg, transport=httpx.MockTransport(handler))
    p = await m.waehle("sys", "frage", ["eins", "zwei", "drei"])
    assert round(p["eins"], 2) == 0.70 and round(p["drei"], 2) == 0.10
    assert gesehen["guided_choice"] == ["A", "B", "C"] and gesehen["max_tokens"] == 1 and gesehen["logprobs"] is True
    await m.schliessen()


async def test_structured_outputs_und_fehler(cfg, monkeypatch):
    monkeypatch.setenv("VLLM_STRUCTURED", "structured_outputs")
    from app.config import laden
    c2 = laden()
    gesehen = {}

    def ok(req):
        gesehen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}]})

    m = Modelle(c2, transport=httpx.MockTransport(ok))
    assert await m.json("s", "u", {"type": "object"}) == {"a": 1}
    assert "structured_outputs" in gesehen and gesehen["temperature"] == 0
    m2 = Modelle(c2, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ModellFehler):
        await m2.json("s", "u", {})
    m3 = Modelle(c2, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "kein json"}}]})))
    with pytest.raises(ModellFehler):
        await m3.json("s", "u", {})
