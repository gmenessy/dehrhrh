"""Paket 3: Aktenplan stufenweise, Einwendungen bündeln, Kriterien-Mapping."""
from __future__ import annotations

import asyncio
import csv
import difflib
import io
import re

from .config import Config
from .dienste import KEINE, Korpus, _gather, _nachweis
from .dokument import Dokument
from .llm import ModellFehler
from .paket2 import _liste
from .zitat import norm


# ================================================================ Aktenplan
def aktenplan_baum(eintraege: list[dict]) -> list[dict]:
    """Flache Liste [{az, titel, beschreibung?}] -> Baum über Präfixe ('0' > '02' > '022' > '022.3')."""
    if not eintraege or len(eintraege) > 5000:
        raise ValueError("Aktenplan: 1 bis 5000 Einträge")
    knoten = {}
    for e in eintraege:
        az = str(e.get("az", "")).strip()
        if not az or not e.get("titel") or az in knoten:
            raise ValueError("jeder Eintrag braucht ein eindeutiges az und einen titel")
        knoten[az] = {"az": az, "titel": e["titel"], "beschreibung": e.get("beschreibung"), "kinder": []}
    schluessel = lambda s: s.replace(".", "").replace(" ", "")  # noqa: E731
    wurzeln = []
    for az, k in knoten.items():
        eltern = [p for p in knoten if p != az and schluessel(az).startswith(schluessel(p)) and len(schluessel(p)) < len(schluessel(az))]
        if eltern:
            knoten[max(eltern, key=lambda p: len(schluessel(p)))]["kinder"].append(k)
        else:
            wurzeln.append(k)
    return wurzeln


def _text(k: dict) -> str:
    return f"{k['az']} {k['titel']}" + (f": {k['beschreibung']}" if k.get("beschreibung") else "")


async def _beste(m, sys_: str, nutzer: str, kandidaten: list[dict]) -> tuple[dict | None, float, list[dict]]:
    """Auswahl unter beliebig vielen Kandidaten: Runden zu je 18 + 'keine passt', Sieger treten gegeneinander an."""
    runde = kandidaten
    verlauf = []
    while True:
        gruppen = [runde[i:i + 18] for i in range(0, len(runde), 18)]
        ps = await asyncio.gather(*(m.waehle(sys_, nutzer, [_text(x) for x in g] + [KEINE]) for g in gruppen))
        sieger = []
        for g, p in zip(gruppen, ps):
            best = max(p, key=p.get)
            if best != KEINE:
                sieger.append((g[[_text(x) for x in g].index(best)], p[best], p))
        if len(gruppen) == 1:
            p = ps[0]
            alt = sorted(((x, p[_text(x)]) for x in runde), key=lambda t: -t[1])[:3]
            verlauf = [{"az": x["az"], "titel": x["titel"], "p": round(q, 4)} for x, q in alt]
            if not sieger:
                return None, round(p[KEINE], 4), verlauf
            return sieger[0][0], round(sieger[0][1], 4), verlauf
        if not sieger:
            return None, 0.0, []
        runde = [s[0] for s in sieger]


async def aktenplan(k: Korpus, m, cfg: Config, eintraege: list[dict], min_p: float = 0.5) -> dict:
    baum = aktenplan_baum(eintraege)
    text, gek = k.gekuerzt(min(cfg.kontext_zeichen, 8000))
    sys_ = "Ordne das Schriftstück der passenden Position des Aktenplans zu."
    pfad, ebene, kandidaten, grund = [], 0, baum, None
    while kandidaten:
        ebene += 1
        wahl, p, alternativen = await _beste(m, sys_, f"Schriftstück:\n{text}", kandidaten)
        if wahl is None:
            grund = f"auf Ebene {ebene} passt keine Position"
            break
        pfad.append({"ebene": ebene, "az": wahl["az"], "titel": wahl["titel"], "p": p, "alternativen": alternativen})
        if p < min_p:
            grund = f"Ebene {ebene} unsicher (p={p})"
            break
        kandidaten = wahl["kinder"]
    blatt = bool(pfad) and grund is None
    auto = blatt and all(s["p"] >= cfg.auto_ab for s in pfad) and not gek
    if blatt and not auto:
        grund = "gekürzter Text" if gek else "unter der Schwelle"
    return {"az": pfad[-1]["az"] if pfad else None, "titel": pfad[-1]["titel"] if pfad else None,
            "vollstaendig": blatt, "pfad": pfad, "pfad_status": "auto" if auto else "mensch", "grund": grund,
            "gekuerzt": gek, "stufe": 1}


# ================================================================ Einwendungen bündeln
SYS_EINW = ("Du bereitest eine Abwägungstabelle vor. Zerlege die Einwendung in ihre einzelnen Kernpunkte (Bedenken, "
            "Forderungen, Hinweise), ordne jeden einem Thema aus der Liste zu ('sonstiges', wenn keines passt), "
            "formuliere ihn neutral in einem Satz und belege ihn mit einem wörtlichen Zitat und der Seitennummer.")


def gleichlautend(texte: list[str], schwelle: float = 0.9) -> list[list[int]]:
    """Gruppen fast gleicher Einwendungen (Formbriefe, Unterschriftenaktionen)."""
    normal = [re.sub(r"\s+", " ", norm(t)) for t in texte]
    gruppe = list(range(len(texte)))
    for i in range(len(texte)):
        for j in range(i + 1, len(texte)):
            if gruppe[j] != j or not normal[i] or not normal[j]:
                continue
            sm = difflib.SequenceMatcher(None, normal[i], normal[j], autojunk=False)
            if sm.real_quick_ratio() >= schwelle and sm.quick_ratio() >= schwelle and sm.ratio() >= schwelle:
                gruppe[j] = gruppe[i]
    out: dict[int, list[int]] = {}
    for i, g in enumerate(gruppe):
        out.setdefault(g, []).append(i)
    return [v for v in out.values() if len(v) > 1]


async def einwendungen(k: Korpus, m, cfg: Config, doks: list[Dokument], themen: list[dict]) -> dict:
    if len(doks) > 500:
        raise ValueError("höchstens 500 Einwendungen je Anfrage")
    ids = [t.get("id") for t in themen]
    if not themen or not all(ids) or len(set(ids)) != len(ids) or len(themen) > 40:
        raise ValueError("themen: 1 bis 40 Einträge mit eindeutiger id")
    texte = ["\n".join(a.roh for a in k.seiten if a.dok == d.name) for d in doks]
    gruppen = gleichlautend(texte)
    vertreter = {g[0]: g for g in gruppen}
    kopie_von = {j: g[0] for g in gruppen for j in g[1:]}
    themenliste = "\n".join(f"- {t['id']}: {t.get('beschreibung', '')}" for t in themen)
    punkt = {"type": "object", "properties": {"thema": {"type": "string", "enum": ids + ["sonstiges"]},
                                              "kernpunkt": {"type": "string", "maxLength": 300}}}
    zu_lesen = [i for i in range(len(doks)) if i not in kopie_von]  # gleichlautende nur einmal lesen
    sem = asyncio.Semaphore(max(1, cfg.parallel // 2))

    async def eine(i):
        async with sem:
            teil = Korpus([doks[i]], k.sw.aktiv, k.sw)
            return await _liste(teil, m, cfg, SYS_EINW, f"Themen:\n{themenliste}\n\nEinwendung:", "kernpunkte", punkt, 15, 2500)

    gelesen = dict(zip(zu_lesen, await asyncio.gather(*(eine(i) for i in zu_lesen))))
    einw, tabelle = [], {t: [] for t in ids + ["sonstiges"]}
    for i, d in enumerate(doks):
        quelle = kopie_von.get(i, i)
        punkte = gelesen[quelle]
        einw.append({"nr": i + 1, "dokument": d.name, "kernpunkte": len(punkte),
                     **({"gleichlautend_mit": kopie_von[i] + 1} if i in kopie_von else {}),
                     **({"gleichlautende": len(vertreter[i])} if i in vertreter else {})})
        if i in kopie_von:
            continue  # inhaltlich bereits über die erste Fassung erfasst
        n_gleich = len(vertreter.get(i, [i]))
        for p in punkte:
            tabelle.setdefault(p.get("thema") or "sonstiges", []).append({
                "einwender_nr": i + 1, "anzahl_gleichlautend": n_gleich, "kernpunkt": p.get("kernpunkt"),
                "zitat": p.get("zitat"), "seite": p.get("seite"), "belegt": p["belegt"]})
    themen_aus = [{"thema": t, "beschreibung": next((x.get("beschreibung") for x in themen if x["id"] == t), "sonstiges"),
                   "kernpunkte": len(v), "einwender": sum(x["anzahl_gleichlautend"] for x in v) if v else 0, "eintraege": v}
                  for t, v in tabelle.items() if v]
    return {"einwendungen": einw, "themen": sorted(themen_aus, key=lambda t: -t["einwender"]),
            "gleichlautende_gruppen": [[j + 1 for j in g] for g in gruppen],
            "unbelegt": sum(1 for t in themen_aus for e in t["eintraege"] if not e["belegt"]),
            "hinweis_abwaegung": "Vorbereitung der Abwägung; die Abwägung selbst trifft die Verwaltung bzw. das Gremium."}


def abwaegung_csv(erg: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Thema", "Einwender Nr.", "gleichlautend (Anzahl)", "Kernpunkt", "Zitat", "Seite", "belegt", "Abwägung", "Beschlussvorschlag"])
    for t in erg["themen"]:
        for e in t["eintraege"]:
            w.writerow([t["thema"], e["einwender_nr"], e["anzahl_gleichlautend"], e["kernpunkt"], e["zitat"], e["seite"],
                        "ja" if e["belegt"] else "nein", "", ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ================================================================ Kriterien-Mapping
SYS_KRIT_RL = ("Du liest eine Förder- oder Verwaltungsrichtlinie. Liste die prüfbaren Anforderungen an einen Antrag "
               "(Voraussetzungen, Fördergegenstände, Ausschlüsse, Pflichtangaben) je als kurzen Satz mit wörtlichem Zitat und Seite.")
SYS_KRIT_BELEG = ("Suche für jede Anforderung die Stelle im Antrag, die darauf eingeht. Gib ein wörtliches Zitat (höchstens "
                  "250 Zeichen) und die Seitennummer an; steht nichts dazu im Antrag, setze zitat und seite auf null.")


async def kriterien_aus_richtlinie(k_rl: Korpus, m, cfg: Config) -> list[dict]:
    eintrag = {"type": "object", "properties": {"kriterium": {"type": "string", "maxLength": 300}}}
    roh = await _liste(k_rl, m, cfg, SYS_KRIT_RL, "Anforderungen:", "anforderungen", eintrag, 30, 3000)
    return [{"id": f"K{i + 1}", "kriterium": r["kriterium"], "quelle": {"zitat": r.get("zitat"), "seite": r.get("seite"), "belegt": r["belegt"]}}
            for i, r in enumerate(r for r in roh if r.get("kriterium"))]


async def kriterien(k: Korpus, m, cfg: Config, liste: list[dict]) -> dict:
    if not liste or len(liste) > 40:
        raise ValueError("1 bis 40 Kriterien")
    for i, x in enumerate(liste):
        x.setdefault("id", f"K{i + 1}")
        if not x.get("kriterium"):
            raise ValueError("jedes Kriterium braucht einen Text 'kriterium'")
    # 1) Decision je Kriterium (wie Nachweise): geht der Antrag darauf ein?
    nachweise = await asyncio.gather(*(_nachweis(k, m, cfg, {"id": x["id"], "aussage": f"Der Antrag geht auf folgende Anforderung ein und "
                                                                                       f"legt dar, wie sie erfüllt wird: {x['kriterium']}"})
                                       for x in liste))
    # 2) Beleg-Zitate per LLM, geprüft gegen den Antrag
    schema = {"type": "object", "required": [x["id"] for x in liste], "properties": {x["id"]: {
        "type": "object", "required": ["zitat", "seite"], "properties": {"zitat": {"type": ["string", "null"], "maxLength": 300},
                                                                      "seite": {"type": ["integer", "null"]}}} for x in liste}}
    anford = "\n".join(f"{x['id']}: {k.sw.anwenden(x['kriterium'])}" for x in liste)
    belege: dict[str, dict] = {}
    try:
        for f in k.fenster(cfg.kontext_zeichen):
            offen = [x["id"] for x in liste if x["id"] not in belege]
            if not offen:
                break
            r = await m.json(SYS_KRIT_BELEG, f"Anforderungen:\n{anford}\n\nAntrag:\n{k.render(f)}", schema, 200 + 90 * len(liste))
            for kid in offen:
                z = (r.get(kid) or {}).get("zitat")
                g = k.finde(z, f, (r.get(kid) or {}).get("seite")) if z else None
                if g is not None:
                    belege[kid] = {"zitat": k.sw.rueck(z), **k.ort(g)}
        fail = False
    except ModellFehler:
        fail = True
    matrix = []
    for x, n in zip(liste, nachweise):
        b = belege.get(x["id"])
        if n["status"] == "vorhanden" and b:
            status = "belegt"
        elif n["status"] == "fehlt" and not b:
            status = "nicht_belegt"
        else:
            status = "pruefen"   # Widerspruch zwischen Entscheidung und Beleg oder unsicher
        matrix.append({"id": x["id"], "kriterium": x["kriterium"], "status": status, "p": n.get("p_ja"),
                       **({"beleg": b} if b else {}), **({"quelle": x["quelle"]} if x.get("quelle") else {})})
    zaehl = {s: sum(1 for x in matrix if x["status"] == s) for s in ("belegt", "nicht_belegt", "pruefen")}
    return {"kriterien": matrix, "zusammenfassung": zaehl, **({"fail_closed": True} if fail else {}),
            "hinweis_bewertung": "Zeigt, worauf der Antrag eingeht; die fachliche Bewertung und Förderentscheidung trifft die Sachbearbeitung."}
