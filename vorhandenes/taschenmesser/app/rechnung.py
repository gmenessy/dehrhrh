"""Rechnungseingang: XRechnung (UBL/CII) und ZUGFeRD/Factur-X ohne Modell lesen und rechnerisch prüfen.

Keine Schema-/Schematron-Validierung (dafür gibt es den KoSIT-Validator); geprüft werden Pflichtangaben,
Rechenwerte, IBAN und optional der Abgleich mit der Bestellung. PDFs ohne eingebettetes XML -> Extraktion per LLM.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from .schwaerzen import iban_gueltig

ZUGFERD_NAMEN = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "zugferd_invoice.xml")


def _l(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kind(el, *pfad):
    """Pfad über lokale Namen (Namespace-unabhängig)."""
    for name in pfad:
        if el is None:
            return None
        el = next((c for c in el if _l(c.tag) == name), None)
    return el


def _kinder(el, name):
    return [c for c in (el if el is not None else []) if _l(c.tag) == name]


def _t(el, *pfad) -> str | None:
    e = _kind(el, *pfad)
    return e.text.strip() if e is not None and e.text and e.text.strip() else None


def _d(s: str | None) -> Decimal | None:
    try:
        return Decimal(s) if s is not None else None
    except InvalidOperation:
        return None


def _datum(s: str | None) -> str | None:
    if not s:
        return None
    if re.fullmatch(r"\d{8}", s):  # CII Format 102
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def xml_aus_pdf(data: bytes) -> bytes | None:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        for i in range(pdf.count_attachments()):
            a = pdf.get_attachment(i)
            name = a.get_name().lower()
            if name in ZUGFERD_NAMEN or name.endswith(".xml"):
                return bytes(a.get_data())
    finally:
        pdf.close()
    return None


def _ubl(root) -> dict:
    gutschrift = _l(root.tag) == "CreditNote"
    vk = _kind(root, "AccountingSupplierParty", "Party")
    summen = _kind(root, "LegalMonetaryTotal")
    zeilen = []
    for z in _kinder(root, "CreditNoteLine" if gutschrift else "InvoiceLine"):
        menge = _kind(z, "CreditedQuantity" if gutschrift else "InvoicedQuantity")
        zeilen.append({"bezeichnung": _t(z, "Item", "Name"), "menge": _d(menge.text if menge is not None else None),
                       "einheit": menge.get("unitCode") if menge is not None else None,
                       "einzelpreis": _d(_t(z, "Price", "PriceAmount")), "netto": _d(_t(z, "LineExtensionAmount"))})
    steuer = sum((_d(_t(tt, "TaxAmount")) or Decimal(0)) for tt in _kinder(root, "TaxTotal"))
    return {"format": "XRechnung UBL" + (" (Gutschrift)" if gutschrift else ""),
            "rechnungsnummer": _t(root, "ID"), "datum": _datum(_t(root, "IssueDate")),
            "faellig": _datum(_t(root, "DueDate") or _t(root, "PaymentMeans", "PaymentDueDate")),
            "waehrung": _t(root, "DocumentCurrencyCode"), "leitweg_id": _t(root, "BuyerReference"),
            "bestellnummer": _t(root, "OrderReference", "ID"),
            "verkaeufer": _t(vk, "PartyLegalEntity", "RegistrationName") or _t(vk, "PartyName", "Name"),
            "ust_id": _t(vk, "PartyTaxScheme", "CompanyID"),
            "iban": _t(root, "PaymentMeans", "PayeeFinancialAccount", "ID"),
            "summe_positionen": _d(_t(summen, "LineExtensionAmount")), "netto": _d(_t(summen, "TaxExclusiveAmount")),
            "steuer": steuer, "brutto": _d(_t(summen, "TaxInclusiveAmount")), "zahlbetrag": _d(_t(summen, "PayableAmount")),
            "positionen": zeilen}


def _cii(root) -> dict:
    doc = _kind(root, "ExchangedDocument")
    tx = _kind(root, "SupplyChainTradeTransaction")
    ag, st = _kind(tx, "ApplicableHeaderTradeAgreement"), _kind(tx, "ApplicableHeaderTradeSettlement")
    vk = _kind(ag, "SellerTradeParty")
    summen = _kind(st, "SpecifiedTradeSettlementHeaderMonetarySummation")
    ust = next((_t(r, "ID") for r in _kinder(vk, "SpecifiedTaxRegistration") if _kind(r, "ID") is not None
                and _kind(r, "ID").get("schemeID") == "VA"), None)
    zeilen = []
    for z in _kinder(tx, "IncludedSupplyChainTradeLineItem"):
        menge = _kind(z, "SpecifiedLineTradeDelivery", "BilledQuantity")
        zeilen.append({"bezeichnung": _t(z, "SpecifiedTradeProduct", "Name"),
                       "menge": _d(menge.text if menge is not None else None),
                       "einheit": menge.get("unitCode") if menge is not None else None,
                       "einzelpreis": _d(_t(z, "SpecifiedLineTradeAgreement", "NetPriceProductTradePrice", "ChargeAmount")),
                       "netto": _d(_t(z, "SpecifiedLineTradeSettlement", "SpecifiedTradeSettlementLineMonetarySummation", "LineTotalAmount"))})
    steuer_el = _kinder(summen, "TaxTotalAmount")
    return {"format": "ZUGFeRD/XRechnung CII", "rechnungsnummer": _t(doc, "ID"),
            "datum": _datum(_t(doc, "IssueDateTime", "DateTimeString")),
            "faellig": _datum(_t(st, "SpecifiedTradePaymentTerms", "DueDateDateTime", "DateTimeString")),
            "waehrung": _t(st, "InvoiceCurrencyCode"), "leitweg_id": _t(ag, "BuyerReference"),
            "bestellnummer": _t(ag, "BuyerOrderReferencedDocument", "IssuerAssignedID"),
            "verkaeufer": _t(vk, "Name"), "ust_id": ust,
            "iban": _t(st, "SpecifiedTradeSettlementPaymentMeans", "PayeePartyCreditorFinancialAccount", "IBANID"),
            "summe_positionen": _d(_t(summen, "LineTotalAmount")), "netto": _d(_t(summen, "TaxBasisTotalAmount")),
            "steuer": _d(steuer_el[0].text) if steuer_el else None, "brutto": _d(_t(summen, "GrandTotalAmount")),
            "zahlbetrag": _d(_t(summen, "DuePayableAmount")), "positionen": zeilen}


def lesen_xml(xml: bytes) -> dict:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ValueError(f"Rechnungs-XML nicht lesbar: {e}") from e
    art = _l(root.tag)
    if art in ("Invoice", "CreditNote"):
        return _ubl(root)
    if art == "CrossIndustryInvoice":
        return _cii(root)
    raise ValueError(f"kein Rechnungsformat erkannt (Wurzel {art})")


def _gleich_betrag(a: Decimal | None, b: Decimal | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= Decimal("0.01")


def pruefen(r: dict, bestellung: dict | None = None, xrechnung: bool = True) -> list[dict]:
    p = []

    def add(name, ok, hinweis=None):
        p.append({"pruefung": name, "ok": ok, **({"hinweis": hinweis} if hinweis and not ok else {})})

    fehlen = [k for k in ("rechnungsnummer", "datum", "verkaeufer", "brutto") if not r.get(k)]
    add("pflichtangaben", not fehlen, "fehlt: " + ", ".join(fehlen))
    if xrechnung:
        add("leitweg_id", bool(r.get("leitweg_id")), "Leitweg-ID (BT-10) fehlt")
    pos = [z["netto"] for z in r.get("positionen", []) if z.get("netto") is not None]
    if pos and r.get("summe_positionen") is not None:
        add("summe_positionen", _gleich_betrag(sum(pos), r["summe_positionen"]),
            f"Positionen ergeben {sum(pos)}, angegeben {r['summe_positionen']}")
    for z in r.get("positionen", []):
        if None not in (z.get("menge"), z.get("einzelpreis"), z.get("netto")) and not _gleich_betrag(z["menge"] * z["einzelpreis"], z["netto"]):
            add("position_rechnerisch", False, f"{z.get('bezeichnung')}: {z['menge']} × {z['einzelpreis']} ≠ {z['netto']}")
    if None not in (r.get("netto"), r.get("steuer"), r.get("brutto")):
        add("netto_plus_steuer", _gleich_betrag(r["netto"] + r["steuer"], r["brutto"]),
            f"{r['netto']} + {r['steuer']} ≠ {r['brutto']}")
    if r.get("zahlbetrag") is not None and r.get("brutto") is not None:
        add("zahlbetrag", r["zahlbetrag"] <= r["brutto"] + Decimal("0.01"), "Zahlbetrag größer als Bruttobetrag")
    if r.get("iban"):
        add("iban", iban_gueltig(r["iban"]), "IBAN-Prüfziffer falsch")
    if bestellung:
        from .werkzeuge import _gleich
        if bestellung.get("bestellnummer"):
            add("bestellnummer", _gleich(str(r.get("bestellnummer") or ""), str(bestellung["bestellnummer"])),
                f"Rechnung nennt {r.get('bestellnummer')!r}, Bestellung {bestellung['bestellnummer']!r}")
        if bestellung.get("lieferant"):
            add("lieferant", bool(r.get("verkaeufer")) and _gleich(r["verkaeufer"], bestellung["lieferant"]),
                f"Verkäufer {r.get('verkaeufer')!r} ≠ Bestellung {bestellung['lieferant']!r}")
        if bestellung.get("brutto_max") is not None and r.get("brutto") is not None:
            add("betrag_bestellung", r["brutto"] <= Decimal(str(bestellung["brutto_max"])) + Decimal("0.01"),
                f"Brutto {r['brutto']} über bestelltem Höchstbetrag {bestellung['brutto_max']}")
        if bestellung.get("leitweg_id"):
            add("leitweg_id_passt", (r.get("leitweg_id") or "").strip() == str(bestellung["leitweg_id"]).strip(),
                "Leitweg-ID gehört nicht zu dieser Stelle")
    return p


def json_fest(r):
    """Decimal -> float für die Antwort."""
    if isinstance(r, dict):
        return {k: json_fest(v) for k, v in r.items()}
    if isinstance(r, list):
        return [json_fest(v) for v in r]
    return float(r) if isinstance(r, Decimal) else r


RECHNUNG_SCHEMA = {"type": "object", "properties": {
    "rechnungsnummer": {"type": "string", "description": "Rechnungsnummer"},
    "datum": {"type": "string", "format": "date", "description": "Rechnungsdatum"},
    "faellig": {"type": "string", "format": "date", "description": "Fälligkeitsdatum oder Zahlungsziel als Datum"},
    "verkaeufer": {"type": "string", "description": "Name des Rechnungsstellers"},
    "ust_id": {"type": "string", "description": "Umsatzsteuer-Identifikationsnummer des Rechnungsstellers (DE…)"},
    "iban": {"type": "string", "format": "iban", "description": "IBAN des Rechnungsstellers"},
    "bestellnummer": {"type": "string", "description": "Bestellnummer oder Auftragsnummer des Auftraggebers"},
    "leitweg_id": {"type": "string", "description": "Leitweg-ID"},
    "netto": {"type": "number", "format": "betrag", "description": "Nettobetrag gesamt"},
    "steuer": {"type": "number", "format": "betrag", "description": "Umsatzsteuerbetrag gesamt"},
    "brutto": {"type": "number", "format": "betrag", "description": "Bruttobetrag gesamt"},
    "zahlbetrag": {"type": "number", "format": "betrag", "description": "zu zahlender Betrag"}}}
