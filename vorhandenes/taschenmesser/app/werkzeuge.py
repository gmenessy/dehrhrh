"""Paket 1: abgleichen, ampel, zerlegen, nachforderung. Regeln vor Modell; das Modell entscheidet nie allein."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import re
import zipfile
from zoneinfo import ZoneInfo

from .config import Config
from .dienste import KEINE, Korpus, _nachweis, extrahieren, korpus
from .dokument import Dokument
from .llm import ModellFehler
from .schema import pruefe_schema
from .schwaerzen import Schwaerzung
from .zitat import norm

BERLIN = ZoneInfo("Europe/Berlin")


def heute() -> dt.date:
    return dt.datetime.now(BERLIN).date()


def _vgl(s: str) -> str:
    """Vergleichsform für Text: normalisiert, ohne Satzzeichen."""
    return re.sub(r"\s+", " ", re.sub(r"[.,;:()\"'/\-]", " ", norm(s))).strip()


def _gleich(a, b, toleranz: float = 0) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= toleranz
    sa, sb = _vgl(str(a)), _vgl(str(b))
    return sa == sb or sorted(sa.split()) == sorted(sb.split())  # "Musterfrau, Erika" == "Erika Musterfrau"


# ================================================================ abgleichen
async def abgleichen(k: Korpus, m, cfg: Config, doks: list[Dokument], schema: dict) -> dict:
    """Dasselbe Schema aus jedem Dokument lesen und Feld für Feld vergleichen."""
    felder = pruefe_schema(schema)
    if len(doks) < 2:
        raise ValueError("abgleichen braucht mindestens zwei Dokumente")
    teil = [Korpus([d], k.sw.aktiv, k.sw) for d in doks]  # gemeinsame Schwärzung: gleiche Platzhalter
    ohne_pflicht = {**schema, "required": []}  # Pflicht gilt hier nicht je Dokument
    erg = await asyncio.gather(*(extrahieren(t, m, cfg, ohne_pflicht) for t in teil))
    aus, abweichend, unsicher = {}, [], []
    for n, f in felder.items():
        werte = [{"dokument": d.name, **{x: e["daten"][n].get(x) for x in ("wert", "status", "seite", "zitat")}}
                 for d, e in zip(doks, erg) if e["daten"][n]["status"] != "fehlt"]
        ok = [w for w in werte if w["status"] == "ok"]
        if not ok:
            status = "fehlt"
        elif len(ok) == 1:
            status = "einmal"
        elif all(_gleich(ok[0]["wert"], w["wert"], float(f.get("toleranz", 0))) for w in ok[1:]):
            status = "gleich"
        else:
            status = "abweichung"
            abweichend.append(n)
        if any(w["status"] != "ok" for w in werte):
            unsicher.append(n)
        aus[n] = {"status": status, "werte": werte, **({"unsicher": True} if n in unsicher else {})}
    ergebnis = "abweichung" if abweichend else "pruefen" if unsicher else "stimmig"
    return {"ergebnis": ergebnis, "abweichungen": abweichend, "unsicher": unsicher, "felder": aus}


# ================================================================ ampel (Prüfkorb)
_VERGLEICH = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
OPS = {"fehlt", "vorhanden", "ungueltig", "==", "!=", "in", "nicht_in", "enthaelt", *_VERGLEICH}


def _rhs(v):
    """'heute', 'heute-30', 'heute+14' -> ISO-Datum; sonst unverändert."""
    if isinstance(v, str):
        m_ = re.fullmatch(r"heute(?:([+-])(\d{1,5}))?", v.strip())
        if m_:
            tage = int(m_.group(2) or 0) * (-1 if m_.group(1) == "-" else 1)
            return (heute() + dt.timedelta(days=tage)).isoformat()
    return v


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _bedingung_pruefen(c: dict, pfad: str = "wenn", nw_ids: frozenset = frozenset()) -> set[str]:
    """Syntax prüfen, bevor irgendetwas läuft; gibt die verwendeten Felder zurück."""
    if not isinstance(c, dict):
        raise ValueError(f"{pfad}: Bedingung muss ein Objekt sein")
    if "alle" in c or "eine" in c:
        teile = c.get("alle", c.get("eine"))
        if not isinstance(teile, list) or not teile:
            raise ValueError(f"{pfad}: alle/eine braucht eine nicht leere Liste")
        return set().union(*(_bedingung_pruefen(x, pfad, nw_ids) for x in teile))
    if "nicht" in c:
        return _bedingung_pruefen(c["nicht"], pfad, nw_ids)
    if "nachweis" in c:
        if c.get("status") not in ("vorhanden", "fehlt", "unsicher"):
            raise ValueError(f"{pfad}: nachweis braucht status vorhanden|fehlt|unsicher")
        if c["nachweis"] not in nw_ids:
            raise ValueError(f"{pfad}: Nachweis {c['nachweis']} ist in pruefplan.nachweise nicht definiert")
        return set()
    if "feld" not in c or c.get("op") not in OPS:
        raise ValueError(f"{pfad}: feld und op ({', '.join(sorted(OPS))}) angeben")
    return {c["feld"], *([c["feld2"]] if "feld2" in c else [])}


def _bedingung(c: dict, werte: dict, nachweise: dict) -> bool | None:
    """True/False, None = nicht prüfbar (Wert unklar oder fehlt für einen Vergleich)."""
    if "alle" in c:
        r = [_bedingung(x, werte, nachweise) for x in c["alle"]]
        return False if False in r else None if None in r else True
    if "eine" in c:
        r = [_bedingung(x, werte, nachweise) for x in c["eine"]]
        return True if True in r else None if None in r else False
    if "nicht" in c:
        r = _bedingung(c["nicht"], werte, nachweise)
        return None if r is None else not r
    if "nachweis" in c:
        st = nachweise.get(c["nachweis"])  # None: nicht geprüft (kein Dokument) -> nicht prüfbar
        if st is None:
            return None
        return True if st == c["status"] else None if st == "unsicher" else False
    f, op = werte.get(c["feld"], {"status": "fehlt"}), c["op"]
    st = f.get("status")
    if op in ("fehlt", "vorhanden"):
        if st in ("ohne_fundstelle", "ungueltig"):
            return None
        return (st != "ok") == (op == "fehlt")
    if op == "ungueltig":
        return st == "ungueltig"
    if st != "ok":
        return None
    a = f["wert"]
    if "feld2" in c:
        g = werte.get(c["feld2"], {})
        if g.get("status") != "ok":
            return None
        b = g["wert"]
    else:
        b = _rhs(c.get("wert"))
    if op in ("==", "!="):
        return _gleich(a, b, float(c.get("toleranz", 0))) == (op == "==")
    if op in ("in", "nicht_in"):
        treffer = any(_gleich(a, x) for x in (b if isinstance(b, list) else [b]))
        return treffer == (op == "in")
    if op == "enthaelt":
        return norm(str(b)) in norm(str(a))
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(a)):
        return _VERGLEICH[op](na, nb)
    return _VERGLEICH[op](str(a), str(b))  # ISO-Daten vergleichen sich als Text


async def ampel(doks: list[Dokument], schwaerzen: bool, m, cfg: Config, plan: dict, extern: dict | None) -> dict:
    regeln = plan.get("regeln")
    if not isinstance(regeln, list) or not regeln:
        raise ValueError("pruefplan.regeln fehlt")
    schwellen = {"gelb": 1, "rot": 3, **plan.get("schwellen", {})}
    if not schwellen["gelb"] <= schwellen["rot"]:
        raise ValueError("schwellen: gelb muss <= rot sein")
    nw_liste = plan.get("nachweise", [])
    nw_ids = frozenset(n.get("id") for n in nw_liste if isinstance(n, dict))
    ids = set()
    for r in regeln:
        if not r.get("id") or r["id"] in ids:
            raise ValueError("jede Regel braucht eine eindeutige id")
        ids.add(r["id"])
        r["_felder"] = _bedingung_pruefen(r.get("wenn"), f"regel {r['id']}", nw_ids)
    schema = plan.get("felder")
    if schema:
        pruefe_schema(schema)
    extern = extern or {}

    werte: dict[str, dict] = {n: {"wert": v, "status": "ok" if v not in (None, "") else "fehlt", "quelle": "fachverfahren"}
                              for n, v in extern.items()}
    nachweise: dict[str, str] = {}
    nw_erg, fail_closed = [], False
    if doks and (schema or nw_liste):
        k = await korpus(doks, schwaerzen, m, cfg)
        offen = {n: f for n, f in (schema or {}).get("properties", {}).items() if n not in werte}
        if offen:
            try:
                ex = await extrahieren(k, m, cfg, {"type": "object", "properties": offen})
                for n, d in ex["daten"].items():
                    werte[n] = {**d, "quelle": "dokument"}
            except ModellFehler:
                fail_closed = True
        nw_erg = list(await asyncio.gather(*(_nachweis(k, m, cfg, nw) for nw in nw_liste)))
        nachweise = {n["id"]: n["status"] for n in nw_erg}
        fail_closed = fail_closed or any(n.get("fail_closed") for n in nw_erg)

    ausgewertet, punkte, ko, offen_r = [], 0, False, []
    for r in regeln:
        try:
            hit = _bedingung(r["wenn"], werte, nachweise)
        except ValueError as e:
            raise ValueError(f"regel {r['id']}: {e}") from e
        gewicht = r.get("gewicht", 1)
        ergebnis = "ausgeloest" if hit else "nicht_pruefbar" if hit is None else "nicht_ausgeloest"
        if hit:
            punkte += gewicht
            ko = ko or bool(r.get("ko"))
        if hit is None:
            offen_r.append(r["id"])
        belege = [{"feld": n, **{x: werte.get(n, {}).get(x) for x in ("wert", "status", "dokument", "seite", "zitat", "quelle")}}
                  for n in sorted(r["_felder"])]
        ausgewertet.append({"id": r["id"], "ergebnis": ergebnis, "gewicht": gewicht, "ko": bool(r.get("ko")),
                            "hinweis": r.get("hinweis"), "belege": belege})
    korb = "rot" if ko or punkte >= schwellen["rot"] else "gelb" if punkte >= schwellen["gelb"] else "gruen"
    gruende = [x["hinweis"] or x["id"] for x in ausgewertet if x["ergebnis"] == "ausgeloest"]
    unsicher = [n["id"] for n in nw_erg if n["status"] == "unsicher"]
    if korb == "gruen" and (offen_r or unsicher or fail_closed):  # Unklares ist nie grün
        korb = "gelb"
        gruende.append("nicht alles prüfbar: " + ", ".join(offen_r + unsicher) if offen_r or unsicher else "Modell nicht erreichbar")
    return {"korb": korb, "punkte": punkte, "schwellen": schwellen, "gruende": gruende, "regeln": ausgewertet,
            "nicht_pruefbar": offen_r, "daten": werte, "nachweise": nw_erg, "stufe": 0 if not doks else 2,
            **({"fail_closed": True} if fail_closed else {})}


# ================================================================ zerlegen
FORTS = "Fortsetzung des vorherigen Dokuments"
SEITE_N = re.compile(r"\bSeite\s+(\d{1,3})\s*(?:von|/)\s*(\d{1,3})\b", re.I)


async def zerlegen(k: Korpus, m, cfg: Config, typen: list[dict]) -> dict:
    """Seitenweise: neuer Dokumentanfang (mit Typ) oder Fortsetzung. 'Seite n von m' entscheidet ohne Modell."""
    if not typen or len(typen) > 17:
        raise ValueError("1 bis 17 Dokumenttypen")
    ids = [t.get("id") for t in typen]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("Typ-IDs müssen vorhanden und eindeutig sein")
    texte = [f"{t['id']}: {t.get('beschreibung', '')}".strip(": ") for t in typen]
    sys_ = ("Du sortierst einen Scan-Stapel. Entscheide für die aktuelle Seite: Beginnt hier ein neues Dokument "
            f"(dann dessen Typ) oder setzt sie das vorherige Dokument fort ('{FORTS}', z. B. Seite 2 eines Antrags)? "
            "Ein weiteres Dokument desselben Typs (z. B. die nächste Monatsabrechnung) ist ein neues Dokument.")

    async def seite(i: int):
        a = k.seiten[i]
        if len(a.roh.strip()) < 10:
            return {"entscheidung": "leer", "p": 1.0, "stufe": 0}
        erste = i == 0 or k.seiten[i - 1].dok != a.dok
        sn = SEITE_N.search(a.roh)
        if sn and not erste and int(sn.group(1)) > 1:
            return {"entscheidung": FORTS, "p": 1.0, "stufe": 0, "regel": sn.group(0)}
        opts = texte + ([] if erste else [FORTS]) + [KEINE]
        vorher = "" if erste else f"Ende der vorherigen Seite:\n{k.seiten[i - 1].red[-600:]}\n\n"
        p = await m.waehle(sys_, f"{vorher}Aktuelle Seite:\n{a.red[:3500]}", opts)
        best = max(p, key=p.get)
        ent = FORTS if best == FORTS else KEINE if best == KEINE else ids[texte.index(best)]
        return {"entscheidung": ent, "p": round(p[best], 4), "stufe": 1}

    sem = asyncio.Semaphore(cfg.parallel)

    async def begrenzt(i):
        async with sem:
            return await seite(i)

    ents = await asyncio.gather(*(begrenzt(i) for i in range(len(k.seiten))))
    segmente: list[dict] = []
    for a, e in zip(k.seiten, ents):
        neu = not segmente or segmente[-1]["_datei"] != a.dok or e["entscheidung"] not in (FORTS, "leer")
        if neu:
            typ = None if e["entscheidung"] in (FORTS, KEINE, "leer") else e["entscheidung"]
            segmente.append({"nr": len(segmente) + 1, "typ": typ, "seiten": [], "_datei": a.dok})
        segmente[-1]["seiten"].append({"datei": a.dok, "seite": a.seite, **e})
    for s in segmente:
        ps = [x["p"] for x in s["seiten"] if x["entscheidung"] != "leer"]
        s["p_min"] = min(ps) if ps else None
        s["leerseiten"] = sum(1 for x in s["seiten"] if x["entscheidung"] == "leer")
        auto = s["typ"] is not None and s["p_min"] is not None and s["p_min"] >= cfg.auto_ab
        s["pfad"] = "auto" if auto else "mensch"
        s["grund"] = None if auto else ("Typ unbekannt" if s["typ"] is None else "unter der Schwelle")
        del s["_datei"]
    return {"dokumente": segmente, "seiten_gesamt": len(k.seiten),
            "pfad": "auto" if all(s["pfad"] == "auto" for s in segmente) else "mensch"}


def zerlegung_zip(doks: list[Dokument], ergebnis: dict) -> bytes:
    """Je Segment eine PDF; Bilder werden dafür in PDF-Seiten umgewandelt."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageSequence

    quellen = {}
    for d in doks:
        if d.art == "pdf":
            quellen[d.name] = pdfium.PdfDocument(d.daten)
        elif d.art == "bild":
            frames = [f.convert("RGB") for f in ImageSequence.Iterator(Image.open(io.BytesIO(d.daten)))]
            buf = io.BytesIO()
            frames[0].save(buf, "PDF", save_all=True, append_images=frames[1:], resolution=200)
            quellen[d.name] = pdfium.PdfDocument(buf.getvalue())
        else:
            raise ValueError("ZIP-Ausgabe nur für PDF- und Bilddateien")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for s in ergebnis["dokumente"]:
            neu = pdfium.PdfDocument.new()
            for x in s["seiten"]:
                neu.import_pages(quellen[x["datei"]], [x["seite"] - 1])
            b = io.BytesIO()
            neu.save(b)
            z.writestr(f"{s['nr']:02d}_{s['typ'] or 'unbekannt'}.pdf", b.getvalue())
        z.writestr("zerlegung.json", json.dumps(ergebnis, ensure_ascii=False, indent=1))
    return out.getvalue()


# ================================================================ nachforderung
VORLAGE = {
    "nachforderung": {
        "betreff": "Nachforderung von Unterlagen",
        "einleitung": "wir haben Ihren Antrag{eingang} erhalten. Damit wir ihn bearbeiten können, benötigen wir noch Folgendes:",
        "aufforderung": "Bitte reichen Sie die Unterlagen bis zum {frist} ein{az}.",
        "folge": "Liegen die Unterlagen bis dahin nicht vor, entscheiden wir auf Grundlage der vorliegenden Unterlagen.",
    },
    "anhoerung": {
        "betreff": "Gelegenheit zur Stellungnahme",
        "einleitung": "bevor wir über Ihren Antrag{eingang} entscheiden, geben wir Ihnen Gelegenheit, sich zu folgenden Punkten zu äußern{rg}:",
        "aufforderung": "Ihre Stellungnahme können Sie uns bis zum {frist} schriftlich oder elektronisch zusenden{az}.",
        "folge": "Äußern Sie sich bis dahin nicht, entscheiden wir auf Grundlage der vorliegenden Unterlagen.",
    },
}
SYS_NF = ("Du formulierst Punkte für ein Behördenschreiben in einfache, freundliche Sprache um. Jeder Punkt wird eine "
          "kurze Aufforderung oder Frage an die Bürgerin oder den Bürger. Füge keine neuen Anforderungen, Fristen, Zahlen "
          "oder Rechtsgrundlagen hinzu und lasse nichts weg. Platzhalter wie [NAME_1] bleiben unverändert.")


def fristende(tage: int, ab: dt.date | None = None) -> dt.date:
    """Kalendertage ab heute; fällt das Ende auf Samstag, Sonntag oder Feiertag (BW), gilt der nächste Werktag."""
    from .fristen import naechster_werktag
    return naechster_werktag((ab or heute()) + dt.timedelta(days=tage))[0]


def _neue_zahlen(alt: str, neu: str) -> bool:
    return bool(set(re.findall(r"\d+", neu)) - set(re.findall(r"\d+", alt)))


async def nachforderung(m, cfg: Config, punkte: list[str], art: str = "nachforderung", frist_tage: int = 14,
                        anrede: str | None = None, aktenzeichen: str | None = None, eingang: str | None = None,
                        verfahren: str | None = None, rechtsgrundlage: str | None = None, folgenhinweis: str | None = None,
                        formulieren: bool = True) -> dict:
    if art not in VORLAGE:
        raise ValueError("art: nachforderung oder anhoerung")
    punkte = [p.strip() for p in punkte if isinstance(p, str) and p.strip()]
    if not punkte or len(punkte) > 30:
        raise ValueError("1 bis 30 Punkte angeben")
    if not 1 <= frist_tage <= 180:
        raise ValueError("frist_tage zwischen 1 und 180")
    formuliert, stufe, grund = list(punkte), 0, None
    if formulieren:
        sw = Schwaerzung()
        sw.lernen(punkte)
        red = [sw.anwenden(p) for p in punkte]
        schema = {"type": "object", "required": ["punkte"], "properties": {"punkte": {
            "type": "array", "minItems": len(red), "maxItems": len(red), "items": {"type": "string", "maxLength": 300}}}}
        liste = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(red))
        try:
            r = await m.json(SYS_NF, f"Art: {art}\nVerfahren: {verfahren or '-'}\n\nPunkte:\n{liste}", schema, 200 + 80 * len(red))
            neu = [sw.rueck(x) for x in r.get("punkte", [])]
            if len(neu) != len(punkte):
                grund = "Modellantwort unvollständig; Originalpunkte verwendet"
            else:
                # Schutz gegen Erfindungen: neue Zahlen oder stark verlängerte Punkte -> Original behalten
                formuliert = [o if (not n.strip() or _neue_zahlen(o, n) or len(n) > 2 * len(o) + 60) else n.strip()
                              for o, n in zip(punkte, neu)]
                stufe = 2
        except ModellFehler as e:
            grund = f"Modell nicht erreichbar ({e}); Originalpunkte verwendet"
    v = VORLAGE[art]
    frist = fristende(frist_tage)
    az = f" und geben Sie dabei das Aktenzeichen {aktenzeichen} an" if aktenzeichen else ""
    betreff = " – ".join(x for x in [verfahren, f"Aktenzeichen {aktenzeichen}" if aktenzeichen else None, v["betreff"]] if x)
    folge = v["folge"] if folgenhinweis is None else folgenhinweis
    text = "\n\n".join(x for x in [
        f"{anrede or 'Sehr geehrte Damen und Herren'},",
        v["einleitung"].format(eingang=f" vom {eingang}" if eingang else "", rg=f" ({rechtsgrundlage})" if rechtsgrundlage else ""),
        "\n".join(f"{i + 1}. {p}" for i, p in enumerate(formuliert)),
        v["aufforderung"].format(frist=frist.strftime("%d.%m.%Y"), az=az) + (f" {folge}" if folge else ""),
        "Bei Fragen erreichen Sie uns unter den oben genannten Kontaktdaten.",
        "Mit freundlichen Grüßen"] if x)
    return {"art": art, "betreff": betreff, "text": text, "frist": frist.isoformat(),
            "punkte": [{"original": o, "formuliert": f} for o, f in zip(punkte, formuliert)],
            "stufe": stufe, **({"grund": grund} if grund else {})}
