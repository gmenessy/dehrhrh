"""Fundstellen prüfen: ein Zitat muss nach Normalisierung wörtlich im Seitentext stehen."""
from __future__ import annotations

import re
import unicodedata

_ANF = str.maketrans({"„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'", "«": '"', "»": '"', "\u00ad": "", "\ufffe": "", "\x02": ""})  # weiche Trennung (PDFium: U+FFFE)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_ANF)
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)  # Silbentrennung am Zeilenende
    s = re.sub(r"\s+", " ", s)
    return s.strip().casefold()


def belegt(zitat: str | None, text: str) -> bool:
    if not zitat:
        return False
    z = norm(zitat)
    return len(z) >= 3 and z in norm(text)


def finde_seite(zitat: str | None, seiten: list[tuple[int, str]], bevorzugt: int | None = None) -> int | None:
    """Seite, auf der das Zitat steht; die vom Modell genannte Seite zuerst."""
    reihen = sorted(seiten, key=lambda s: s[0] != bevorzugt)
    for nr, text in reihen:
        if belegt(zitat, text):
            return nr
    return None
