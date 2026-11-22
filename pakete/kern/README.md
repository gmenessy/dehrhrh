# kern

Gemeinsame Bibliothek für Runner, Taschenmesser und RAG (Implementierung 4). Wird nur erweitert, nie dupliziert.

## Module

| Modul | Inhalt |
| --- | --- |
| `kern.config` | `lade(cls)` liest eine `@dataclass(frozen=True)` aus der Umgebung (Feldname in Großbuchstaben). Typen `str`, `int`, `float`, `bool` (`true`/`false`/`1`/`0`), `Path`, `list[str]` (kommagetrennt). Felder auf `_datei` erhalten den Inhalt der Datei. Fehlende oder ungültige Werte: `SystemExit` mit Variablennamen, ohne Werte. |
| `kern.fehler` | `Fehlercode` (Implementierung 6, mit `status`), `PlattformFehler`, `fehler_antwort`, `registriere_handler`, `FehlerMiddleware` (unerwartete Ausnahmen → `INTERN` ohne Details, ADR-106) |
| `kern.antwort` | `Meta`, `Fundstelle`, `Antwort[T]`, ULID (`neue_ulid`, `ist_ulid`), Kontext (`kontext_setzen`, `kontext`, `anfrage_id`), `AnfrageIdMiddleware` |
| `kern.log` | `richte_ein(dienst)`: JSON auf stdout, nur Felder der Allowlist, Kontext automatisch, `ereignis` nur als Bezeichner (ADR-106) |
| `kern.metrik` | eigene `REGISTRY`, Standardmetriken, `MetrikMiddleware` (Route als Muster), `registriere_metriken` |
| `kern.trace` | `richte_ein(dienst)` (OTLP/HTTP nur mit Endpunkt), `span(name, **attribute)` mit Allowlist |
| `kern.kanonisch` | `kanonisch(obj) -> bytes`, `sha256_hex`, `sha256_praefix` |
| `kern.server` | `TlsEinstellung`, `baue`/`starte(app_pfad, port, *, tls=None)` mit Granian (ASGI, HTTP/1.1 und HTTP/2); `prod` ohne TLS bricht ab; mit `ca` werden Client-Zertifikate verlangt |
| `kern.app` | `neue_app(dienst, version, *, bereit=None) -> FastAPI` |

## `neue_app`

```python
from kern.app import neue_app

app = neue_app("runner", "0.1.0", bereit=pruefe_datenbank)  # bereit: async () -> bool
```

Middleware von außen nach innen: Anfrage-ID → Metriken → Sicherheitsheader → Fehler → Router.

| Endpunkt | Zugang | Antwort |
| --- | --- | --- |
| `GET /health` | ohne | `{"status": "ok"}` |
| `GET /ready` | ohne | `200 {"status": "bereit"}` oder `503 {"status": "nicht_bereit"}` |
| `GET /metrics` | `Authorization: Bearer <Scrape-Token>` | Prometheus-Text; sonst `401 NICHT_ANGEMELDET` |
| `GET /docs`, `GET /openapi.json` | ohne | nur außerhalb von `prod` |

Jede Antwort trägt `X-Anfrage-Id` (eine gültige eingehende ULID wird übernommen) und `X-Content-Type-Options: nosniff`. Den `Server`-Header von Granian entfernt der Eingang (ADR-105).

## Variablen

| Variable | Bedeutung |
| --- | --- |
| `UMGEBUNG` | Pflicht: `dev`, `test` oder `prod` |
| `METRIK_TOKEN_DATEI` | Datei mit dem Scrape-Token; ohne Datei ist `/metrics` immer gesperrt |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP/HTTP-Endpunkt; leer = kein Export |
