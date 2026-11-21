# S15 – Modi `kette` und `hybrid`, Wartezustände

**Ziel:** Deterministische Ketten mit `verzweigung`, `fuer_jede`, `warten` und `freigabe` sowie `hybrid` mit gekapseltem Agent-Knoten laufen als persistente Läufe; `WAITING_HUMAN` und `WAITING_CITIZEN` funktionieren inklusive Nachreichung und Fristablauf.

**Voraussetzungen:** S14

**Kontext lesen:** Architektur 6.2, 9.1 (`nachreichung`) · Konzept: Modi, Beispiel `posteingang` · Gesamtspezifikation 4 (Archetypen)

## Aufgaben

1. Bedingungen für `verzweigung`: eigener Parser (kein `eval`) für Ausdrücke mit Pfaden, Literalen (`true`, `false`, Zahlen, Strings, Listen), Operatoren `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`, `and`, `or`, `not`, Klammern; Linter L007 (S07) bleibt maßgeblich.
   ```yaml
   - id: weiche
     verzweigung:
       - wenn: $.vollstaendig.wert.option == "ja"
         dann: gebuehr
       - sonst: nachforderung
   ```
   `dann` nennt den Schritt, mit dem es weitergeht (Sprungziel); danach läuft die Kette in Dateireihenfolge weiter bis zum nächsten Endknoten. Neuer Endknoten `an_mensch: {grund}` (Schema und Linter erweitern: jeder Zweig endet in `an_mensch`, `freigabe`-Folge oder Ausgabe; keine Rücksprünge).
2. `fuer_jede`: `ueber: <pfad auf Liste>`, `als: element`, `schritte: [...]`, Obergrenze `max: 50` (Linter); Ergebnisse als Liste.
3. `warten`:
   - `auf: buerger` → erzeugt ein Nachreichungs-Token = HMAC-SHA256 (Plattformschlüssel, `lauf_id` + 256-Bit-Zufallswert); gespeichert werden nur Zufallswert und Token-Hash, Ablauf aus `frist: P14D`; Status `WAITING_CITIZEN`; `GET /runs/{id}` liefert dem Fachverfahren des Mandanten das Token, solange der Lauf wartet (nie in Ereignissen, SSE oder Logs); `POST /api/v1/runs/{id}/nachreichung` mit `Authorization: Nachreichung <token>` und neuen Eingaben/Dateien setzt fort (`$.nachreichung.*`);
   - Fristablauf → Fortsetzung im Zweig `bei_ablauf`;
   - `auf: zeit` mit `bis`.
   Zeitsteuerung im Worker (Abfrage fälliger Läufe je Minute).
4. `freigabe` (Knoten): legt Freigabe mit `klartext_hash` des bis dahin zurückgebildeten Ergebnisses an, Status `WAITING_HUMAN`, Ablauf 24 h. Fortsetzung über interne Funktion `freigabe_entschieden(...)`; die API folgt in S16. Ablauf ohne Entscheidung → `AN_MENSCH`/`FREIGABE_ABGELAUFEN`.
5. `hybrid`: Agent-Knoten (S13) innerhalb einer Kette; danach deterministische Rückkehr; Linter L004/L005 gelten.
6. Gate-Verstoß oder `an_mensch` in einem Lauf → Status `AN_MENSCH` mit Grund; das Fachverfahren sieht ihn über `GET /runs/{id}` und SSE.
7. Pilot `posteingang` auf `modus: hybrid` umstellen (wie Konzept-Beispiel: `schwaerzen → triage (agent) → frist (skill) → gate → freigabe`), `nutzung: [anwendung, vorgang]`; in der Anwendung endet er nach `gate` (der Runner schneidet `freigabe` und folgende Schritte für `/skills` ab; Linter prüft, dass das möglich ist).
8. Pilot `usecases/gewerbe/` (Modus `kette`, `nutzung: vorgang`), Graph ohne Schleifen:
   ```
   schwaerzen → extrahieren (llm) → vollstaendig (entscheidung) → weiche
     ja   → gebuehr (regel) → gate → freigabe
     nein → nachforderung (skill taschenmesser.nachforderung) → warten (buerger, P14D)
              nachgereicht → schwaerzen_2 → extrahieren_2 → vollstaendig_2 → weiche_2
                               ja   → gebuehr (regel) → gate → freigabe
                               nein → an_mensch
              bei_ablauf   → an_mensch
   ```
   Gemeinsame Folgeschritte (`gebuehr`, `gate`, `freigabe`) sind Sprungziele, keine Kopien. Goldset 30 Fälle.

## Akzeptanzkriterien

- [ ] Ausdrucksparser: Tabellentests inkl. Ablehnung von Funktionsaufrufen, Attributzugriff, Indizes außerhalb von Pfaden.
- [ ] `gewerbe` unvollständig: Lauf geht in `WAITING_CITIZEN`; Nachreichung mit falschem Token 401, mit richtigem Token setzt fort und endet in `WAITING_HUMAN`; Token zweimal verwenden → 409.
- [ ] Fristablauf (Uhr im Test verstellbar) führt in den Zweig `bei_ablauf`.
- [ ] Freigabe läuft nach 24 h ab → `AN_MENSCH`/`FREIGABE_ABGELAUFEN`.
- [ ] `posteingang` liefert über `/skills` ein Ergebnis ohne Freigabe und über `/processes` einen Lauf in `WAITING_HUMAN`.
- [ ] Neustart während `WAITING_CITIZEN` verliert nichts.

## Nicht in dieser Session

Freigabe-API und Oberfläche (S16), Wirkung (S18).

## Prüfung

```
make check && make up && make test-int && make pakete
```
