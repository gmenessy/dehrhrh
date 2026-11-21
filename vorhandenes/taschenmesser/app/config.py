"""Konfiguration nur über Umgebungsvariablen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _keys(raw: str) -> dict[str, str]:
    """API_KEYS="mandant-a:schluessel1,mandant-b:schluessel2" -> {schluessel: mandant}."""
    out: dict[str, str] = {}
    for teil in filter(None, (t.strip() for t in raw.split(","))):
        mandant, _, key = teil.partition(":")
        if not key:
            raise ValueError("API_KEYS erwartet mandant:schluessel")
        out[key.strip()] = mandant.strip()
    return out


@dataclass(frozen=True)
class Config:
    llm_url: str
    llm_model: str
    decision_url: str
    decision_model: str
    api_keys: dict[str, str] = field(repr=False)
    dev: bool
    max_mb: int
    max_seiten: int
    ocr_sprache: str
    ocr_dpi: int
    ocr_timeout: int
    kontext_zeichen: int
    llm_timeout: float
    decision_timeout: float
    parallel: int
    schwaerzen_ner: str
    structured: str
    decision_temperatur: float
    ja_ab: float
    nein_bis: float
    auto_ab: float


def laden() -> Config:
    llm_url = os.environ.get("LLM_URL", "http://vllm-llm:8000/v1").rstrip("/")
    llm_model = os.environ.get("LLM_MODEL", "gemma-4-31b")
    cfg = Config(
        llm_url=llm_url,
        llm_model=llm_model,
        # ohne eigenes Decision-Profil beantwortet das LLM die Auswahlfragen über Logprobs
        decision_url=(os.environ.get("DECISION_URL") or llm_url).rstrip("/"),
        decision_model=os.environ.get("DECISION_MODEL") or llm_model,
        api_keys=_keys(os.environ.get("API_KEYS", "")),
        dev=os.environ.get("DEV", "0") == "1",
        max_mb=_i("MAX_MB", 20),
        max_seiten=_i("MAX_SEITEN", 100),
        ocr_sprache=os.environ.get("OCR_SPRACHE", "deu"),
        ocr_dpi=_i("OCR_DPI", 200),
        ocr_timeout=_i("OCR_TIMEOUT", 60),
        kontext_zeichen=_i("KONTEXT_ZEICHEN", 40000),  # ~10.000 Token
        llm_timeout=_f("LLM_TIMEOUT", 120),
        decision_timeout=_f("DECISION_TIMEOUT", 10),
        parallel=_i("PARALLEL", 8),
        schwaerzen_ner=os.environ.get("SCHWAERZEN_NER", "llm"),
        structured=os.environ.get("VLLM_STRUCTURED", "guided"),  # guided | structured_outputs
        decision_temperatur=_f("DECISION_TEMPERATUR", 1.0),
        ja_ab=_f("JA_AB", 0.85),
        nein_bis=_f("NEIN_BIS", 0.15),
        auto_ab=_f("AUTO_AB", 0.80),
    )
    if not cfg.api_keys and not cfg.dev:
        raise RuntimeError("API_KEYS fehlt. Für lokale Entwicklung DEV=1 setzen.")
    if cfg.structured not in ("guided", "structured_outputs"):
        raise RuntimeError("VLLM_STRUCTURED muss guided oder structured_outputs sein")
    return cfg
