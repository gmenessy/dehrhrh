# S13 – Modus `agent`

**Ziel:** Eine begrenzte Tool-Calling-Schleife über eine feste Liste lesender Werkzeuge, mit Budgets, festen Argumenten, Schleifenschutz, Fremdtext-Sperre und Structured-Output-Rückfall. Danach ist die Nutzung **in der Anwendung** vollständig vorführbar.

**Voraussetzungen:** S12

**Kontext lesen:** Architektur 7.1 (`runner.agent`), 10 · Konzept: Modi, Sicherheit (ASI01, ASI02, ASI08)

## Aufgaben

1. Schrittart `agent` (allein in `modus: agent`, als Knoten in `hybrid` ab S15):
   ```yaml
   - id: triage
     agent:
       modell: llm
       auftrag: prompts/triage.md
       skills:
         - taschenmesser.posteingang: { aemter: ref://musterhausen/aemter.json }
         - taschenmesser.aktenplan:   { aktenplan: ref://musterhausen/aktenplan.json }
       grenzen: { max_loops: 5, timeout_s: 60 }
       ausgabe_schema: schemas/triage.json
     mit: { text: $.schutz.wert.text, dateien: $.schutz.wert.handles }
   ```
   Die im Paket gesetzten Argumente je Werkzeug sind feste Argumente dieses Schritts (zusätzlich zu denen aus dem Register).
2. `runner.agent.schleife`:
   - Werkzeugdefinitionen für das Modell aus `eingabe_schema` des Registers, ohne die festen Argumente;
   - Antwort mit `tool_calls` → je Aufruf: Werkzeug in der Liste? Argumente gegen Schema? feste Argumente setzen, Handles statt Dateien, Aufruf über das Register; Ergebnis (redigiert, mit Längengrenze) als Werkzeugnachricht im Datenblock-Format aus S12;
   - unbekanntes Werkzeug oder ungültige Argumente → Fehlermeldung an das Modell, zählt als Schleife; zweites Mal → `AN_MENSCH`/`WERKZEUG`;
   - gleicher Aufruf (Werkzeug + Argumente) zum dritten Mal → `AN_MENSCH`/`SCHLEIFE`;
   - Budgets: `max_loops`, `max_llm_aufrufe` (Paket), `timeout_s`, Tokenbudget → `AN_MENSCH`/`BUDGET`;
   - Abschluss: Modell antwortet ohne `tool_calls` → letzter Aufruf mit `json_schema` = Ausgabeschema; ungültig → `AN_MENSCH`/`SCHEMA`.
3. Fremdtext-Sperre: Sobald ein Werkzeug mit `liest_fremdtext: true` aufgerufen wurde, sind im selben Lauf keine Werkzeuge außer `read` mehr aufrufbar (im Agent ohnehin nur `read`; die Sperre gilt für den ganzen Lauf und wird in S15/S18 wirksam). Zustand im Laufkontext, im Audit vermerkt.
4. Structured-Output-Rückfall (`agent.rueckfall: aktion` oder automatisch, wenn das Modell kein Tool-Calling meldet): je Runde `json`-Aufruf mit Schema
   `{"oneOf": [{"werkzeug": enum, "argumente": {…}}, {"fertig": true, "ergebnis": <Ausgabeschema>}]}`.
5. Pilotpaket `usecases/posteingang/` zunächst als `modus: agent`, `nutzung: anwendung`: `schwaerzen → agent (triage) → gate [VAL-001, VAL-006, VAL-007, VAL-008]`; Mandantendaten `config/mandanten/musterhausen/aemter.json`, `aktenplan.json` (synthetisch); Goldset 40 Fälle; Drehbuch.
6. Audit-Vorbereitung: je Werkzeugaufruf ein inhaltsfreies Ereignis (`werkzeug`, `dauer_ms`, `status`) im Laufkontext; Persistenz in S14.

## Akzeptanzkriterien

- [ ] Drehbuch ruft ein Werkzeug außerhalb der Liste zweimal → `an_mensch`/`WERKZEUG`, das Werkzeug wurde nie aufgerufen.
- [ ] Modell setzt `aemter` auf eine andere Datei → der feste Wert wird verwendet, Metrik zählt.
- [ ] Drei gleiche Aufrufe → `an_mensch`/`SCHLEIFE`; `max_loops` überschritten → `an_mensch`/`BUDGET`.
- [ ] Rückfallmodus liefert mit einer Attrappe ohne Tool-Calling dasselbe Ergebnis wie der Tool-Calling-Modus für fünf Goldset-Fälle.
- [ ] `posteingang` besteht `uc eval` mit ≥ 0,9 gegen die Attrappe, wird signiert und ist für `musterhausen` aktiv.
- [ ] Integration über den Eingang: `POST /api/v1/skills/posteingang` mit einem Brief liefert Amt und Aktenplan mit `meta`, und das Attrappen-Protokoll enthält keinen Klartext (Prüfung wie S10).

## Nicht in dieser Session

`hybrid` (S15), Persistenz (S14).

## Prüfung

```
make check && make up && make test-int && make pakete
```
