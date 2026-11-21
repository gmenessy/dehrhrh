# S06 – Werkzeugregister

**Ziel:** Der Runner kennt Werkzeuge nur über `registry/tools.yaml`, prüft deren Beschreibungen gegen gepinnte Hashes, sperrt abweichende Werkzeuge und ruft Werkzeuge über die Transporte `rest`, `mcp` und `local` mit unüberschreibbaren festen Argumenten auf.

**Voraussetzungen:** S04, S05

**Kontext lesen:** Architektur 9.4, 5.4, 7.1 (`runner.register`) · Konzept: Abschnitt Werkzeugregister

## Aufgaben

1. `contracts/register.schema.json` (JSON Schema 2020-12) für `registry/tools.yaml`: Pflicht- und optionale Felder laut Architektur 9.4; `additionalProperties: false`; `wirkung` ∈ {`read`, `reversible_write`, `irreversible_write`}; `stufen` ⊆ {0, 1, 2}; Regel: `nutzt_modell: true` verlangt `feste_argumente` mit `schwaerzen: true`; `reversible_write` verlangt `idempotenz` und `kompensation`.
2. `config/ziele.dev.yaml`: symbolische Ziele (`taschenmesser`, `rag`, `dms`, …) → Basis-URL, Lese-Timeout, Transportoptionen. Der Runner liest `ZIELE_DATEI`. Ziel-URLs außerhalb dieser Datei gibt es nicht (SSRF-Schutz).
3. `runner.register.modell`: Pydantic-Modelle `Werkzeug`, `Register`; Laden mit Schema- und Konsistenzprüfung (eindeutige IDs, `kompensation` verweist auf existierendes Werkzeug, `ziel` existiert in `ziele.yaml`).
4. `runner.register.beschreibung`: `beschreibung_hash = sha256_praefix(kanonisch({id, beschreibung, eingabe_schema, ausgabe_schema}))`. Quelle je Transport: `rest` → `GET <ziel>/v1/werkzeuge` (Taschenmesser, S04), `mcp` → `tools/list`, `local` → Docstring und Schemas der registrierten Funktion.
5. `runner.register.pruefung`: beim Start und alle 10 min alle Hashes neu berechnen; Abweichung oder unerreichbare Beschreibung → Werkzeug gesperrt, Metrik `plattform_register_gesperrt{werkzeug}`, Log-Ereignis `register_abweichung`; Aufruf eines gesperrten Werkzeugs → 503 `REGISTER_ABWEICHUNG`.
6. `runner.register.transport`:
   - `rest`: `POST <ziel><pfad>` mit JSON oder Multipart (Handles werden in S09 zu Dateien aufgelöst; hier Schnittstelle vorbereiten über einen übergebenen Datei-Auflöser), Dienst-Token mit dem Werkzeug-Scope, `Idempotency-Key`, wenn `idempotenz` gesetzt;
   - `mcp`: offizielles SDK, Streamable HTTP, Dienst-Token mit Zielgruppe des MCP-Servers (kein Durchreichen des Nutzertokens);
   - `local`: registrierte Python-Funktionen in `runner.register.lokal`: `regeln.tabelle` (Entscheidungstabelle aus JSON: erste passende Zeile, Bedingungen `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`; Stufe 0) und `daten.pruefen` (JSON-Schema-Prüfung).
   - Ergebnis immer gegen `ausgabe_schema` geprüft; Abweichung → Fehler `EINGABE_UNGUELTIG` mit Grund `VAL-007`.
7. Feste Argumente: `aufrufen(werkzeug, argumente, kontext)` setzt `feste_argumente` aus dem Kontext (`mandant`, `raum`, `schwaerzen` …) und überschreibt mitgegebene Werte; jede Überschreibung zählt `plattform_feste_argumente_ueberschrieben_total{werkzeug}`.
8. CLI `registry pin [--schreiben]` (berechnet Hashes gegen laufende Ziele, zeigt Unterschiede, schreibt nur mit `--schreiben`) und `registry check` (Rückgabecode ≠ 0 bei Abweichung).
9. `registry/tools.yaml` mit allen 25 Taschenmesser-Werkzeugen (`taschenmesser.<name>`, Wirkung `read`, Scope `tm.<name>`, `nutzt_modell` aus `/v1/werkzeuge`), `taschenmesser.text`, und den lokalen Werkzeugen. Hashes per `registry pin --schreiben` gegen die Dev-Umgebung.

## Akzeptanzkriterien

- [ ] Unbekanntes Feld, doppelte ID, fehlende Kompensation bei `reversible_write`, `nutzt_modell` ohne `schwaerzen: true` → Ladefehler mit klarer Meldung.
- [ ] Test mit Test-MCP-Server (SDK, in-process oder als Fixture-Dienst): `tools/list` liefert Beschreibung, Hash passt; nach Änderung der Beschreibung wird das Werkzeug bei der nächsten Prüfung gesperrt und ein Aufruf liefert 503 `REGISTER_ABWEICHUNG`.
- [ ] Feste Argumente: ein mitgegebenes `mandant: "fremd"` wird durch den Kontextmandanten ersetzt; Metrik zählt.
- [ ] `regeln.tabelle`: Tabellentests inkl. „keine Zeile passt“ → Ergebnis `None` (kein Standardwert).
- [ ] Ergebnis, das nicht dem `ausgabe_schema` entspricht → Fehler mit Grund `VAL-007`.
- [ ] `registry pin` ist deterministisch (zweimal gleiches Ergebnis); `registry check` gegen Dev-Umgebung grün (Integration).

## Nicht in dieser Session

Pakete, Pläne, Modi (ab S07); Einträge `wissen.*` (S17) und `dms.*` (S18).

## Prüfung

```
make check && make up && make test-int
```
