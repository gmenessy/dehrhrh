import datetime as dt
import io
import json
import re
from email.message import EmailMessage
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient

from app import fristen as fr
from app import vorlage
from app.main import app
from app.paket2 import ist_deutsch, saetze
from conftest import FakeModelle, pdf_bytes

DATEN = Path(__file__).parent / "daten"


@pytest.fixture(autouse=True)
def _aufraeumen():
    yield
    for k in ("cfg", "modelle"):
        if hasattr(app.state, k):
            delattr(app.state, k)


def _client(cfg, fake=None):
    app.state.cfg, app.state.modelle = cfg, fake or FakeModelle()
    return TestClient(app)


def _datei(name, data, typ="application/octet-stream"):
    return ("dateien", (name, data, typ))


# ---------------------------------------------------------------- Formate
def _mail(anhang: bytes | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Erika Musterfrau <erika@example.org>"
    msg["To"] = "gewerbeamt@esslingen.example"
    msg["Subject"] = "Widerspruch gegen Gebührenbescheid 32.1-2026/17"
    msg["Date"] = "Mon, 16 Nov 2026 09:12:00 +0100"
    msg.set_content("Sehr geehrte Damen und Herren,\nhiermit lege ich Widerspruch gegen den Bescheid vom 02.11.2026 ein.\n"
                    "Bitte antworten Sie bis zum 30.11.2026.\nMit freundlichen Grüßen\nErika Musterfrau")
    if anhang:
        msg.add_attachment(anhang, maintype="application", subtype="pdf", filename="bescheid.pdf")
    return bytes(msg)


def test_formate_lesen(cfg):
    with _client(cfg) as c:
        r = c.post("/v1/text", files=[_datei("brief.docx", (DATEN / "brief.docx").read_bytes()),
                                      _datei("brief.odt", (DATEN / "brief.odt").read_bytes()),
                                      _datei("kosten.xlsx", (DATEN / "kosten.xlsx").read_bytes()),
                                      _datei("mail.eml", _mail(pdf_bytes(["Gebührenbescheid vom 02.11.2026 über 45,00 EUR"])))])
    assert r.status_code == 200, r.text
    d = {x["name"]: x for x in r.json()["dokumente"]}
    assert [s["text"].strip()[-31:] for s in d["brief.docx"]["seiten"]][1] == "Seite zwei: Lageplan liegt bei."
    assert "Fläche | 12 m²" in d["brief.docx"]["seiten"][0]["text"] and d["brief.docx"]["seiten"][0]["quelle"] == "docx"
    assert "Lageplan" in d["brief.odt"]["seiten"][0]["text"]
    assert "Gerüst | 1200" in d["kosten.xlsx"]["seiten"][0]["text"]
    mail = d["mail.eml"]["seiten"][0]["text"]
    assert mail.startswith("Von: Erika Musterfrau <erika@example.org>") and "Widerspruch gegen den Bescheid" in mail
    assert "45,00 EUR" in d["mail.eml/bescheid.pdf"]["seiten"][0]["text"]                          # Anhang als eigenes Dokument


def test_kaputtes_docx(cfg):
    with _client(cfg) as c:
        r = c.post("/v1/text", files=[_datei("x.docx", b"PK\x03\x04kaputt")])
    assert r.status_code in (415, 422)


# ---------------------------------------------------------------- Posteingang
def test_posteingang_mail(cfg):
    def json_fn(n, s):
        return {k: {"wert": None, "seite": None, "zitat": None} for k in s["properties"]} | {
            "aktenzeichen_bezug": {"wert": "32.1-2026/17", "seite": 1, "zitat": "Gebührenbescheid 32.1-2026/17"},
            "anliegen": {"wert": "Widerspruch gegen Gebührenbescheid", "seite": 1, "zitat": "hiermit lege ich Widerspruch gegen den Bescheid"},
            "absender": {"wert": "falsch", "seite": 1, "zitat": "Sehr geehrte Damen und Herren"}}
    aemter = [{"id": "32", "beschreibung": "Ordnungsamt, Gewerbe"}, {"id": "20", "beschreibung": "Kämmerei"}]

    def waehle_fn(n, o):
        if o[0].startswith("dringend"):
            return {o[0]: 0.2, o[1]: 0.8}
        return {o[0]: 0.9, o[1]: 0.05, o[2]: 0.05}
    with _client(cfg, FakeModelle(json_fn=json_fn, waehle_fn=waehle_fn)) as c:
        b = c.post("/v1/posteingang", files=[_datei("mail.eml", _mail())], data={"aemter": json.dumps(aemter)}).json()
    d = b["daten"]
    assert d["absender"]["wert"] == "Erika Musterfrau <erika@example.org>" and d["absender"]["stufe"] == 0   # Mailkopf schlägt Modell
    assert d["datum"]["wert"] == "2026-11-16" and d["betreff"]["quelle"] == "mailkopf"
    assert d["aktenzeichen_bezug"]["wert"] == "32.1-2026/17" and d["anliegen"]["status"] == "ok"
    assert b["dringlichkeit"]["wert"] == "normal" and b["zustaendigkeit"]["wert"] == "32"
    assert [f["datum"] for f in b["fristen"]] == ["2026-11-30"]


# ---------------------------------------------------------------- Fristen
def test_feiertage_und_fristende():
    assert fr.ostern(2026) == dt.date(2026, 4, 5) and fr.ostern(2027) == dt.date(2027, 3, 28)
    assert fr.fristende(dt.date(2026, 1, 31), 1, "monat")["fristende"] == "2026-03-02"            # 28.02. Sa -> Mo
    assert fr.fristende(dt.date(2026, 3, 30), 4, "tage")["verschoben"][0] == "03.04.2026 Karfreitag"
    assert fr.fristende(dt.date(2026, 12, 18), 1, "wochen")["fristende"] == "2026-12-28"
    assert fr.fristende(dt.date(2026, 5, 14), 3, "wochen")["fristende"] == "2026-06-05"           # 04.06. Fronleichnam
    with pytest.raises(ValueError):
        fr.fristende(dt.date(2026, 1, 1), 1, "stunden")


def test_fristen_endpoint(cfg):
    text = ("Gegen diesen Bescheid kann innerhalb eines Monats nach Bekanntgabe Widerspruch erhoben werden.\n"
            "Die Gebühr ist binnen 14 Tagen zu zahlen.\nUnterlagen bitte bis zum 6. Januar 2027 nachreichen.")
    fake = FakeModelle(json_fn=lambda n, s: {"fristen": [{"wofuer": "Widerspruch", "beginn": "bekanntgabe"},
                                                         {"wofuer": "Zahlung der Gebühr", "beginn": "bekanntgabe"},
                                                         {"wofuer": "Nachreichen von Unterlagen", "beginn": "unbekannt"}]})
    with _client(cfg, fake) as c:
        b = c.post("/v1/fristen", data={"text": text, "beginn": "2026-12-01"}).json()
        r = c.post("/v1/fristende", data={"beginn": "2026-12-24", "wert": 2, "einheit": "tage"}).json()
    f = b["fristen"]
    assert [x["art"] for x in f] == ["dauer", "dauer", "datum"] and f[0]["wofuer"] == "Widerspruch"
    assert f[0]["berechnung"]["fristende"] == "2027-01-04"            # 01.01. Neujahr (Fr) -> Mo 04.01.
    assert f[2]["datum"] == "2027-01-06" and f[2]["hinweis"] == "fällt auf Heilige Drei Könige"
    assert r["fristende"] == "2026-12-28" and r["verschoben"] == ["26.12.2026 2. Weihnachtstag", "27.12.2026 Sonntag"]


# ---------------------------------------------------------------- Chronik
def test_chronik(cfg):
    akte = ["Antrag vom 02.03.2026 auf Sondernutzung eingegangen.\nAm 10.03.2026 Anhörung des Antragstellers.",
            "Bescheid erlassen am 01.04.2026."]

    def json_fn(n, s):  # beide Dokumente liegen in einem Fenster
        return {"ereignisse": [
            {"datum": "01.04.2026", "ereignis": "Bescheid erlassen", "seite": 2, "zitat": "Bescheid erlassen am 01.04.2026"},
            {"datum": "10.03.2026", "ereignis": "Anhörung", "seite": 1, "zitat": "Am 10.03.2026 Anhörung des Antragstellers"},
            {"datum": "02.03.2026", "ereignis": "Antrag eingegangen", "seite": 1, "zitat": "Antrag vom 02.03.2026"},
            {"datum": "02.03.2026", "ereignis": "Antrag eingegangen", "seite": 1, "zitat": "Antrag vom 02.03.2026"},
            {"datum": None, "ereignis": "Ortstermin", "seite": 1, "zitat": "Ortstermin mit Bauhof"}]}
    with _client(cfg, FakeModelle(json_fn=json_fn)) as c:
        b = c.post("/v1/chronik", files=[_datei("akte.pdf", pdf_bytes(akte[:1])), _datei("bescheid.pdf", pdf_bytes(akte[1:]))]).json()
    ch = b["chronik"]
    assert [e["datum"] for e in ch] == ["2026-03-02", "2026-03-10", "2026-04-01", None]
    assert ch[2]["dokument"] == "bescheid.pdf" and ch[3]["belegt"] is False and b["unbelegt"] == 1


# ---------------------------------------------------------------- Übersetzen
def test_uebersetzen(cfg):
    de = "Sehr geehrte Damen und Herren, ich bitte um Rückruf, da ich die Unterlagen nicht mit der Post erhalten habe."
    fake = FakeModelle()
    with _client(cfg, fake) as c:
        b = c.post("/v1/uebersetzen", data={"text": de}).json()
    assert b["stufe"] == 0 and fake.json_calls == []

    def json_fn(n, s):
        assert "[EMAIL_1]" in n and "ali@example.org" not in n
        return {"sprache": "en", "text": "Bitte kontaktieren Sie mich unter [EMAIL_1] wegen meiner Aufenthaltserlaubnis."}
    with _client(cfg, FakeModelle(json_fn=json_fn)) as c:
        b = c.post("/v1/uebersetzen", data={"text": "Please contact me at ali@example.org regarding my residence permit."}).json()
    assert b["sprache"] == "en" and "ali@example.org" in b["seiten"][0]["text"] and b["seiten"][0]["fehlende_platzhalter"] == []
    assert "keine amtliche" in b["hinweis_uebersetzung"]
    assert not ist_deutsch("Please contact me at the office regarding my residence permit and the documents.")


# ---------------------------------------------------------------- Fassungsvergleich
ALT = """§ 1 Geltungsbereich
Diese Satzung gilt für öffentliche Straßen der Stadt.
§ 2 Gebühren
(1) Die Gebühr beträgt 50 Euro je Monat. Sie wird gem. Abs. 2 fällig.
(2) Die Gebühr ist mit Bekanntgabe fällig. Ermäßigungen sind ausgeschlossen."""
NEU = """§ 1 Geltungsbereich
Diese Satzung gilt für öffentliche Straßen der Stadt.
§ 2 Gebühren
(1) Die Gebühr beträgt 60 Euro je Monat. Sie wird gem. Abs. 2 fällig.
(2) Die Gebühr ist mit Bekanntgabe fällig.
(3) Für gemeinnützige Vereine entfällt die Gebühr."""


def test_saetze_abkuerzungen():
    s = saetze("Sie wird gem. Abs. 2 fällig. Das gilt z. B. am 1. Januar. Nächster Satz.")
    assert s == ["Sie wird gem. Abs. 2 fällig.", "Das gilt z. B. am 1. Januar.", "Nächster Satz."]


def test_vergleichen(cfg):
    def json_fn(n, s):
        nummern = [int(x) for x in re.findall(r"\[(\d+)\]", n)]
        return {"punkte": [{"aenderungen": [nummern[0]], "zusammenfassung": "Gebühr steigt von 50 auf 60 Euro."},
                           {"aenderungen": [999], "zusammenfassung": "erfunden"}]}
    with _client(cfg, FakeModelle(json_fn=json_fn)) as c:
        b = c.post("/v1/vergleichen", data={"alt": ALT, "neu": NEU}).json()
    arten = [(a["art"], a.get("markiert")) for a in b["aenderungen"]]
    assert ("geaendert", "(1) Die Gebühr beträgt [-50-] {+60+} Euro je Monat.") in arten
    assert any(a["art"] == "entfernt" and a["alt"] == "Ermäßigungen sind ausgeschlossen." for a in b["aenderungen"])
    assert any(a["art"] == "eingefuegt" and "gemeinnützige Vereine" in a["neu"] for a in b["aenderungen"])
    assert b["wesentlich"] == [{"aenderungen": [1], "zusammenfassung": "Gebühr steigt von 50 auf 60 Euro."}]   # 999 verworfen


# ---------------------------------------------------------------- Antwort aus Textbausteinen
BAUSTEINE = [
    {"id": "anrede", "text": "Sehr geehrte Frau {{name}},", "immer": True},
    {"id": "eingang", "text": "vielen Dank für Ihr Schreiben vom {{datum|datum}}.", "immer": True},
    {"id": "widerspruch", "titel": "Widerspruch eingegangen", "wann": "Schreiben enthält einen Widerspruch",
     "text": "Ihren Widerspruch gegen den Bescheid {{aktenzeichen}} haben wir erhalten und prüfen ihn."},
    {"id": "ratenzahlung", "titel": "Ratenzahlung", "wann": "Bitte um Ratenzahlung", "text": "Eine Ratenzahlung ist möglich."},
    {"id": "akteneinsicht", "titel": "Akteneinsicht", "wann": "Bitte um Akteneinsicht", "text": "Akteneinsicht nach Terminabsprache."}]


def test_antwort(cfg):
    def waehle_fn(n, o):
        p = 0.95 if "Widerspruch eingegangen" in n else 0.5 if "Akteneinsicht" in n else 0.03
        return {o[0]: p, o[1]: 1 - p}
    fake = FakeModelle(waehle_fn=waehle_fn, json_fn=lambda n, s: {"aktenzeichen": {"wert": "32.1-2026/17", "seite": 1, "zitat": "32.1-2026/17"}})
    text = "Widerspruch gegen Bescheid 32.1-2026/17. Ich möchte auch Einsicht in die Unterlagen."
    with _client(cfg, fake) as c:
        b = c.post("/v1/antwort", data={"text": text, "bausteine": json.dumps(BAUSTEINE),
                                         "daten": json.dumps({"name": "Musterfrau", "datum": "2026-11-16"})}).json()
    assert b["text"] == ("Sehr geehrte Frau Musterfrau,\n\nvielen Dank für Ihr Schreiben vom 16.11.2026.\n\n"
                         "Ihren Widerspruch gegen den Bescheid 32.1-2026/17 haben wir erhalten und prüfen ihn.")
    assert b["zu_pruefen"] == [{"id": "akteneinsicht", "p": 0.5}] and b["pfad"] == "mensch"
    assert len(fake.waehle_calls) == 3                                     # 'immer'-Bausteine ohne Modell


# ---------------------------------------------------------------- Vorlage
def test_vorlage_text():
    fehlende = set()
    t = vorlage.fuellen("Sehr geehrte{{#frau}} Frau{{/frau}}{{^frau}}r Herr{{/frau}} {{p.name}}, Betrag {{b|betrag}}, "
                        "Frist {{f|datum}}, {{x}}", {"frau": False, "p": {"name": "Weber"}, "b": 1234.5, "f": "2026-12-04"}, fehlende)
    assert t == "Sehr geehrter Herr Weber, Betrag 1.234,50 €, Frist 04.12.2026, {{x}}" and fehlende == {"x"}


def test_vorlage_docx(cfg):
    with _client(cfg) as c:
        r = c.post("/v1/vorlage", files={"vorlage": ("bescheid.docx", (DATEN / "vorlage.docx").read_bytes())},
                   data={"daten": json.dumps({"frau": True, "name": "Erika Musterfrau", "eingang": "2026-11-02", "gebuehr": 45.5})})
        assert r.status_code == 200 and r.headers["x-fehlende"] == "az"
        from app.formate import docx_seiten
        assert docx_seiten(r.content)[0].startswith("Sehr geehrte Frau Erika Musterfrau,\nIhre Gewerbeanzeige vom 02.11.2026")
        r = c.post("/v1/vorlage", data={"vorlage_text": "{{a}} {{b}}", "daten": '{"a": 1}', "streng": "true"})
        assert r.status_code == 422 and "b" in r.json()["fehler"]


# ---------------------------------------------------------------- Beschlüsse
def test_beschluesse(cfg):
    prot = ["TOP 4 Sanierung Turnhalle\nDer Gemeinderat beschließt die Sanierung mit 18 Ja, 2 Nein, 1 Enthaltung.\n"
            "Die Verwaltung wird beauftragt, bis 31.03.2027 die Ausschreibung vorzubereiten."]

    def json_fn(n, s):
        return {"beschluesse": [{"top": "4", "gegenstand": "Sanierung Turnhalle", "beschluss": "Sanierung beschlossen",
                                 "ergebnis": "angenommen", "ja": 18, "nein": 2, "enthaltung": 1,
                                 "auftraege": [{"was": "Ausschreibung vorbereiten", "wer": "Verwaltung", "bis": "31.03.2027"}],
                                 "seite": 1, "zitat": "Der Gemeinderat beschließt die Sanierung"}]}
    with _client(cfg, FakeModelle(json_fn=json_fn)) as c:
        b = c.post("/v1/beschluesse", files=[_datei("protokoll.pdf", pdf_bytes(prot))]).json()
    assert b["beschluesse"][0]["belegt"] and b["beschluesse"][0]["ja"] == 18
    assert b["auftraege"] == [{"was": "Ausschreibung vorbereiten", "wer": "Verwaltung", "bis": "2027-03-31", "top": "4",
                               "gegenstand": "Sanierung Turnhalle"}]


# ---------------------------------------------------------------- Rechnung
UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>RE-2026-0815</cbc:ID><cbc:IssueDate>2026-11-10</cbc:IssueDate><cbc:DueDate>2026-12-10</cbc:DueDate>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode><cbc:BuyerReference>08115000-12345-67</cbc:BuyerReference>
 <cac:OrderReference><cbc:ID>B-4711</cbc:ID></cac:OrderReference>
 <cac:AccountingSupplierParty><cac:Party><cac:PartyTaxScheme><cbc:CompanyID>DE123456789</cbc:CompanyID></cac:PartyTaxScheme>
  <cac:PartyLegalEntity><cbc:RegistrationName>Gerüstbau Weber GmbH</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party></cac:AccountingSupplierParty>
 <cac:PaymentMeans><cac:PayeeFinancialAccount><cbc:ID>DE89370400440532013000</cbc:ID></cac:PayeeFinancialAccount></cac:PaymentMeans>
 <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">{steuer}</cbc:TaxAmount></cac:TaxTotal>
 <cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID="EUR">1500.00</cbc:LineExtensionAmount>
  <cbc:TaxExclusiveAmount currencyID="EUR">1500.00</cbc:TaxExclusiveAmount><cbc:TaxInclusiveAmount currencyID="EUR">1785.00</cbc:TaxInclusiveAmount>
  <cbc:PayableAmount currencyID="EUR">1785.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
 <cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity unitCode="DAY">10</cbc:InvoicedQuantity><cbc:LineExtensionAmount currencyID="EUR">1200.00</cbc:LineExtensionAmount>
  <cac:Item><cbc:Name>Gerüststellung</cbc:Name></cac:Item><cac:Price><cbc:PriceAmount currencyID="EUR">120.00</cbc:PriceAmount></cac:Price></cac:InvoiceLine>
 <cac:InvoiceLine><cbc:ID>2</cbc:ID><cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity><cbc:LineExtensionAmount currencyID="EUR">300.00</cbc:LineExtensionAmount>
  <cac:Item><cbc:Name>Absperrung</cbc:Name></cac:Item><cac:Price><cbc:PriceAmount currencyID="EUR">300.00</cbc:PriceAmount></cac:Price></cac:InvoiceLine>
</Invoice>"""

CII = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
 xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
 xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
 <rsm:ExchangedDocument><ram:ID>2026-77</ram:ID><ram:IssueDateTime><udt:DateTimeString format="102">20261105</udt:DateTimeString></ram:IssueDateTime></rsm:ExchangedDocument>
 <rsm:SupplyChainTradeTransaction>
  <ram:IncludedSupplyChainTradeLineItem><ram:SpecifiedTradeProduct><ram:Name>Druckerpapier A4</ram:Name></ram:SpecifiedTradeProduct>
   <ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice><ram:ChargeAmount>4.50</ram:ChargeAmount></ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>
   <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="PK">20</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
   <ram:SpecifiedLineTradeSettlement><ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>95.00</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation></ram:SpecifiedLineTradeSettlement>
  </ram:IncludedSupplyChainTradeLineItem>
  <ram:ApplicableHeaderTradeAgreement><ram:BuyerReference>08115000-12345-67</ram:BuyerReference>
   <ram:SellerTradeParty><ram:Name>Bürobedarf Klein KG</ram:Name><ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">DE987654321</ram:ID></ram:SpecifiedTaxRegistration></ram:SellerTradeParty>
  </ram:ApplicableHeaderTradeAgreement>
  <ram:ApplicableHeaderTradeSettlement><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
   <ram:SpecifiedTradeSettlementHeaderMonetarySummation><ram:LineTotalAmount>95.00</ram:LineTotalAmount><ram:TaxBasisTotalAmount>95.00</ram:TaxBasisTotalAmount>
    <ram:TaxTotalAmount currencyID="EUR">18.05</ram:TaxTotalAmount><ram:GrandTotalAmount>113.05</ram:GrandTotalAmount><ram:DuePayableAmount>113.05</ram:DuePayableAmount>
   </ram:SpecifiedTradeSettlementHeaderMonetarySummation></ram:ApplicableHeaderTradeSettlement>
 </rsm:SupplyChainTradeTransaction></rsm:CrossIndustryInvoice>"""


def _zugferd(xml: str) -> bytes:
    pdf = pdfium.PdfDocument(pdf_bytes(["Rechnung 2026-77 Bürobedarf Klein KG"]))
    pdf.new_attachment("factur-x.xml").set_data(xml.encode())
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_rechnung_ubl_ok_und_kontierung(cfg):
    konten = [{"id": "5231", "beschreibung": "Unterhaltung Straßen"}, {"id": "5431", "beschreibung": "Geschäftsausgaben, Büro"}]
    fake = FakeModelle(waehle_fn=lambda n, o: {o[0]: 0.9, o[1]: 0.05, o[2]: 0.05})
    with _client(cfg, fake) as c:
        b = c.post("/v1/rechnung", files=[_datei("re.xml", UBL.format(steuer="285.00").encode())],
                   data={"bestellung": json.dumps({"bestellnummer": "B-4711", "lieferant": "Gerüstbau Weber GmbH", "brutto_max": 2000}),
                         "konten": json.dumps(konten)}).json()
    assert b["ergebnis"] == "ok" and b["stufe"] == 0 and b["rechnung"]["brutto"] == 1785.0
    assert [p["pruefung"] for p in b["pruefungen"] if not p["ok"]] == []
    assert b["rechnung"]["positionen"][0] == {"bezeichnung": "Gerüststellung", "menge": 10.0, "einheit": "DAY", "einzelpreis": 120.0, "netto": 1200.0}
    assert b["kontierung"]["wert"] == "5231" and "Gerüststellung" in fake.waehle_calls[0]


def test_rechnung_rechenfehler_und_bestellung(cfg):
    with _client(cfg) as c:
        b = c.post("/v1/rechnung", files=[_datei("re.xml", UBL.format(steuer="258.00").encode())],
                   data={"bestellung": json.dumps({"bestellnummer": "B-4712", "brutto_max": 1500})}).json()
    falsch = {p["pruefung"]: p["hinweis"] for p in b["pruefungen"] if not p["ok"]}
    assert set(falsch) == {"netto_plus_steuer", "bestellnummer", "betrag_bestellung"} and b["ergebnis"] == "pruefen"


def test_rechnung_zugferd_cii(cfg):
    with _client(cfg) as c:
        b = c.post("/v1/rechnung", files=[_datei("re.pdf", _zugferd(CII), "application/pdf")]).json()
    r = b["rechnung"]
    assert b["quelle"] == "xml" and r["format"].startswith("ZUGFeRD") and r["datum"] == "2026-11-05"
    assert r["verkaeufer"] == "Bürobedarf Klein KG" and r["ust_id"] == "DE987654321" and r["brutto"] == 113.05
    assert {p["pruefung"]: p["ok"] for p in b["pruefungen"]}["position_rechnerisch"] is False   # 20 × 4,50 ≠ 95,00


def test_rechnung_pdf_ohne_xml(cfg):
    def json_fn(n, s):
        werte = {"rechnungsnummer": ("RE-9", "Rechnung Nr. RE-9"), "datum": ("01.11.2026", "Datum: 01.11.2026"),
                 "verkaeufer": ("Malerbetrieb Huber", "Malerbetrieb Huber"), "brutto": ("119,00", "Gesamt brutto 119,00 EUR"),
                 "netto": ("100,00", "Netto 100,00 EUR"), "steuer": ("19,00", "USt 19,00 EUR")}
        return {k: ({"wert": werte[k][0], "seite": 1, "zitat": werte[k][1]} if k in werte else {"wert": None, "seite": None, "zitat": None})
                for k in s["properties"]}
    seite = ["Malerbetrieb Huber\nRechnung Nr. RE-9\nDatum: 01.11.2026\nNetto 100,00 EUR\nUSt 19,00 EUR\nGesamt brutto 119,00 EUR"]
    with _client(cfg, FakeModelle(json_fn=json_fn)) as c:
        b = c.post("/v1/rechnung", files=[_datei("scan.pdf", pdf_bytes(seite), "application/pdf")]).json()
    assert b["quelle"] == "extraktion" and b["stufe"] == 2 and b["ergebnis"] == "pruefen"   # Extraktion nie automatisch ok
    assert b["rechnung"]["brutto"] == 119.0 and all(p["ok"] for p in b["pruefungen"])
    assert b["fundstellen"]["brutto"]["zitat"] == "Gesamt brutto 119,00 EUR"
