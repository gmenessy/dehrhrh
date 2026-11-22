"""JSON-Logging ohne Inhalte: nur Felder der Allowlist (Implementierung 4.1)."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from kern.antwort import kontext
from kern.metrik import LOG_FELD_VERWORFEN

ERLAUBT = frozenset(
    {
        "zeit", "stufe", "dienst", "ereignis", "anfrage_id", "trace_id", "mandant", "paket", "version", "lauf_id",
        "schritt_id", "werkzeug", "modell", "modell_digest", "dauer_ms", "status", "fehler_code", "grund",
        "tokens_ein", "tokens_aus", "anzahl", "groesse_bytes",
    }
)  # fmt: skip

# Ereignisse sind Bezeichner; Freitext koennte Inhalte tragen
_EREIGNIS = re.compile(r"[a-z0-9_.]{1,64}")
EREIGNIS_UNGUELTIG = "ereignis_ungueltig"

# Attribute jedes LogRecord; alles darueber hinaus kam ueber `extra`
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, dienst: str) -> None:
        super().__init__()
        self.dienst = dienst

    def format(self, record: logging.LogRecord) -> str:
        # Nachricht ist das Ereignis; Argumente werden nie eingesetzt
        zeile: dict[str, Any] = {
            "zeit": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "stufe": record.levelname,
            "dienst": self.dienst,
            "ereignis": record.msg
            if isinstance(record.msg, str) and _EREIGNIS.fullmatch(record.msg)
            else EREIGNIS_UNGUELTIG,
        }
        zeile.update(kontext())
        if record.exc_info and record.exc_info[0] is not None:
            zeile["fehler_code"] = record.exc_info[0].__name__
        for feld, wert in vars(record).items():
            if feld in _STANDARD:
                continue
            if feld in ERLAUBT:
                zeile[feld] = wert
            else:
                LOG_FELD_VERWORFEN.labels(feld=feld).inc()
        return json.dumps(zeile, ensure_ascii=False, default=str)


class _StdoutHandler(logging.Handler):
    """Schreibt auf das jeweils aktuelle `sys.stdout`."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stdout.write(self.format(record) + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001, S110 - handleError wuerde Nachricht und Argumente nach stderr schreiben
            pass


def richte_ein(dienst: str, stufe: int = logging.INFO) -> logging.Logger:
    """Ersetzt alle Handler der Wurzel durch den JSON-Handler; gilt auch fuer fremde Logger."""
    wurzel = logging.getLogger()
    for handler in list(wurzel.handlers):
        wurzel.removeHandler(handler)
    handler = _StdoutHandler()
    handler.setFormatter(JsonFormatter(dienst))
    wurzel.addHandler(handler)
    wurzel.setLevel(stufe)
    for name in list(logging.root.manager.loggerDict):
        fremd = logging.getLogger(name)
        fremd.handlers.clear()
        fremd.propagate = True
    return logging.getLogger(dienst)
