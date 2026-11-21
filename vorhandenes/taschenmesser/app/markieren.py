"""Fundstellen in der PDF als Markierung (Highlight-Annotation) einzeichnen – Textebene oder OCR-Wortboxen.

Das Original bleibt unverändert; Markierungen sind Annotationen mit Kommentar ("feld: wert") und lassen sich im
PDF-Betrachter ein- und ausblenden oder löschen.
"""
from __future__ import annotations

import ctypes
import io
import re
import unicodedata

from .config import Config
from .dokument import MIN_TEXT, Dokument, ocr_woerter
from .zitat import _ANF

TRENNUNG = re.compile(r"-[ \t]*\r?\n\s*")
FARBEN = {"ok": (255, 214, 0), "abweichung": (255, 120, 90), "hinweis": (120, 190, 255)}


def _normkarte(text: str) -> tuple[str, list[int]]:
    """Normalisierte Fassung (wie zitat.norm) plus Index zurück auf das Original-Zeichen."""
    weg = set()
    for m in TRENNUNG.finditer(text):  # Silbentrennung am Zeilenende überspringen
        weg.update(range(m.start(), m.end()))
    out, idx, leer = [], [], True
    for i, c in enumerate(text):
        if i in weg:
            continue
        c2 = unicodedata.normalize("NFKC", c).translate(_ANF)
        if not c2:
            continue
        if c2.isspace():
            if not leer:
                out.append(" ")
                idx.append(i)
                leer = True
            continue
        for ch in c2.casefold():
            out.append(ch)
            idx.append(i)
        leer = False
    return "".join(out), idx


def _suche(zitat: str, text: str) -> tuple[int, int] | None:
    """Original-Zeichenbereich [start, ende) des Zitats oder None."""
    z, _ = _normkarte(zitat)
    z = z.strip()
    if len(z) < 3:
        return None
    t, idx = _normkarte(text)
    pos = t.find(z)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(z) - 1] + 1


def _rechtecke_text(page, zitat: str) -> list[tuple[float, float, float, float]] | None:
    tp = page.get_textpage()
    text = tp.get_text_range()
    if len(text.strip()) < MIN_TEXT:
        return None
    treffer = _suche(zitat, text)
    if not treffer:
        return []
    s, e = treffer
    n = tp.count_rects(s, e - s)
    return [tp.get_rect(i) for i in range(n)]


def _rechtecke_ocr(page, zitat: str, woerter: list) -> list[tuple[float, float, float, float]]:
    text, start = "", []
    for w in woerter:
        start.append(len(text))
        text += w[0] + " "
    treffer = _suche(zitat, text)
    if not treffer:
        return []
    s, e = treffer
    gewaehlt = [w for w, a in zip(woerter, start) if a < e and a + len(w[0]) > s]
    zeilen: dict[tuple, list] = {}
    for w in gewaehlt:
        zeilen.setdefault(w[1], []).append(w)
    return [(min(w[2] for w in ws), min(w[3] for w in ws), max(w[4] for w in ws), max(w[5] for w in ws)) for ws in zeilen.values()]


def _annotieren(page, rechtecke, kommentar: str, farbe: tuple[int, int, int]) -> None:
    import pypdfium2.raw as r

    annot = r.FPDFPage_CreateAnnot(page.raw, r.FPDF_ANNOT_HIGHLIGHT)
    try:
        r.FPDFAnnot_SetColor(annot, r.FPDFANNOT_COLORTYPE_Color, *farbe, 110)
        for (l, b, rr, t) in rechtecke:
            q = r.FS_QUADPOINTSF(l, t, rr, t, l, b, rr, b)
            r.FPDFAnnot_AppendAttachmentPoints(annot, ctypes.byref(q))
        box = r.FS_RECTF(min(x[0] for x in rechtecke), max(x[3] for x in rechtecke),
                         max(x[2] for x in rechtecke), min(x[1] for x in rechtecke))
        r.FPDFAnnot_SetRect(annot, ctypes.byref(box))
        puffer = ctypes.create_string_buffer(kommentar.encode("utf-16-le") + b"\0\0")
        r.FPDFAnnot_SetStringValue(annot, b"Contents", ctypes.cast(puffer, ctypes.POINTER(r.FPDF_WCHAR)))
        r.FPDFAnnot_SetStringValue(annot, b"T", ctypes.cast(ctypes.create_string_buffer("Taschenmesser".encode("utf-16-le") + b"\0\0"),
                                                            ctypes.POINTER(r.FPDF_WCHAR)))
    finally:
        r.FPDFPage_CloseAnnot(annot)


def fundstellen_aus(eingabe) -> list[dict]:
    """Akzeptiert eine Liste {seite, zitat, text?, dokument?} oder direkt die Antwort von
    /v1/extrahieren (daten), /v1/zusammenfassen (kernpunkte), /v1/abgleichen (felder) oder /v1/ampel (daten)."""
    if isinstance(eingabe, list):
        return eingabe
    if not isinstance(eingabe, dict):
        raise ValueError("fundstellen: Liste oder Ergebnis-Objekt")
    out = []
    for n, d in (eingabe.get("daten") or {}).items():
        if isinstance(d, dict) and d.get("zitat") and d.get("status") == "ok":
            out.append({"seite": d.get("seite"), "dokument": d.get("dokument"), "zitat": d["zitat"], "text": f"{n}: {d.get('wert')}"})
    for p in eingabe.get("kernpunkte") or []:
        if p.get("belegt"):
            out.append({"seite": p.get("seite"), "dokument": p.get("dokument"), "zitat": p["zitat"], "text": p.get("text", "")})
    for n, f in (eingabe.get("felder") or {}).items():
        for w in f.get("werte", []):
            if w.get("zitat") and w.get("status") == "ok":
                out.append({"seite": w.get("seite"), "dokument": w.get("dokument"), "zitat": w["zitat"],
                            "text": f"{n}: {w.get('wert')}", "art": "abweichung" if f.get("status") == "abweichung" else "ok"})
    return out


def markieren(dok: Dokument, cfg: Config, fundstellen: list[dict]) -> tuple[bytes, dict]:
    import pypdfium2 as pdfium
    from PIL import Image, ImageSequence

    if dok.art == "bild":  # Scan als Bild -> PDF, Markierung über OCR-Boxen
        frames = [f.convert("RGB") for f in ImageSequence.Iterator(Image.open(io.BytesIO(dok.daten)))]
        buf = io.BytesIO()
        frames[0].save(buf, "PDF", save_all=True, append_images=frames[1:], resolution=200)
        pdf = pdfium.PdfDocument(buf.getvalue())
    elif dok.art == "pdf":
        pdf = pdfium.PdfDocument(dok.daten)
    else:
        raise ValueError("markieren braucht eine PDF- oder Bilddatei")
    ocr_cache: dict[int, list] = {}
    markiert, nicht = [], []
    try:
        for i, f in enumerate(fundstellen):
            zitat = (f.get("zitat") or "").strip()
            if not zitat:
                continue
            if f.get("dokument") not in (None, dok.name):
                continue
            seiten = list(range(len(pdf)))
            if isinstance(f.get("seite"), int) and 1 <= f["seite"] <= len(pdf):  # genannte Seite zuerst
                seiten.remove(f["seite"] - 1)
                seiten.insert(0, f["seite"] - 1)
            gefunden = None
            for s in seiten:
                page = pdf[s]
                rects = _rechtecke_text(page, zitat)
                if rects is None:  # keine Textebene -> OCR mit Wortpositionen
                    if s not in ocr_cache:
                        skala = cfg.ocr_dpi / 72
                        h = page.get_height()
                        ocr_cache[s] = [(t, k, l / skala, h - u / skala, r_ / skala, h - o / skala)
                                        for (t, k, l, o, r_, u) in ocr_woerter(page.render(scale=skala).to_pil(), cfg)]
                    rects = _rechtecke_ocr(page, zitat, ocr_cache[s])
                if rects:
                    _annotieren(page, rects, f.get("text") or zitat[:200], FARBEN.get(f.get("art", "ok"), FARBEN["ok"]))
                    gefunden = s + 1
                    break
            (markiert if gefunden else nicht).append({"nr": i, "seite": gefunden or f.get("seite"), "text": f.get("text")})
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue(), {"markiert": len(markiert), "nicht_gefunden": nicht}
    finally:
        pdf.close()
