# S18 – Outbox und Zusteller

**Ziel:** Nach einer Freigabe entsteht Wirkung ausschließlich über die Outbox; der Zusteller führt sie in Zone C genau einmal aus, prüft vorher den `klartext_hash` und kompensiert bei Fehlern. Danach ist die Nutzung **Ende-zu-Ende** vorführbar.

**Voraussetzungen:** S16

**Kontext lesen:** Architektur 6.2, 5.1–5.4 (Zone C, Netz `aus_zusteller`), 8 (`outbox`), ADR-001 · Gesamtspezifikation 6 (Outbox & Zusteller)

## Aufgaben

1. Schrittart `wirkung` (nur nach `freigabe`, Linter L005):
   ```yaml
   - id: zuweisen
     wirkung: dms.vorgang_zuweisen
     mit: { vorgang: $.eingabe.vorgang_id, amt: $.triage.wert.amt }
   ```
   Der Runner schreibt einen Outbox-Eintrag mit **redigierten** Argumenten, `idempotenz_schluessel = sha256(lauf_id | schritt_id)`, `klartext_hash` der Freigabe; Lauf wartet im Status `LAEUFT` mit Ereignis `wirkung_ausstehend`.
2. `runner.zusteller` (Prozess `python -m runner.zusteller`, gleiches Image, eigene DB-Rolle `plattform_zusteller`, eigener Dienst-Client mit Scope des Werkzeugs):
   - holt `outbox` mit `FOR UPDATE SKIP LOCKED`, setzt `IN_ARBEIT`;
   - lädt Platzhaltertabelle aus `klartext`, bildet Argumente zurück, berechnet den `klartext_hash` des freigegebenen Ergebnisses neu und vergleicht mit der Freigabe → Abweichung: `FEHLER`/`HASH_ABWEICHUNG`, kein Aufruf, Alarm-Metrik;
   - ruft das Ziel über das Register (Transport `rest`) mit `Idempotency-Key` und dem bei der Freigabe getauschten Token-Kontext (`act` im Header `X-Plattform-Act` als signiertes JWT des Runners oder per Token Exchange zur Laufzeit – Entscheidung als ADR);
   - Erfolg → `ERLEDIGT`, Ergebnis (redigiert) speichern, Lauf fortsetzen; vorübergehender Fehler → bis zu 5 Versuche mit exponentieller Wartezeit; endgültiger Fehler → Kompensation aller vorher erledigten Wirkungen dieses Laufs in umgekehrter Reihenfolge, Lauf `AN_MENSCH`/`ZIEL_FEHLER`.
3. `werkzeuge/dms-attrappe`: `POST /v1/vorgaenge/zuweisen`, `POST /v1/zuweisungen/aufheben`, `POST /v1/vermerke`, `DELETE /v1/vermerke/{id}`; Idempotenzspeicher (gleicher Schlüssel → gleiche Antwort, keine zweite Wirkung); `GET /v1/werkzeuge` für Hash-Pins; `GET /_protokoll`; konfigurierbare Fehler für Tests.
4. Registereinträge `dms.vorgang_zuweisen`, `dms.zuweisung_aufheben`, `dms.vermerk_schreiben`, `dms.vermerk_loeschen` (`reversible_write`, `idempotenz: header:Idempotency-Key`, `kompensation`), Ziel `dms` in `ziele.dev.yaml`, Pins.
5. Pilotpakete erweitern: `posteingang` → nach `freigabe` `wirkung: dms.vorgang_zuweisen`; `gewerbe` → nach `freigabe` `wirkung: dms.vermerk_schreiben` (Vermerk mit Prüfergebnis und Gebühr).
6. Keycloak-Realm: Client `zusteller` (Client Credentials, Scopes der `dms.*`-Werkzeuge, z. B. `dms.schreiben`).
7. Compose: Dienst `zusteller` aus dem Runner-Image, Netze nur `intern`, `idp` und `aus_zusteller`, `read_only`, keine Ports; `dms-attrappe` nur in `aus_zusteller`.
8. Ereignisse und Audit: `outbox_angelegt`, `outbox_erledigt`, `outbox_fehler`, `kompensiert`.

## Akzeptanzkriterien

- [ ] E2E `gewerbe` vollständig: Lauf → `WAITING_HUMAN` → Freigabe → genau ein Vermerk in der DMS-Attrappe → Lauf `ABGESCHLOSSEN`.
- [ ] Zusteller wird nach dem Zielaufruf, aber vor dem Statuswechsel beendet; nach Neustart erneuter Aufruf mit gleichem `Idempotency-Key`, die Attrappe zählt genau eine Wirkung.
- [ ] Veränderung des Klartexts in der Datenbank zwischen Freigabe und Zustellung → `HASH_ABWEICHUNG`, kein Aufruf im Protokoll der Attrappe.
- [ ] Zwei Wirkungen, die zweite scheitert endgültig → die erste wird kompensiert, Lauf `AN_MENSCH`.
- [ ] Netz (Integration): aus `zusteller` ist `llm`, `decision`, `rag-api` und `runner:8000` nicht erreichbar; aus `runner` ist `dms-attrappe` nicht erreichbar.
- [ ] Die Rolle `plattform_zusteller` kann `laeufe` nicht ändern außer über die vorgesehene Funktion (SQL-Test).

## Nicht in dieser Session

`irreversible_write`, echte Fachverfahrens-Adapter.

## Prüfung

```
make check && make up && make test-int && make e2e
```
