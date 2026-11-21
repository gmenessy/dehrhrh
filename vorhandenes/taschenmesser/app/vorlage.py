"""Schreiben aus Vorlage füllen – ohne Modell.

Syntax: {{feld}}, {{feld|datum}} (ISO -> TT.MM.JJJJ), {{feld|betrag}} (1234.5 -> 1.234,50 €), {{feld|gross}},
Blöcke {{#feld}}…{{/feld}} (nur wenn gesetzt) und {{^feld}}…{{/feld}} (nur wenn leer). Punkte greifen in Objekte:
{{antragsteller.name}}. In DOCX wirken Blöcke nur innerhalb eines Absatzes.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from xml.etree import ElementTree as ET

FELD = re.compile(r"\{\{\s*([\w.]+)\s*(?:\|\s*(\w+)\s*)?\}\}")
BLOCK = re.compile(r"\{\{([#^])\s*([\w.]+)\s*\}\}(.*?)\{\{/\s*\2\s*\}\}", re.S)
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _hole(daten: dict, pfad: str):
    v = daten
    for teil in pfad.split("."):
        if not isinstance(v, dict) or teil not in v:
            return None
        v = v[teil]
    return v


def _format(v, filt: str | None) -> str:
    if filt == "datum" and isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return dt.date.fromisoformat(v).strftime("%d.%m.%Y")
    if filt == "betrag" and isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    if filt == "gross":
        return str(v).upper()
    if isinstance(v, bool):
        return "ja" if v else "nein"
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(v)


def fuellen(text: str, daten: dict, fehlende: set[str] | None = None) -> str:
    fehlende = set() if fehlende is None else fehlende

    def block(m):
        v = _hole(daten, m.group(2))
        an = bool(v) if m.group(1) == "#" else not v
        return fuellen(m.group(3), daten, fehlende) if an else ""

    text = BLOCK.sub(block, text)

    def feld(m):
        v = _hole(daten, m.group(1))
        if v is None or v == "":
            fehlende.add(m.group(1))
            return m.group(0)
        return _format(v, m.group(2))

    return FELD.sub(feld, text)


def platzhalter(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in FELD.finditer(text)))


def docx_fuellen(data: bytes, daten: dict) -> tuple[bytes, set[str]]:
    """Platzhalter je Absatz: Word zerlegt '{{name}}' oft in mehrere Läufe – der Absatztext wird zusammengeführt,
    der erste Lauf behält seine Formatierung."""
    fehlende: set[str] = set()
    ein = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    ET.register_namespace("w", W)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in ein.infolist():
            inhalt = ein.read(info.filename)
            if re.fullmatch(r"word/(document|header\d*|footer\d*)\.xml", info.filename) and b"{{" in inhalt:
                inhalt = _xml_fuellen(inhalt, daten, fehlende)
            z.writestr(info, inhalt)
    return out.getvalue(), fehlende


def _xml_fuellen(xml: bytes, daten: dict, fehlende: set[str]) -> bytes:
    # Namespaces der Originaldatei erhalten (Word ist empfindlich bei fehlenden Präfixen)
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(xml), events=["start-ns"]):
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass
    root = ET.fromstring(xml)
    for p in root.iter(f"{{{W}}}p"):
        ts = list(p.iter(f"{{{W}}}t"))
        voll = "".join(t.text or "" for t in ts)
        if "{{" not in voll:
            continue
        neu = fuellen(voll, daten, fehlende)
        ts[0].text = neu
        ts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        for t in ts[1:]:
            t.text = ""
    aus = ET.tostring(root, encoding="unicode").encode()
    erstes = re.compile(rb"<(?![?!])[^>]+>")
    alt_tag, neu_tag = erstes.search(xml).group(0), erstes.search(aus).group(0)
    # Original-Wurzel behalten (Word braucht alle Namespaces aus mc:Ignorable), fehlende Deklarationen ergänzen
    vorhanden = set(re.findall(rb'xmlns(?::\w+)?="[^"]*"', alt_tag))
    extra = [d for d in re.findall(rb'xmlns(?::\w+)?="[^"]*"', neu_tag) if d not in vorhanden]
    tag = alt_tag[:-1].rstrip(b"/") + b"".join(b" " + d for d in extra) + b">"
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + aus.replace(neu_tag, tag, 1)
