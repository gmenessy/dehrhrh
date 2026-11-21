# S11 – Ausgangs-Gate und Goldset-Runner

**Ziel:** Die geschlossene Liste VAL-001 bis VAL-008 ist als Schrittart `gate` umgesetzt und deterministisch getestet; `uc eval` misst Pakete gegen ihr Goldset und liefert die Evidenz für die Signatur.

**Voraussetzungen:** S10

**Kontext lesen:** Architektur 10 · Gesamtspezifikation 3.2 (VAL-001 bis VAL-008), 8.1 · Konzept: Ausgangs-Gate

## Aufgaben

1. Schema-Annotationen für Ausgabeschemas (in `contracts/paket.schema.json` dokumentieren, Linter kennt sie):
   - `x-frist: {ereignis: "<pfad>", art: "<frist_art>"}` (VAL-002)
   - `x-tabelle: "rules/<datei>.json"` mit `x-eingabe: {…}` (VAL-003)
   - `x-zitat: {quelle: "<handle oder pfad>"}` (VAL-004)
   - `x-norm: {stichtag: "<pfad>"}` mit Normenliste `rules/normen.json` (`id`, `fassung_von`, `fassung_bis`) (VAL-005)
   - `x-aus-werkzeug: "<schritt_id>.<pfad>"` (VAL-007)
   - `x-aus-datensatz: "<pfad in eingabe>"` für Empfänger, Anrede, Aktenzeichen (VAL-008)
2. `runner.gates` mit je einer Funktion `pruefe_val_00n(ergebnis, kontext) -> list[Verstoss]`:
   - **VAL-001** Ergebnis entspricht exakt dem Ausgabeschema.
   - **VAL-002** jedes `x-frist`-Feld = Ergebnis von `taschenmesser.fristen` (Stufe 0) für das referenzierte Ereignis.
   - **VAL-003** jedes `x-tabelle`-Feld = Ergebnis von `regeln.tabelle`; Listen als Mengen verglichen.
   - **VAL-004** jedes `x-zitat`-Feld ist nach `kern.text`-Normalisierung Teilstring der Quelle (redigierte Fassung).
   - **VAL-005** jede Norm-ID im Feld ist in `normen.json` und gilt am Stichtag.
   - **VAL-006** über alle Textfelder läuft die Erkennung des Taschenmessers (`/intern/v1/schwaerzen` auf dem redigierten Ergebnis); jeder Fund, der kein bekannter Platzhalter des Vorgangs ist, ist ein Verstoß.
   - **VAL-007** Werte aus Werkzeugen stammen aus einem Schritt mit gepinntem Werkzeug und stimmen mit dessen gespeichertem Ergebnis überein; Werkzeugdaten stehen nur in Feldern mit `x-aus-werkzeug`.
   - **VAL-008** jeder Platzhalter im Ergebnis existiert typgleich in der Platzhaltertabelle; `x-aus-datensatz`-Felder sind gleich dem Eingabewert.
3. Schrittart `gate` mit `regeln: [VAL-…]`; Verstoß: synchron 422 `GATE_VERSTOSS` mit `grund` (erste verletzte Regel) und Liste aller Gründe in `fehler.details` (nur IDs und Feldpfade, keine Werte); Metrik `plattform_gate_verstoss_total{regel,paket}`. In Läufen (ab S14) führt ein Verstoß zum Status `AN_MENSCH`.
4. Linter-Erweiterung: L015 jede im Gate genannte Regel hat im Ausgabeschema mindestens ein passendes Annotationsfeld (VAL-001, VAL-006, VAL-008 ausgenommen); L016 unbekannte Annotation → Fehler.
5. `uc eval <pfad> [--modell-url …] [--ausgabe eval.json]`: führt das Paket in-process gegen `goldset.jsonl` aus (Zeile: `{eingabe, dateien?, erwartet, erwartet_status}`), vergleicht feldweise (exakt, Listen als Mengen, Datumsfelder normalisiert), zählt `an_mensch`, Gate-Verstöße, Laufzeit; schreibt `eval.json` mit `goldset_sha256`, `eval_pass_rate`, `modell_digest`, `linter_ok`, Feldstatistik. Standard gegen die Modell-Attrappe mit dem Drehbuch des Pakets (`usecases/<id>/drehbuch.yaml`, wird nicht mit ausgeliefert, nicht signiert).
6. `make pakete`: für jedes Paket unter `usecases/` → `uc lint`, `uc eval`, `uc sign` (Dev-Schlüssel), `uc pack`.
7. Pilot `frist` bekommt ein Ausgabeschema mit `x-frist` und ein `gate: [VAL-001, VAL-002]`.

## Akzeptanzkriterien

- [ ] Je VAL-Regel mindestens ein positiver und ein negativer Test, darunter: Zitat mit weichem Trennzeichen (bestanden), erfundener Platzhalter `[PERSON_9]` (VAL-008), Klarname im Ergebnis (VAL-006), Frist um einen Tag verschoben (VAL-002), Norm außerhalb der Fassung (VAL-005).
- [ ] Gate-Antwort enthält keine Feldwerte, nur Regel-IDs und Feldpfade.
- [ ] `uc eval` ist mit der Attrappe deterministisch (zweimal gleiches `eval.json` außer Zeitstempel); `eval_pass_rate` für `frist` = 1,0.
- [ ] `make pakete` erzeugt signierte Archive; `uc verify` besteht.
- [ ] Abdeckung `runner.gates` ≥ 85 %, `mypy --strict` grün.

## Nicht in dieser Session

Decision-Vertrag `ausgang.gate` als zusätzliche Modellprüfung (optional, nach MVP).

## Prüfung

```
make check && make up && make test-int && make pakete
```
