# CLAUDE.md – Arbeitsregeln für Claude Code

Du implementierst den MVP der Use-Case-Plattform in 20 Sessions. Jede Session ist in sich abgeschlossen und endet mit grünem `make check` und einem Commit.

## Vor jeder Session lesen

1. `docs/fortschritt.md` – was ist fertig, was ist offen
2. `sessions/Sxx-*.md` – die aktuelle Session
3. die in der Session unter „Kontext“ genannten Abschnitte aus `docs/01-architektur.md` und `docs/02-implementierung.md`
4. `docs/entscheidungen.md` – Abweichungen aus früheren Sessions

Lies nicht alles auf einmal; lies, was die Session nennt.

## Regeln

- **Umfang:** Nur was die Session verlangt. Was eine spätere Session baut, nicht vorwegnehmen. Fehlt etwas aus einer früheren Session, zuerst das ergänzen und im Fortschritt vermerken.
- **Sicherheit vor Bequemlichkeit:** Die Leitregeln (Architektur, Abschnitt 2) und die Sicherheitsregeln (Implementierung, Abschnitt 8) werden nie abgeschwächt, auch nicht vorübergehend oder „nur für Tests“ im Produktivcode.
- **Abhängigkeiten:** Nur die Liste in Implementierung 1.1. Alles andere braucht ein ADR in `docs/entscheidungen.md`.
- **Unklarheiten:** Nicht nachfragen, sondern die einfachste Lösung wählen, die alle Regeln erfüllt, und sie als ADR festhalten.
- **Tests:** Akzeptanzkriterien zuerst als Tests. Kein Test ruft echte Modelle; nutze `werkzeuge/modell-attrappe`. Bestehende Tests nur ändern, wenn der Test nachweislich falsch ist (im Commit begründen).
- **Keine Inhalte in Logs:** keine Prompts, Dokumente, Modellausgaben, Tokens.
- **Kein toter Code, keine Platzhalter.** Was nicht fertig wird, steht unter „Offene Punkte“ im Fortschritt.
- **Sprache:** Fachbegriffe deutsch, Bezeichner ASCII (`ae`, `oe`, `ue`), Kommentare deutsch und knapp.

## Ende jeder Session

1. `make check` grün; zusätzlich die in der Session genannten `make test-int` / `make e2e`.
2. `docs/fortschritt.md`: Status auf „fertig“, Datum, Abweichungen (ADR-Nummern), offene Punkte.
3. README des betroffenen Dienstes um neue Endpunkte und Variablen ergänzen.
4. Ein Commit: `Sxx: <Ziel der Session>` mit Stichpunkten.

## Befehle

```
make setup      # einmalig
make check      # lint, typ, test – vor jedem Commit
make up / down  # Compose mit Dev-Profil
make test-int   # Integration
make e2e        # Ende-zu-Ende
```
