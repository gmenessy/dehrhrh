# S19 – Zugänge: OpenAI-API, MCP, Java-Client

**Ziel:** Freigegebene `read`-Pakete sind ohne Umbau aus Chat-Oberflächen (OpenAI-kompatibel), aus fremden Agenten (MCP) und aus Fachverfahren in Java (aktuelle LTS) nutzbar – mit denselben Gates und Rechten wie über `/api/v1`.

**Voraussetzungen:** S17 (und S13 für Pakete mit `nutzung: anwendung`)

**Kontext lesen:** Architektur 9.1 (außerhalb `/api/v1`), ADR-009, ADR-014 · Konzept: Zwei Nutzungsarten, Integration · MCP-Spezifikation 2026-07-28 (Authorization, Security Best Practices)

## Aufgaben

1. OpenAI-kompatibel unter `/openai/v1`:
   - `GET /models`: aktive Pakete des Mandanten mit `chat.eingabe_feld` als `uc/<id>`;
   - `POST /chat/completions`: letzte Nutzernachricht → Eingabefeld des Pakets; frühere Nachrichten werden ignoriert (im README dokumentiert); synchroner Lauf wie `/skills/{id}`; Antworttext = Ergebnis als Markdown mit Fundstellen; Erweiterungsfeld `x_plattform: {meta, status}`; `stream: true` liefert das fertige Ergebnis erst nach dem Gate in einem Chunk plus `[DONE]` (nie vorher); `an_mensch` → Antworttext mit Hinweis und `finish_reason: "stop"`; Gate-Verstoß → HTTP 422 im OpenAI-Fehlerformat mit `code`.
   - Nicht unterstützt (400 mit klarer Meldung): `tools`, `functions`, `response_format` vom Client, Bilder.
2. MCP-Server im Runner unter `/mcp` (offizielles SDK, Streamable HTTP):
   - Werkzeuge = aktive Pakete mit `nutzung: anwendung` und ohne Dateieingaben; Name `uc_<id>`, Beschreibung aus dem Paket, `inputSchema` = Eingabeschema, Annotation `readOnlyHint: true`;
   - Autorisierung: Bearer-Token mit Zielgruppe der Plattform, Scope `uc.ausfuehren`; `GET /.well-known/oauth-protected-resource` (RFC 9728) mit Issuer; kein Durchreichen fremder Tokens;
   - Werkzeugaufruf läuft durch dieselbe Ausführung wie `/skills/{id}`; Ergebnis als `structuredContent` plus Textfassung.
3. Java-Beispiel `werkzeuge/java-client` (Java 25, ohne Build-Werkzeug, Single-File-Programm `PlattformClient.java`):
   - Token per Client Credentials, Aufruf `POST /api/v1/skills/frist`, Ausgabe der Frist; `java.net.http.HttpClient` mit `Version.HTTP_2`, Timeouts, Fehlerausgabe mit `fehler.code`;
   - `make java-beispiel` startet es in `eclipse-temurin:25-jdk` gegen die Compose-Umgebung;
   - README mit Hinweis: jeweils aktuelle LTS als Mindestversion.
4. OpenAPI vervollständigen (`contracts/openapi/runner.json`, `rag.json`, `taschenmesser.json`) und in der CI auf Aktualität prüfen.
5. Quoten und Metriken je Zugang (`zugang=api|openai|mcp`).

## Akzeptanzkriterien

- [ ] `httpx`-Test im OpenAI-Format: `model: uc/auskunft` liefert belegte Antwort; `uc/gewerbe` (nur `vorgang`) erscheint nicht in `/models` und liefert 404.
- [ ] Streaming-Test: Bei einem Gate-Verstoß wird kein einziger Inhalts-Chunk gesendet.
- [ ] MCP-Test mit SDK-Client: `list_tools` zeigt `uc_frist`, `uc_auskunft`, `uc_posteingang` nicht (Dateieingabe); `call_tool` liefert Ergebnis; Token mit falscher Zielgruppe → 401.
- [ ] `/.well-known/oauth-protected-resource` liefert gültiges JSON mit `resource` und `authorization_servers`.
- [ ] `make java-beispiel` gibt die erwartete Frist aus; die Verbindung nutzt HTTP/2 (Ausgabe der ausgehandelten Version).

## Nicht in dieser Session

Mehrturnige Chats mit Gedächtnis, Datei-Uploads über MCP.

## Prüfung

```
make check && make up && make test-int && make java-beispiel
```
