"""Die Werkzeuge: zusammenfassen, extrahieren, klassifizieren, prüfen. Stufenregel: Regel vor Modell vor Mensch."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from .config import Config
from .dokument import Dokument
from .llm import ModellFehler
from .schema import modell_schema, normalisiere, pruefe_schema
from .schwaerzen import EMAIL, IBAN, Schwaerzung, iban_gueltig
from .zitat import belegt, finde_seite

KEINE = "keine der Optionen passt"


@dataclass
class Abschnitt:
    g: int          # globale Seitennummer über alle Dokumente
    dok: str
    seite: int
    roh: str        # Klartext
    red: str        # redigierte Fassung, nur diese sieht ein Modell


class Korpus:
    """Alle Seiten einer Anfrage, einmal redigiert."""

    def __init__(self, doks: list[Dokument], schwaerzen: bool, sw: Schwaerzung | None = None):
        self.sw = sw or Schwaerzung(schwaerzen)
        roh = [(d.name, s.nr, s.text) for d in doks for s in d.seiten]
        self.sw.lernen([t for _, _, t in roh])
        self.seiten = [Abschnitt(g, dok, nr, t, self.sw.anwenden(t)) for g, (dok, nr, t) in enumerate(roh, 1)]

    def finde(self, zitat: str | None, abschnitte: list[Abschnitt], bevorzugt: int | None = None) -> int | None:
        """Seite des Zitats: erst in der redigierten Fassung, dann zurückübersetzt im Klartext."""
        g = finde_seite(zitat, [(a.g, a.red) for a in abschnitte], bevorzugt)
        if g is None and zitat:
            g = finde_seite(self.sw.rueck(zitat), [(a.g, a.roh) for a in abschnitte], bevorzugt)
        return g

    def ort(self, g: int | None) -> dict:
        for a in self.seiten:
            if a.g == g:
                return {"dokument": a.dok, "seite": a.seite}
        return {"dokument": None, "seite": None}

    def fenster(self, budget: int) -> list[list[Abschnitt]]:
        """Seiten zu Fenstern bis zum Zeichenbudget; zu lange Seiten werden geteilt."""
        out, akt, laenge = [], [], 0
        for a in self.seiten:
            teile = [a.red[i:i + budget] for i in range(0, max(len(a.red), 1), budget)] or [""]
            for t in teile:
                if akt and laenge + len(t) > budget:
                    out.append(akt)
                    akt, laenge = [], 0
                akt.append(Abschnitt(a.g, a.dok, a.seite, a.roh, t))
                laenge += len(t)
        if akt:
            out.append(akt)
        return out

    @staticmethod
    def render(fenster: list[Abschnitt]) -> str:
        return "\n\n".join(f"=== Seite {a.g} ({a.dok}, S. {a.seite}) ===\n{a.red}" for a in fenster)

    def gekuerzt(self, budget: int) -> tuple[str, bool]:
        """Für Auswahlfragen: Anfang und Ende, wenn der Text zu lang ist."""
        voll = self.render(self.seiten)
        if len(voll) <= budget:
            return voll, False
        kopf = int(budget * 0.75)
        return voll[:kopf] + "\n[…]\n" + voll[-(budget - kopf):], True


async def _gather(aufgaben, n: int):
    sem = asyncio.Semaphore(n)

    async def lauf(a):
        async with sem:
            return await a

    return await asyncio.gather(*(lauf(a) for a in aufgaben))


# ---------------------------------------------------------------- Personenerkennung per LLM (vor der Schwärzung)
SYS_NER = ("Du findest Personenbezug in Verwaltungsdokumenten. Liste jeden Namen einer natürlichen Person und jede "
           "Wohn- oder Postanschrift (nur Straße und Hausnummer) genau so, wie er im Text steht. Keine Behörden, Firmen, "
           "Orte, Gesetze oder Berufsbezeichnungen. Steht nichts im Text, gib leere Listen zurück.")
SCHEMA_NER = {"type": "object", "required": ["personen", "anschriften"], "properties": {
    "personen": {"type": "array", "maxItems": 60, "items": {"type": "string", "maxLength": 80}},
    "anschriften": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 120}}}}


async def korpus(doks: list[Dokument], schwaerzen: bool, m, cfg: Config) -> Korpus:
    """Korpus bauen; mit SCHWAERZEN_NER=llm findet das Modell zusätzlich Namen im Fließtext.
    Übernommen wird nur, was wörtlich im Text steht. Fällt das Modell aus, bricht die Anfrage ab (fail-closed)."""
    sw = Schwaerzung(schwaerzen)
    if schwaerzen and cfg.schwaerzen_ner == "llm":
        texte = [s.text for d in doks for s in d.seiten]
        fenster, akt = [], ""
        for t in texte:
            for i in range(0, max(len(t), 1), cfg.kontext_zeichen):
                teil = t[i:i + cfg.kontext_zeichen]
                if akt and len(akt) + len(teil) > cfg.kontext_zeichen:
                    fenster.append(akt)
                    akt = ""
                akt += teil + "\n\n"
        if akt.strip():
            fenster.append(akt)
        antworten = await _gather([m.json(SYS_NER, f, SCHEMA_NER, 800) for f in fenster], cfg.parallel)
        for f, a in zip(fenster, antworten):
            for n in a.get("personen", []):
                if isinstance(n, str) and len(n.strip()) >= 3 and "\n" not in n and n.strip() in f:
                    sw.name_hinzu(n.strip())
            for adr in a.get("anschriften", []):
                if isinstance(adr, str) and "\n" not in adr and adr.strip() in f and re.search(r"\d", adr):
                    sw.adresse_hinzu(adr.strip())
    return Korpus(doks, schwaerzen, sw)


# ---------------------------------------------------------------- zusammenfassen
LAENGE = {"kurz": (3, 80), "mittel": (5, 150), "lang": (8, 300)}
SYS_ZUS = ("Du fasst Verwaltungsdokumente sachlich zusammen. Nur Aussagen, die im Text stehen; keine Bewertung, "
           "keine Empfehlung. Zu jedem Kernpunkt die Seitennummer aus der Überschrift '=== Seite n' und ein wörtliches "
           "Zitat aus dem Text (höchstens 200 Zeichen) als Beleg. Platzhalter wie [NAME_1] oder [EMAIL_1] lässt du unverändert.")


async def zusammenfassen(k: Korpus, m, cfg: Config, laenge: str = "mittel", fokus: str | None = None) -> dict:
    if laenge not in LAENGE:
        raise ValueError("laenge: kurz, mittel oder lang")
    n, woerter = LAENGE[laenge]
    schema = {"type": "object", "required": ["zusammenfassung", "kernpunkte"], "properties": {
        "zusammenfassung": {"type": "string"},
        "kernpunkte": {"type": "array", "maxItems": n, "items": {"type": "object", "required": ["text", "seite", "zitat"],
                       "properties": {"text": {"type": "string"}, "seite": {"type": "integer"}, "zitat": {"type": "string"}}}}}}
    auftrag = f"Höchstens {woerter} Wörter, höchstens {n} Kernpunkte." + (f" Schwerpunkt: {fokus}." if fokus else "")
    fenster = k.fenster(cfg.kontext_zeichen)
    teile = await _gather([m.json(SYS_ZUS, f"{auftrag}\n\n{k.render(f)}", schema, 2000) for f in fenster], cfg.parallel)
    kern = [p for t in teile for p in t.get("kernpunkte", [])]
    if len(teile) == 1:
        text = teile[0].get("zusammenfassung", "")
    else:  # map-reduce über die Teilzusammenfassungen
        teiltexte = "\n\n".join(f"Teil {i + 1}: {t.get('zusammenfassung', '')}" for i, t in enumerate(teile))
        r = await m.json("Fasse die Teilzusammenfassungen zu einer Zusammenfassung zusammen. Nichts hinzufügen.",
                         f"{auftrag}\n\n{teiltexte}", {"type": "object", "required": ["zusammenfassung"],
                                                       "properties": {"zusammenfassung": {"type": "string"}}}, 1200)
        text = r.get("zusammenfassung", "")
        schritt = max(1, len(kern) // n)
        kern = kern[::schritt][:n]
    punkte = []
    for p in kern:
        g = k.finde(p.get("zitat"), k.seiten, p.get("seite"))
        punkte.append({"text": k.sw.rueck(p.get("text", "")), "zitat": k.sw.rueck(p.get("zitat")),
                       "belegt": g is not None, **k.ort(g)})
    return {"zusammenfassung": k.sw.rueck(text), "kernpunkte": punkte,
            "unbelegte_kernpunkte": sum(1 for p in punkte if not p["belegt"]), "stufe": 2}


# ---------------------------------------------------------------- extrahieren
SYS_EXT = ("Du extrahierst Felder aus Verwaltungsdokumenten. Übernimm nur Werte, die im Text stehen; erfinde nichts. "
           "Gib zu jedem Wert die Seitennummer aus der Überschrift '=== Seite n' und ein wörtliches Zitat aus dem Text an, "
           "das den Wert enthält (höchstens 200 Zeichen). Steht ein Wert nicht im Text, setze wert, seite und zitat auf null. "
           "Platzhalter wie [NAME_1], [ADRESSE_1] oder [IBAN_1] übernimmst du unverändert als Wert.")


def _stufe0(k: Korpus, name: str, f: dict) -> dict | None:
    """IBAN und E-Mail regelbasiert, wenn genau ein gültiger Wert im Dokument steht."""
    muster = {"iban": IBAN, "email": EMAIL}.get(f.get("format"))
    if not muster:
        return None
    treffer: dict[str, Abschnitt] = {}
    for a in k.seiten:
        for t in muster.findall(a.roh):
            if f.get("format") == "iban" and not iban_gueltig(t):
                continue
            treffer.setdefault(re.sub(r"\s", "", t).upper() if f.get("format") == "iban" else t, a)
    if len(treffer) != 1:
        return None
    wert, a = next(iter(treffer.items()))
    zitat = next(z for z in muster.findall(a.roh) if (re.sub(r"\s", "", z).upper() if f.get("format") == "iban" else z) == wert)
    return {"wert": wert, "status": "ok", "stufe": 0, "zitat": zitat, "dokument": a.dok, "seite": a.seite}


def _feldliste(felder: dict[str, dict], namen: list[str]) -> str:
    zeilen = []
    for n in namen:
        f = felder[n]
        art = f.get("format") or f.get("type", "string")
        extra = f" (eine von: {', '.join(map(str, f['enum']))})" if "enum" in f else ""
        zeilen.append(f"- {n} ({art}){extra}: {f.get('description', '')}")
    return "\n".join(zeilen)


async def extrahieren(k: Korpus, m, cfg: Config, schema: dict) -> dict:
    felder = pruefe_schema(schema)
    pflicht = set(schema.get("required", []))
    erg: dict[str, dict] = {}
    for n, f in felder.items():
        s0 = _stufe0(k, n, f)
        if s0:
            erg[n] = s0
    for fenster in k.fenster(cfg.kontext_zeichen):
        offen = [n for n in felder if erg.get(n, {}).get("status") != "ok"]
        if not offen:
            break
        teil = {n: felder[n] for n in offen}
        antwort = await m.json(SYS_EXT, f"Felder:\n{_feldliste(felder, offen)}\n\nText:\n{k.render(fenster)}",
                               modell_schema(teil), 300 + 120 * len(offen))
        for n in offen:
            r = antwort.get(n) or {}
            if r.get("wert") is None:
                continue
            g = k.finde(r.get("zitat"), fenster, r.get("seite"))
            wert = k.sw.rueck(r["wert"])
            if g is None:
                if n not in erg:
                    erg[n] = {"wert": None, "vorschlag": wert, "status": "ohne_fundstelle", "stufe": 2,
                              "zitat": k.sw.rueck(r.get("zitat")), "dokument": None, "seite": None,
                              "hinweis": "Zitat steht nicht im Text; Wert nicht übernommen"}
                continue
            norm, fehler = normalisiere(wert, felder[n])
            neu = {"wert": norm, "status": "ungueltig" if fehler else "ok", "stufe": 2,
                   "zitat": k.sw.rueck(r.get("zitat")), **k.ort(g)}
            if fehler:
                neu["hinweis"] = fehler
            if n not in erg or erg[n]["status"] != "ok":
                erg[n] = neu
    for n in felder:
        erg.setdefault(n, {"wert": None, "status": "fehlt", "stufe": 2, "zitat": None, "dokument": None, "seite": None})
    fehlend = [n for n in felder if n in pflicht and erg[n]["status"] != "ok"]
    return {"daten": {n: erg[n] for n in felder}, "vollstaendig": not fehlend, "fehlende_pflichtfelder": fehlend}


# ---------------------------------------------------------------- klassifizieren
async def klassifizieren(k: Korpus, m, cfg: Config, optionen: list[dict]) -> dict:
    if not optionen or len(optionen) > 19:
        raise ValueError("1 bis 19 Optionen")
    ids = [o["id"] for o in optionen]
    if len(set(ids)) != len(ids):
        raise ValueError("Options-IDs müssen eindeutig sein")
    texte = [f"{o['id']}: {o.get('beschreibung', '')}".strip(": ") for o in optionen] + [KEINE]
    text, gek = k.gekuerzt(min(cfg.kontext_zeichen, 12000))
    try:
        p = await m.waehle("Ordne das Dokument genau einer Option zu.", f"Dokument:\n{text}", texte)
    except ModellFehler as e:
        return {"wert": None, "p": None, "pfad": "mensch", "grund": str(e), "verteilung": {}, "stufe": 1, "fail_closed": True}
    vert = {i: round(p[t], 4) for i, t in zip(ids, texte)}
    vert[KEINE] = round(p[KEINE], 4)
    best = max(vert, key=vert.get)
    auto = best != KEINE and vert[best] >= cfg.auto_ab and not gek
    grund = None if auto else ("keine Option passt" if best == KEINE else "gekürzter Text" if gek else "unter der Schwelle")
    return {"wert": None if best == KEINE else best, "p": vert[best], "pfad": "auto" if auto else "mensch",
            "grund": grund, "verteilung": dict(sorted(vert.items(), key=lambda x: -x[1])), "gekuerzt": gek, "stufe": 1}


# ---------------------------------------------------------------- prüfen (Vollständigkeit)
async def _nachweis(k: Korpus, m, cfg: Config, nw: dict) -> dict:
    fenster = k.fenster(min(cfg.kontext_zeichen, 16000))
    sys = "Du prüfst Unterlagen. Beurteile nur anhand des Textes, ob die Aussage belegt ist."
    try:
        ps = await _gather([m.waehle(sys, f"Text:\n{k.render(f)}\n\nAussage: {nw['aussage']}", ["ja, belegt", "nein, nicht belegt"])
                            for f in fenster], cfg.parallel)
    except ModellFehler as e:
        return {"id": nw["id"], "status": "unsicher", "p_ja": None, "grund": str(e), "fail_closed": True, "stufe": 1}
    i = max(range(len(ps)), key=lambda j: ps[j]["ja, belegt"])
    p_ja = ps[i]["ja, belegt"]
    status = "vorhanden" if p_ja >= cfg.ja_ab else "fehlt" if p_ja <= cfg.nein_bis else "unsicher"
    ort = {"dokument": fenster[i][0].dok, "seiten": sorted({a.seite for a in fenster[i]})} if status == "vorhanden" else {}
    return {"id": nw["id"], "aussage": nw["aussage"], "status": status, "p_ja": round(p_ja, 4), "stufe": 1, **ort}


async def pruefen(k: Korpus, m, cfg: Config, checkliste: dict) -> dict:
    nachweise = checkliste.get("nachweise", [])
    schema = checkliste.get("pflichtfelder")
    if not nachweise and not schema:
        raise ValueError("checkliste braucht nachweise und/oder pflichtfelder")
    for nw in nachweise:
        if not nw.get("id") or not nw.get("aussage"):
            raise ValueError("jeder Nachweis braucht id und aussage")
    aufgaben = [_nachweis(k, m, cfg, nw) for nw in nachweise]
    nw_erg = list(await asyncio.gather(*aufgaben)) if aufgaben else []
    felder = None
    if schema:
        try:
            felder = await extrahieren(k, m, cfg, schema)
        except ModellFehler as e:
            felder = {"daten": {}, "vollstaendig": False, "fehlende_pflichtfelder": list(schema.get("required", [])),
                      "fail_closed": True, "grund": str(e)}
    fehlt = [n["id"] for n in nw_erg if n["status"] == "fehlt"]
    unsicher = [n["id"] for n in nw_erg if n["status"] == "unsicher"]
    feld_offen = felder["fehlende_pflichtfelder"] if felder else []
    feld_unsicher = [n for n in feld_offen if felder["daten"].get(n, {}).get("status") in ("ungueltig", "ohne_fundstelle")]
    feld_fehlt = [n for n in feld_offen if n not in feld_unsicher]
    if unsicher or feld_unsicher or (felder or {}).get("fail_closed"):
        ergebnis = "pruefen"
    elif fehlt or feld_fehlt:
        ergebnis = "unvollstaendig"
    else:
        ergebnis = "vollstaendig"
    beschr = {n: (schema or {}).get("properties", {}).get(n, {}).get("description", n) for n in feld_fehlt}
    text_nf = {nw["id"]: nw.get("nachforderung") or nw["aussage"] for nw in nachweise}  # vorformulierter Text, falls angegeben
    nachforderung = [text_nf[n["id"]] for n in nw_erg if n["status"] == "fehlt"] + [beschr[n] for n in feld_fehlt]
    return {"ergebnis": ergebnis, "nachweise": nw_erg, "pflichtfelder": felder,
            "nachforderung": nachforderung, "zu_pruefen": unsicher + feld_unsicher}
