"""Fristen: finden (Regel), berechnen (§ 31 VwVfG i. V. m. §§ 187 ff. BGB), Feiertage Baden-Württemberg.

Die Berechnung ist bewusst einfach und deterministisch: Ereignisfrist (der Tag des Ereignisses zählt nicht mit),
Ende nach Tagen/Wochen/Monaten/Jahren, Monatsende-Regel, Verschiebung auf den nächsten Werktag bei Samstag,
Sonntag oder gesetzlichem Feiertag. Sonderfälle (Bekanntgabefiktion, Fristverlängerung, Fristen nach Stunden)
entscheidet die Sachbearbeitung.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re

MONATE = {m: i + 1 for i, m in enumerate(["januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
                                           "september", "oktober", "november", "dezember"])}
MONATE["maerz"] = 3
ZAHLWORT = {"ein": 1, "eine": 1, "einer": 1, "eines": 1, "einem": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4,
            "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
            "vierzehn": 14, "einundzwanzig": 21, "achtundzwanzig": 28, "dreißig": 30}
EINHEITEN = {"tag": "tage", "woche": "wochen", "monat": "monate", "jahr": "jahre"}


def ostern(jahr: int) -> dt.date:
    """Gregorianischer Ostersonntag (anonymer Algorithmus)."""
    a, b, c = jahr % 19, jahr // 100, jahr % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    monat, tag = divmod(h + l_ - 7 * m + 114, 31)
    return dt.date(jahr, monat, tag + 1)


def feiertage_bw(jahr: int) -> dict[dt.date, str]:
    o = ostern(jahr)
    t = lambda n: o + dt.timedelta(days=n)  # noqa: E731
    return {dt.date(jahr, 1, 1): "Neujahr", dt.date(jahr, 1, 6): "Heilige Drei Könige", t(-2): "Karfreitag",
            t(1): "Ostermontag", dt.date(jahr, 5, 1): "Tag der Arbeit", t(39): "Christi Himmelfahrt",
            t(50): "Pfingstmontag", t(60): "Fronleichnam", dt.date(jahr, 10, 3): "Tag der Deutschen Einheit",
            dt.date(jahr, 11, 1): "Allerheiligen", dt.date(jahr, 12, 25): "1. Weihnachtstag",
            dt.date(jahr, 12, 26): "2. Weihnachtstag"}


def kein_werktag(d: dt.date) -> str | None:
    return feiertage_bw(d.year).get(d) or ("Samstag" if d.weekday() == 5 else "Sonntag" if d.weekday() == 6 else None)


def naechster_werktag(d: dt.date) -> tuple[dt.date, list[str]]:
    gruende = []
    while (g := kein_werktag(d)):
        gruende.append(f"{d.strftime('%d.%m.%Y')} {g}")
        d += dt.timedelta(days=1)
    return d, gruende


def _plus_monate(d: dt.date, n: int) -> dt.date:
    j, m = divmod(d.month - 1 + n, 12)
    jahr, monat = d.year + j, m + 1
    return dt.date(jahr, monat, min(d.day, calendar.monthrange(jahr, monat)[1]))  # § 188 Abs. 3 BGB


def fristende(beginn: dt.date, wert: int, einheit: str) -> dict:
    """Ereignisfrist ab 'beginn' (Tag des Ereignisses, z. B. Bekanntgabe). Ergebnis mit Begründung der Verschiebung."""
    einheit = {**EINHEITEN, **{v: v for v in EINHEITEN.values()}}.get(str(einheit).lower(), "")
    if not einheit or not isinstance(wert, int) or not 0 < wert <= 3650:
        raise ValueError("einheit: tage|wochen|monate|jahre, wert 1..3650")
    if einheit == "tage":
        roh = beginn + dt.timedelta(days=wert)
    elif einheit == "wochen":
        roh = beginn + dt.timedelta(weeks=wert)
    elif einheit == "monate":
        roh = _plus_monate(beginn, wert)
    else:
        roh = _plus_monate(beginn, 12 * wert)
    ende, gruende = naechster_werktag(roh)
    return {"beginn": beginn.isoformat(), "dauer": {"wert": wert, "einheit": einheit}, "rechnerisch": roh.isoformat(),
            "fristende": ende.isoformat(), "verschoben": gruende,
            "grundlage": "§ 31 VwVfG i. V. m. §§ 187 Abs. 1, 188, 193 BGB; Feiertage Baden-Württemberg"}


# ---------------------------------------------------------------- finden
_ZAHL = r"(\d{1,3}|" + "|".join(sorted(ZAHLWORT, key=len, reverse=True)) + r")"
DAUER = re.compile(rf"\b(?:innerhalb|binnen|Frist von|nach Ablauf von|spätestens nach)\s+(?:von\s+)?{_ZAHL}\s+"
                   r"(Tag|Woche|Monat|Jahr)(?:e|en|es|s|n)?\b", re.I)
_DATUM = r"(\d{1,2})\.\s?(?:(\d{1,2})\.\s?|(" + "|".join(MONATE) + r")\s+)(\d{4}|\d{2})\b"
DATUM = re.compile(r"\b(?:bis(?:\s+(?:zum|spätestens(?:\s+(?:zum|am))?|einschließlich))?|spätestens(?:\s+(?:am|zum|bis(?:\s+zum)?))?|"
                   r"Frist(?:ende|ablauf)?\s*:|Termin\s*:|Abgabe(?:termin)?\s*:)\s+(?:(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),?\s+(?:den\s+)?)?"
                   + _DATUM, re.I)


def _zahl(s: str) -> int:
    return int(s) if s.isdigit() else ZAHLWORT[s.lower()]


def _zeile(text: str, s: int, e: int) -> str:
    a = text.rfind("\n", 0, s) + 1
    b = text.find("\n", e)
    b = len(text) if b < 0 else b
    if b - a > 300:
        a, b = max(a, s - 120), min(b, e + 120)
    return text[a:b].strip()


def finden(text: str) -> list[dict]:
    """Fristausdrücke im Text: explizites Datum ('bis zum 15.12.2026') oder Dauer ('innerhalb eines Monats')."""
    out = []
    for m in DATUM.finditer(text):
        tag, mon_z, mon_w, jahr = m.group(1), m.group(2), m.group(3), m.group(4)
        jahr = int(jahr) + (2000 if len(jahr) == 2 else 0)
        try:
            d = dt.date(jahr, int(mon_z) if mon_z else MONATE[mon_w.lower()], int(tag))
        except ValueError:
            continue
        out.append({"art": "datum", "datum": d.isoformat(), "ausdruck": m.group(0), "zitat": _zeile(text, *m.span()),
                    "pos": m.start(), **({"hinweis": f"fällt auf {g}"} if (g := kein_werktag(d)) else {})})
    for m in DAUER.finditer(text):
        out.append({"art": "dauer", "dauer": {"wert": _zahl(m.group(1)), "einheit": EINHEITEN[m.group(2).lower()]},
                    "ausdruck": m.group(0), "zitat": _zeile(text, *m.span()), "pos": m.start()})
    return sorted(out, key=lambda x: x["pos"])
