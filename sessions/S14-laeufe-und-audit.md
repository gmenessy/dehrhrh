# S14 – Persistenz, Läufe, Audit-Kette

**Ziel:** Läufe werden in PostgreSQL mit Zeilen-Sicherheit je Mandant gespeichert, asynchron ausgeführt, nach Neustart fortgesetzt und über SSE beobachtet; Klartext liegt verschlüsselt in Zone A und wird fristgerecht gelöscht; jedes Ereignis steht in einer manipulationssicheren Audit-Kette.

**Voraussetzungen:** S13

**Kontext lesen:** Architektur 6.2, 8, 8.1, ADR-003, ADR-013 · Implementierung 5

## Aufgaben

1. Migrationen `sql/0001_schema.sql` ff.: Schema `plattform`, alle Tabellen aus Architektur 8 (inkl. `ereignisse`, `nachreichungen`), Indizes, Rollen aus Implementierung 5, Zeilen-Sicherheit (`ENABLE` und `FORCE ROW LEVEL SECURITY`, Policy auf `current_setting('app.tenant_id', true)`), Rechte: `audit` nur `INSERT` und `SELECT` für Anwendungsrollen; Trigger, der `UPDATE`/`DELETE` auf `audit` mit Fehler abbricht.
2. `runner.laufzeit.migration`: Migrator (`python -m runner.laufzeit.migration`), Tabelle `plattform.migrationen`, Prüfsumme je Datei; geänderte, bereits angewandte Datei → Abbruch.
3. `runner.laufzeit.db`: Pool (psycopg 3 async), `transaktion(mandant)` mit `SET LOCAL app.tenant_id`; Verbindungs-URL aus `DATABASE_URL_DATEI`.
4. `runner.laufzeit.laeufe`: anlegen, Schritt speichern (Idempotenz über `(lauf_id, nr)`), Status setzen, Ereignis schreiben; Worker im Runner-Prozess (`LAUF_WORKER`, Standard 8) holt Läufe mit `FOR UPDATE SKIP LOCKED`, Heartbeat-Spalte; Läufe mit abgelaufenem Heartbeat werden übernommen und ab dem ersten nicht abgeschlossenen Schritt fortgesetzt.
5. `runner.zonen.ablage.Datenbank`: `KlartextAblage` auf Tabelle `klartext` (verschlüsselt nach S10), `loeschen_am` = Abschluss + 30 Tage; Original- und redigierte Dateien in S3 wie bisher.
6. Endpunkte: `GET /api/v1/processes`, `POST /api/v1/processes/{id}/run` (synchron bis `timeout_s` des Pakets, sonst `?async=true` → 202 `{run_id}`), `GET /api/v1/runs/{id}` (Status, Schritte ohne Inhalte, Ergebnis zurückgebildet nur mit Scope `uc.lesen` und eigenem Mandanten, `Cache-Control: no-store`), `GET /api/v1/runs/{id}/events` (SSE aus `ereignisse`, `Last-Event-ID`, Heartbeat-Kommentar alle 15 s).
7. `runner.laufzeit.audit`: `schreibe(mandant, ereignis, **daten)` unter `pg_advisory_xact_lock(hash(mandant))`: `seq` fortlaufend, `hash = sha256(vorher_hash || kanonisch(eintrag))`; `daten` nur mit erlaubten Schlüsseln (wie Log-Allowlist). Ereignisse: `lauf_angelegt`, `schritt_beendet`, `gate_verstoss`, `an_mensch`, `freigabe_*`, `outbox_*`, `paket_geladen`, `paket_abgelehnt`, `register_abweichung`, `geloescht`.
8. CLI `uc audit pruefen --mandant <m>` (Kette nachrechnen) und `uc audit export --mandant <m> --datum <d>` (JSONL + Ed25519-Signatur mit Plattform-Schlüssel nach `backups/audit/<mandant>/<datum>.jsonl(.sig)`), stündlicher Löschjob im Runner: abgelaufener Klartext, Läufe älter als Aufbewahrung (Konfiguration), S3-Reste; je Löschung Audit-Eintrag `geloescht` mit Anzahl.
9. Synchrone Skill-Aufrufe schreiben nur Audit, keinen Lauf (Architektur 6.1).

## Akzeptanzkriterien

- [ ] Integration mit zwei Mandanten: Token von `beispielstadt` sieht Lauf von `musterhausen` nicht (404), auch nicht über SSE; direkte SQL-Abfrage als `plattform_runner` ohne `app.tenant_id` liefert keine Zeilen.
- [ ] `UPDATE` und `DELETE` auf `audit` schlagen fehl; manuell (als Eigentümerrolle) verändertes Audit-Feld wird von `uc audit pruefen` mit `seq` gemeldet.
- [ ] Runner wird während eines Laufs mit fünf Schritten nach Schritt 2 hart beendet; nach Neustart wird der Lauf abgeschlossen, jeder Schritt genau einmal in `schritte`, jedes Werkzeug genau einmal aufgerufen (Attrappen-Protokoll bzw. Taschenmesser-Metrik).
- [ ] SSE: Client verbindet nach Abbruch mit `Last-Event-ID` und erhält nur neue Ereignisse.
- [ ] Löschjob entfernt Klartext mit `loeschen_am` in der Vergangenheit und schreibt `geloescht`.
- [ ] Migration zweimal ausführen ist folgenlos; geänderte alte Migration → Abbruch.

## Nicht in dieser Session

Steuerknoten und Wartezustände (S15), Freigaben (S16).

## Prüfung

```
make check && make up && make test-int
```
