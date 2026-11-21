# S09 – Runner-API und Modus `skill`

**Ziel:** Der Runner läuft als Dienst hinter dem Eingang, liefert den Paketkatalog, führt Pakete im Modus `skill` synchron aus, nimmt signierte Pakete per Upload an und stellt dem Taschenmesser den Modell-Proxy bereit. Erster Pilot: `frist`.

**Voraussetzungen:** S08 (und S03, S04, S05)

**Kontext lesen:** Architektur 6.1, 9.1, 9.2, 5.3 · Implementierung 4 (`kern.s3`), 7

## Aufgaben

1. `runner.api.app`: `kern.app.neue_app("runner", …)`, `lifespan` lädt Konfiguration, Register (S06), Pakete (S08), Modellclient (S05), HTTP-Clients, S3-Client; `/ready` prüft Register, Pakete, Modelle, Datenbank (ab S14), S3.
2. `kern.s3`: `lege_ab(mandant, bucket, schluessel, daten)`, `lies`, `loesche`, `presigned_get`; Schlüssel immer mit Präfix `<mandant>/`; Mandant aus `Identitaet`, nie aus der Anfrage.
3. Dateien und Handles: `POST /api/v1/skills/{id}` nimmt JSON oder Multipart (`eingabe` als JSON-Feld, `dateien[]`). Jede Datei: Größenprüfung, `kern.clamd`, Ablage in `laufdaten/<mandant>/<anfrage_id>/original/<n>`, Handle `dok:<n>`. Werkzeuge erhalten Dateien über den Datei-Auflöser aus S06.
4. `runner.engine.ausfuehrung` (Grundgerüst, in S10–S15 erweitert): führt einen Plan Schritt für Schritt aus, mit Budgetzählung (`max_schritte`, `timeout_s`), Pfadausdrücken, Ergebnis je Schritt als `{wert, quelle, stufe, dauer_ms}`. In dieser Session nur die Schrittart `skill`; Pakete mit anderen Schrittarten werden bis zu deren Session mit `PAKET_UNGUELTIG` abgelehnt (kein stilles Überspringen). Werkzeuge mit `nutzt_modell: true` werden mit dem festen Argument `schwaerzen: true` aufgerufen.
5. Routen nach Architektur 9.1: `GET /api/v1/skills`, `POST /api/v1/skills/{id}` (nur Pakete mit `nutzung: anwendung`, nur `read`), `POST /admin/packages` (tar.gz ≤ 5 MB, sicher entpacken: keine absoluten Pfade, kein `..`, keine Links; prüfen nach S08; Ablage in `PAKETE_DIR`; Aktivierung bleibt in `mandanten.yaml`).
6. Modell-Proxy `POST /intern/v1/chat/completions` (Scope `modell.nutzen`): reicht an `ModellClient` mit Priorität `vorgang` weiter; nimmt nur bekannte Modellnamen; Tokenmetriken mit Label `quelle=taschenmesser`. Der Proxy behandelt eingehende Texte als `Redigiert`, weil das Taschenmesser intern schwärzt (Architektur 7.3); die Umwandlung geschieht ausschließlich über eine eng benannte Funktion in `runner.zonen` (`als_redigiert_vom_taschenmesser`), damit die AST-Regel aus S10 gilt; die Vertrauensannahme ist dort kommentiert und auf den Scope `modell.nutzen` beschränkt.
7. Antwort mit `meta` (Paket, Version, `plan_hash`, Reife, `dauer_ms`, `stufen`), Quote je Mandant über `kern.quote`.
8. Pilotpaket `usecases/frist/` (Modus `skill`, Werkzeug `taschenmesser.fristen`, Eingabe `{ereignis_datum, frist_art, bundesland}`, Goldset mit 30 Fällen), signiert mit Dev-Schlüssel, in `mandanten.yaml` für `musterhausen` aktiv.
9. Compose: `runner` (Netze `rand`, `idp`, `intern`, `modell`), `eingang` mit Caddy (`deploy/caddy/Caddyfile`: TLS mit Dev-Zertifikat, HTTP/1.1 und HTTP/2, Weiterleitung an `runner:8000` per h2c in Dev).
10. OpenAPI nach `contracts/openapi/runner.json` exportieren (`make openapi`), im CI auf Aktualität prüfen.

## Akzeptanzkriterien

- [ ] Integration über den Eingang: Token von Keycloak (`fachverfahren-dev`) → `POST https://localhost/api/v1/skills/frist` über HTTP/2 → richtige Frist, `meta.plan_hash` gesetzt; derselbe Aufruf über HTTP/1.1 funktioniert.
- [ ] Paket nicht für den Mandanten aktiv → 404; Token des Mandanten `beispielstadt` sieht `frist` nicht im Katalog.
- [ ] Upload eines manipulierten Pakets → 422 `SIGNATUR_UNGUELTIG`; Upload mit `../`-Pfad im Archiv → 422 `PAKET_UNGUELTIG`.
- [ ] Modell-Proxy ohne Scope `modell.nutzen` → 403; mit Scope → Antwort der Attrappe; unbekannter Modellname → 422.
- [ ] Datei mit EICAR → 422 `VIRUS`, nichts in S3 abgelegt.
- [ ] `pruefe_routen` für den Runner grün.

## Nicht in dieser Session

Schwärzen im Runner (S10), Modellschritte (S12), Läufe in der Datenbank (S14).

## Prüfung

```
make check && make up && make test-int
```
