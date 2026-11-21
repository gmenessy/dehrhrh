# S17 – RAG-Service

**Ziel:** Ein eigenständiger RAG-Service mit eigener Ingestion, hybrider Suche in Qdrant über gRPC, Relevanzprüfung, belegter Antwort mit Zitatprüfung und Enthaltung; erreichbar über REST und MCP; Pilot `auskunft`.

**Voraussetzungen:** S13 (S14 für den Worker-Datenbankzugriff)

**Kontext lesen:** Architektur 7.4, 6.3, 5.2–5.4 · Konzept: Abschnitt RAG-Service · Implementierung 1 (`qdrant-client`)

## Aufgaben

1. Paket `rag` (`dienste/rag`), Prozesse `rag-api` und `rag-worker`, Datenbank `rag` mit Migrationen `dienste/rag/sql/`: `raeume(mandant, raum, aktive_version, gemeinsam bool)`, `quellen(id, mandant, raum, s3_schluessel, sha256, titel, fassung_von, fassung_bis, status)`, `jobs(id, art, status, …)`; Zeilen-Sicherheit wie S14.
2. Ingestion (`rag-worker`, Job je Quelle):
   1. Quelle aus S3 `wissen-quellen/<mandant>/<raum>/…`;
   2. Text über `taschenmesser /v1/text` (Dienst-Token `tm.text`);
   3. Abschnitte entlang Überschriften und Paragraphen (`§`), Zielgröße 800 Token, Überlappung 100, Kontextzeile `Titel › Gliederung`;
   4. dense über `EMBEDDING_URL` (`/v1/embeddings`, BGE-M3), sparse als Term-Häufigkeiten über gehashte, kleingeschriebene Wortstämme (eigene Funktion), Qdrant-Sparse-Vektor mit `modifier: idf`;
   5. Upsert in Collection `wissen_<mandant>_<raum>_v<n>` über gRPC (`prefer_grpc=True`), Payload: `quelle_id`, `titel`, `gliederung`, `seite`, `fassung_von`, `fassung_bis`, `text`;
   6. Goldset des Raums gegen die neue Version; bestanden → Alias `wissen_<mandant>_<raum>` umstellen, sonst alte Version bleibt.
   Entscheidung „sparse ohne Zusatzmodell“ als ADR festhalten.
3. Suche: Qdrant Query API mit zwei Prefetches (dense, sparse) und RRF-Fusion, Filter auf Stichtag (`fassung_von ≤ stichtag ≤ fassung_bis`), `limit` 30; danach Relevanzprüfung je Treffer über das Decision Model (`guided_choice` ja/nein, Schwelle 0,7), höchstens 8 Treffer weiter.
4. Antwort: LLM mit Ausgabeschema `{aussagen: [{text, quelle_id, zitat}], enthaltung: bool, verweis?: string}`; jede Aussage: Zitat muss nach `kern.text` Teilstring des Abschnitts sein, sonst entfernt; keine Aussage übrig → `enthaltung: true` mit Verweis auf die zuständige Stelle aus den Raum-Metadaten.
5. API: `POST /v1/suchen`, `POST /v1/antworten` (Scope `wissen.lesen`), `POST /v1/raeume/{raum}/quellen`, `GET /v1/raeume` (Scope `wissen.admin`); MCP-Server unter `/mcp` mit Werkzeugen `wissen.suchen` und `wissen.antworten` (Streamable HTTP, Bearer mit Zielgruppe `rag`). Mandant aus dem Token; ein Raum eines anderen Mandanten ist nur bei `gemeinsam: true` lesbar.
6. Registereinträge `wissen.suchen`, `wissen.antworten` (Transport `mcp`, `liest_fremdtext: true`, `feste_argumente: [mandant, raum]`), Pins per `registry pin`.
7. `rag.eval`: Recall@30 und korrekte Enthaltung über `goldset.jsonl` je Raum (`frage`, `erwartete_quellen`, `ohne_antwort`); `make rag-eval`.
8. Pilot `usecases/auskunft/` (`modus: agent`, `nutzung: anwendung`): Werkzeuge `wissen.suchen`, `wissen.antworten` mit `raum: satzungen`; Ausgabe mit `x-zitat`; Gate [VAL-001, VAL-004, VAL-006]. Synthetische Satzungstexte (eigene Texte, keine fremden Werke) als Testbestand, Goldset 40 Fragen, davon 8 ohne Antwort.
9. Keycloak-Realm: Client `rag` (Client Credentials, Scope `tm.text`), Zielgruppe `rag` für Tokens des Runners mit Scope `wissen.lesen`.
10. Compose: `qdrant` (Netz `modell`, Volume), `rag-api` (Netze `modell`, `idp`), `rag-worker` (Netze `modell`, `intern`, `idp`).

## Akzeptanzkriterien

- [ ] Ingestion der Testsatzungen; Alias zeigt auf die neue Version erst nach bestandenem Goldset; fehlgeschlagenes Goldset lässt die alte Version aktiv.
- [ ] Recall@30 ≥ 0,85 und korrekte Enthaltung ≥ 0,95 auf dem Testbestand mit der Attrappe (Drehbuch für Relevanz und Antwort) – dokumentiert, dass die echte Messung gegen BGE-M3/Gemma im Betrieb erfolgt.
- [ ] Aussage mit nicht belegtem Zitat wird entfernt; ohne verbleibende Aussage `enthaltung: true`.
- [ ] Mandant `beispielstadt` kann Raum `satzungen` von `musterhausen` nicht lesen; gemeinsamer Raum ist lesbar.
- [ ] MCP: `tools/list` und `tools/call` über das SDK; Hash-Pin im Register passt.
- [ ] `auskunft` über `/api/v1/skills/auskunft` liefert belegte Antwort bzw. Enthaltung.

## Nicht in dieser Session

Reranker, Mehrsprachigkeit, Löschung einzelner Quellen über die API (manuell per Neuaufbau).

## Prüfung

```
make check && make up && make test-int && make rag-eval && make pakete
```
