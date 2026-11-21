"""Paket 2: Posteingang, Fristen, Vorgangschronik, Arbeitsübersetzung, Fassungsvergleich, Antwort aus Textbausteinen,
Beschlusskontrolle, Rechnungseingang. Regeln zuerst; das Modell liefert nur, was es belegen kann."""
from __future__ import annotations

import asyncio
import datetime as dt
import difflib
import re
from collections import Counter

from . import fristen as fr
from . import rechnung as rg
from . import vorlage
from .config import Config
from .dienste import Korpus, _gather, extrahieren, klassifizieren
from .dokument import Dokument, Seite
from .llm import ModellFehler
from .schema import normalisiere
from .schwaerzen import PLATZHALTER

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


# ================================================================ gemeinsame Listen-Extraktion
async def _liste(k: Korpus, m, cfg: Config, system: str, auftrag: str, feld: str, eintrag: dict, max_n: int,
                 tokens: int = 2500) -> list[dict]:
    """Liste aus allen Fenstern; jeder Eintrag braucht ein Zitat (wird geprüft) und wird zurückübersetzt."""
    eintrag = {**eintrag, "properties": {**eintrag["properties"], "seite": {"type": ["integer", "null"]},
                                         "zitat": {"type": "string", "maxLength": 400}}}
    eintrag["required"] = list(eintrag["properties"])
    schema = {"type": "object", "required": [feld], "properties": {feld: {"type": "array", "maxItems": max_n, "items": eintrag}}}
    fenster = k.fenster(cfg.kontext_zeichen)
    antworten = await _gather([m.json(system, f"{auftrag}\n\n{k.render(f)}", schema, tokens) for f in fenster], cfg.parallel)
    out = []
    for f, a in zip(fenster, antworten):
        for e in a.get(feld, []) or []:
            g = k.finde(e.get("zitat"), f, e.get("seite"))
            e = k.sw.rueck(e)
            out.append({**e, "belegt": g is not None, **k.ort(g)})
    return out


# ================================================================ Posteingang
POSTEINGANG = {"type": "object", "properties": {
    "absender": {"type": "string", "description": "Name der absendenden Person oder Stelle"},
    "absender_art": {"type": "string", "enum": ["Bürger/in", "Unternehmen", "Behörde", "Gericht", "Rechtsanwalt/Kanzlei",
                                                 "Verein/Verband", "sonstige"], "description": "Art des Absenders"},
    "datum": {"type": "string", "format": "date", "description": "Datum des Schreibens"},
    "betreff": {"type": "string", "description": "Betreff des Schreibens"},
    "aktenzeichen_bezug": {"type": "string", "description": "unser Aktenzeichen oder Geschäftszeichen, auf das sich das Schreiben bezieht"},
    "zeichen_absender": {"type": "string", "description": "eigenes Zeichen oder Aktenzeichen des Absenders"},
    "anliegen": {"type": "string", "description": "Was will der Absender? Ein kurzer Satz; als Zitat die Stelle, an der das Anliegen steht"}}}
DRINGEND = ["dringend: Frist läuft bald ab, Eilantrag, Gefahr, Gericht oder Beschwerde über Verzögerung", "normal"]


async def posteingang(k: Korpus, m, cfg: Config, doks: list[Dokument], aemter: list[dict] | None) -> dict:
    ex = await extrahieren(k, m, cfg, POSTEINGANG)
    daten = ex["daten"]
    mail = next((d for d in doks if d.kopf), None)
    if mail:  # Stufe 0: E-Mail-Kopf ist verlässlicher als jede Extraktion
        for feld, wert in (("absender", mail.kopf.get("von")), ("betreff", mail.kopf.get("betreff")), ("datum", mail.kopf.get("datum"))):
            if wert:
                daten[feld] = {"wert": wert, "status": "ok", "stufe": 0, "quelle": "mailkopf", "dokument": mail.name, "seite": 1, "zitat": None}
    text, gek = k.gekuerzt(min(cfg.kontext_zeichen, 12000))
    try:
        p = await m.waehle("Wie dringend ist dieses Schreiben für die Verwaltung?", f"Schreiben:\n{text}", DRINGEND)
        best = max(p, key=p.get)
        dringlichkeit = {"wert": "dringend" if best == DRINGEND[0] else "normal", "p": round(p[best], 4),
                         "pfad": "auto" if p[best] >= cfg.auto_ab and not gek else "mensch"}
    except ModellFehler as e:
        dringlichkeit = {"wert": None, "p": None, "pfad": "mensch", "grund": str(e), "fail_closed": True}
    zust = await klassifizieren(k, m, cfg, aemter) if aemter else None
    gefunden = []
    for a in k.seiten:
        for f in fr.finden(a.roh):
            f.pop("pos")
            gefunden.append({**f, "dokument": a.dok, "seite": a.seite})
    return {"daten": daten, "dringlichkeit": dringlichkeit, "zustaendigkeit": zust, "fristen": gefunden,
            "dokumente": [d.name for d in doks]}


# ================================================================ Fristen
BEGINN = ["bekanntgabe", "zustellung", "zugang", "datum_des_schreibens", "eingang", "sonstiges", "unbekannt"]


async def fristen_finden(k: Korpus, m, cfg: Config, beginn: str | None, beschreiben: bool) -> dict:
    liste = []
    for a in k.seiten:
        for f in fr.finden(a.roh):
            f.pop("pos")
            liste.append({**f, "dokument": a.dok, "seite": a.seite})
    stufe, grund = 0, None
    if beschreiben and liste:
        red = [k.sw.anwenden(f["zitat"]) for f in liste]
        schema = {"type": "object", "required": ["fristen"], "properties": {"fristen": {
            "type": "array", "minItems": len(red), "maxItems": len(red), "items": {
                "type": "object", "required": ["wofuer", "beginn"], "properties": {
                    "wofuer": {"type": "string", "maxLength": 160}, "beginn": {"type": "string", "enum": BEGINN}}}}}}
        try:
            r = await m.json("Beschreibe zu jeder Fristangabe kurz, wofür die Frist gilt und womit sie beginnt. Nichts erfinden.",
                             "\n".join(f"{i + 1}. {z}" for i, z in enumerate(red)), schema, 150 + 60 * len(red))
            if len(r.get("fristen", [])) == len(liste):
                for f, b in zip(liste, r["fristen"]):
                    f["wofuer"], f["beginn"] = k.sw.rueck(b.get("wofuer")), b.get("beginn")
                stufe = 2
            else:
                grund = "Modellantwort unvollständig"
        except ModellFehler as e:
            grund = f"Modell nicht erreichbar ({e}); Fristen ohne Beschreibung"
    if beginn:
        try:
            start = dt.date.fromisoformat(beginn)
        except ValueError as e:
            raise ValueError("beginn als JJJJ-MM-TT") from e
        for f in liste:
            if f["art"] == "dauer":
                f["berechnung"] = fr.fristende(start, f["dauer"]["wert"], f["dauer"]["einheit"])
    return {"fristen": liste, "stufe": stufe, **({"grund": grund} if grund else {}),
            **({} if beginn else {"hinweis_berechnung": "Für Fristen nach Dauer 'beginn' (Tag der Bekanntgabe/des Zugangs) angeben."})}


# ================================================================ Vorgangschronik
SYS_CHRONIK = ("Du erstellst eine Chronik eines Verwaltungsvorgangs. Erfasse jedes datierte Ereignis (Antrag, Schreiben, "
               "Bescheid, Anhörung, Zahlung, Telefonat, Frist) mit Datum TT.MM.JJJJ, einem kurzen Satz, der Seitennummer "
               "aus '=== Seite n' und einem wörtlichen Zitat. Nur was im Text steht. Platzhalter wie [NAME_1] bleiben.")
EREIGNIS = {"type": "object", "properties": {"datum": {"type": ["string", "null"]}, "ereignis": {"type": "string", "maxLength": 240}}}


async def chronik(k: Korpus, m, cfg: Config) -> dict:
    roh = await _liste(k, m, cfg, SYS_CHRONIK, "Chronik der Ereignisse:", "ereignisse", EREIGNIS, 25)
    gesehen, liste = set(), []
    for e in roh:
        d, fehler = normalisiere(e.get("datum"), {"format": "date"}) if e.get("datum") else (None, None)
        e["datum"] = d if d and not fehler else None
        schluessel = (e["datum"], re.sub(r"\W+", "", (e.get("ereignis") or "").casefold())[:60])
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        liste.append(e)
    liste.sort(key=lambda e: (e["datum"] is None, e["datum"] or ""))
    return {"chronik": liste, "unbelegt": sum(1 for e in liste if not e["belegt"]),
            "ohne_datum": sum(1 for e in liste if not e["datum"])}


# ================================================================ Arbeitsübersetzung
DE_WOERTER = set("der die das und ist nicht mit von zu den ein eine für auf im dem des sie wir bitte ich ihr sehr wird "
                 "wurde haben hat oder auch als bei nach".split())


def ist_deutsch(text: str) -> bool:
    w = re.findall(r"[a-zäöüß]+", text.lower())
    return len(w) >= 8 and sum(x in DE_WOERTER for x in w) / len(w) > 0.12


async def uebersetzen(k: Korpus, m, cfg: Config, ziel: str = "Deutsch", erzwingen: bool = False) -> dict:
    gesamt = " ".join(a.roh for a in k.seiten)
    if ziel.lower() in ("de", "deutsch") and ist_deutsch(gesamt) and not erzwingen:
        return {"sprache": "de", "seiten": [{"dokument": a.dok, "seite": a.seite, "text": a.roh} for a in k.seiten],
                "stufe": 0, "hinweis_uebersetzung": "Text ist bereits deutsch; nicht übersetzt."}
    schema = {"type": "object", "required": ["sprache", "text"], "properties": {
        "sprache": {"type": "string", "maxLength": 12}, "text": {"type": "string"}}}
    sys_ = (f"Übersetze den Text vollständig und sinngetreu ins {ziel}. Gib die Ausgangssprache als ISO-639-1-Code an. "
            "Platzhalter wie [NAME_1] oder [IBAN_1] übernimmst du unverändert. Nichts weglassen, nichts ergänzen.")
    teile = []  # (abschnitt, stück)
    for a in k.seiten:
        for i in range(0, max(len(a.red), 1), 4000):
            teile.append((a, a.red[i:i + 4000]))
    antworten = await _gather([m.json(sys_, t or " ", schema, min(8192, len(t) + 300)) for _, t in teile], cfg.parallel)
    seiten: dict[tuple, dict] = {}
    for (a, t), r in zip(teile, antworten):
        fehlen = sorted(set(PLATZHALTER.findall(t)) - set(PLATZHALTER.findall(r.get("text", ""))))
        s = seiten.setdefault((a.dok, a.seite), {"dokument": a.dok, "seite": a.seite, "sprache": r.get("sprache"), "text": "", "fehlende_platzhalter": []})
        s["text"] += k.sw.rueck(r.get("text", ""))
        s["fehlende_platzhalter"] += [k.sw.rueck(x) for x in fehlen]
    sprache = Counter(s["sprache"] for s in seiten.values()).most_common(1)[0][0] if seiten else None
    return {"sprache": sprache, "ziel": ziel, "seiten": list(seiten.values()), "stufe": 2,
            "hinweis_uebersetzung": "Arbeitsübersetzung für die Sachbearbeitung, keine amtliche oder beglaubigte Übersetzung."}


# ================================================================ Fassungsvergleich
ABK = ["z. B.", "z.B.", "d. h.", "u. a.", "i. V. m.", "bzw.", "Abs.", "Nr.", "Art.", "ggf.", "vgl.", "usw.", "etc.", "Str.",
       "Dr.", "Prof.", "gem.", "lt.", "ca.", "Ziff.", "lit.", "Buchst.", "S.", "Bd.", "Anl.", "i. d. F.", "zzgl.", "inkl."]
_ABK_RE = re.compile("|".join(re.escape(a) for a in sorted(ABK, key=len, reverse=True)))
_SATZ = re.compile(r"(?<=[^\d\s][.!?;:])\s+(?=[A-ZÄÖÜ§(„\"0-9])")


def saetze(text: str) -> list[str]:
    bloecke = re.split(r"\n\s*\n|\n(?=\s*§\s*\d)|\n(?=\s*\(\d+\)\s)", text)
    out = []
    for b in bloecke:
        b = re.sub(r"\s+", " ", b.replace("­", "")).strip()
        if not b:
            continue
        geschuetzt = _ABK_RE.sub(lambda m_: m_.group(0).replace(".", "․"), b)
        out += [s.replace("․", ".") for s in _SATZ.split(geschuetzt) if s.strip()]
    return out


def _markiert(alt: str, neu: str) -> str:
    a, b = alt.split(), neu.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(" ".join(a[i1:i2]))
        if op in ("delete", "replace"):
            out.append("[-" + " ".join(a[i1:i2]) + "-]")
        if op in ("insert", "replace"):
            out.append("{+" + " ".join(b[j1:j2]) + "+}")
    return " ".join(out)


async def vergleichen(k: Korpus, m, cfg: Config, doks: list[Dokument], bewerten: bool) -> dict:
    if len(doks) != 2:
        raise ValueError("genau zwei Dokumente: alte und neue Fassung")
    texte = ["\n".join(a.roh for a in k.seiten if a.dok == d.name) for d in doks]
    alt, neu = saetze(texte[0]), saetze(texte[1])
    sm = difflib.SequenceMatcher(None, alt, neu, autojunk=False)
    aenderungen = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if op == "replace" and i2 - i1 == j2 - j1:
            for x, y in zip(alt[i1:i2], neu[j1:j2]):
                if difflib.SequenceMatcher(None, x.split(), y.split(), autojunk=False).ratio() < 0.4:
                    # kaum Gemeinsamkeit: ehrlicher als "geändert" ist gestrichen + neu
                    aenderungen += [{"art": "entfernt", "alt": x, "neu": None}, {"art": "eingefuegt", "alt": None, "neu": y}]
                else:
                    aenderungen.append({"art": "geaendert", "alt": x, "neu": y, "markiert": _markiert(x, y)})
        else:
            a_txt, n_txt = " ".join(alt[i1:i2]), " ".join(neu[j1:j2])
            art = "entfernt" if op == "delete" else "eingefuegt" if op == "insert" else "geaendert"
            aenderungen.append({"art": art, "alt": a_txt or None, "neu": n_txt or None,
                                **({"markiert": _markiert(a_txt, n_txt)} if art == "geaendert" else {})})
    gekuerzt = len(aenderungen) > 400
    aenderungen = [{"nr": i + 1, **a} for i, a in enumerate(aenderungen[:400])]
    stat = Counter(a["art"] for a in aenderungen)
    erg = {"gleich": not aenderungen, "aenderungen": aenderungen, "statistik": {"saetze_alt": len(alt), "saetze_neu": len(neu), **stat},
           "aehnlichkeit": round(sm.ratio(), 4), **({"gekuerzt": True} if gekuerzt else {}), "stufe": 0}
    if bewerten and aenderungen:
        zeilen = "\n".join(f"[{a['nr']}] {a['art']}: {k.sw.anwenden((a['alt'] or '-')[:400])} → {k.sw.anwenden((a['neu'] or '-')[:400])}"
                           for a in aenderungen[:150])
        schema = {"type": "object", "required": ["punkte"], "properties": {"punkte": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "required": ["aenderungen", "zusammenfassung"], "properties": {
                "aenderungen": {"type": "array", "items": {"type": "integer"}}, "zusammenfassung": {"type": "string", "maxLength": 300}}}}}}
        try:
            r = await m.json("Fasse die inhaltlich wesentlichen Änderungen zwischen alter und neuer Fassung zusammen "
                             "(Rechte, Pflichten, Beträge, Fristen, Zuständigkeiten). Redaktionelles weglassen. "
                             "Nenne zu jedem Punkt die Nummern der Änderungen.", zeilen, schema, 1500)
            gueltig = {a["nr"] for a in aenderungen}
            erg["wesentlich"] = [{"aenderungen": [n for n in p.get("aenderungen", []) if n in gueltig],
                                  "zusammenfassung": k.sw.rueck(p.get("zusammenfassung", ""))}
                                 for p in r.get("punkte", []) if any(n in gueltig for n in p.get("aenderungen", []))]
            erg["stufe"] = 2
        except ModellFehler as e:
            erg["grund"] = f"Bewertung nicht möglich ({e}); Synopse ist vollständig"
    return erg


# ================================================================ Antwort aus Textbausteinen
async def antwort(k: Korpus, m, cfg: Config, bausteine: list[dict], daten: dict, ausfuellen: bool) -> dict:
    if not bausteine or len(bausteine) > 60:
        raise ValueError("1 bis 60 Bausteine")
    ids = [b.get("id") for b in bausteine]
    if not all(ids) or len(set(ids)) != len(ids) or not all(b.get("text") for b in bausteine):
        raise ValueError("jeder Baustein braucht eine eindeutige id und text")
    text, gek = k.gekuerzt(min(cfg.kontext_zeichen, 12000))
    fest = [b for b in bausteine if b.get("immer")]
    frei = [b for b in bausteine if not b.get("immer")]
    sys_ = "Prüfe, ob der Textbaustein inhaltlich zum Anliegen im Schreiben passt und in die Antwort gehört."
    ps = await _gather([m.waehle(sys_, f"Schreiben:\n{text}\n\nBaustein: {b.get('titel', b['id'])}: {b.get('wann') or b['text'][:400]}",
                                 ["ja, passt", "nein, passt nicht"]) for b in frei], cfg.parallel)
    bewertet = {b["id"]: round(p["ja, passt"], 4) for b, p in zip(frei, ps)}
    gewaehlt = [b for b in bausteine if b.get("immer") or bewertet.get(b["id"], 0) >= cfg.ja_ab]
    zu_pruefen = [{"id": b["id"], "p": bewertet[b["id"]]} for b in frei if cfg.nein_bis < bewertet[b["id"]] < cfg.ja_ab]
    roh = "\n\n".join(b["text"] for b in gewaehlt)
    werte = dict(daten or {})
    offen = [p for p in vorlage.platzhalter(roh) if vorlage._hole(werte, p) in (None, "")]
    if ausfuellen and offen:
        einfach = [p for p in offen if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", p)]
        if einfach:
            ex = await extrahieren(k, m, cfg, {"type": "object", "properties": {
                p: {"type": "string", "description": p.replace("_", " ")} for p in einfach}})
            for p, d in ex["daten"].items():
                if d["status"] == "ok":
                    werte[p] = d["wert"]
    fehlende: set[str] = set()
    fertig = vorlage.fuellen(roh, werte, fehlende)
    return {"text": fertig, "bausteine": [{"id": b["id"], "p": bewertet.get(b["id"]), "immer": bool(b.get("immer"))} for b in gewaehlt],
            "zu_pruefen": zu_pruefen, "offene_platzhalter": sorted(fehlende), "gekuerzt": gek,
            "pfad": "auto" if not zu_pruefen and not fehlende and not gek else "mensch"}


# ================================================================ Beschlusskontrolle
SYS_BESCHLUSS = ("Du liest ein Sitzungsprotokoll. Erfasse jeden Beschluss: Tagesordnungspunkt, Gegenstand, Beschlusswortlaut, "
                 "Ergebnis, Stimmen (falls genannt) und daraus folgende Aufträge mit Zuständigen und Termin (TT.MM.JJJJ). "
                 "Seitennummer aus '=== Seite n' und ein wörtliches Zitat des Beschlusses. Nichts erfinden.")
BESCHLUSS = {"type": "object", "properties": {
    "top": {"type": ["string", "null"], "maxLength": 20}, "gegenstand": {"type": "string", "maxLength": 300},
    "beschluss": {"type": "string", "maxLength": 1200},
    "ergebnis": {"type": "string", "enum": ["angenommen", "abgelehnt", "vertagt", "zur Kenntnis genommen", "unklar"]},
    "ja": {"type": ["integer", "null"]}, "nein": {"type": ["integer", "null"]}, "enthaltung": {"type": ["integer", "null"]},
    "auftraege": {"type": "array", "maxItems": 10, "items": {"type": "object", "required": ["was", "wer", "bis"], "properties": {
        "was": {"type": "string", "maxLength": 300}, "wer": {"type": ["string", "null"]}, "bis": {"type": ["string", "null"]}}}}}}


async def beschluesse(k: Korpus, m, cfg: Config) -> dict:
    liste = await _liste(k, m, cfg, SYS_BESCHLUSS, "Beschlüsse:", "beschluesse", BESCHLUSS, 30, 4000)
    auftraege = []
    for b in liste:
        for a in b.get("auftraege", []):
            if a.get("bis"):
                d, fehler = normalisiere(a["bis"], {"format": "date"})
                a["bis"] = d if not fehler else None
            auftraege.append({**a, "top": b.get("top"), "gegenstand": b.get("gegenstand")})
    auftraege.sort(key=lambda a: (a["bis"] is None, a["bis"] or ""))
    return {"beschluesse": liste, "auftraege": auftraege, "unbelegt": sum(1 for b in liste if not b["belegt"])}


# ================================================================ Rechnungseingang
async def rechnung(doks: list[Dokument], schwaerzen: bool, m, cfg: Config, bestellung: dict | None, konten: list[dict] | None) -> dict:
    from .dienste import korpus

    d = doks[0]
    xml = None
    if d.art == "pdf":
        xml = rg.xml_aus_pdf(d.daten)
    elif d.daten.lstrip()[:200].lstrip(b"\xef\xbb\xbf").startswith(b"<"):
        xml = d.daten
    k = None
    if xml:
        r = rg.lesen_xml(xml)
        stufe, quelle = 0, "xml"
    else:  # Papier-/PDF-Rechnung ohne XML: Extraktion mit Fundstellen
        k = await korpus(doks, schwaerzen, m, cfg)
        ex = await extrahieren(k, m, cfg, rg.RECHNUNG_SCHEMA)
        from decimal import Decimal
        r = {n: (Decimal(str(v["wert"])) if isinstance(v["wert"], (int, float)) and not isinstance(v["wert"], bool) else v["wert"])
             for n, v in ex["daten"].items()}
        r.update({"format": "PDF/Scan (extrahiert)", "positionen": [], "summe_positionen": None, "fundstellen": ex["daten"]})
        stufe, quelle = 2, "extraktion"
    pruef = rg.pruefen(r, bestellung, xrechnung=quelle == "xml")
    kontierung = None
    if konten:
        text = "\n".join(filter(None, [r.get("verkaeufer")] + [z.get("bezeichnung") for z in r.get("positionen", [])])) or \
            "\n".join(a.roh for a in (k.seiten if k else []))[:4000]
        kk = Korpus([Dokument("rechnung", d.sha256, [Seite(1, text, "text")])], schwaerzen)
        kontierung = await klassifizieren(kk, m, cfg, konten)
    ergebnis = "ok" if all(p["ok"] for p in pruef) and stufe == 0 else "pruefen"
    return {"ergebnis": ergebnis, "rechnung": rg.json_fest({x: y for x, y in r.items() if x != "fundstellen"}),
            **({"fundstellen": r["fundstellen"]} if "fundstellen" in r else {}), "pruefungen": pruef,
            "kontierung": kontierung, "quelle": quelle, "stufe": stufe}
