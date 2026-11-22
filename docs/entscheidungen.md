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

### ADR-100: Branchname aus der Ausführungsumgebung
Session: S01 · Datum: 2026-11-22
Kontext: Implementierung 11 verlangt `session/Sxx-kurzname`; die Cloud-Umgebung gibt den Branch vor und erlaubt nur diesen.
Entscheidung: Gearbeitet wird auf dem vorgegebenen Branch; Commit-Titel bleibt `Sxx: <Ziel>`.
Folgen: Sessions sind am Commit-Titel erkennbar, nicht am Branchnamen.

### ADR-101: Dev-Netz `dev_host` für Integrationstests
Session: S01 · Datum: 2026-11-22
Kontext: `postgres` und `minio` hängen nur an `intern` (`internal: true`); Docker veröffentlicht für solche Container keine Ports. Integrationstests laufen auf dem Host.
Entscheidung: Nur `compose.dev.yaml` fügt das Netz `dev_host` (nicht intern) hinzu und veröffentlicht 5432 und 9000 auf `${DEV_BIND:-127.0.0.1}`. Tests lesen den Host aus `PLATTFORM_TEST_HOST` (Standard `127.0.0.1`; in CI mit Docker-in-Docker `docker`).
Folgen: Die sieben Netze aus Architektur 5.2 bleiben unverändert; `dev_host` existiert nie außerhalb von Dev. Die Netzmatrix (S20) prüft gegen die Basisdatei.

### ADR-102: MinIO-Images nicht mehr auf Docker Hub
Session: S01 · Datum: 2026-11-22
Kontext: `minio/minio` und `minio/mc` liefern auf Docker Hub 404; die Images aus S01 sind öffentlich nicht mehr beziehbar.
Entscheidung: Compose nutzt `${MINIO_IMAGE:-minio/minio:RELEASE.2025-09-07T16-13-09Z}` und `${MC_IMAGE:-minio/mc:RELEASE.2025-08-13T08-35-41Z}` (letzte offizielle Releases, z. B. aus einem internen Spiegel). Die Wahl eines Ersatzes (eigener Build aus Quellcode oder ein gepflegter Community-Build) ist eine Lieferkettenentscheidung des Betreibers und wurde nicht eigenmächtig getroffen.
Folgen: Der MinIO-Teil von `tests/integration/test_infrastruktur.py` lief in S01 nicht (offener Punkt). Bis zur Entscheidung `MINIO_IMAGE`/`MC_IMAGE` auf ein verfügbares Image setzen.

### ADR-103: Härtung von PostgreSQL und MinIO mit Benutzer 10001
Session: S01 · Datum: 2026-11-22
Kontext: `x-hart` setzt `read_only` und Benutzer 10001; die offiziellen Images erwarten eigene Benutzer und beschreibbare Verzeichnisse.
Entscheidung: PostgreSQL mit `PGDATA=/var/lib/postgresql/data/pgdata` (Unterverzeichnis im Volume, gehört 10001) und `tmpfs` für Socket und `/tmp`. MinIO legt in Dev seine Daten auf `tmpfs` (`uid=10001`); Buckets entstehen bei jedem `make up` neu durch `minio-init`. Geheimnisse als Dateien über Compose-Secrets, erzeugt von `make setup` in `config/schluessel/` (nicht im Git).
Folgen: Dev-Objekte überleben `make down` nicht; Datenbankdaten schon (Volume `postgres-daten`).
