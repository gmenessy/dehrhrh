# Architektur-Spezifikation: Use-Case-Plattform (MVP)

Version 1.0 · Stand 2026-11-21 · Quelle: Konzept „Spezifikation Use-Case-Plattform“ und Gesamtspezifikation „Verwaltungs-KI-Plattform & Use-Case-Fabrik“

Dieses Dokument legt fest, **was** gebaut wird und **wie die Teile zusammenhängen**. Wie programmiert wird, steht in `02-implementierung.md`. In welcher Reihenfolge, steht in `sessions/`.

---

## 1. Zweck und Umfang des MVP

Die Plattform macht aus vorhandenen Bausteinen mit wenig Aufwand viele Use Cases. Ein Use Case ist ein signiertes, deklaratives Paket (YAML, Schemas, Regeln, Goldset) ohne ausführbaren Code. Derselbe Use Case läuft

- **in der Anwendung**: synchron, nur lesend, liefert einen Vorschlag,
- **Ende-zu-Ende**: als Lauf vom Eingang bis zur Zustellung, mit Wartezuständen und menschlicher Freigabe.

**Der MVP umfasst** Stufe 1 und 2 des Konzepts sowie den Kern von Stufe 3, damit beide Nutzungsarten Ende-zu-Ende vorführbar sind:

| Enthalten | Nicht enthalten |
| --- | --- |
| Modi `skill`, `agent`, `kette`, `hybrid` | Reifegrad R2, Teilautomatik |
| Zonen, Schwärzen, Rückabbildung | Use-Case-Fabrik mit Agenten (nur CLI-Werkzeuge `uc`) |
| Werkzeugregister mit Hash-Pins | DBOS Transact, Kubernetes |
| Ed25519-Signierung und Laufbedingung | Decision-Serving als eigener Dienst |
| Ausgangs-Gate VAL-001 bis VAL-008 | zweite GPU, Reranker |
| Läufe, Wartezustände, Freigaben, Freigabeliste | `irreversible_write` (Versand an Bürger) |
| Outbox und Zusteller mit `reversible_write` gegen einen DMS-Adapter (Attrappe) | echte Fachverfahrens-Adapter |
| RAG-Service mit Qdrant | Standby-Maschine, Wiederanlauf |
| Zugänge REST, OpenAI-kompatibel, MCP | eigene Oberfläche für Fachbereiche |

**Pilotpakete im MVP:** `frist` (skill), `posteingang` (hybrid), `auskunft` (agent mit RAG), `gewerbe` (kette mit Nachreichung, Freigabe und Wirkung). Dazu das Testpaket `vollstaendigkeit` (lineare kette, S12).

---

## 2. Leitregeln (verbindlich für jede Zeile Code)

1. **Mach es einfach.** Keine Komponente ohne Use Case, der sie braucht.
2. **Konfiguration statt Code.** Pakete enthalten keinen Code. Fehlende Fähigkeiten werden Werkzeuge.
3. **Stufenregel.** Stufe 0 Regel, Stufe 1 Decision Model, Stufe 2 LLM, Stufe 3 Mensch. Die schwächste ausreichende Stufe gewinnt.
4. **Modelle sehen keinen Klartext.** Vor jedem Modellaufruf steht die redigierte Fassung. Dateien nur als Handles `dok:n`. Durchgesetzt im Typsystem (Abschnitt 7.2).
5. **Wirkung nur nach Freigabe** und nur über Outbox und Zusteller.
6. **Fail-closed.** Unsicherheit, Zeitüberschreitung, Signatur- oder Hashabweichung führen zum Menschen oder zur Ablehnung, nie zu einem Standardwert.
7. **Kein Umgehen des Runners.** Kein Client spricht direkt mit Modellen oder schreibenden Werkzeugen.
8. **Inhaltsfreie Protokolle.** Keine Prompts, Dokumente oder Ausgaben in Logs, Traces oder Metriken.

---

## 3. Systemkontext

```
 Fachverfahren (REST, Java ab LTS)   Chat-Oberflächen (OpenAI-API)   Agenten (MCP)   Sachbearbeitung (Freigabeliste)
                     \                         |                         |              /
                      +-------------------- Eingang (TLS, HTTP/2, Rate-Limit) ---------+
                                                   |
                                                Runner  ---- IdP (OIDC, JWKS, Token Exchange)
                                                   |
                         +-------------------------+--------------------------+
                         |                         |                          |
                  Zone A: Klartext          Zone B: redigiert           Zone C: Wirkung
              Taschenmesser, Sandbox,     Modellserver (vLLM),        Zusteller ──> DMS-Adapter
              PostgreSQL, S3, clamd       Embedding, RAG, Qdrant
```

Externe Systeme im Rechenzentrum: IdP (OIDC), S3-kompatibler Speicher, Prometheus, OpenTelemetry-Collector, SIEM, Secret-Store. Für Entwicklung und Tests ersetzt Compose sie durch Keycloak, MinIO und Attrappen.

---

## 4. Komponenten

### 4.1 Eigene Images (drei)

| Image | Prozesse | Aufgabe | Zone |
| --- | --- | --- | --- |
| `plattform/runner` | `runner` (API), `zusteller` (Worker) | Pakete laden und prüfen, Läufe ausführen, Gates, Zonenwechsel, Freigaben, Outbox, Audit; Zusteller führt freigegebene Wirkungen aus | A (runner), C (zusteller) |
| `plattform/taschenmesser` | `taschenmesser` (API), `sandbox` (Parse-Modus) | 25 zustandslose Dokumentwerkzeuge, Schwärzen; im Sandbox-Modus nur Parsen, ohne Netz | A |
| `plattform/rag` | `rag-api`, `rag-worker` | Wissensräume, Ingestion, hybride Suche, belegte Antwort | B |

### 4.2 Fremde Komponenten

| Komponente | Rolle | MVP-Betrieb |
| --- | --- | --- |
| vLLM `llm` | Gemma 4 31B, OpenAI-API, `guided_json`, Tool-Calling | GPU-Host; in Tests `modell-attrappe` |
| vLLM `decision` | Clef-flash, `guided_choice` mit Logprobs | GPU-Host; in Tests `modell-attrappe` |
| `embedding` | BGE-M3 dense + sparse, `/v1/embeddings` | CPU; in Tests deterministische Attrappe |
| Qdrant | Vektorindex, REST 6333 und gRPC 6334 | Container |
| PostgreSQL 17 | Läufe, Freigaben, Outbox, Audit, Klartext-Ablage | Container; im RZ vorhanden möglich |
| S3 | Dateien | RZ; in Dev MinIO |
| clamd | Virenprüfung | Container |
| IdP | OIDC, JWKS, Token Exchange RFC 8693 | RZ; in Dev Keycloak |
| Eingang | TLS, HTTP/2, Weiterleitung, Rate-Limit grob | RZ-Proxy; in Dev Caddy |

### 4.3 Eigene Hilfsprogramme (keine Dienste)

| Programm | Zweck |
| --- | --- |
| `uc` (CLI im Runner-Paket) | `uc neu`, `uc lint`, `uc plan`, `uc eval`, `uc sign`, `uc verify`, `uc upload` |
| `registry` (CLI im Runner-Paket) | `registry pin` (Hashes der Werkzeugbeschreibungen berechnen), `registry check` |
| `modell-attrappe` | OpenAI-kompatibler Testserver mit Drehbüchern |
| `dms-attrappe` | REST-Ziel für `reversible_write`, protokolliert Aufrufe, prüft Idempotenz |

---

## 5. Zonen, Netze, Ports

### 5.1 Zonen

| Zone | Inhalt | Regel |
| --- | --- | --- |
| A Klartext | Originaldokumente, Platzhaltertabellen, Rückabbildung | verlässt Zone A nur redigiert; Ablage verschlüsselt je Mandant |
| B redigiert | Modelle, Embedding, RAG | kein Weg nach außen, keine Zugangsdaten zu Fachverfahren |
| C Wirkung | Zusteller, Zielsysteme | einziger Ort mit schreibendem Zugriff nach außen; kein eingehender Pfad |

### 5.2 Docker-Netze (Compose)

| Netz | `internal` | Mitglieder |
| --- | --- | --- |
| `rand` | nein | eingang, runner |
| `idp` | ja | keycloak, runner, taschenmesser, rag-api, rag-worker, zusteller |
| `intern` | ja | runner, taschenmesser, postgres, minio, clamd, rag-worker, zusteller |
| `parse` | ja | taschenmesser, sandbox |
| `modell` | ja | runner, rag-api, rag-worker, llm, decision, embedding, qdrant |
| `aus_zusteller` | nein im Betrieb, ja in Dev | zusteller, dms-attrappe |
| `beobachtung` | ja | alle Dienste, otel-collector, prometheus (Dev-Profil) |

Die Sandbox hängt **nur** an `parse`. Der Zusteller hängt **nur** an `intern` (Datenbank, S3), `idp` (Dienst-Token) und `aus_zusteller`.

### 5.3 Ports

| Dienst | Port | Protokoll |
| --- | --- | --- |
| eingang | 443 | HTTPS, HTTP/1.1 und HTTP/2 (ALPN) |
| runner | 8000 | HTTP/2 + mTLS (h2c nur in Dev) |
| taschenmesser | 8080 | HTTP/2 + mTLS |
| sandbox | 8081 | HTTP/2 + mTLS |
| rag-api | 8090 | HTTP/2 + mTLS |
| llm / decision / embedding | 8100 / 8101 / 8102 | HTTP/1.1, OpenAI-API |
| qdrant | 6333 / 6334 | REST / gRPC |
| postgres | 5432 | TLS |
| minio | 9000 | HTTP(S), S3 |
| clamd | 3310 | clamd-Protokoll |
| keycloak (Dev) | 8443 | HTTPS |
| dms-attrappe | 8200 | HTTP |

Metriken liegen bei jedem eigenen Dienst unter `/metrics` auf dem Dienstport, erreichbar nur aus `beobachtung` (Prüfung per Quellnetz im Dienst: Pfad `/metrics` verlangt ein eigenes Scrape-Token).

### 5.4 Erlaubte Verbindungen (Netzmatrix)

| Von | Nach | Zweck |
| --- | --- | --- |
| eingang | runner | alle externen Aufrufe |
| runner | taschenmesser | Werkzeuge, Schwärzen |
| runner | llm, decision | Modellaufrufe (nur redigiert) |
| runner | rag-api | Wissen (REST oder MCP) |
| runner | postgres, minio | Zustand, Dateien |
| runner, taschenmesser, rag-api, rag-worker, zusteller | keycloak | JWKS, Dienst-Tokens, Token Exchange (nur runner) |
| taschenmesser | sandbox | Parsen unbekannter Dateien |
| taschenmesser | clamd | Virenprüfung |
| taschenmesser | runner (`/intern/v1/chat/completions`) | Modellaufrufe nur über den Modell-Proxy (siehe 7.3); direkt zu llm/decision verboten |
| rag-api | embedding, qdrant, llm, decision | Suche und Antwort |
| rag-worker | minio, taschenmesser, embedding, qdrant | Ingestion |
| zusteller | postgres, minio, dms-attrappe | Outbox abarbeiten |

Alles andere ist verboten. Die Matrix wird in S20 als Test geprüft (`tests/netz/test_matrix.py` versucht verbotene Verbindungen und erwartet Fehler).

---

## 6. Datenflüsse

### 6.1 In der Anwendung (synchron, `POST /api/v1/skills/{id}`)

1. Eingang terminiert TLS, leitet an Runner.
2. Runner `gateway`: JWT prüfen, Mandant aus Claim, Scope `uc.ausfuehren` und paketeigener Scope.
3. Paket aus Cache (beim Laden signaturgeprüft), Plan mit `plan_hash`.
4. Eingabe gegen Schema. Dateien: Virenprüfung, Ablage in S3 `laufdaten/<mandant>/<lauf>/`, ab dann Handle `dok:n`.
5. Schritt `schwaerzen`: Taschenmesser liefert redigierten Text und Platzhaltertabelle. Tabelle verschlüsselt in Zone A (PostgreSQL `klartext`).
6. Schritte nach Plan (Stufenregel). Modellaufrufe nur mit `Redigiert`-Werten.
7. Ausgangs-Gate mit den im Paket deklarierten VAL-Regeln.
8. Rückabbildung der Platzhalter, `klartext_hash`.
9. Antwort mit `ergebnis`, `fundstellen`, `meta`. Kein Lauf in der Datenbank außer Audit-Einträgen; Platzhaltertabelle wird nach Antwort gelöscht.

Wirkung: keine. Pakete mit Schritten der Wirkung `reversible_write` sind für diesen Endpunkt gesperrt.

### 6.2 Ende-zu-Ende (`POST /api/v1/processes/{id}/run?async=true`)

1. Wie 6.1 Schritte 1–4, dann Lauf in `laeufe` mit Status `ANGELEGT`, Antwort `202` mit `run_id`.
2. Ein Worker im Runner-Prozess holt den Lauf (`FOR UPDATE SKIP LOCKED`), führt Schritte aus, speichert jeden Schritt.
3. Knoten `freigabe`: Freigabe in `freigaben` mit `klartext_hash`, Lauf `WAITING_HUMAN`.
4. `POST /api/v1/approvals/{id}`: erteilen, korrigieren, ablehnen; Token Exchange liefert `act`.
5. Bei Erteilung: Lauf setzt fort. Knoten `wirkung`: Outbox-Eintrag mit redigierten Argumenten, Idempotenzschlüssel, `klartext_hash`.
6. Zusteller holt Eintrag, bildet Klartext neu, prüft Hash gegen Freigabe, ruft Ziel mit `Idempotency-Key`, schreibt Ergebnis.
7. Lauf `ABGESCHLOSSEN`. Ereignisse laufend über `GET /api/v1/runs/{id}/events` (SSE).

### 6.3 Wissensfrage

Runner ruft `wissen.suchen` oder `wissen.antworten` (Register, Transport REST oder MCP) mit festen Argumenten `mandant`, `raum`. RAG-Service: hybride Suche, Relevanzprüfung (Decision), Antwort (LLM) im Schema mit Fundstelle je Aussage, Zitatprüfung, sonst Enthaltung.

---

## 7. Interne Struktur der eigenen Dienste

### 7.1 Runner (Python-Paket `runner`)

| Modul | Verantwortung |
| --- | --- |
| `runner.api` | FastAPI-App, Routen, SSE, Fehlerabbildung |
| `runner.gateway` | JWT, Mandant, Scopes, Quoten, Token Exchange, Handles |
| `runner.register` | `registry/tools.yaml` laden, Hash-Pins prüfen, Transporte `rest`, `mcp`, `local` |
| `runner.paket` | Paketschema, Linter, Plan-Compiler, `plan_hash`, Signaturprüfung, Laufbedingung |
| `runner.engine` | deterministische Knoten, Pfadausdrücke, Modi `skill`, `kette`, `hybrid` |
| `runner.agent` | Tool-Calling-Schleife, Budgets, feste Argumente, Fremdtext-Sperre, Structured-Output-Rückfall |
| `runner.zonen` | Typen `Klartext`, `Redigiert`, Schwärzen, Rückabbildung, Verschlüsselung |
| `runner.modelle` | Modellclient (OpenAI-API), Prioritätsklassen, Digest-Vermerk, Decision-Verträge |
| `runner.gates` | VAL-001 bis VAL-008 |
| `runner.laufzeit` | PostgreSQL-Zugriff, Läufe, Wartezustände, Freigaben, Outbox, Audit-Kette, Löschjob |
| `runner.zusteller` | Outbox-Worker (eigener Prozess) |
| `runner.zugaenge` | OpenAI-kompatibler Endpunkt, MCP-Server |
| `runner.cli` | `uc`, `registry` |

Abhängigkeitsrichtung: `api` → `engine`/`agent` → `gates`, `modelle`, `register`, `zonen` → `laufzeit` → `kern`. Kein Modul importiert `api`.

### 7.2 Zonen im Typsystem

```python
class Klartext(str): ...          # darf runner.modelle nie erreichen
class Redigiert(str): ...         # einziger Texttyp, den runner.modelle annimmt
```

`runner.modelle.ModellClient.chat()` akzeptiert nur `Redigiert` und `Handle`. Ein Test (S10) prüft per AST, dass kein Code `Redigiert(...)` außerhalb von `runner.zonen` konstruiert.

### 7.3 Taschenmesser

Bestehender Dienst (Version 1.4.0, Ordner `vorhandenes/taschenmesser`). Änderungen im MVP:

- API-Schlüssel ersetzt durch JWT (Scopes `tm.<werkzeug>`), Mandant aus Claim.
- Modellaufrufe des Taschenmessers (`zusammenfassen`, `extrahieren`, `klassifizieren` …) gehen nicht direkt an vLLM, sondern an den **Modell-Proxy des Runners** (`POST /intern/v1/chat/completions`). Der Proxy erzwingt Prioritäten, Quoten und Audit. Modellnutzende Werkzeuge schwärzen intern vor jedem Modellaufruf und bilden das Ergebnis zurück (Bestand des Taschenmessers); der Runner erzwingt das über das feste Argument `schwaerzen=true` im Register.
- Neuer interner Endpunkt `POST /intern/v1/schwaerzen` (Scope `tm.zuordnung`, nur für den Runner): liefert redigierte Seiten **und** die Platzhalterzuordnung. Die Zuordnung ist Klartext und bleibt in Zone A (Runner, verschlüsselte Ablage).
- **Schwärzen läuft ohne LLM auf Klartext** (ADR-011): Regeln, Prüfziffern (IBAN, Steuer-ID), Wörterbücher (Vornamen, Straßen) und Muster (`SCHWAERZEN_NER=regel`). Das Decision Model darf nur Kandidatenfenster bewerten, die bereits regelbasiert vorredigiert sind. Der Recall wird im Goldset gemessen (Ziel 0,99); eine Abweichung von dieser Regel nur per ADR.
- Parse-Modus: derselbe Code mit `TM_MODUS=sandbox` stellt nur `/v1/text` bereit, ohne Netz, read-only, Zeit- und Speichergrenze.
- ASGI-Server Granian statt Uvicorn.

### 7.4 RAG-Service

| Teil | Verantwortung |
| --- | --- |
| `rag.api` | `POST /v1/suchen`, `POST /v1/antworten`, MCP-Werkzeuge `wissen.suchen`, `wissen.antworten`, `/v1/raeume` (Admin) |
| `rag.worker` | Ingestion-Jobs aus Tabelle `rag_jobs` (eigene Datenbank `rag`), Text über Taschenmesser, Abschnitte, Embedding, Upsert |
| `rag.index` | Qdrant über gRPC, Collections `wissen_<raum>_v<n>`, Alias `wissen_<raum>` |
| `rag.eval` | Goldset Recall@30, Enthaltung |

---

## 8. Datenmodell (PostgreSQL, Schema `plattform`)

Alle Tabellen mit Mandantenbezug haben `mandant text not null` und Zeilen-Sicherheit `mandant = current_setting('app.tenant_id')`. Die Anwendung setzt `SET LOCAL app.tenant_id` je Transaktion. Der Zusteller nutzt eine eigene Rolle mit Zugriff nur auf `outbox`, `freigaben` (lesen), `klartext` (lesen), `audit` (einfügen).

| Tabelle | Wichtige Spalten |
| --- | --- |
| `pakete` | `mandant`, `paket_id`, `version`, `reife` (`R0`,`R1`), `manifest jsonb`, `plan_hash`, `aktiv bool`, `geladen_am` |
| `laeufe` | `id uuid`, `mandant`, `paket_id`, `version`, `plan_hash`, `modus`, `status`, `eingabe_ref`, `ergebnis jsonb` (redigiert), `budget jsonb`, `fehler_code`, `erstellt`, `aktualisiert`, `abschluss` |
| `schritte` | `lauf_id`, `nr`, `schritt_id`, `typ`, `status`, `stufe`, `konfidenz`, `dauer_ms`, `ergebnis jsonb` (redigiert), `fehler_code` |
| `klartext` | `lauf_id`, `art` (`platzhalter`,`dokument`), `chiffrat bytea`, `nonce bytea`, `schluessel_id`, `loeschen_am` |
| `freigaben` | `id uuid`, `lauf_id`, `schritt_id`, `stufe`, `klartext_hash`, `status`, `grund`, `korrektur jsonb`, `person_sub`, `act jsonb`, `ablauf` |
| `outbox` | `id uuid`, `lauf_id`, `freigabe_id`, `werkzeug_id`, `argumente jsonb` (redigiert), `idempotenz_schluessel`, `klartext_hash`, `status`, `versuche`, `naechster_versuch`, `ergebnis jsonb` |
| `ereignisse` | `lauf_id`, `seq`, `zeit`, `typ`, `daten jsonb` (inhaltsfrei) – Quelle für SSE und `Last-Event-ID` |
| `nachreichungen` | `lauf_id`, `zufall`, `token_hash`, `ablauf`, `eingeloest_am` |
| `audit` | `mandant`, `seq bigint`, `zeit`, `ereignis`, `lauf_id`, `paket_id`, `plan_hash`, `modell_digest`, `daten jsonb` (inhaltsfrei), `vorher_hash`, `hash` |

Statuswerte `laeufe.status`: `ANGELEGT`, `LAEUFT`, `WAITING_HUMAN`, `WAITING_CITIZEN`, `AN_MENSCH`, `ABGESCHLOSSEN`, `ABGELEHNT`, `FEHLER`.
Statuswerte `outbox.status`: `NEU`, `IN_ARBEIT`, `ERLEDIGT`, `FEHLER`, `KOMPENSIERT`.

`audit` ist append-only: Rolle der Anwendung hat nur `INSERT`, `hash = sha256(vorher_hash || kanonisches_json(eintrag))`. Täglicher Export mit Ed25519-Signatur nach S3 `backups/audit/`.

### 8.1 S3-Buckets

| Bucket | Inhalt | Ablauf |
| --- | --- | --- |
| `laufdaten` | Eingangsdateien, Zwischenstände, Präfix `<mandant>/<lauf>/` | Lifecycle 24 h |
| `ergebnisse` | erzeugte Dateien | Lifecycle 7 Tage |
| `wissen-quellen` | Quellen für RAG | versioniert |
| `backups` | Audit-Export, Datenbanksicherung | nach Betriebskonzept |

Nur S3-Basis-API: `PutObject`, `GetObject`, `DeleteObject`, `ListObjectsV2`, Multipart, presigned GET.

---

## 9. Verträge

### 9.1 Externe API des Runners (`/api/v1`)

| Methode und Pfad | Scope | Zweck |
| --- | --- | --- |
| `GET /skills` | `uc.lesen` | Katalog der Pakete mit `nutzung: anwendung` |
| `POST /skills/{id}` | `uc.ausfuehren` | synchron, nur `read` |
| `GET /processes` | `uc.lesen` | Katalog der Pakete mit `nutzung: vorgang` |
| `POST /processes/{id}/run` | `uc.ausfuehren` | `?async=true` → `202 {run_id}` |
| `GET /runs/{id}` | `uc.lesen` | Status, Schritte, Ergebnis |
| `GET /runs/{id}/events` | `uc.lesen` | SSE |
| `GET /approvals?status=OFFEN` | `uc.freigeben` | offene Freigaben des Mandanten |
| `GET /approvals/{id}` | `uc.freigeben` | Freigabe mit rückabgebildetem Klartext |
| `POST /approvals/{id}` | `uc.freigeben` | `{entscheidung: erteilen|korrigieren|ablehnen, grund, korrektur?, klartext_hash}` |
| `POST /admin/packages` | `uc.admin` | signiertes Paket (tar.gz) hochladen |
| `POST /runs/{id}/nachreichung` | Nachreichungs-Token | Bürger reicht nach, Lauf setzt fort (`WAITING_CITIZEN`) |

Außerhalb von `/api/v1`:

| Methode und Pfad | Scope | Zweck |
| --- | --- | --- |
| `GET /openai/v1/models`, `POST /openai/v1/chat/completions` | `uc.ausfuehren` | OpenAI-kompatibel, `model: uc/<id>`; Basis-URL für Clients `…/openai/v1` |
| `/mcp` | `uc.ausfuehren` | MCP Streamable HTTP, Werkzeuge = aktive `read`-Pakete |
| `GET /.well-known/oauth-protected-resource` | – | Protected Resource Metadata (RFC 9728) für MCP-Clients |
| `GET /freigaben/` | – (Anmeldung im Browser) | Freigabeliste, statische Seite |
| `GET /health`, `GET /ready`, `GET /metrics` | – / – / Scrape-Token | Betrieb |

Interne API: `POST /intern/v1/chat/completions` (Modell-Proxy für das Taschenmesser, Scope `modell.nutzen`, nur aus `intern`). Der RAG-Service liegt selbst in Zone B und ruft Modelle direkt.

### 9.2 Antwortformat

```json
{
  "ergebnis": { "...": "..." },
  "fundstellen": [ { "dok": "dok:1", "seite": 2, "zitat": "..." } ],
  "meta": {
    "anfrage_id": "01J…", "paket": "posteingang", "version": "1.2.0",
    "plan_hash": "sha256:…", "modell_digest": "sha256:…", "reife": "R0",
    "dauer_ms": 1840, "stufen": { "0": 3, "1": 1, "2": 1 }
  }
}
```

Fehler:

```json
{ "fehler": { "code": "GATE_VERSTOSS", "grund": "VAL-004", "nachricht": "Zitat nicht in Quelle", "fail_closed": true }, "meta": { … } }
```

Feste Fehlercodes in `02-implementierung.md`, Abschnitt 6.

### 9.3 Paketformat

`usecases/<id>/process.yaml`, `schemas/*.json`, `rules/*.json`, `prompts/*.md`, `goldset.jsonl`, `einstufung.yaml`, optional `contracts/*.yaml`. Schema in `contracts/paket.schema.json` (S07). Beispiel siehe Konzept, Abschnitt Use Cases.

### 9.4 Werkzeugregister

`registry/tools.yaml`, Schema in `contracts/register.schema.json`. Pflichtfelder: `id`, `transport` (`rest`,`mcp`,`local`), `ziel` (symbolisch), `scope`, `wirkung`, `stufen`, `beschreibung_hash`. Optional: `liest_fremdtext`, `nutzt_modell` (Werkzeug ruft selbst ein Modell; Pflicht dann `feste_argumente` mit `schwaerzen: true`, sonst lehnt der Register-Linter ab), `feste_argumente`, `kompensation`, `idempotenz`, `eingabe_schema`, `ausgabe_schema`, `pfad`. Zieladressen nur in `config/ziele.yaml` je Umgebung.

### 9.5 Signatur

Manifest `signatur.yaml` im Paket: `algorithmus: ed25519`, `key_id`, `signiert_am`, `payload_sha256`, `dateien_sha256`, `register_pin`, `evidenz` (`linter_ok`, `goldset_sha256`, `eval_pass_rate`, `review_freigabe_ids`), `wert`. Kanonisierung: JSON mit sortierten Schlüsseln, UTF-8, ohne Leerraum, über das Manifest ohne `wert`.

---

## 10. Sicherheitsarchitektur (Kurzfassung)

| Schicht | Maßnahme | Session |
| --- | --- | --- |
| Identität | JWT über JWKS, `aud`, `exp` ≤ 5 min, Mandant nur aus Claim, Scopes, Token Exchange mit `act` | S03 |
| Eingabe | Größen- und Typgrenzen, clamd fail-closed, Parsen in Sandbox ohne Netz | S04 |
| Lieferkette | Ed25519-Pakete, Register-Hash-Pins, Modell-Digest, SBOM, gepinnte Abhängigkeiten | S06, S08, S20 |
| Zonen | Typen `Klartext`/`Redigiert`, verschlüsselte Ablage AES-256-GCM je Mandant | S10 |
| Agent | nur `read`, Budgets, feste Argumente, Fremdtext-Sperre | S13 |
| Ausgang | VAL-001 bis VAL-008 | S11 |
| Wirkung | Freigabe mit `klartext_hash`, Outbox, Zusteller, Idempotenz, Kompensation | S16, S18 |
| Transport | mTLS intern, HTTP/2, Netzmatrix | S20 |
| Nachweis | Audit-Hash-Kette, inhaltsfreies Logging, OTel | S02, S14 |
| Prüfung | OWASP API Top 10, Fuzzing, Injection-Korpus nach OWASP Agentic Top 10 | S20 |

---

## 11. Architekturentscheidungen (ADR-Kurzform)

| Nr | Entscheidung | Begründung | Revision bei |
| --- | --- | --- | --- |
| ADR-001 | Drei eigene Images; Zusteller und Sandbox sind Prozesse bestehender Images | wenig Betrieb | Lastprofil verlangt Trennung |
| ADR-002 | Python 3.13, FastAPI, Pydantic v2, Granian | Bestand Taschenmesser, HTTP/2 | – |
| ADR-003 | PostgreSQL als Zustands- und Jobspeicher, Workflow-Port austauschbar | eine Datenbank | Wiederaufnahme reicht nicht → DBOS |
| ADR-004 | Ed25519 mit `cryptography` | Standard, schnell, kleine Signaturen | – |
| ADR-005 | REST außen und intern über HTTP/2; gRPC nur zu Qdrant | ein Vertrag je Dienst, wenig Werkzeuge | messbarer Engpass interner Aufrufe |
| ADR-006 | Zonen im Typsystem erzwungen | Fehler zur Entwicklungszeit statt im Betrieb | – |
| ADR-007 | Keine Codeausführung durch Modelle oder Pakete | größtes Risiko, kein Bedarf | – |
| ADR-008 | Nur S3-Basis-API | jeder kompatible Speicher | – |
| ADR-009 | Java nur ab aktueller LTS (derzeit 25) | HTTP/2 ohne Zusatzbibliothek, Sicherheitsupdates | neue LTS |
| ADR-010 | Modell-Attrappe mit Drehbüchern für alle Tests | deterministische Tests ohne GPU | – |
| ADR-011 | Schwärzen ohne LLM auf Klartext (Regeln, Prüfziffern, Wörterbuch, Decision auf Vorredigiertem) | Leitregel 4 | Recall < 0,99 im Goldset |
| ADR-012 | Freigabeliste als Vanilla-HTML/JS, vom Runner ausgeliefert | kein Frontend-Projekt, BITV | – |
| ADR-013 | Migrationen als nummerierte SQL-Dateien, eigener kleiner Migrator | keine ORM-Abhängigkeit | – |
| ADR-014 | MCP über offizielles Python-SDK, Transport Streamable HTTP | Spezifikation | – |

---

## 12. Qualitätsziele des MVP

| Ziel | Wert | Nachweis |
| --- | --- | --- |
| Synchroner Skill ohne LLM | p95 < 500 ms (ohne Dateiparsen) | Lasttest S20 |
| LLM-Schritt | p95 < 15 s | gegen vLLM, nicht gegen Attrappe |
| Parallelität | 40 parallele Läufe | Lasttest S20 mit Attrappe |
| Gate | jede VAL-Regel mit positivem und negativem Test | S11 |
| Injection | 100 % des Korpus blockiert oder ohne Wirkung | S20 |
| Schwärzen-Recall | ≥ 0,99 im Goldset | S04 |
| Testabdeckung | ≥ 85 % Zeilen in `runner.gates`, `runner.zonen`, `runner.paket` | CI |
