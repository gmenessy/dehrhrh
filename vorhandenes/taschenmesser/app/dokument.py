"""Dateien lesen: PDF-Textebene, OCR nur für Seiten ohne Text, Bilder, Klartext."""
from __future__ import annotations

import hashlib
import io
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

MIN_TEXT = 25  # weniger Zeichen auf einer PDF-Seite -> OCR


class DokumentFehler(Exception):
    def __init__(self, status: int, meldung: str):
        super().__init__(meldung)
        self.status = status
        self.meldung = meldung


@dataclass
class Seite:
    nr: int
    text: str
    quelle: str  # textebene | ocr | text


@dataclass
class Dokument:
    name: str
    sha256: str
    seiten: list[Seite] = field(default_factory=list)
    art: str = "text"                                  # pdf | bild | text | docx | odt | xlsx | mail
    daten: bytes = field(default=b"", repr=False)      # Original, nur für zerlegen/markieren/rechnung
    kopf: dict | None = None                           # E-Mail-Kopf (von, an, betreff, datum)
    hinweise: list[str] = field(default_factory=list)

    @property
    def ocr_seiten(self) -> int:
        return sum(1 for s in self.seiten if s.quelle == "ocr")

    def meta(self) -> dict:
        m = {"name": self.name, "sha256": self.sha256, "seiten": len(self.seiten), "ocr_seiten": self.ocr_seiten}
        if self.hinweise:
            m["hinweise"] = self.hinweise
        return m


def _art(data: bytes) -> str:
    from .formate import FormatFehler, ist_eml, zip_art

    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff") or data[:4] in (b"II*\x00", b"MM\x00*"):
        return "bild"
    if data.startswith(b"PK\x03\x04"):
        try:
            return zip_art(data) or "unbekannt"
        except FormatFehler as e:
            raise DokumentFehler(413, str(e)) from e
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return "msg"
    if ist_eml(data):
        return "eml"
    return "text"


def ocr_bild(bild, cfg: Config) -> str:
    """Tesseract als eigener Prozess mit Zeitlimit; kein Netz, nur eine temporäre Datei."""
    return _tesseract(bild, cfg, [])


def _tesseract(bild, cfg: Config, ausgabe: list[str]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        pfad = Path(tmp) / "seite.png"
        bild.convert("L").save(pfad)
        try:
            r = subprocess.run(["tesseract", str(pfad), "stdout", "-l", cfg.ocr_sprache, "--psm", "3", *ausgabe],
                               capture_output=True, timeout=cfg.ocr_timeout, check=False)
        except subprocess.TimeoutExpired as e:
            raise DokumentFehler(422, "OCR-Zeitlimit überschritten") from e
        if r.returncode != 0:
            raise DokumentFehler(422, "OCR fehlgeschlagen: " + r.stderr.decode(errors="replace")[:200])
        return r.stdout.decode("utf-8", errors="replace")


def ocr_woerter(bild, cfg: Config) -> list[tuple[str, tuple, int, int, int, int]]:
    """Wörter mit Pixel-Box: (text, zeilenschlüssel, links, oben, rechts, unten)."""
    out = []
    for z in _tesseract(bild, cfg, ["tsv"]).splitlines()[1:]:
        f = z.split("\t")
        if len(f) == 12 and f[0] == "5" and f[11].strip():
            l, t, w, h = map(int, f[6:10])
            out.append((f[11], (f[2], f[3], f[4]), l, t, l + w, t + h))
    return out


def _pdf(data: bytes, cfg: Config) -> list[Seite]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as e:  # beschädigte oder verschlüsselte PDF
        raise DokumentFehler(422, f"PDF nicht lesbar: {e}") from e
    try:
        if len(pdf) > cfg.max_seiten:
            raise DokumentFehler(413, f"mehr als {cfg.max_seiten} Seiten")
        seiten = []
        for i in range(len(pdf)):
            page = pdf[i]
            text = page.get_textpage().get_text_range().replace("\ufffe", "").replace("\x02", "")  # Trennstelle
            if len(text.strip()) >= MIN_TEXT:
                seiten.append(Seite(i + 1, text, "textebene"))
            else:
                bild = page.render(scale=cfg.ocr_dpi / 72).to_pil()
                seiten.append(Seite(i + 1, ocr_bild(bild, cfg), "ocr"))
            page.close()
        return seiten
    finally:
        pdf.close()


def _bild(data: bytes, cfg: Config) -> list[Seite]:
    from PIL import Image, ImageSequence

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise DokumentFehler(422, f"Bild nicht lesbar: {e}") from e
    seiten = []
    for i, frame in enumerate(ImageSequence.Iterator(img)):
        if i >= cfg.max_seiten:
            raise DokumentFehler(413, f"mehr als {cfg.max_seiten} Seiten")
        seiten.append(Seite(i + 1, ocr_bild(frame.copy(), cfg), "ocr"))
    return seiten


def lesen_alle(name: str, data: bytes, cfg: Config, tiefe: int = 0) -> list[Dokument]:
    """Wie lesen(), aber E-Mails liefern zusätzlich jeden Anhang als eigenes Dokument (Name: 'mail.eml/anhang.pdf')."""
    from . import formate

    if len(data) > cfg.max_mb * 1024 * 1024:
        raise DokumentFehler(413, f"Datei größer als {cfg.max_mb} MB")
    art = _art(data) if data else "text"
    if art not in ("eml", "msg"):
        return [lesen(name, data, cfg)]
    try:
        kopf, text, anhaenge = (formate.eml_lesen if art == "eml" else formate.msg_lesen)(data)
    except Exception as e:  # beschädigte Mail
        raise DokumentFehler(422, f"E-Mail nicht lesbar: {e}") from e
    mail = Dokument(name=name, sha256=hashlib.sha256(data).hexdigest(), seiten=[Seite(1, text, "text")],
                    art="mail", daten=data, kopf=kopf)
    out = [mail]
    for i, (an, inhalt) in enumerate(anhaenge):
        if i >= 20 or tiefe >= 2:
            mail.hinweise.append(f"Anhang übersprungen (Grenze): {an}")
            continue
        try:
            out.extend(lesen_alle(f"{name}/{an}", inhalt, cfg, tiefe + 1))
        except DokumentFehler as e:
            mail.hinweise.append(f"Anhang nicht gelesen: {an} ({e.meldung})")
    return out


def lesen(name: str, data: bytes, cfg: Config) -> Dokument:
    if len(data) > cfg.max_mb * 1024 * 1024:
        raise DokumentFehler(413, f"Datei größer als {cfg.max_mb} MB")
    if not data:
        raise DokumentFehler(422, "leere Datei")
    art = _art(data)
    if art == "pdf":
        seiten = _pdf(data, cfg)
    elif art == "bild":
        seiten = _bild(data, cfg)
    elif art in ("docx", "odt", "xlsx"):
        from . import formate
        try:
            texte = {"docx": formate.docx_seiten, "odt": formate.odt_seiten, "xlsx": formate.xlsx_seiten}[art](data)
        except (formate.FormatFehler, KeyError, StopIteration, ValueError, Exception) as e:
            raise DokumentFehler(422, f"{art.upper()} nicht lesbar: {e}") from e
        if len(texte) > cfg.max_seiten:
            raise DokumentFehler(413, f"mehr als {cfg.max_seiten} Seiten")
        seiten = [Seite(i + 1, t, art) for i, t in enumerate(texte)]
    elif art in ("eml", "msg", "unbekannt"):
        raise DokumentFehler(415, "E-Mails über lesen_alle lesen" if art != "unbekannt" else "ZIP-Format nicht unterstützt")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise DokumentFehler(415, "Format nicht unterstützt: PDF, Bild, DOCX, ODT, XLSX, EML, MSG oder UTF-8-Text") from e
        teile = text.split("\f")  # Seitenvorschub trennt Seiten
        seiten = [Seite(i + 1, t, "text") for i, t in enumerate(teile)]
    return Dokument(name=name or "dokument", sha256=hashlib.sha256(data).hexdigest(), seiten=seiten, art=art, daten=data)
