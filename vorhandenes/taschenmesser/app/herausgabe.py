"""Schwärzen zur Herausgabe (Akteneinsicht, LIFG, öffentliche Sitzungsvorlagen).

Vorgehen: Funde aus der Schwärzung (Regeln + optional LLM-NER) plus 'zusatz' minus 'ausnahmen' -> alle Vorkommen je
Seite (Textebene oder OCR-Wortboxen) -> betroffene Seiten werden gerastert und schwarz überdeckt, unbetroffene Seiten
unverändert übernommen -> neue PDF ohne Metadaten, Anhänge, Formulare -> Kontroll-OCR der gerasterten Seiten.
Findet die Kontrolle noch einen geschwärzten Begriff, wird nichts ausgegeben (fail-closed).
"""
from __future__ import annotations

import io
import re

from .config import Config
from .dokument import MIN_TEXT, Dokument, ocr_bild, ocr_woerter
from .markieren import _normkarte
from .schwaerzen import Schwaerzung

KATEGORIEN = {"NAME", "ADRESSE", "EMAIL", "TELEFON", "IBAN", "GEBDATUM", "STEUERID", "SVNR", "AUSWEIS"}


def begriffe(sw: Schwaerzung, kategorien: set[str], zusatz: list[str], ausnahmen: list[str]) -> dict[str, str]:
    """Klartext -> Kategorie; Ausnahmen (z. B. Namen von Amtsträgern) werden nicht geschwärzt."""
    aus = {_n(a) for a in ausnahmen if a.strip()}
    out = {}
    for ph, wert in sw.zuordnung.items():
        kat = re.match(r"\[([A-Z]+)_", ph).group(1)
        if kat in kategorien and _n(wert) not in aus and not any(_n(wert) in a.split() for a in aus):
            out[wert] = kat
    for wert, kat in sw.literale.items():  # Namensbestandteile: "Musterfrau" allein muss auch weg
        if kat in kategorien and wert not in out and _n(wert) not in aus and not any(_n(wert) in a.split() for a in aus):
            out[wert] = kat
    for z in zusatz:
        if z.strip() and len(z.strip()) >= 2:
            out.setdefault(z.strip(), "ZUSATZ")
    return out


def _n(s: str) -> str:
    return _normkarte(s)[0].strip()


def _alle(begriff: str, text: str) -> list[tuple[int, int]]:
    """Alle Vorkommen (Original-Zeichenbereiche), Wortgrenzen beachtet."""
    z = _n(begriff)
    if len(z) < 2:
        return []
    t, idx = _normkarte(text)
    out, pos = [], 0
    while (i := t.find(z, pos)) >= 0:
        vor = t[i - 1] if i > 0 else " "
        nach = t[i + len(z)] if i + len(z) < len(t) else " "
        if not (vor.isalnum() or nach.isalnum()):
            out.append((idx[i], idx[i + len(z) - 1] + 1))
        pos = i + 1
    return out


def _boxen_text(page, begriffe_: dict[str, str]) -> tuple[list, list] | None:
    """PDF-Rechtecke (l, b, r, t) je Fund oder None, wenn die Seite keine Textebene hat."""
    tp = page.get_textpage()
    text = tp.get_text_range()
    if len(text.strip()) < MIN_TEXT:
        return None
    boxen, funde = [], []
    for b, kat in begriffe_.items():
        for s, e in _alle(b, text):
            n = tp.count_rects(s, e - s)
            boxen += [tp.get_rect(i) for i in range(n)]
            funde.append(kat)
    return boxen, funde


def _boxen_ocr(woerter: list, begriffe_: dict[str, str]) -> tuple[list, list]:
    """Pixelboxen (l, o, r, u) aus OCR-Wörtern."""
    text, start = "", []
    for w in woerter:
        start.append(len(text))
        text += w[0] + " "
    boxen, funde = [], []
    for b, kat in begriffe_.items():
        for s, e in _alle(b, text):
            boxen += [(w[2], w[3], w[4], w[5]) for w, a in zip(woerter, start) if a < e and a + len(w[0]) > s]
            funde.append(kat)
    return boxen, funde


def vorschau(k_seiten, begriffe_: dict[str, str]) -> list[dict]:
    """Was würde geschwärzt? Für die interne Prüfung (enthält Klartext – nicht herausgeben)."""
    out = []
    for b, kat in sorted(begriffe_.items(), key=lambda x: (x[1], x[0])):
        seiten = [a.seite for a in k_seiten if _alle(b, a.roh)]
        if seiten:
            out.append({"text": b, "kategorie": kat, "seiten": sorted(set(seiten)), "anzahl": sum(len(_alle(b, a.roh)) for a in k_seiten)})
    return out


def schwaerzen_pdf(dok: Dokument, cfg: Config, begriffe_: dict[str, str], rand_pt: float = 1.5) -> tuple[bytes, dict]:
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw, ImageSequence

    if dok.art == "bild":
        frames = [f.convert("RGB") for f in ImageSequence.Iterator(Image.open(io.BytesIO(dok.daten)))]
        buf = io.BytesIO()
        frames[0].save(buf, "PDF", save_all=True, append_images=frames[1:], resolution=200)
        quelle = pdfium.PdfDocument(buf.getvalue())
    elif dok.art == "pdf":
        quelle = pdfium.PdfDocument(dok.daten)
    else:
        raise ValueError("Schwärzen zur Herausgabe braucht eine PDF- oder Bilddatei")
    skala = cfg.ocr_dpi / 72
    neu = pdfium.PdfDocument.new()
    gerastert, zaehler, kontrolle = [], {}, []
    try:
        for i in range(len(quelle)):
            page = quelle[i]
            w_pt, h_pt = page.get_size()
            r = _boxen_text(page, begriffe_)
            if r is None:  # Scan: OCR-Wortboxen in Pixeln der Renderauflösung
                bild = page.render(scale=skala).to_pil().convert("RGB")
                pix, funde = _boxen_ocr(ocr_woerter(bild, cfg), begriffe_)
            else:
                pdf_boxen, funde = r
                bild = page.render(scale=skala).to_pil().convert("RGB") if pdf_boxen else None
                pix = [(l_ * skala, (h_pt - t) * skala, rr * skala, (h_pt - b) * skala) for (l_, b, rr, t) in pdf_boxen]
            if not pix:
                neu.import_pages(quelle, [i])  # unverändert (Text, Vektoren); Metadaten des Originals bleiben draußen
                continue
            zeichnen = ImageDraw.Draw(bild)
            rand = rand_pt * skala
            for (l_, o, rr, u) in pix:
                zeichnen.rectangle([l_ - rand, o - rand, rr + rand, u + rand], fill="black")
            for kat in funde:
                zaehler[kat] = zaehler.get(kat, 0) + 1
            # Kontroll-OCR: steht ein geschwärzter Begriff noch lesbar auf der Seite?
            rest = ocr_bild(bild, cfg)
            reste = [b for b in begriffe_ if len(_n(b)) >= 4 and _alle(b, rest)]
            if reste:
                kontrolle.append({"seite": i + 1, "anzahl": len(reste)})
            seite_pdf = io.BytesIO()
            bild.save(seite_pdf, "PDF", resolution=cfg.ocr_dpi)
            einzel = pdfium.PdfDocument(seite_pdf.getvalue())
            neu.import_pages(einzel, [0])
            einzel.close()
            gerastert.append(i + 1)
        if kontrolle:
            raise RuntimeError(kontrolle)
        out = io.BytesIO()
        neu.save(out)
        return out.getvalue(), {"geschwaerzt": zaehler, "gerasterte_seiten": gerastert, "seiten": len(quelle)}
    finally:
        neu.close()
        quelle.close()
