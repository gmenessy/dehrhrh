# S04 – Taschenmesser übernehmen und härten

**Ziel:** Der bestehende Taschenmesser-Dienst läuft im Monorepo auf `kern`, mit JWT statt API-Schlüssel, Virenprüfung fail-closed, Parse-Sandbox ohne Netz, Schwärzen ohne LLM auf Klartext und Modellaufrufen über den (späteren) Modell-Proxy.

**Voraussetzungen:** S03

**Kontext lesen:** Architektur 7.3, 5.2–5.4, ADR-011 · Implementierung 4 (`kern.clamd`, `kern.text`), 8 · `vorhandenes/taschenmesser/README.md`

## Aufgaben

1. Bestand übernehmen: `vorhandenes/taschenmesser/app` → `dienste/taschenmesser/src/taschenmesser`, Tests und Testdaten nach `dienste/taschenmesser/tests/unit` bzw. `tests/daten`. Importe anpassen. Alle 82 Bestandstests laufen grün (Anpassung nur für Authentifizierung). `vorhandenes/` danach löschen.
2. Auf `kern` umstellen: `kern.app.neue_app`, Fehler über `kern.fehler` (bestehende `DokumentFehler` abbilden), Logging über `kern.log`, Metriken.
3. Authentifizierung: `X-Api-Key` entfernen; jede Werkzeugroute `braucht("tm.<werkzeug>")`; Mandant aus `Identitaet`. `pruefe_routen` im Test.
4. `kern.text` aus `zitat.py` übernehmen (Normalisierung, `belegt`); Taschenmesser nutzt es.
5. `kern.clamd`: eigener kleiner Client für `INSTREAM` über TCP (keine Fremdbibliothek), Zeitgrenze 10 s. Jede hochgeladene Datei wird vor dem Lesen geprüft. Fund → 422 `VIRUS`; clamd nicht erreichbar → 503 `VIRENPRUEFUNG_FEHLT`.
6. Parse-Sandbox: `TM_MODUS=sandbox` startet nur `POST /v1/text` (Dateien → Text je Seite), ohne Modellzugriff. Im Normalmodus gibt das Taschenmesser das Parsen von Dateien an `SANDBOX_URL` ab (Dienst-Token mit Scope `tm.text`). Compose: Dienst `sandbox` nur im Netz `parse`, read-only, `tmpfs /tmp 256m`, `mem_limit 1g`, `pids_limit 64`; Zeitgrenze je Anfrage 30 s; Schutz vor ZIP-Bomben (Entpackgröße ≤ 100 MB, Einträge ≤ 1.000) für DOCX/ODT/XLSX.
7. Schwärzen nach ADR-011: Standard `SCHWAERZEN_NER=regel`; der LLM-Pfad auf Klartext wird entfernt. Ergänzung: Wörterbuch `src/taschenmesser/daten/vornamen.txt` (häufige deutsche Vornamen, Quelle und Lizenz im Dateikopf; ohne Netzzugang mit einer gepflegten Startliste beginnen und als offenen Punkt vermerken) für Namen ohne Auslöser, nur in Kombination mit einem großgeschriebenen Folgewort.
8. Neuer Endpunkt `POST /intern/v1/schwaerzen` (Scope `tm.zuordnung`): wie `/v1/schwaerzen`, zusätzlich `zuordnung: {platzhalter: klartext}` und `typen: {platzhalter: typ}`. Antwort mit `Cache-Control: no-store`.
9. Neuer Endpunkt `GET /v1/werkzeuge` (Scope `tm.text`): je Werkzeug `id`, `beschreibung`, `eingabe_schema`, `ausgabe_schema`, `nutzt_modell` – Grundlage für die Hash-Pins in S06. Schemas aus den Pydantic-Modellen bzw. Formularparametern ableiten.
10. Modellaufrufe: `LLM_URL` und `DECISION_URL` ersetzt durch `RUNNER_MODELL_PROXY_URL` (OpenAI-API, Modellname wird mitgegeben; Dienst-Token mit Scope `modell.nutzen`). Bis S09 zeigt die Variable in Tests auf die Modell-Attrappe bzw. einen `httpx.MockTransport`.
11. Schwärzen-Goldset `dienste/taschenmesser/tests/goldset/schwaerzen.jsonl` mit mindestens 150 synthetischen Fällen (Namen mit und ohne Auslöser, Adressen, IBAN, Steuer-ID, E-Mail, Telefon, Geburtsdatum, Störfälle wie Behördennamen); Skript `make goldset-schwaerzen` gibt Recall und Präzision je Kategorie aus.
12. Keycloak-Realm um Client `taschenmesser` ergänzen (Client Credentials, Scopes `modell.nutzen`, `tm.text`).
13. Granian statt Uvicorn; Dockerfile auf `deploy/Dockerfile.python` mit Tesseract (Bestand); Compose-Dienste `taschenmesser` (Netze `intern`, `parse`, `idp`), `sandbox`, `clamd` (Netz `intern`).

## Akzeptanzkriterien

- [ ] Alle übernommenen Bestandstests grün.
- [ ] `X-Api-Key` wird nicht mehr akzeptiert; jede Route außer `/health`, `/ready`, `/metrics` ist geschützt (`pruefe_routen`).
- [ ] EICAR-Testdatei → 422 `VIRUS`; clamd gestoppt → 503 `VIRENPRUEFUNG_FEHLT` (Integration).
- [ ] Integration: Aus dem Container `sandbox` schlägt eine Verbindung zu `minio:9000` und zu einer externen Adresse fehl.
- [ ] Schwärzen-Goldset: Recall ≥ 0,99 für IBAN, Steuer-ID, E-Mail, Telefon, Geburtsdatum mit Auslöser; Werte für Namen und Adressen gemessen und in `docs/fortschritt.md` eingetragen (unter 0,99 → offener Punkt).
- [ ] `/intern/v1/schwaerzen` liefert die Zuordnung nur mit Scope `tm.zuordnung`; `/v1/schwaerzen` liefert sie nie.
- [ ] Test mit `MockTransport`: Ein modellnutzendes Werkzeug ruft ausschließlich `RUNNER_MODELL_PROXY_URL` auf, und der übermittelte Text enthält keinen Wert aus der Zuordnung.

## Nicht in dieser Session

Modell-Proxy im Runner (S09), mTLS (S20), Registereinträge (S06).

## Prüfung

```
make check && make up && make test-int && make goldset-schwaerzen
```
