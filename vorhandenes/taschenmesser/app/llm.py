"""Zugriff auf vLLM (OpenAI-kompatibel): strukturierte JSON-Ausgabe (Stufe 2) und Auswahl über Logprobs (Stufe 1)."""
from __future__ import annotations

import json
import math
import string

import httpx

from .config import Config

BUCHSTABEN = string.ascii_uppercase


class ModellFehler(Exception):
    """Modell nicht erreichbar oder Antwort unbrauchbar: der Aufrufer entscheidet fail-closed."""


class Modelle:
    def __init__(self, cfg: Config, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        self._http = httpx.AsyncClient(transport=transport, timeout=cfg.llm_timeout)

    async def schliessen(self):
        await self._http.aclose()

    def _struct(self, art: str, wert) -> dict:
        if self.cfg.structured == "structured_outputs":
            return {"structured_outputs": {art: wert}}
        return {f"guided_{art}": wert}

    async def _post(self, url: str, body: dict, timeout: float) -> dict:
        try:
            r = await self._http.post(f"{url}/chat/completions", json=body, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ModellFehler(f"Modellaufruf fehlgeschlagen: {type(e).__name__}") from e

    async def json(self, system: str, nutzer: str, schema: dict, max_tokens: int = 1500) -> dict:
        body = {
            "model": self.cfg.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": nutzer}],
            "temperature": 0, "seed": 42, "max_tokens": max_tokens,
            **self._struct("json", schema),
        }
        antwort = await self._post(self.cfg.llm_url, body, self.cfg.llm_timeout)
        try:
            inhalt = antwort["choices"][0]["message"]["content"]
            return json.loads(inhalt)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise ModellFehler("Antwort ist kein gültiges JSON") from e

    async def waehle(self, system: str, nutzer: str, optionen: list[str]) -> dict[str, float]:
        """Optionen als Buchstaben, genau ein Token, Wahrscheinlichkeiten aus den Logprobs."""
        if not 2 <= len(optionen) <= 20:
            raise ValueError("2 bis 20 Optionen")
        bs = list(BUCHSTABEN[: len(optionen)])
        liste = "\n".join(f"{b}) {o}" for b, o in zip(bs, optionen))
        body = {
            "model": self.cfg.decision_model,
            "messages": [{"role": "system", "content": system + "\nAntworte nur mit einem Buchstaben."},
                         {"role": "user", "content": f"{nutzer}\n\nOptionen:\n{liste}\n\nAntwort:"}],
            "temperature": 0, "max_tokens": 1, "logprobs": True, "top_logprobs": len(bs),
            **self._struct("choice", bs),
        }
        antwort = await self._post(self.cfg.decision_url, body, self.cfg.decision_timeout)
        try:
            top = antwort["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        except (KeyError, IndexError, TypeError) as e:
            raise ModellFehler("Antwort ohne Logprobs") from e
        lp = {b: -math.inf for b in bs}
        for t in top:
            tok = str(t.get("token", "")).strip().upper()
            if tok in lp:
                lp[tok] = max(lp[tok], float(t["logprob"]))
        if all(v == -math.inf for v in lp.values()):
            raise ModellFehler("keine Option in den Logprobs")
        temp = self.cfg.decision_temperatur
        m = max(lp.values())
        ex = {b: math.exp((v - m) / temp) if v > -math.inf else 0.0 for b, v in lp.items()}
        summe = sum(ex.values())
        return {o: ex[b] / summe for b, o in zip(bs, optionen)}
