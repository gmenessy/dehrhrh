"""HTTP-Schnittstelle des Taschenmessers. Zustandslos: nichts wird gespeichert, Logs enthalten keine Inhalte."""
from __future__ import annotations

import datetime as _dt
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from . import dienste, herausgabe, paket2, paket3, vorlage, werkzeuge
from . import fristen as fristmodul
from .markieren import fundstellen_aus, markieren
from .config import Config, laden
from .dokument import Dokument, DokumentFehler, Seite, lesen, lesen_alle
from .llm import ModellFehler, Modelle
from .schema import SchemaFehler, pruefe_schema

audit = logging.getLogger("audit")
HINWEIS = "Vorschlag zur Prüfung durch die Sachbearbeitung; keine Entscheidung."


class Sitzung:
    """Zählt Modellaufrufe je Anfrage."""

    def __init__(self, m):
        self.m, self.llm, self.decision = m, 0, 0

    async def json(self, *a, **kw):
        self.llm += 1
        return await self.m.json(*a, **kw)

    async def waehle(self, *a, **kw):
        self.decision += 1
        return await self.m.waehle(*a, **kw)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "cfg"):
        app.state.cfg = laden()
    if not hasattr(app.state, "modelle"):
        app.state.modelle = Modelle(app.state.cfg)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    yield
    if hasattr(app.state.modelle, "schliessen"):
        await app.state.modelle.schliessen()


app = FastAPI(title="Taschenmesser für Fachverfahren", version="1.0.0", lifespan=lifespan)


def mandant(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    cfg: Config = request.app.state.cfg
    if cfg.dev and not cfg.api_keys:
        return "dev"
    if not x_api_key or x_api_key not in cfg.api_keys:
        raise HTTPException(401, "X-API-Key fehlt oder ist ungültig")
    return cfg.api_keys[x_api_key]


async def _doks(request: Request, dateien: list[UploadFile], text: str | None, pflicht: bool = True) -> list[Dokument]:
    cfg: Config = request.app.state.cfg
    doks: list[Dokument] = []
    for d in dateien or []:
        data = await d.read()
        doks.extend(await run_in_threadpool(lesen_alle, d.filename or "dokument", data, cfg))
    if text:
        import hashlib
        doks.append(Dokument("text", hashlib.sha256(text.encode()).hexdigest(), [Seite(1, text, "text")]))
    if not doks and pflicht:
        raise HTTPException(422, "dateien oder text angeben")
    gesehen: dict[str, int] = {}
    for d in doks:  # eindeutige Namen, damit Fundstellen und Abgleich eindeutig sind
        n = gesehen[d.name] = gesehen.get(d.name, 0) + 1
        if n > 1:
            d.name = f"{d.name} ({n})"
    return doks


def _json_feld(name: str, roh: str | None, pflicht: bool = True):
    if roh is None:
        if pflicht:
            raise HTTPException(422, f"{name} fehlt")
        return None
    try:
        return json.loads(roh)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"{name} ist kein gültiges JSON: {e.msg}") from e


async def _ausfuehren(request: Request, route: str, mandant_id: str, doks: list[Dokument], arbeit) -> JSONResponse:
    rid, t0 = uuid.uuid4().hex[:16], time.perf_counter()
    s = Sitzung(request.app.state.modelle)
    status, ergebnis = 200, None
    try:
        ergebnis = await arbeit(s)
    except (SchemaFehler, ValueError) as e:
        status, ergebnis = 422, {"fehler": str(e)}
    except ModellFehler as e:
        status, ergebnis = 503, {"fehler": str(e), "fail_closed": True, "hinweis": "Bitte manuell bearbeiten oder später erneut senden."}
    dauer = round((time.perf_counter() - t0) * 1000)
    meta = {"anfrage": rid, "mandant": mandant_id, "dokumente": [d.meta() for d in doks],
            "modellaufrufe": {"llm": s.llm, "decision": s.decision}, "dauer_ms": dauer}
    audit.info(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ereignis": "anfrage", "route": route,
                           "status": status, **meta}, ensure_ascii=False))
    if isinstance(ergebnis, Response):  # Datei-Antwort (PDF, ZIP): Metadaten als Header
        ergebnis.headers["X-Anfrage"] = rid
        ergebnis.headers["X-Modellaufrufe"] = f"llm={s.llm};decision={s.decision}"
        return ergebnis
    body = {**(ergebnis or {}), "meta": meta}
    if status == 200:
        body["hinweis"] = HINWEIS
    return JSONResponse(body, status_code=status)


def _mit(fn, doks, schwaerzen: bool, cfg, *args):
    """Arbeit für _ausfuehren: erst Korpus (mit Schwärzung), dann das Werkzeug."""
    async def arbeit(s):
        return await fn(await dienste.korpus(doks, schwaerzen, s, cfg), s, cfg, *args)
    return arbeit


@app.exception_handler(DokumentFehler)
async def _dok_fehler(_: Request, e: DokumentFehler):
    return JSONResponse({"fehler": e.meldung}, status_code=e.status)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/text")
async def text_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                        m: str = Depends(mandant)):
    """Text je Seite: PDF-Textebene, OCR nur für Seiten ohne Text."""
    doks = await _doks(request, dateien, text)

    async def arbeit(_):
        return {"dokumente": [{**d.meta(), "seiten": [{"nr": s.nr, "quelle": s.quelle, "text": s.text} for s in d.seiten]} for d in doks]}
    return await _ausfuehren(request, "text", m, doks, arbeit)


@app.post("/v1/schwaerzen")
async def schwaerzen_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                              m: str = Depends(mandant)):
    """Redigierte Fassung: Namen, Anschriften, Geburtsdatum, Kennungen und Kontaktdaten als Platzhalter. Die Zuordnung wird nicht zurückgegeben."""
    doks = await _doks(request, dateien, text)

    async def arbeit(s):
        k = await dienste.korpus(doks, True, s, request.app.state.cfg)
        return {"seiten": [{"dokument": a.dok, "seite": a.seite, "text": a.red} for a in k.seiten], "funde": k.sw.zaehlung()}
    return await _ausfuehren(request, "schwaerzen", m, doks, arbeit)


@app.post("/v1/zusammenfassen")
async def zusammenfassen_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                                  laenge: str = Form("mittel"), fokus: str | None = Form(None), schwaerzen: bool = Form(True),
                                  m: str = Depends(mandant)):
    doks = await _doks(request, dateien, text)
    cfg = request.app.state.cfg
    return await _ausfuehren(request, "zusammenfassen", m, doks,
                             _mit(dienste.zusammenfassen, doks, schwaerzen, cfg, laenge, fokus))


@app.post("/v1/extrahieren")
async def extrahieren_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                               schema_feld: str = Form(..., alias="schema"), schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Schema-getrieben: jedes Feld mit Wert, Fundstelle und Status; ohne belegtes Zitat kein Wert."""
    doks = await _doks(request, dateien, text)
    cfg, sch = request.app.state.cfg, _json_feld("schema", schema_feld)
    try:
        pruefe_schema(sch)  # vor jedem Modellaufruf
    except SchemaFehler as e:
        raise HTTPException(422, str(e)) from e
    return await _ausfuehren(request, "extrahieren", m, doks,
                             _mit(dienste.extrahieren, doks, schwaerzen, cfg, sch))


@app.post("/v1/klassifizieren")
async def klassifizieren_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                                  optionen: str = Form(...), schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Dokumenttyp oder Zuständigkeit: Auswahl mit Wahrscheinlichkeit, immer mit 'keine passt'."""
    doks = await _doks(request, dateien, text)
    cfg, opt = request.app.state.cfg, _json_feld("optionen", optionen)
    if not isinstance(opt, list) or not all(isinstance(o, dict) and o.get("id") for o in opt):
        raise HTTPException(422, "optionen: Liste von {id, beschreibung}")
    return await _ausfuehren(request, "klassifizieren", m, doks,
                             _mit(dienste.klassifizieren, doks, schwaerzen, cfg, opt))


@app.post("/v1/pruefen")
async def pruefen_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                           checkliste: str = Form(...), schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Vollständigkeitsprüfung: Nachweise (ja/nein mit Wahrscheinlichkeit) und Pflichtfelder (Extraktion)."""
    doks = await _doks(request, dateien, text)
    cfg, cl = request.app.state.cfg, _json_feld("checkliste", checkliste)
    if not isinstance(cl, dict):
        raise HTTPException(422, "checkliste muss ein Objekt sein")
    return await _ausfuehren(request, "pruefen", m, doks,
                             _mit(dienste.pruefen, doks, schwaerzen, cfg, cl))


# ================================================================ Paket 1
@app.post("/v1/abgleichen")
async def abgleichen_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                              schema_feld: str = Form(..., alias="schema"), schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Kreuzprüfung: dasselbe Schema aus jedem Dokument, Feld für Feld verglichen (Antrag ↔ Nachweise)."""
    doks = await _doks(request, dateien, text)
    cfg, sch = request.app.state.cfg, _json_feld("schema", schema_feld)
    try:
        pruefe_schema(sch)
    except SchemaFehler as e:
        raise HTTPException(422, str(e)) from e
    if len(doks) < 2:
        raise HTTPException(422, "mindestens zwei Dokumente")
    return await _ausfuehren(request, "abgleichen", m, doks, _mit(werkzeuge.abgleichen, doks, schwaerzen, cfg, doks, sch))


@app.post("/v1/ampel")
async def ampel_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                         pruefplan: str = Form(...), daten: str | None = Form(None), schwaerzen: bool = Form(True),
                         m: str = Depends(mandant)):
    """Prüfkorb grün/gelb/rot aus gewichteten Regeln über Felder (aus Dokument oder Fachverfahren) und Nachweise."""
    doks = await _doks(request, dateien, text, pflicht=False)
    cfg, plan, ext = request.app.state.cfg, _json_feld("pruefplan", pruefplan), _json_feld("daten", daten, pflicht=False)
    if not isinstance(plan, dict) or (ext is not None and not isinstance(ext, dict)):
        raise HTTPException(422, "pruefplan und daten müssen Objekte sein")
    return await _ausfuehren(request, "ampel", m, doks, lambda s: werkzeuge.ampel(doks, schwaerzen, s, cfg, plan, ext))


@app.post("/v1/zerlegen")
async def zerlegen_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), typen: str = Form(...),
                            als_zip: bool = Form(False), schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Scan-Stapel in Einzeldokumente trennen und typisieren; optional als ZIP mit einer PDF je Dokument."""
    doks = await _doks(request, dateien, None)
    cfg, ty = request.app.state.cfg, _json_feld("typen", typen)
    if not isinstance(ty, list) or not all(isinstance(t, dict) for t in ty):
        raise HTTPException(422, "typen: Liste von {id, beschreibung}")

    async def arbeit(s):
        erg = await werkzeuge.zerlegen(await dienste.korpus(doks, schwaerzen, s, cfg), s, cfg, ty)
        if not als_zip:
            return erg
        data = await run_in_threadpool(werkzeuge.zerlegung_zip, doks, erg)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="zerlegung.zip"', "X-Pfad": erg["pfad"]})
    return await _ausfuehren(request, "zerlegen", m, doks, arbeit)


@app.post("/v1/nachforderung")
async def nachforderung_endpoint(request: Request, punkte: str | None = Form(None), pruefergebnis: str | None = Form(None),
                                 art: str = Form("nachforderung"), frist_tage: int = Form(14), anrede: str | None = Form(None),
                                 aktenzeichen: str | None = Form(None), eingang: str | None = Form(None),
                                 verfahren: str | None = Form(None), rechtsgrundlage: str | None = Form(None),
                                 folgenhinweis: str | None = Form(None), formulieren: bool = Form(True), m: str = Depends(mandant)):
    """Entwurf eines Nachforderungs- oder Anhörungsschreibens. Anrede und Aktenzeichen sieht das Modell nie."""
    liste = _json_feld("punkte", punkte, pflicht=False)
    if liste is None:
        pe = _json_feld("pruefergebnis", pruefergebnis, pflicht=False)
        if not isinstance(pe, dict):
            raise HTTPException(422, "punkte (Liste) oder pruefergebnis (Antwort von /v1/pruefen) angeben")
        liste = pe.get("nachforderung", [])
    if not isinstance(liste, list):
        raise HTTPException(422, "punkte muss eine Liste sein")
    cfg = request.app.state.cfg
    return await _ausfuehren(request, "nachforderung", m, [], lambda s: werkzeuge.nachforderung(
        s, cfg, liste, art, frist_tage, anrede, aktenzeichen, eingang, verfahren, rechtsgrundlage, folgenhinweis, formulieren))


@app.post("/v1/markieren")
async def markieren_endpoint(request: Request, dateien: list[UploadFile] = File(...), fundstellen: str = Form(...),
                             m: str = Depends(mandant)):
    """PDF mit markierten Fundstellen zurück. fundstellen: Liste {seite, zitat, text} oder eine Antwort von
    /v1/extrahieren, /v1/zusammenfassen oder /v1/abgleichen."""
    doks = await _doks(request, dateien, None)
    if len(doks) != 1:
        raise HTTPException(422, "genau eine Datei")
    cfg = request.app.state.cfg
    try:
        fs = fundstellen_aus(_json_feld("fundstellen", fundstellen))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    async def arbeit(_):
        data, info = await run_in_threadpool(markieren, doks[0], cfg, fs)
        name = doks[0].name.rsplit(".", 1)[0] + "_markiert.pdf"
        return Response(data, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{name.encode("ascii", "replace").decode()}"',
            "X-Markiert": str(info["markiert"]), "X-Nicht-Gefunden": str(len(info["nicht_gefunden"]))})
    return await _ausfuehren(request, "markieren", m, doks, arbeit)


# ================================================================ Paket 2
def _liste_von_objekten(name: str, wert, pflicht: bool = True) -> list[dict] | None:
    if wert is None and not pflicht:
        return None
    if not isinstance(wert, list) or not all(isinstance(x, dict) for x in wert):
        raise HTTPException(422, f"{name}: Liste von Objekten")
    return wert


@app.post("/v1/posteingang")
async def posteingang_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                               aemter: str | None = Form(None), schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Eingangsdaten (Absender, Datum, Betreff, Bezug, Anliegen), Dringlichkeit, optional Zuständigkeit, Fristen im Text."""
    doks = await _doks(request, dateien, text)
    cfg, am = request.app.state.cfg, _liste_von_objekten("aemter", _json_feld("aemter", aemter, False), False)
    return await _ausfuehren(request, "posteingang", m, doks, _mit(paket2.posteingang, doks, schwaerzen, cfg, doks, am))


@app.post("/v1/fristen")
async def fristen_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                           beginn: str | None = Form(None), beschreiben: bool = Form(True), schwaerzen: bool = Form(True),
                           m: str = Depends(mandant)):
    """Fristangaben finden (Regel), optional beschreiben (LLM) und mit 'beginn' berechnen (Feiertage BW)."""
    doks = await _doks(request, dateien, text)
    cfg = request.app.state.cfg
    return await _ausfuehren(request, "fristen", m, doks, _mit(paket2.fristen_finden, doks, schwaerzen, cfg, beginn, beschreiben))


@app.post("/v1/fristende")
async def fristende_endpoint(request: Request, beginn: str = Form(...), wert: int = Form(...), einheit: str = Form(...),
                             m: str = Depends(mandant)):
    """Fristrechner: Ereignis am 'beginn', Dauer 'wert' 'einheit' (tage|wochen|monate|jahre)."""
    async def arbeit(_):
        try:
            start = _dt.date.fromisoformat(beginn)
        except ValueError as e:
            raise ValueError("beginn als JJJJ-MM-TT") from e
        return fristmodul.fristende(start, wert, einheit)
    return await _ausfuehren(request, "fristende", m, [], arbeit)


@app.post("/v1/chronik")
async def chronik_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                           schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Zeitstrahl eines Vorgangs über alle Dokumente, jedes Ereignis mit Fundstelle."""
    doks = await _doks(request, dateien, text)
    return await _ausfuehren(request, "chronik", m, doks, _mit(paket2.chronik, doks, schwaerzen, request.app.state.cfg))


@app.post("/v1/uebersetzen")
async def uebersetzen_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                               ziel: str = Form("Deutsch"), erzwingen: bool = Form(False), schwaerzen: bool = Form(True),
                               m: str = Depends(mandant)):
    """Arbeitsübersetzung (nicht amtlich); deutscher Text wird ohne Modell erkannt und nicht übersetzt."""
    doks = await _doks(request, dateien, text)
    return await _ausfuehren(request, "uebersetzen", m, doks, _mit(paket2.uebersetzen, doks, schwaerzen, request.app.state.cfg, ziel, erzwingen))


@app.post("/v1/vergleichen")
async def vergleichen_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), alt: str | None = Form(None),
                               neu: str | None = Form(None), bewerten: bool = Form(True), schwaerzen: bool = Form(True),
                               m: str = Depends(mandant)):
    """Fassungsvergleich (alt, neu): satzweise Synopse ohne Modell, optional wesentliche Änderungen per LLM."""
    doks = await _doks(request, dateien, None, pflicht=False)
    import hashlib
    for name, t in (("alt", alt), ("neu", neu)):
        if t:
            doks.append(Dokument(name, hashlib.sha256(t.encode()).hexdigest(), [Seite(1, t, "text")]))
    if len(doks) != 2:
        raise HTTPException(422, "genau zwei Fassungen: zwei Dateien oder alt und neu")
    return await _ausfuehren(request, "vergleichen", m, doks, _mit(paket2.vergleichen, doks, schwaerzen, request.app.state.cfg, doks, bewerten))


@app.post("/v1/antwort")
async def antwort_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                           bausteine: str = Form(...), daten: str | None = Form(None), ausfuellen: bool = Form(True),
                           schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Antwortentwurf aus dem Bausteinkatalog des Amts: Auswahl per Decision Model, kein frei formulierter Text."""
    doks = await _doks(request, dateien, text)
    bs = _liste_von_objekten("bausteine", _json_feld("bausteine", bausteine))
    dt_ = _json_feld("daten", daten, False) or {}
    if not isinstance(dt_, dict):
        raise HTTPException(422, "daten muss ein Objekt sein")
    return await _ausfuehren(request, "antwort", m, doks, _mit(paket2.antwort, doks, schwaerzen, request.app.state.cfg, bs, dt_, ausfuellen))


@app.post("/v1/vorlage")
async def vorlage_endpoint(request: Request, vorlage_datei: UploadFile | None = File(None, alias="vorlage"),
                           vorlage_text: str | None = Form(None), daten: str = Form(...), streng: bool = Form(False),
                           m: str = Depends(mandant)):
    """Vorlage füllen ohne Modell: Text oder DOCX mit {{feld}}, {{feld|datum}}, {{#feld}}…{{/feld}}."""
    werte = _json_feld("daten", daten)
    if not isinstance(werte, dict):
        raise HTTPException(422, "daten muss ein Objekt sein")
    inhalt = await vorlage_datei.read() if vorlage_datei else None

    async def arbeit(_):
        if inhalt and inhalt.startswith(b"PK"):
            data, fehlende = await run_in_threadpool(vorlage.docx_fuellen, inhalt, werte)
            if streng and fehlende:
                raise ValueError("fehlende Werte: " + ", ".join(sorted(fehlende)))
            name = (vorlage_datei.filename or "vorlage.docx").rsplit(".", 1)[0] + "_gefuellt.docx"
            return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            headers={"Content-Disposition": f'attachment; filename="{name.encode("ascii", "replace").decode()}"',
                                     "X-Fehlende": ",".join(sorted(fehlende))})
        quelle = inhalt.decode("utf-8") if inhalt else vorlage_text
        if not quelle:
            raise ValueError("vorlage (Datei) oder vorlage_text angeben")
        fehlende: set[str] = set()
        text = vorlage.fuellen(quelle, werte, fehlende)
        if streng and fehlende:
            raise ValueError("fehlende Werte: " + ", ".join(sorted(fehlende)))
        return {"text": text, "fehlende": sorted(fehlende), "stufe": 0}
    return await _ausfuehren(request, "vorlage", m, [], arbeit)


@app.post("/v1/beschluesse")
async def beschluesse_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                               schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Beschlusskontrolle: Beschlüsse, Ergebnisse, Stimmen und Aufträge (wer, was, bis) aus Protokollen."""
    doks = await _doks(request, dateien, text)
    return await _ausfuehren(request, "beschluesse", m, doks, _mit(paket2.beschluesse, doks, schwaerzen, request.app.state.cfg))


@app.post("/v1/rechnung")
async def rechnung_endpoint(request: Request, dateien: list[UploadFile] = File(...), bestellung: str | None = Form(None),
                            konten: str | None = Form(None), schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Rechnungseingang: XRechnung/ZUGFeRD ohne Modell lesen und prüfen; PDF ohne XML per Extraktion. Optional
    Abgleich mit der Bestellung und Kontierungsvorschlag."""
    doks = await _doks(request, dateien, None)
    if len(doks) != 1:
        raise HTTPException(422, "genau eine Rechnung")
    cfg, best = request.app.state.cfg, _json_feld("bestellung", bestellung, False)
    kt = _liste_von_objekten("konten", _json_feld("konten", konten, False), False)
    if best is not None and not isinstance(best, dict):
        raise HTTPException(422, "bestellung muss ein Objekt sein")
    return await _ausfuehren(request, "rechnung", m, doks, lambda s: paket2.rechnung(doks, schwaerzen, s, cfg, best, kt))


# ================================================================ Paket 3
@app.post("/v1/aktenplan")
async def aktenplan_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                             aktenplan: str = Form(...), min_p: float = Form(0.5), schwaerzen: bool = Form(True),
                             m: str = Depends(mandant)):
    """Aktenzeichen-Vorschlag stufenweise über den Aktenplan (flache Liste {az, titel, beschreibung}; Ebenen über Präfixe)."""
    doks = await _doks(request, dateien, text)
    plan = _liste_von_objekten("aktenplan", _json_feld("aktenplan", aktenplan))
    return await _ausfuehren(request, "aktenplan", m, doks, _mit(paket3.aktenplan, doks, schwaerzen, request.app.state.cfg, plan, min_p))


@app.post("/v1/herausgabe")
async def herausgabe_endpoint(request: Request, dateien: list[UploadFile] = File(...), kategorien: str | None = Form(None),
                              zusatz: str | None = Form(None), ausnahmen: str | None = Form(None), vorschau: bool = Form(False),
                              m: str = Depends(mandant)):
    """Schwärzen zur Herausgabe: vorschau=true liefert die Fundliste zur Prüfung, sonst die geschwärzte PDF.
    Betroffene Seiten werden gerastert; eine Kontroll-OCR verhindert die Ausgabe, wenn etwas durchscheint."""
    doks = await _doks(request, dateien, None)
    if len(doks) != 1:
        raise HTTPException(422, "genau eine Datei")
    cfg = request.app.state.cfg
    kat = set(_json_feld("kategorien", kategorien, False) or herausgabe.KATEGORIEN)
    if not kat <= herausgabe.KATEGORIEN:
        raise HTTPException(422, f"kategorien aus {sorted(herausgabe.KATEGORIEN)}")
    zus, aus = _json_feld("zusatz", zusatz, False) or [], _json_feld("ausnahmen", ausnahmen, False) or []
    if not all(isinstance(x, str) for x in [*zus, *aus]):
        raise HTTPException(422, "zusatz und ausnahmen: Listen von Texten")

    async def arbeit(s):
        k = await dienste.korpus(doks, True, s, cfg)
        begr = herausgabe.begriffe(k.sw, kat, zus, aus)
        if vorschau:
            return {"funde": herausgabe.vorschau(k.seiten, begr), "kategorien": sorted(kat),
                    "hinweis_vorschau": "Interne Prüfliste mit Klartext – nicht herausgeben. Ergänzen über 'zusatz', ausnehmen über 'ausnahmen'."}
        try:
            data, info = await run_in_threadpool(herausgabe.schwaerzen_pdf, doks[0], cfg, begr)
        except RuntimeError as e:
            return JSONResponse({"fehler": "Kontroll-OCR fand nach dem Schwärzen noch lesbare Begriffe; keine Ausgabe.",
                                 "seiten": e.args[0], "fail_closed": True}, status_code=409)
        name = doks[0].name.rsplit(".", 1)[0] + "_geschwaerzt.pdf"
        return Response(data, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{name.encode("ascii", "replace").decode()}"',
            "X-Geschwaerzt": ";".join(f"{k_}={v}" for k_, v in sorted(info["geschwaerzt"].items())),
            "X-Gerasterte-Seiten": ",".join(map(str, info["gerasterte_seiten"]))})
    return await _ausfuehren(request, "herausgabe", m, doks, arbeit)


@app.post("/v1/einwendungen")
async def einwendungen_endpoint(request: Request, dateien: list[UploadFile] = File(...), themen: str = Form(...),
                                format: str = Form("json"), schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Einwendungen bündeln: Kernpunkte je Einwendung nach Thema, gleichlautende erkannt; als JSON oder CSV (Abwägungstabelle)."""
    doks = await _doks(request, dateien, None)
    th = _liste_von_objekten("themen", _json_feld("themen", themen))
    if format not in ("json", "csv"):
        raise HTTPException(422, "format: json oder csv")
    cfg = request.app.state.cfg

    async def arbeit(s):
        erg = await paket3.einwendungen(await dienste.korpus(doks, schwaerzen, s, cfg), s, cfg, doks, th)
        if format == "csv":
            return Response(paket3.abwaegung_csv(erg), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="abwaegungstabelle.csv"'})
        return erg
    return await _ausfuehren(request, "einwendungen", m, doks, arbeit)


@app.post("/v1/kriterien")
async def kriterien_endpoint(request: Request, dateien: list[UploadFile] = File(default=[]), text: str | None = Form(None),
                             kriterien: str | None = Form(None), richtlinie: UploadFile | None = File(None),
                             schwaerzen: bool = Form(True), m: str = Depends(mandant)):
    """Kriterien-Mapping: Für jede Anforderung (Liste oder aus der Richtlinie gelesen) zeigen, ob und wo der Antrag darauf eingeht."""
    doks = await _doks(request, dateien, text)
    cfg = request.app.state.cfg
    liste = _liste_von_objekten("kriterien", _json_feld("kriterien", kriterien, False), False)
    rl_doks = await run_in_threadpool(lesen_alle, richtlinie.filename or "richtlinie", await richtlinie.read(), cfg) if richtlinie else []
    if not liste and not rl_doks:
        raise HTTPException(422, "kriterien (Liste) oder richtlinie (Datei) angeben")

    async def arbeit(s):
        krit = liste
        if not krit:
            krit = await paket3.kriterien_aus_richtlinie(await dienste.korpus(rl_doks, schwaerzen, s, cfg), s, cfg)
            if not krit:
                raise ValueError("aus der Richtlinie ließen sich keine Anforderungen lesen")
        return await paket3.kriterien(await dienste.korpus(doks, schwaerzen, s, cfg), s, cfg, krit)
    return await _ausfuehren(request, "kriterien", m, doks + rl_doks, arbeit)
