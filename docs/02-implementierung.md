# Implementierungs-Spezifikation: Use-Case-Plattform (MVP)

Version 1.0 · Stand 2026-11-21 · gilt für alle Sessions S01 bis S20

Dieses Dokument legt fest, **wie** programmiert wird: Technik, Struktur, Konventionen, Tests, Sicherheit im Code. Was gebaut wird, steht in `01-architektur.md`. Bei Widerspruch gilt die Architektur; die Abweichung wird als ADR in `docs/entscheidungen.md` festgehalten.

---

## 1. Technikstapel

| Bereich | Festlegung |
| --- | --- |
| Sprache | Python 3.13 |
| Paket- und Umgebungsverwaltung | `uv` mit Workspace, ein `uv.lock` im Wurzelverzeichnis |
| Web | FastAPI, Pydantic v2 |
| ASGI-Server | Granian (`--interface asgi --http auto`), HTTP/1.1 und HTTP/2 |
| HTTP-Client | `httpx` mit `http2=True`, ein Pool je Zieldienst |
| Datenbank | PostgreSQL 17, `psycopg` 3 (async, Pool), kein ORM |
| Objektspeicher | S3-Basis-API über `boto3` (in `asyncio.to_thread`) |
| Kryptographie | `cryptography` (Ed25519, AES-256-GCM, HKDF) |
| JWT | `PyJWT[crypto]` |
| YAML / Schema | `PyYAML` (nur `safe_load`), `jsonschema` |
| MCP | offizielles `mcp`-SDK, Transport Streamable HTTP |
| Vektorindex | `qdrant-client` mit `prefer_grpc=True` |
| Beobachtung | `opentelemetry-sdk`, OTLP-Exporter, `prometheus-client` |
| Tests | `pytest`, `pytest-asyncio`, `coverage`; Fuzzing in S20 mit `schemathesis` |
| Qualität | `ruff` (Lint und Format), `mypy --strict` für `kern`, `runner.paket`, `runner.gates`, `runner.zonen` |
| Lieferkette | `pip-audit`, `cyclonedx-py` (SBOM) |
| Container | Docker, Compose v2; Basis `python:3.13-slim`, Benutzer-ID 10001 |
| Java (nur Beispiel-Client) | aktuelle LTS, derzeit Java 25, `java.net.http.HttpClient` |

### 1.1 Erlaubte Laufzeitabhängigkeiten

Nur diese Pakete dürfen in `dependencies` stehen. Jede weitere braucht ein ADR.

```
fastapi, pydantic, granian, httpx[http2], python-multipart,
psycopg[binary,pool], boto3, cryptography, PyJWT[crypto], PyYAML, jsonschema,
mcp, qdrant-client, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-http,
prometheus-client,
# nur Taschenmesser (Bestand):
pypdfium2, pillow, olefile
```

Versionen werden beim ersten `uv lock` in S01 gepinnt; Updates nur gesammelt mit `pip-audit`-Lauf.

---

## 2. Repository-Struktur

```
/
├── CLAUDE.md                     Arbeitsregeln für Claude Code
├── README.md
├── Makefile
├── pyproject.toml                uv-Workspace
├── uv.lock
├── docs/
│   ├── 01-architektur.md
│   ├── 02-implementierung.md
│   ├── entscheidungen.md         ADRs ab ADR-100 (Abweichungen während der Umsetzung)
│   └── fortschritt.md            Stand je Session
├── sessions/S01-….md … S20-….md
├── pakete/
│   └── kern/                     gemeinsame Bibliothek (Python-Paket `kern`)
├── dienste/
│   ├── runner/                   Python-Paket `runner` (API, Zusteller, CLI)
│   ├── taschenmesser/            Python-Paket `taschenmesser` (aus vorhandenes/ übernommen)
│   └── rag/                      Python-Paket `rag` (API, Worker)
├── werkzeuge/
│   ├── modell-attrappe/          OpenAI-kompatibler Testserver
│   ├── dms-attrappe/             Ziel für reversible_write
│   └── java-client/              Beispiel-Client Java 25 (S19)
├── contracts/
│   ├── paket.schema.json
│   ├── register.schema.json
│   ├── decision/*.yaml           Decision-Verträge
│   └── openapi/                  erzeugte OpenAPI-Dateien
├── registry/tools.yaml
├── usecases/<id>/                Pilotpakete
├── config/
│   ├── mandanten.yaml
│   ├── ziele.dev.yaml            symbolisches Ziel → Adresse
│   └── schluessel/               nur Dev-Schlüssel, in .gitignore außer *.pub.dev
├── deploy/
│   ├── compose.yaml
│   ├── compose.dev.yaml          Keycloak, MinIO, Attrappen
│   ├── caddy/Caddyfile
│   ├── keycloak/realm-dev.json
│   └── pki/                      Skript für Dev-CA und Dienstzertifikate
├── sql/                          Migrationen 0001_….sql
└── tests/
    ├── e2e/                      gegen Compose
    ├── netz/                     Netzmatrix
    └── sicherheit/               Injection-Korpus, Fuzzing
```

Jeder Dienst hat `src/<paket>/`, `tests/`, `pyproject.toml`, `Dockerfile`.

---

## 3. Konventionen

### 3.1 Sprache und Namen

- Fachbegriffe deutsch, wie im Bestand des Taschenmessers: `lauf`, `schritt`, `freigabe`, `mandant`, `paket`.
- Bezeichner nur ASCII: `ae`, `oe`, `ue`, `ss` (`pruefen`, `schwaerzen`, `zusteller`).
- Allgemeine Technikbegriffe englisch, wenn das deutsche Wort unüblich ist (`client`, `handler`, `pool`).
- Kommentare und Docstrings deutsch, kurz, nur wo der Code nicht für sich spricht.
- API-Felder `snake_case`, deutsch.

### 3.2 Code

- Vollständige Typannotationen; `from __future__ import annotations` nicht nötig (3.13).
- I/O ausschließlich `async`. CPU-lastige Arbeit (PDF, Krypto über große Daten) in `asyncio.to_thread`.
- Pydantic-Modelle für jede API-Ein- und Ausgabe, `model_config = ConfigDict(extra="forbid")`.
- Keine globalen veränderlichen Zustände außer in `lifespan` erzeugten, an `app.state` gebundenen Objekten.
- Funktionen höchstens rund 50 Zeilen, Module höchstens rund 400 Zeilen; sonst teilen.
- Keine Platzhalter, kein `TODO` ohne Eintrag in `docs/fortschritt.md` unter „Offene Punkte“.

### 3.3 Konfiguration

- Nur über Umgebungsvariablen; Geheimnisse nur als Datei (`*_DATEI`), nie als Variable mit Wert.
- `kern.config.lade(Klasse)` liest eine `@dataclass(frozen=True)` aus der Umgebung, prüft Pflichtfelder, bricht beim Start ab (fail-closed).

| Variable | Dienst | Bedeutung |
| --- | --- | --- |
| `UMGEBUNG` | alle | `dev`, `test`, `prod` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` | alle | Tokenprüfung |
| `OIDC_TOKEN_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET_DATEI` | runner, zusteller | Token Exchange, Dienst-Tokens |
| `DATABASE_URL_DATEI` | runner, zusteller, rag | Verbindungszeichenkette als Datei |
| `S3_ENDPOINT`, `S3_ZUGANG_DATEI` | runner, zusteller, rag-worker | S3 |
| `MODELL_LLM_URL`, `MODELL_LLM_NAME`, `MODELL_LLM_DIGEST` | runner, rag | LLM |
| `MODELL_DECISION_URL`, `MODELL_DECISION_NAME`, `MODELL_DECISION_DIGEST` | runner, rag | Decision Model |
| `EMBEDDING_URL`, `EMBEDDING_DIGEST` | rag | BGE-M3 |
| `TM_URL` | runner, rag-worker | Taschenmesser |
| `SANDBOX_URL` | taschenmesser | Parse-Sandbox |
| `CLAMD_ADRESSE` | taschenmesser | `host:port` |
| `RUNNER_MODELL_PROXY_URL` | taschenmesser | Modell-Proxy |
| `REGISTER_DATEI`, `ZIELE_DATEI`, `PAKETE_DIR`, `MANDANTEN_DATEI` | runner | Register, Ziele, Pakete, Mandanten |
| `SIGNATUR_SCHLUESSEL_DIR` | runner | öffentliche Ed25519-Schlüssel `<key_id>.pub` |
| `KLARTEXT_SCHLUESSEL_DIR` | runner, zusteller | Hauptschlüssel je Mandant `<mandant>.key` (32 Byte) |
| `TLS_ZERT`, `TLS_SCHLUESSEL`, `TLS_CA` | alle | mTLS |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | alle | Traces |
| `METRIK_TOKEN_DATEI` | alle | Scrape-Token für `/metrics` |

---

## 4. Kernbibliothek `kern`

Gemeinsam genutzt von Runner, Taschenmesser und RAG. Entsteht in S02, wird nur erweitert, nie dupliziert.

| Modul | Inhalt |
| --- | --- |
| `kern.config` | `lade()`, Dateigeheimnisse |
| `kern.fehler` | `PlattformFehler(code, nachricht, grund=None, status=…, fail_closed=True)`, FastAPI-Handler, Fehlercodes (Abschnitt 6) |
| `kern.antwort` | `Meta`, `Antwort[T]`, Anfrage-ID (ULID) |
| `kern.log` | JSON-Logging mit Feld-Allowlist; unbekannte Felder werden verworfen, nicht gekürzt |
| `kern.metrik` | Registry, Standardmetriken (`plattform_anfragen_total`, `plattform_dauer_sekunden`, `plattform_modell_tokens_total`, `plattform_gate_verstoss_total`), `/metrics` mit Token |
| `kern.trace` | OTel-Setup, Attribute `plattform.*`, `gen_ai.*` ohne Inhalte |
| `kern.auth` | JWT-Prüfung, JWKS-Cache, `Identitaet(sub, mandant, scopes, act)`, FastAPI-Abhängigkeit `braucht(scope)` |
| `kern.http` | `client_fuer(ziel)` mit HTTP/2, Timeouts, mTLS, Wiederholung nur für idempotente Methoden |
| `kern.kanonisch` | kanonisches JSON, `sha256_hex()`, `sha256_praefix()` |
| `kern.server` | Granian-Start mit TLS-Einstellungen |
| `kern.clamd` | Virenprüfung über das clamd-Protokoll (`INSTREAM`), fail-closed (ab S04) |
| `kern.text` | Normalisierung für Zitatprüfung: NFKC, Leerraum, Silbentrennung, weiche Trennzeichen (ab S04, aus dem Taschenmesser übernommen) |
| `kern.s3` | S3-Basis-API, Präfix je Mandant, presigned GET (ab S09) |

### 4.1 Log-Allowlist

Erlaubte Felder: `zeit`, `stufe`, `dienst`, `ereignis`, `anfrage_id`, `trace_id`, `mandant`, `paket`, `version`, `lauf_id`, `schritt_id`, `werkzeug`, `modell`, `modell_digest`, `dauer_ms`, `status`, `fehler_code`, `grund`, `tokens_ein`, `tokens_aus`, `anzahl`, `groesse_bytes`. Alles andere wird entfernt. Ein Test (S02) prüft, dass ein Feld `text` oder `prompt` nie ausgegeben wird.

---

## 5. Datenbank

- Migrationen in `sql/NNNN_name.sql`, vorwärts, idempotent wo möglich; Migrator `runner.laufzeit.migration` führt sie in einer Transaktion je Datei aus und vermerkt sie in `plattform.migrationen`.
- Rollen: `plattform_migration` (Eigentümer), `plattform_runner`, `plattform_zusteller`, `plattform_rag`. Keine Anwendung nutzt die Eigentümerrolle.
- Jede Transaktion mit Mandantenbezug beginnt mit `SET LOCAL app.tenant_id = $1`. Hilfsfunktion `laufzeit.db.transaktion(mandant)`.
- Jobs: `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` mit Statuswechsel in derselben Transaktion.
- Zeitangaben `timestamptz`, UTC.

---

## 6. Fehlercodes

| Code | HTTP | Bedeutung |
| --- | --- | --- |
| `NICHT_ANGEMELDET` | 401 | Token fehlt oder ungültig |
| `KEIN_RECHT` | 403 | Scope oder Mandant passt nicht |
| `NICHT_GEFUNDEN` | 404 | Paket, Lauf, Freigabe unbekannt |
| `EINGABE_UNGUELTIG` | 422 | Schema verletzt |
| `ZU_GROSS` | 413 | Datei oder Anfrage zu groß |
| `VIRUS` | 422 | Virenfund |
| `VIRENPRUEFUNG_FEHLT` | 503 | clamd nicht erreichbar (fail-closed) |
| `KONTINGENT` | 429 | Quote erschöpft, mit `Retry-After` |
| `PAKET_UNGUELTIG` | 422 | Linter, Schema, Signatur |
| `SIGNATUR_UNGUELTIG` | 422 | Ed25519 oder Hashes stimmen nicht |
| `REGISTER_ABWEICHUNG` | 503 | `beschreibung_hash` weicht ab, Werkzeug gesperrt |
| `LAUFBEDINGUNG` | 403 | KI-VO-Einstufung fehlt außerhalb R0 |
| `BUDGET` | 422 | Schritt-, Token- oder Zeitbudget überschritten |
| `GATE_VERSTOSS` | 422 | VAL-Regel verletzt, `grund` = VAL-ID |
| `AN_MENSCH` | 200 | Ergebnis unsicher, Vorgang an Menschen übergeben (kein Fehler, Status im Ergebnis) |
| `ZONENVERLETZUNG` | 500 | Klartext würde Zone A verlassen; Abbruch, Alarm |
| `FREIGABE_ABGELAUFEN` | 409 | Freigabe älter als 24 h |
| `HASH_ABWEICHUNG` | 409 | `klartext_hash` passt nicht |
| `ZIEL_FEHLER` | 502 | Zielsystem antwortet mit Fehler |
| `MODELL_FEHLER` | 503 | Modell nicht erreichbar oder Digest falsch |
| `INTERN` | 500 | unerwarteter Fehler, ohne Details nach außen |

---

## 7. HTTP und Transport

- Server: `kern.server.starte(app, port)` startet Granian mit `--http auto`; in `prod` TLS mit Client-Zertifikatsprüfung. Kann Granian die Client-Zertifikatsprüfung nicht, terminiert ein Caddy-Sidecar mTLS (Entscheidung in S20 als ADR).
- Clients: Verbindungs-Timeout 2 s, Lese-Timeout je Ziel aus `ziele.yaml` (Standard 30 s, Modelle 120 s). Wiederholung höchstens zweimal, nur `GET` und Aufrufe mit `Idempotency-Key`.
- Jeder ausgehende Aufruf trägt `traceparent` und `X-Anfrage-Id`, nie Inhalte im Header.
- Antworten ohne `Server`-Header; Sicherheitsheader (`X-Content-Type-Options`, `Content-Security-Policy` für die Freigabeliste, `Cache-Control: no-store` für Antworten mit Klartext).

---

## 8. Sicherheitsregeln im Code

Verboten: `eval`, `exec`, `pickle`, `yaml.load` ohne `SafeLoader`, `subprocess` mit `shell=True`, `os.system`, dynamische Imports aus Paketdaten, Pfade aus Eingaben ohne Normalisierung und Präfixprüfung, URLs aus Eingaben ohne Allowlist, Vergleich von Geheimnissen ohne `hmac.compare_digest`, Logging von Request-Bodies.

Pflicht:
- Eingabegrößen begrenzen, bevor gelesen wird (Datei 20 MB, Anfrage 25 MB, 100 Seiten).
- Zeitgrenzen für jede externe Operation.
- Fehler nach außen nur mit Code und kurzer Nachricht; Details nur im Log (inhaltsfrei).
- Neue Endpunkte brauchen eine `braucht(scope)`-Abhängigkeit; `kern.auth.pruefe_routen` (S03) prüft in jedem Dienst, dass jede Route außer `/health`, `/ready` und `/metrics` eine hat.

`ruff`-Regeln: `S` (bandit), `B`, `E`, `F`, `I`, `UP`, `ASYNC`, `PL` (Auswahl). `S`-Ausnahmen nur in Tests.

---

## 9. Tests

| Ebene | Ort | Läuft mit | Inhalt |
| --- | --- | --- | --- |
| Unit | `dienste/*/tests/unit`, `pakete/kern/tests` | `make test` | ohne Netz, ohne Docker |
| Integration | `dienste/*/tests/integration` | `make test-int` | gegen Compose-Dienste (Postgres, MinIO, Attrappen) |
| E2E | `tests/e2e` | `make e2e` | volle Compose-Umgebung, Pilotpakete |
| Sicherheit | `tests/sicherheit` | `make sicherheit` | Injection-Korpus, Fuzzing, Netzmatrix |

- Kein Test ruft ein echtes Modell. Modellverhalten kommt aus Drehbüchern der `modell-attrappe`.
- Jede Session liefert Tests für ihre Akzeptanzkriterien; bestehende Tests dürfen nur bei nachgewiesenem Fehler im Test geändert werden.
- Abdeckung: mindestens 85 % in `kern`, `runner.paket`, `runner.gates`, `runner.zonen`; sonst mindestens 70 %.

### 9.1 Modell-Attrappe

FastAPI-Dienst `werkzeuge/modell-attrappe`, Endpunkte `/v1/models`, `/v1/chat/completions`, `/v1/embeddings`. Antworten aus Drehbüchern `drehbuecher/*.yaml`:

```yaml
- wenn: { modell: gemma-4-31b, enthaelt: "Ordne den redigierten Posteingang" }
  antwort:
    tool_calls:
      - { name: taschenmesser.posteingang, argumente: { } }
- wenn: { modell: gemma-4-31b, schritt: 2, enthaelt: "Ordne" }
  antwort: { json: { amt: "Ordnungsamt", aktenplan: "32.10", begruendung: "[PERSON_1] meldet …" } }
- wenn: { modell: clef-flash, auswahl: [ja, nein], enthaelt: "relevant" }
  antwort: { wahl: ja, logprob: -0.05 }
- standard: { fehler: 500 }
```

Regeln: erste passende gewinnt; `standard` gilt sonst; jede Anfrage wird unter `/_protokoll` abrufbar gespeichert (für Tests, die prüfen, dass kein Klartext ankam). `/v1/models` liefert den konfigurierten Namen und ein Feld `digest`. Embeddings: deterministisch aus SHA-256 des Textes (dense 1024 Dimensionen normiert, sparse aus Token-Hashes).

---

## 10. Make-Ziele

| Ziel | Wirkung |
| --- | --- |
| `make setup` | `uv sync`, Dev-PKI, Dev-Schlüssel |
| `make lint` | `ruff check`, `ruff format --check` |
| `make typ` | `mypy` für die strikten Pakete |
| `make test` | Unit-Tests aller Pakete |
| `make check` | `lint` + `typ` + `test` (Pflicht vor jedem Commit) |
| `make up` / `make down` | Compose mit Dev-Profil |
| `make test-int` | Integrationstests (startet benötigte Dienste) |
| `make e2e` | E2E-Tests |
| `make sicherheit` | Sicherheitstests |
| `make sbom` | SBOM je Image, `pip-audit` |
| `make pakete` | alle `usecases/*` linten, evaluieren, signieren (Dev-Schlüssel) (ab S11) |
| `make goldset-schwaerzen` | Recall und Präzision des Schwärzens je Kategorie (ab S04) |
| `make openapi` | OpenAPI-Dateien nach `contracts/openapi/` (ab S09) |
| `make rag-eval` | Recall@30 und Enthaltung je Wissensraum (ab S17) |
| `make java-beispiel` | Java-Client in `eclipse-temurin:25-jdk` gegen Compose (ab S19) |

---

## 11. Arbeitsweise je Session

1. `docs/fortschritt.md` und die Session-Datei lesen; Voraussetzungen prüfen (`make check` muss vorher grün sein).
2. Branch `session/Sxx-kurzname`.
3. Umsetzen in der Reihenfolge der Aufgaben; Tests zuerst für die Akzeptanzkriterien.
4. Abweichung von Architektur oder dieser Spezifikation: entscheiden, als ADR in `docs/entscheidungen.md` eintragen (Nummer ab ADR-100, Kontext, Entscheidung, Folgen), weitermachen.
5. Ende: `make check` grün, geforderte Integrations- oder E2E-Tests grün, `docs/fortschritt.md` aktualisiert (Status, Abweichungen, offene Punkte), ein Commit `Sxx: <Ziel>` mit kurzer Liste der Änderungen.

**Definition of Done je Session:** alle Akzeptanzkriterien als Tests vorhanden und grün; keine neue Abhängigkeit außerhalb 1.1 ohne ADR; keine Sicherheitsregel aus Abschnitt 8 verletzt; keine Inhalte in Logs; Dokumentation der neuen Endpunkte und Konfiguration im README des Dienstes.
