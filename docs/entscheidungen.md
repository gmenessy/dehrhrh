# Entscheidungen während der Umsetzung

Grundsatzentscheidungen ADR-001 bis ADR-014 stehen in `01-architektur.md`, Abschnitt 11. Hier werden Abweichungen und Präzisierungen aus den Sessions festgehalten, fortlaufend ab ADR-100.

## Vorlage

```
### ADR-1NN: <Titel>
Session: Sxx · Datum: JJJJ-MM-TT
Kontext: <was war unklar oder passte nicht>
Entscheidung: <was gilt jetzt>
Folgen: <was ändert sich, was ist zu beachten>
```

## Einträge

### ADR-100: GitHub Actions statt GitLab-CI
Session: S01 · Datum: 2026-11-22
Kontext: S01 verlangt `.gitlab-ci.yml`; das Repository liegt auf GitHub.
Entscheidung: CI in `.github/workflows/ci.yml`. Job `check` (`make check`) bei jedem Push und Pull Request; Job `test-int` (`make setup`, `make test-int`, `make down`) manuell über `workflow_dispatch`. Actions per Commit-SHA gepinnt, Rechte `contents: read`, ohne gespeicherte Zugangsdaten im Checkout.
Folgen: `.gitlab-ci.yml` entfällt. Spätere Sessions ergänzen Jobs in diesem Workflow.

### ADR-101: Branchname aus der Ausführungsumgebung
Session: S01 · Datum: 2026-11-22
Kontext: Implementierung 11 verlangt `session/Sxx-kurzname`; die Cloud-Umgebung gibt den Branch vor und erlaubt nur diesen.
Entscheidung: Gearbeitet wird auf dem vorgegebenen Branch; Commit-Titel bleibt `Sxx: <Ziel>`.
Folgen: Sessions sind am Commit-Titel erkennbar, nicht am Branchnamen.

### ADR-102: Dev-Netz `dev_host` für Integrationstests
Session: S01 · Datum: 2026-11-22
Kontext: `postgres` und `minio` hängen nur an `intern` (`internal: true`); Docker veröffentlicht für solche Container keine Ports. Integrationstests laufen auf dem Host.
Entscheidung: Nur `compose.dev.yaml` fügt das Netz `dev_host` (nicht intern) hinzu und veröffentlicht 5432 und 9000 auf `${DEV_BIND:-127.0.0.1}`. Tests lesen den Host aus `PLATTFORM_TEST_HOST` (Standard `127.0.0.1`; auf dem GitHub-Runner ebenso).
Folgen: Die sieben Netze aus Architektur 5.2 bleiben unverändert; `dev_host` existiert nie außerhalb von Dev. Die Netzmatrix (S20) prüft gegen die Basisdatei.

### ADR-103: MinIO-Images aus Community-Build
Session: S01 · Datum: 2026-11-22
Kontext: `minio/minio` und `minio/mc` liefern auf Docker Hub 404; die Images aus S01 sind öffentlich nicht mehr beziehbar.
Entscheidung (vom Auftraggeber am 2026-11-22 getroffen): Dev und Tests nutzen den gepflegten Community-Build `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` und `pgsty/mc:RELEASE.2026-11-12T00-00-00Z`, jeweils per Digest gepinnt. Überschreibbar über `MINIO_IMAGE` und `MC_IMAGE` (z. B. interner Spiegel).
Folgen: Nur Dev/Test; im Betrieb ist S3 ein RZ-Dienst. Updates nur bewusst mit neuem Digest. Der Build läuft gehärtet (Benutzer 10001, read-only, ohne Capabilities).

### ADR-104: Härtung von PostgreSQL und MinIO mit Benutzer 10001
Session: S01 · Datum: 2026-11-22
Kontext: `x-hart` setzt `read_only` und Benutzer 10001; die offiziellen Images erwarten eigene Benutzer und beschreibbare Verzeichnisse.
Entscheidung: PostgreSQL mit `PGDATA=/var/lib/postgresql/data/pgdata` (Unterverzeichnis im Volume, gehört 10001) und `tmpfs` für Socket und `/tmp`. MinIO legt in Dev seine Daten auf `tmpfs` (`uid=10001`); Buckets entstehen bei jedem `make up` neu durch `minio-init`. Geheimnisse als Dateien über Compose-Secrets, erzeugt von `make setup` in `config/schluessel/` (nicht im Git).
Folgen: Dev-Objekte überleben `make down` nicht; Datenbankdaten schon (Volume `postgres-daten`).
