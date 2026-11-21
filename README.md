# MVP-Kit: Use-Case-Plattform

Startpaket, mit dem Claude Code den MVP der Use-Case-Plattform in 20 Sessions umsetzt.

| Datei | Inhalt |
| --- | --- |
| `CLAUDE.md` | Arbeitsregeln für Claude Code (wird automatisch gelesen) |
| `docs/01-architektur.md` | Was gebaut wird: Komponenten, Zonen, Netze, Datenflüsse, Datenmodell, Verträge, ADRs |
| `docs/02-implementierung.md` | Wie gebaut wird: Technik, Struktur, Konventionen, Tests, Sicherheit im Code |
| `docs/fortschritt.md` | Stand je Session, offene Punkte |
| `docs/entscheidungen.md` | Abweichungen während der Umsetzung (ADR-100 ff.) |
| `sessions/S01 … S20` | je Session: Ziel, Voraussetzungen, Aufgaben, Akzeptanzkriterien |
| `vorhandenes/taschenmesser/` | bestehender Dienst (1.4.0, 82 Tests), wird in S04 übernommen |

## Benutzung

1. Neues Git-Repository anlegen, den Inhalt dieses Kits hineinkopieren, committen.
2. Claude Code im Repository starten.
3. Je Session eine neue Claude-Code-Sitzung mit diesem Auftrag:

   > Führe Session S01 aus (`sessions/S01-repo-und-werkzeuge.md`). Halte dich an CLAUDE.md.

4. Nach jeder Session: Ergebnis prüfen (Diff, `make check`, bei Bedarf `make e2e`), Branch mergen, nächste Session.

Die Sessions bauen aufeinander auf; die Reihenfolge ist verbindlich. Abhängigkeiten stehen in jeder Session unter „Voraussetzungen“.

## Reihenfolge

| Block | Sessions | Ergebnis |
| --- | --- | --- |
| Fundament | S01–S03 | Repo, Kernbibliothek, Identität |
| Bausteine | S04–S06 | Taschenmesser gehärtet, Modellzugang, Werkzeugregister |
| Pakete | S07–S08 | Paketformat, Linter, Plan, Signatur |
| In der Anwendung | S09–S13 | Runner-API, Zonen, Ausgangs-Gate, Stufenregel, Agent |
| Ende-zu-Ende | S14–S18 | Läufe, Kette/Hybrid, Freigaben, RAG, Zusteller |
| Abschluss | S19–S20 | Zugänge, Härtung, Abnahme |

Nach S13 ist die Nutzung **in der Anwendung** vorführbar, nach S18 **Ende-zu-Ende**.

## Voraussetzungen der Umgebung

Docker mit Compose v2, `uv`, Python 3.13, GNU Make. Für echte Modelle ein GPU-Host mit vLLM (nicht nötig für Tests: die Modell-Attrappe ersetzt ihn).
