"""Goldset-Messung: echte Fälle mit erwartetem Ergebnis durch den laufenden Dienst schicken, Treffer messen und
Schwellen (AUTO_AB, JA_AB, NEIN_BIS) für eine Zielgenauigkeit vorschlagen.

    python -m app.goldset faelle.jsonl --url http://localhost:8080 --key $KEY --ziel 0.98 --bericht bericht.md

Eine Zeile je Fall (Pfade relativ zur JSONL-Datei):
    {"route": "extrahieren", "dateien": ["a.pdf"], "parameter": {"schema": {...}}, "erwartet": {"iban": "DE89…", "plz": "73728"}}
    {"route": "klassifizieren", "dateien": ["b.pdf"], "parameter": {"optionen": [...]}, "erwartet": "gewerbeanzeige"}
    {"route": "pruefen", "dateien": ["c.pdf"], "parameter": {"checkliste": {...}}, "erwartet": {"ausweis": true, "hr": false}}
    {"route": "aktenplan", "text": "…", "parameter": {"aktenplan": [...]}, "erwartet": "022.31"}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .werkzeuge import _gleich

SCHWELLEN = [round(0.50 + i * 0.01, 2) for i in range(50)]


def _anfrage(client, fall: dict, basis: Path, key: str | None) -> dict:
    daten = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)) for k, v in fall.get("parameter", {}).items()}
    if fall.get("text"):
        daten["text"] = fall["text"]
    dateien = [("dateien", (Path(p).name, (basis / p).read_bytes())) for p in fall.get("dateien", [])]
    r = client.post(f"/v1/{fall['route']}", data=daten, files=dateien or None, headers={"X-API-Key": key} if key else {})
    return {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else {}}


def _wert_gleich(ist, soll) -> bool:
    if isinstance(soll, (int, float)) and not isinstance(soll, bool) and isinstance(ist, (int, float)):
        return abs(ist - soll) <= 0.01
    return ist is not None and _gleich(ist, soll)


def auswerten(ergebnisse: list[tuple[dict, dict]], ziel: float) -> dict:
    felder: dict[str, dict] = {}
    klass, nachweis, fehler = [], [], []
    for fall, r in ergebnisse:
        if r["status"] != 200:
            fehler.append({"fall": fall.get("id"), "status": r["status"]})
            continue
        b, route, soll = r["body"], fall["route"], fall["erwartet"]
        if route == "extrahieren":
            for f, s in soll.items():
                d = b.get("daten", {}).get(f, {})
                st = felder.setdefault(f, {"richtig": 0, "falsch": 0, "nicht_geliefert": 0, "faelschlich": 0})
                if d.get("status") == "ok":
                    if s is None:
                        st["faelschlich"] += 1
                    elif _wert_gleich(d.get("wert"), s):
                        st["richtig"] += 1
                    else:
                        st["falsch"] += 1
                elif s is not None:
                    st["nicht_geliefert"] += 1
        elif route in ("klassifizieren", "aktenplan"):
            wert = b.get("wert") if route == "klassifizieren" else b.get("az")
            p = b.get("p") if route == "klassifizieren" else min((s["p"] for s in b.get("pfad", [])), default=None)
            if p is not None:
                klass.append((p, wert == soll))
        elif route == "pruefen":
            for n in b.get("nachweise", []):
                if n["id"] in soll and n.get("p_ja") is not None:
                    nachweis.append((n["p_ja"], bool(soll[n["id"]])))
    for st in felder.values():
        n = st["richtig"] + st["falsch"] + st["faelschlich"]
        st["genauigkeit_geliefert"] = round(st["richtig"] / n, 4) if n else None
        st["abdeckung"] = round(st["richtig"] / (st["richtig"] + st["falsch"] + st["nicht_geliefert"]), 4) \
            if st["richtig"] + st["falsch"] + st["nicht_geliefert"] else None
    return {"felder": felder, "auto_ab": _schwelle_auto(klass, ziel), "ja_nein": _schwellen_ja_nein(nachweis, ziel),
            "fehler": fehler, "faelle": len(ergebnisse), "ziel": ziel}


def _schwelle_auto(paare: list[tuple[float, bool]], ziel: float) -> dict:
    """Kleinste Schwelle, ab der die automatisch durchgelassenen Fälle mindestens 'ziel' Genauigkeit haben."""
    if not paare:
        return {"n": 0}
    kurve = []
    for s in SCHWELLEN:
        auto = [ok for p, ok in paare if p >= s]
        kurve.append({"schwelle": s, "auto_anteil": round(len(auto) / len(paare), 4),
                      "genauigkeit": round(sum(auto) / len(auto), 4) if auto else None, "n_auto": len(auto)})
    passend = [k for k in kurve if k["genauigkeit"] is not None and k["genauigkeit"] >= ziel and k["n_auto"] >= 20]
    return {"n": len(paare), "gesamtgenauigkeit": round(sum(ok for _, ok in paare) / len(paare), 4),
            "empfehlung": passend[0]["schwelle"] if passend else None,
            "hinweis": None if passend else "Ziel mit ≥ 20 automatischen Fällen nicht erreichbar – mehr Fälle oder alles zur Sachbearbeitung",
            "kurve": kurve[::5]}


def _schwellen_ja_nein(paare: list[tuple[float, bool]], ziel: float) -> dict:
    if not paare:
        return {"n": 0}
    ja = next((s for s in SCHWELLEN if (sel := [v for p, v in paare if p >= s]) and len(sel) >= 20 and sum(sel) / len(sel) >= ziel), None)
    nein = next((round(1 - s, 2) for s in SCHWELLEN if (sel := [v for p, v in paare if p <= 1 - s]) and len(sel) >= 20
                 and sum(not v for v in sel) / len(sel) >= ziel), None)
    return {"n": len(paare), "ja_ab": ja, "nein_bis": nein,
            "hinweis": None if ja and nein else "zu wenige Fälle (je ≥ 20) oder Ziel nicht erreichbar"}


def bericht(a: dict) -> str:
    z = [f"# Goldset-Messung ({a['faelle']} Fälle, Ziel {a['ziel']:.0%})", ""]
    if a["felder"]:
        z += ["## Extraktion je Feld", "", "| Feld | richtig | falsch | nicht geliefert | fälschlich | Genauigkeit (geliefert) | Abdeckung |",
              "|---|---|---|---|---|---|---|"]
        for f, s in sorted(a["felder"].items()):
            z.append(f"| {f} | {s['richtig']} | {s['falsch']} | {s['nicht_geliefert']} | {s['faelschlich']} | "
                     f"{s['genauigkeit_geliefert']} | {s['abdeckung']} |")
        z.append("")
    if a["auto_ab"].get("n"):
        z += ["## Klassifikation / Aktenplan", "", f"Gesamtgenauigkeit {a['auto_ab']['gesamtgenauigkeit']:.1%} bei {a['auto_ab']['n']} Fällen. "
              f"Empfohlene AUTO_AB: **{a['auto_ab']['empfehlung']}** {a['auto_ab']['hinweis'] or ''}", ""]
    if a["ja_nein"].get("n"):
        z += ["## Nachweise", "", f"Empfohlen: JA_AB **{a['ja_nein']['ja_ab']}**, NEIN_BIS **{a['ja_nein']['nein_bis']}** "
              f"({a['ja_nein']['n']} Entscheidungen) {a['ja_nein']['hinweis'] or ''}", ""]
    if a["fehler"]:
        z += [f"## Fehlgeschlagene Anfragen: {len(a['fehler'])}", ""]
    return "\n".join(z)


def messen(faelle: list[dict], client, basis: Path, key: str | None, ziel: float) -> dict:
    return auswerten([(f, _anfrage(client, f, basis, key)) for f in faelle], ziel)


def main(argv=None) -> int:
    import httpx

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("faelle")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--key")
    ap.add_argument("--ziel", type=float, default=0.98)
    ap.add_argument("--bericht")
    a = ap.parse_args(argv)
    pfad = Path(a.faelle)
    faelle = [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]
    with httpx.Client(base_url=a.url, timeout=600) as c:
        erg = messen(faelle, c, pfad.parent, a.key, a.ziel)
    text = bericht(erg)
    if a.bericht:
        Path(a.bericht).write_text(text, encoding="utf-8")
        Path(a.bericht).with_suffix(".json").write_text(json.dumps(erg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(text)
    return 0 if not erg["fehler"] else 1


if __name__ == "__main__":
    sys.exit(main())
