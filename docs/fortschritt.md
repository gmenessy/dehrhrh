# Fortschritt

| Session | Titel | Status | Datum | ADRs | Bemerkung |
| --- | --- | --- | --- | --- | --- |
| S01 | Repository und Werkzeugkette | fertig mit offenen Punkten | 2026-11-22 | ADR-100, ADR-101, ADR-102, ADR-103 | MinIO-Integrationstest offen (ADR-102) |
| S02 | Kernbibliothek | offen | | | |
| S03 | Identität und Zugriff | offen | | | |
| S04 | Taschenmesser übernehmen und härten | offen | | | |
| S05 | Modellzugang und Modell-Attrappe | offen | | | |
| S06 | Werkzeugregister | offen | | | |
| S07 | Paketformat, Linter, Plan-Compiler | offen | | | |
| S08 | Signierung und Laufbedingung | offen | | | |
| S09 | Runner-API und Modus skill | offen | | | |
| S10 | Zonen: Schwärzen, Platzhalter, Verschlüsselung | offen | | | |
| S11 | Ausgangs-Gate und Goldset-Runner | offen | | | |
| S12 | Stufenregel: Regel, Decision, LLM | offen | | | |
| S13 | Modus agent | offen | | | |
| S14 | Persistenz, Läufe, Audit-Kette | offen | | | |
| S15 | Modi kette und hybrid, Wartezustände | offen | | | |
| S16 | Freigaben und Freigabeliste | offen | | | |
| S17 | RAG-Service | offen | | | |
| S18 | Outbox und Zusteller | offen | | | |
| S19 | Zugänge: OpenAI-API, MCP, Java-Client | offen | | | |
| S20 | Härtung, Sicherheitstests, Abnahme | offen | | | |

Status: `offen`, `in Arbeit`, `fertig`, `fertig mit offenen Punkten`.

## Offene Punkte

(Session, Punkt, Grund, vorgesehen in)

| Session | Punkt | Grund | vorgesehen in |
| --- | --- | --- | --- |
| S01 | MinIO-Teil von `tests/integration/test_infrastruktur.py` (Buckets, Lifecycle, Versionierung) nicht ausgeführt; Datenbankteil grün | Images `minio/minio`, `minio/mc` auf Docker Hub nicht mehr verfügbar, Ersatz ist Betreiberentscheidung (ADR-102) | vor S09 (erste S3-Nutzung) |
