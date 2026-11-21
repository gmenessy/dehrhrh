# S07 – Paketformat, Linter und Plan-Compiler

**Ziel:** Use-Case-Pakete haben ein festes Schema, ein Linter setzt die Sicherheitsregeln vor der Laufzeit durch, und der Compiler erzeugt daraus einen unveränderlichen Plan mit stabilem `plan_hash`.

**Voraussetzungen:** S06

**Kontext lesen:** Architektur 9.3, 2 · Konzept: Abschnitt Use Cases (Modi, Beispiel) · Gesamtspezifikation 4.3 (Beispiel)

## Aufgaben

1. `contracts/paket.schema.json` für `process.yaml`:
   - Kopf: `id` (`^[a-z][a-z0-9-]{2,40}$`), `version` (SemVer), `modus` (`skill`, `kette`, `agent`, `hybrid`), `verantwortlich`, `reife` (`R0`, `R1`), `nutzung` (Teilmenge von `anwendung`, `vorgang`), `beschreibung`, `budget` (`max_schritte`, `max_llm_aufrufe`, `timeout_s`, optional `max_tokens`), `eingabe_schema`, `ausgabe_schema` (Pfade auf `schemas/`), optional `chat: {eingabe_feld}`;
   - Schrittarten: `skill` (Werkzeugaufruf), `regel` (Stufe 0, `regeln.tabelle`), `entscheidung` (Stufe 1, Vertrag), `llm` (Stufe 2, Prompt, Ausgabeschema), `agent`, `gate`, `schwaerzen`, `freigabe`, `wirkung`, `verzweigung`, `fuer_jede`, `warten`;
   - jeder Schritt: `id`, Art-spezifische Felder, `mit` (Argumente mit Pfadausdrücken).
2. `runner.paket.pfad`: Pfadausdrücke `$.eingabe.…`, `$.<schritt>.wert.…`, `$p.<param>`, Listenindex `[n]`; eigener Parser ohne `eval`; `ref://<mandant>/<datei>` gegen `config/mandanten/<mandant>/` (Pfad normalisieren, Präfixprüfung).
3. `runner.paket.linter` mit Regelcodes, jede mit eigener Meldung:
   - L001 nur erlaubte Dateitypen (`.yaml`, `.json`, `.jsonl`, `.md`), keine ausführbaren Bits, keine Symlinks;
   - L002 jedes Werkzeug existiert im Register; L003 Stufe des Schritts ist in `stufen` des Werkzeugs erlaubt;
   - L004 `agent`-Schritte (auch in `hybrid`) nutzen nur `read`-Werkzeuge;
   - L005 `wirkung` nur in `kette`/`hybrid`, nur `reversible_write` im MVP, und auf **jedem** Pfad liegt vorher ein `freigabe`-Schritt;
   - L006 `nutzung: anwendung` verbietet `wirkung`, `freigabe`, `warten`;
   - L007 Bedingungen in `verzweigung` lesen nur Ergebnisse von `regel`-, `entscheidung`- oder `skill`-Schritten mit Stufe 0/1, nie `llm`/`agent`;
   - L008 vor jedem `llm`- oder `agent`-Schritt steht ein `schwaerzen`-Schritt, dessen Ausgabe die Texteingaben speist (Datenfluss über Pfadausdrücke);
   - L009 nach dem letzten `llm`/`agent`-Schritt und vor Ausgabe oder `freigabe` steht ein `gate`;
   - L010 `budget` Pflicht bei `agent`/`hybrid`; Obergrenzen (`max_schritte` ≤ 50, `max_llm_aufrufe` ≤ 20, `timeout_s` ≤ 600);
   - L011 Prompts enthalten keine URLs und nur Platzhalter `{{name}}` aus deklarierten Eingaben;
   - L012 Ausgabeschemas haben überall `additionalProperties: false`;
   - L013 `modus: skill` hat genau einen `skill`-Schritt (plus optional `schwaerzen` und `gate`);
   - L014 Pfadausdrücke verweisen nur auf frühere Schritte; keine Zyklen.
4. `runner.paket.plan`: `Plan` (Pydantic, `frozen=True`) mit aufgelösten Werkzeugen (inkl. Hash-Pin zum Compile-Zeitpunkt), Schritten, Schemas (eingebettet), Prompts, Regeltabellen und `ref://`-Daten (eingebettet mit Hash); `plan_hash = sha256_praefix(kanonisch(plan_ohne_hash))`.
5. CLI `uc neu <id> --modus <m>` (Gerüst, das den Linter besteht), `uc lint <pfad>`, `uc plan <pfad> [--json]`.
6. Testpakete in `dienste/runner/tests/pakete/`: ein gültiges Paket je Modus, für jede Linterregel ein Paket, das genau diese Regel verletzt.

## Akzeptanzkriterien

- [ ] Für jede Regel L001–L014 ein Test mit genau dieser Meldung.
- [ ] Das Beispielpaket aus dem Konzept (Modus `hybrid`, angepasst auf vorhandene Werkzeuge) besteht den Linter.
- [ ] `plan_hash` ist über Läufe und unabhängig von der Schlüsselreihenfolge in YAML stabil; jede Änderung an irgendeiner Paketdatei oder an einer referenzierten `ref://`-Datei ändert ihn.
- [ ] `ref://musterhausen/../../etc/passwd` wird abgelehnt.
- [ ] `uc neu demo --modus agent` erzeugt ein Paket, das `uc lint` besteht.
- [ ] `mypy --strict` für `runner.paket` grün, Abdeckung ≥ 85 %.

## Nicht in dieser Session

Signatur (S08), Ausführung (ab S09).

## Prüfung

```
make check
```
