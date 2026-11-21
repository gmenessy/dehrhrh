# S02 – Kernbibliothek

**Ziel:** Das Paket `kern` mit Konfiguration, Fehlermodell, Antwortformat, inhaltsfreiem Logging, Metriken, Tracing, kanonischem JSON und Serverstart, sodass jeder Dienst sie ohne eigene Varianten nutzt.

**Voraussetzungen:** S01

**Kontext lesen:** Implementierung 3, 4, 6, 7 · Architektur 2 (Leitregel 8), 9.2

## Aufgaben

1. `kern.config`: `lade(cls)` für `@dataclass(frozen=True)`-Klassen; Typen `str`, `int`, `float`, `bool`, `Path`, `list[str]` (kommagetrennt); Felder mit Endung `_datei` lesen den Inhalt der Datei (für Geheimnisse); fehlende Pflichtwerte beenden den Prozess mit einer klaren Meldung **ohne** Werte auszugeben.
2. `kern.fehler`: `PlattformFehler` mit `code`, `nachricht`, `grund`, `status`, `fail_closed`, `retry_after`; alle Codes aus Implementierung 6 als `Enum` mit Standard-HTTP-Status; FastAPI-Exception-Handler für `PlattformFehler`, `RequestValidationError` (→ `EINGABE_UNGUELTIG`, Feldpfade ohne Werte) und unerwartete Ausnahmen (→ `INTERN`, ohne Details).
3. `kern.antwort`: `Meta` (Felder aus Architektur 9.2, alle optional außer `anfrage_id`), `Antwort[T]` generisch; Middleware, die `X-Anfrage-Id` übernimmt (nur ULID-Format, sonst neu) oder erzeugt und in Antwortheader und Kontext setzt. ULID ohne Fremdpaket.
4. `kern.log`: JSON-Logger auf stdout, Feld-Allowlist aus Implementierung 4.1, Kontextvariablen (`anfrage_id`, `mandant`, `lauf_id`) automatisch; nicht erlaubte Felder werden still entfernt und über die Metrik `plattform_log_feld_verworfen_total{feld}` gezählt. Kein Logging von Request-Bodies durch Middleware.
5. `kern.metrik`: eigene `CollectorRegistry`, Standardmetriken (Implementierung 4), Middleware für Anfragen und Dauer (Labels: `dienst`, `route` als Muster, `status`), Route `/metrics` mit Bearer-Token aus `METRIK_TOKEN_DATEI`, Vergleich mit `hmac.compare_digest`.
6. `kern.trace`: OTel-Setup mit OTLP/HTTP, wenn `OTEL_EXPORTER_OTLP_ENDPOINT` gesetzt, sonst ohne Export; Hilfsfunktion `span(name, **attribute)` mit Allowlist für Attributnamen (`plattform.*`, `gen_ai.request.model`, `gen_ai.usage.*`, `http.*`).
7. `kern.kanonisch`: `kanonisch(obj) -> bytes` (JSON, sortierte Schlüssel, `ensure_ascii=False`, ohne Leerraum, UTF-8; Floats nur endlich, sonst Fehler), `sha256_hex`, `sha256_praefix` (`"sha256:<hex>"`).
8. `kern.server`: `starte(app_pfad, port, *, tls: TlsEinstellung | None)` startet Granian (`interface asgi`, `http auto`); in `dev` ohne TLS. Kommandozeile je Dienst kommt in dessen Session.
9. `kern.app`: `neue_app(dienst: str, version: str) -> FastAPI` bündelt Fehler-Handler, Anfrage-ID, Logging, Metriken, `/health` und ein überschreibbares `/ready`; ohne OpenAPI-Docs-UI in `prod`.

## Akzeptanzkriterien

- [ ] Test: Ein Log-Aufruf mit Feldern `text`, `prompt`, `inhalt`, `token` erzeugt eine Zeile ohne diese Felder; erlaubte Felder bleiben.
- [ ] Test: `kanonisch` liefert für gleich bedeutende Objekte mit unterschiedlicher Schlüsselreihenfolge identische Bytes; Testvektoren mit Umlauten und Zahlen liegen in `pakete/kern/tests/vektoren/kanonisch.json`.
- [ ] Test: Fehlende Pflichtvariable beendet `lade` mit `SystemExit` und nennt den Variablennamen, aber keinen Wert.
- [ ] Test: Eine Route, die `PlattformFehler(GATE_VERSTOSS, grund="VAL-004")` wirft, liefert 422 im Format aus Architektur 9.2 mit `fail_closed: true` und `meta.anfrage_id`.
- [ ] Test: Eine unerwartete Ausnahme liefert 500 `INTERN` ohne Stacktrace oder Ausnahmetext.
- [ ] Test: `/metrics` ohne Token 401, mit falschem Token 401, mit richtigem 200.
- [ ] Test: übergebene gültige `X-Anfrage-Id` kommt zurück; ungültige wird ersetzt.
- [ ] Abdeckung `kern` ≥ 85 %; `mypy --strict` grün.

## Nicht in dieser Session

`kern.auth` (S03), `kern.http` mit mTLS (Grundfunktion hier optional, mTLS in S20), `kern.clamd` und `kern.text` (S04), `kern.s3` (S09).

## Prüfung

```
make check
```
