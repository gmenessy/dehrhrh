import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DEV", "1")

from app.config import laden  # noqa: E402
from app.llm import ModellFehler  # noqa: E402

IBAN_OK = "DE89 3704 0044 0532 0130 00"


class FakeModelle:
    """Ersetzt vLLM. json_fn(nutzer, schema) -> dict, waehle_fn(nutzer, optionen) -> dict[str, float]."""

    def __init__(self, json_fn=None, waehle_fn=None, fehler=False, ner_fn=None):
        self.json_fn, self.waehle_fn, self.fehler = json_fn, waehle_fn, fehler
        self.ner_fn = ner_fn or (lambda text: {"personen": [], "anschriften": []})
        self.json_calls, self.waehle_calls, self.ner_calls = [], [], []

    async def json(self, system, nutzer, schema, max_tokens=1500):
        if self.fehler:
            raise ModellFehler("Modell nicht erreichbar")
        if "personen" in schema.get("properties", {}):  # Personenerkennung vor der Schwärzung
            self.ner_calls.append(nutzer)
            return self.ner_fn(nutzer)
        self.json_calls.append(nutzer)
        return self.json_fn(nutzer, schema)

    async def waehle(self, system, nutzer, optionen):
        self.waehle_calls.append(nutzer)
        if self.fehler:
            raise ModellFehler("Modell nicht erreichbar")
        return self.waehle_fn(nutzer, optionen)


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("DEV", "1")
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("SCHWAERZEN_NER", "regel")  # NER per LLM wird in test_schwaerzen.py gezielt geprüft
    return laden()


def pdf_bytes(seiten: list[str], scan_text: str | None = None) -> bytes:
    """PDF mit Textseiten und optional einer Seite, die nur ein Bild enthält (Scan)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for t in seiten:
        y = 800
        for zeile in t.split("\n"):
            c.drawString(60, y, zeile)
            y -= 16
        c.showPage()
    if scan_text:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1654, 600), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 44)
        except OSError:
            font = ImageFont.load_default(size=44)
        y = 40
        for zeile in scan_text.split("\n"):
            d.text((60, y), zeile, fill="black", font=font)
            y += 70
        c.drawImage(ImageReader(img), 0, 500, width=595, height=216)
        c.showPage()
    c.save()
    return buf.getvalue()


def zitat_aus(nutzer: str, muster: str) -> str | None:
    m = re.search(muster, nutzer)
    return m.group(0) if m else None
