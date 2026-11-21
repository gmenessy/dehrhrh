# S05 – Modellzugang und Modell-Attrappe

**Ziel:** Ein Modellclient im Runner, der nur redigierte Texte annimmt, Prioritäten und Digests beachtet und Decision-, JSON- und Tool-Calling-Aufrufe beherrscht; dazu eine Modell-Attrappe, mit der alle Tests ohne GPU deterministisch laufen.

**Voraussetzungen:** S02 (S03, S04 hilfreich, nicht nötig)

**Kontext lesen:** Architektur 4.2, 7.1 (`runner.modelle`), 7.2 · Implementierung 9.1

## Aufgaben

1. `werkzeuge/modell-attrappe` (FastAPI, Granian), Endpunkte `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/embeddings`, `GET /_protokoll`, `DELETE /_protokoll`:
   - Drehbücher aus `DREHBUCH_DIR/*.yaml` im Format aus Implementierung 9.1; Bedingungen `modell`, `enthaelt` (in der letzten Nutzernachricht), `system_enthaelt`, `schritt` (Anzahl bisheriger Assistenten-Nachrichten + 1), `auswahl` (bei `guided_choice`/`structured_outputs.choice`), `werkzeuge_enthalten`;
   - Antwortarten: `text`, `json` (bei `guided_json` gegen das Schema validiert, sonst 400), `tool_calls`, `wahl` mit `logprob` (liefert `logprobs.content[0].top_logprobs` für alle Optionen; die übrigen Optionen erhalten `logprob - 3`), `fehler` (HTTP-Code), `verzoegerung_ms`;
   - beide Parameterformen von vLLM: `guided_choice`/`guided_json` und `structured_outputs`;
   - Embeddings deterministisch aus SHA-256 (dense 1024 Dimensionen, L2-normiert);
   - `usage` mit Tokenzählung (Wörter × 1,3, gerundet);
   - Compose-Profil `attrappe` mit zwei Instanzen `llm` (8100) und `decision` (8101) im Netz `modell`, plus `embedding` (8102).
2. `runner.zonen.typen`: `Klartext(str)`, `Redigiert(str)`, `Handle` (`dok:<n>`). Nur diese Typen, die weitere Zonenlogik folgt in S10.
3. `runner.modelle.client.ModellClient`:
   - `chat(nachrichten: list[Nachricht], *, prioritaet, werkzeuge=None, json_schema=None) -> ModellAntwort`; `Nachricht.inhalt` ist `Redigiert`; zur Laufzeit wird jeder andere `str` mit `ZONENVERLETZUNG` abgelehnt (Typprüfung **und** `isinstance`-Prüfung);
   - `waehle(frage: Redigiert, optionen: list[str], *, prioritaet) -> Wahl(option, konfidenz)` mit Logprobs: Konfidenz = Softmax über die `top_logprobs` der Optionen;
   - `json(...)`: Antwort mit `jsonschema` prüfen, bei Fehler ein zweiter Versuch, dann `MODELL_FEHLER`;
   - Parameterform (`guided` oder `structured_outputs`) per Konfiguration (`VLLM_STRUCTURED`, Bestand Taschenmesser).
4. Prioritäten `interaktiv`, `vorgang`, `batch`: gemeinsame Kapazität `MODELL_SLOTS` (Standard 16); `batch` höchstens ein Viertel, `vorgang` höchstens drei Viertel, `interaktiv` alle. Warten mit Zeitgrenze, danach `KONTINGENT`.
5. Digest: Konfiguration `MODELL_*_NAME` und `MODELL_*_DIGEST`; beim Start `GET /v1/models` – Name muss vorhanden sein; liefert der Server ein Feld `digest`, muss es übereinstimmen. Sonst ist `/ready` falsch und jeder Aufruf `MODELL_FEHLER`. Digest wird in jede `ModellAntwort` übernommen (für `meta` und Audit).
6. Metriken `plattform_modell_tokens_total{modell,richtung,prioritaet}`, `plattform_modell_dauer_sekunden{modell}`; Trace-Span je Aufruf mit `gen_ai.request.model` und `gen_ai.usage.*`, ohne Inhalte.
7. Drehbuch-Bibliothek `werkzeuge/modell-attrappe/drehbuecher/basis.yaml` mit Fällen für die Tests dieser Session.

## Akzeptanzkriterien

- [ ] Attrappe: Tests je Antwortart und je Bedingung; `/_protokoll` speichert Anfragen in Reihenfolge.
- [ ] `ModellClient.chat` mit einem gewöhnlichen `str` oder `Klartext` → `ZONENVERLETZUNG`, ohne dass eine Anfrage die Attrappe erreicht (Protokoll leer).
- [ ] `waehle`: bei `logprob -0.05` für „ja“ ist die Konfidenz > 0,9; die Summe über Optionen ist 1.
- [ ] `json`: zweimal ungültiges JSON → `MODELL_FEHLER`, genau zwei Anfragen im Protokoll.
- [ ] Prioritäten: Bei `MODELL_SLOTS=4` und vier wartenden `batch`-Aufrufen (Attrappe mit Verzögerung) wird ein `interaktiv`-Aufruf ohne Warten auf die Batch-Aufrufe bedient.
- [ ] Digest-Abweichung → `/ready` 503, Aufruf → `MODELL_FEHLER`.
- [ ] `mypy --strict` für `runner.zonen` grün.

## Nicht in dieser Session

Modell-Proxy-Route (S09), Decision-Verträge als Dateien (S11), Tool-Calling-Schleife (S12).

## Prüfung

```
make check
```
