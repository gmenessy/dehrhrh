# S20 – Härtung, Sicherheitstests, Abnahme

**Ziel:** Interne Verbindungen laufen über mTLS und HTTP/2, die Netzmatrix ist bewiesen, Injection-Korpus und Fuzzing bestehen, Images sind gehärtet mit SBOM, Lastziele sind gemessen, und alle vier Pilotpakete laufen in einem Abnahmetest Ende-zu-Ende.

**Voraussetzungen:** S18, S19

**Kontext lesen:** Architektur 5, 10, 12 · Implementierung 7, 8, 9, 10 · Konzept: Sicherheit nach aktuellem Stand (OWASP Agentic Top 10)

## Aufgaben

1. Dev-PKI `deploy/pki/erzeuge.py` (nur `cryptography`): CA, Zertifikate je Dienst mit DNS-Namen der Compose-Dienste, Laufzeit 30 Tage; `make setup` ruft es auf.
2. mTLS: `kern.server` startet mit TLS und Client-Zertifikatsprüfung; `kern.http` nutzt Client-Zertifikate je Ziel. Prüfen, ob Granian die Client-Zertifikatsprüfung beherrscht; falls nicht, Caddy-Sidecar je Dienst (ADR). PostgreSQL mit TLS (`sslmode=verify-full`). Interne Verbindungen über HTTP/2 (Nachweis per Log der ausgehandelten Version im Integrationstest).
3. Netzmatrix-Test `tests/netz/test_matrix.py`: führt in den Containern Verbindungsversuche aus (`docker compose exec <dienst> python -c …`), prüft alle erlaubten Pfade aus Architektur 5.4 und mindestens 15 verbotene (u. a. sandbox → alles, zusteller → modell, taschenmesser → llm direkt, rag-api → postgres-plattform, runner → dms-attrappe).
4. Injection-Korpus `tests/sicherheit/injection/*.yaml`, mindestens 50 Fälle, je Fall Kategorie ASI01–ASI10, Ort (Dokument, Werkzeugergebnis, RAG-Abschnitt, Eingabefeld, Nachreichung), erwartetes Verhalten. Die Attrappe spielt ein **kompromittiertes Modell** (Drehbuch `boese.yaml`), das jeder Anweisung im Material folgt: fremde Werkzeuge aufrufen, feste Argumente überschreiben, URLs ausgeben, Platzhalter erfinden, Klarnamen schreiben, Freigabe vortäuschen. Erwartung: kein nicht gelistetes Werkzeug aufgerufen, keine Wirkung ohne Freigabe, Gate oder `an_mensch` greift, keine Daten anderer Mandanten, kein Klartext im Modellprotokoll. Ziel 100 %.
5. Fuzzing mit `schemathesis` (Testwerkzeug laut Implementierung 1) gegen die OpenAPI von Runner, Taschenmesser und RAG mit gültigem Token: kein 500 außer `INTERN` mit leerem Detail; keine Antwort mit Stacktrace.
6. Authentifizierungsmatrix: für jede Route jeder Dienst ohne Token (401), mit falschem Scope (403), mit fremdem Mandanten (404 bzw. 403).
7. Container-Härtung prüfen (Test über `docker inspect`): Benutzer ≠ root, `ReadonlyRootfs`, `CapDrop ALL`, `no-new-privileges`, Speicher- und PID-Grenzen, Healthcheck; keine Geheimnisse in Umgebungsvariablen (nur `*_DATEI`).
8. `make sbom`: CycloneDX je Image, `pip-audit` ohne Funde der Schwere hoch/kritisch (Ausnahmen nur mit ADR).
9. Lasttest `tests/last/last.py` (asyncio + httpx, keine weitere Abhängigkeit): 40 parallele Läufe `gewerbe` gegen die Attrappe, 200 synchrone `frist`-Aufrufe; Ergebnis p50/p95 nach `docs/messungen.md`.
10. Abnahmetest `tests/e2e/test_abnahme.py`, alle Pakete signiert, R0:
    - `frist` über REST (HTTP/2), OpenAI-API und Java-Client;
    - `posteingang` in der Anwendung (Vorschlag) und als Vorgang (Freigabe → Zuweisung im DMS);
    - `auskunft` mit belegter Antwort und mit Enthaltung;
    - `gewerbe` unvollständig → Nachreichung → Freigabe mit Korrektur → Vermerk im DMS;
    - danach: Audit-Kette beider Mandanten gültig, Attrappen-Protokoll ohne Klartext, Löschjob entfernt abgelaufene Daten (Uhr verstellt).
11. `docs/betrieb.md`: Start, Schlüssel (Signatur, Klartext je Mandant, PKI) und Rotation, Modellwechsel mit Goldsets, Backups und Audit-Export, Löschfristen, Alarme (Gate-Quote, Register-Sperre, Hash-Abweichung, Zonenverletzung), Wiederanlauf.
12. `docs/fortschritt.md`: Abnahmetabelle gegen Architektur 12 (Ziel, Messwert, erfüllt ja/nein).

## Akzeptanzkriterien

- [ ] `make sicherheit` grün: Netzmatrix, Injection-Korpus 100 %, Fuzzing, Authentifizierungsmatrix, Container-Härtung.
- [ ] `make sbom` ohne offene hohe oder kritische Funde.
- [ ] Lasttest: 40 parallele Läufe ohne Fehler; synchroner Skill ohne LLM p95 < 500 ms (Messwert dokumentiert).
- [ ] `make e2e` mit Abnahmetest grün.
- [ ] `docs/betrieb.md` vollständig; Abnahmetabelle ausgefüllt; offene Punkte aller Sessions bewertet (erledigt, nach MVP, Risiko).

## Nicht in dieser Session

Neue Funktionen. Was hier auffällt und nicht in die Session passt, wird offener Punkt.

## Prüfung

```
make check && make up && make test-int && make e2e && make sicherheit && make sbom
```
