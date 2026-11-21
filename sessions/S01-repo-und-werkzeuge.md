# S01 – Repository und Werkzeugkette

**Ziel:** Ein lauffähiges Monorepo mit uv-Workspace, Make-Zielen, Lint, Typprüfung, Tests, Compose-Gerüst mit allen Netzen sowie PostgreSQL und MinIO.

**Voraussetzungen:** keine

**Kontext lesen:** Implementierung 1, 2, 3, 10, 11 · Architektur 5.2, 5.3, 8.1

## Aufgaben

1. Wurzel-`pyproject.toml` als uv-Workspace mit den Mitgliedern `pakete/kern`, `dienste/runner`, `dienste/taschenmesser`, `dienste/rag`, `werkzeuge/modell-attrappe`, `werkzeuge/dms-attrappe`. Jedes Mitglied mit `src/<paket>/__init__.py` (`__version__ = "0.1.0"`), `tests/unit/test_import.py`, eigenem `pyproject.toml`. Paketnamen: `kern`, `runner`, `taschenmesser`, `rag`, `modell_attrappe`, `dms_attrappe`. `dienste/taschenmesser` bleibt in S01 leer bis auf das Gerüst; der Bestand kommt in S04.
2. Gemeinsame Werkzeugkonfiguration im Wurzel-`pyproject.toml`: `ruff` (Regeln laut Implementierung 8, Zeilenlänge 120), `mypy` (strikt für `kern`; weitere Pakete folgen in ihren Sessions), `pytest` (`asyncio_mode = "auto"`, Marker `integration`, `e2e`, `sicherheit`; Standardlauf schließt diese aus).
3. `uv lock` mit den Laufzeitabhängigkeiten aus Implementierung 1.1 (nur die, die ein Mitglied schon braucht; die anderen kommen mit ihrer Session) und den Testwerkzeugen.
4. `Makefile` mit `setup`, `lint`, `typ`, `test`, `check`, `up`, `down`, `test-int`. Weitere Ziele aus Implementierung 10 kommen in ihren Sessions dazu.
5. `deploy/compose.yaml` und `deploy/compose.dev.yaml`:
   - alle Netze aus Architektur 5.2 mit dem jeweiligen `internal`-Wert (`aus_zusteller` in Dev `internal: true`);
   - Anker `x-hart` (read_only, cap_drop ALL, no-new-privileges, user 10001) wie im Taschenmesser-Bestand;
   - `postgres` (17, Volume, Healthcheck, Init-Skript legt Datenbanken `plattform` und `rag` an);
   - `minio` (Dev-Profil) und ein Init-Container `minio-init` (Image `minio/mc`), der die Buckets aus Architektur 8.1 mit Lifecycle (24 h, 7 Tage) und Versionierung für `wissen-quellen` anlegt.
6. `deploy/Dockerfile.python`: Mehrstufiges Muster für alle Python-Dienste (Build mit uv, Laufzeit `python:3.13-slim`, Benutzer 10001, kein Compiler im Laufzeit-Image). Die Dienste nutzen es ab ihren Sessions.
7. `.gitignore` (inkl. `config/schluessel/*` außer `*.pub.dev`, `deploy/pki/out/`), `.editorconfig`, `.gitlab-ci.yml` mit `make check` und einem manuellen Job `make test-int` (wenn ein anderes CI-System genutzt wird: ADR).
8. `docs/fortschritt.md` aktualisieren.

## Akzeptanzkriterien

- [ ] Auf einem frischen Checkout laufen `make setup` und `make check` fehlerfrei.
- [ ] `docker compose -f deploy/compose.yaml -f deploy/compose.dev.yaml config` ist gültig.
- [ ] Test `tests/unit/test_compose.py` (im Wurzel-`tests/`): parst die Compose-Dateien und prüft, dass alle sieben Netze existieren und `internal` wie in Architektur 5.2 gesetzt ist.
- [ ] `make up` startet `postgres` und `minio` gesund; Integrationstest `tests/integration/test_infrastruktur.py` prüft: beide Datenbanken existieren, alle vier Buckets existieren, `laufdaten` hat eine Lifecycle-Regel.
- [ ] `ruff` meldet keine Fehler; `mypy` für `kern` ist strikt und grün.

## Nicht in dieser Session

Keine Fachlogik, keine Dienst-Images außer der Vorlage, kein Keycloak (S03), keine TLS-Zertifikate (S20).

## Prüfung

```
make setup && make check && make up && make test-int && make down
```
