# S08 – Signierung und Laufbedingung

**Ziel:** Nur signierte, unveränderte Pakete mit passenden Register-Pins werden geladen; Reifegrad R1 verlangt eine freigegebene KI-VO-Einstufung.

**Voraussetzungen:** S07

**Kontext lesen:** Architektur 9.5, ADR-004 · Gesamtspezifikation 5.2 · Konzept: Reifegrade

## Aufgaben

1. `runner.paket.signatur`:
   - Manifest nach Architektur 9.5: `payload_sha256` = Hash von `kanonisch(process.yaml geparst, ohne signatur)`, `dateien_sha256` für alle Dateien außer `signatur.yaml` (Pfade relativ, sortiert), `register_pin` (für alle im Plan genutzten Werkzeuge der aktuelle `beschreibung_hash`), `evidenz` aus einer übergebenen `eval.json` (Felder `linter_ok`, `goldset_sha256`, `eval_pass_rate`, `modell_digest`, `review_freigabe_ids`), `plan_hash`;
   - Signatur Ed25519 über `kanonisch(manifest ohne wert)`, `wert` Base64;
   - Prüfung: alle Hashes neu berechnen, Signatur mit öffentlichem Schlüssel `SIGNATUR_SCHLUESSEL_DIR/<key_id>.pub` (PEM), `register_pin` gegen das aktuell geladene Register.
2. `runner.paket.einstufung`: `einstufung.yaml` im Paket mit `risikoklasse` (`minimal`, `begrenzt`, `hoch`), `begruendung`, `freigegeben_von`, `freigegeben_am`, `dsfa_delta_plan_hash`. Laufbedingung: R0 immer; R1 nur mit Freigabe und `dsfa_delta_plan_hash == plan_hash`; `hoch` wird im MVP nie geladen.
3. `runner.paket.lader`: lädt alle Pakete aus `PAKETE_DIR/<id>/<version>/`, je Mandant aktiviert nach `config/mandanten.yaml`:
   ```yaml
   musterhausen:
     pakete:
       frist: { version: 1.0.0, reife: R0 }
   ```
   Die Reife aus `mandanten.yaml` darf die Reife im Paket nicht übersteigen. Ein ungültiges Paket wird übersprungen (Log, Metrik `plattform_paket_abgelehnt{grund}`), die übrigen laden. `/ready` meldet Anzahl geladener und abgelehnter Pakete.
4. CLI: `uc sign <pfad> --schluessel <datei> --key-id <id> --eval <eval.json>`, `uc verify <pfad>`, `uc pack <pfad>` (tar.gz für den Upload in S09).
5. `make setup` erzeugt Dev-Schlüssel `config/schluessel/dev.key` (privat, ignoriert) und `config/schluessel/dev.pub.dev`; der Runner nutzt in `dev` dieses Verzeichnis.
6. Schlüsselrotation: mehrere `.pub` gleichzeitig; Test mit zwei Schlüsseln.

## Akzeptanzkriterien

- [ ] Änderung eines Bytes in `process.yaml`, einer Schema-, Regel-, Prompt- oder Goldset-Datei → `SIGNATUR_UNGUELTIG` mit Nennung der Datei.
- [ ] Neue Datei im Paket, die nicht im Manifest steht → abgelehnt.
- [ ] Unbekannte `key_id` → abgelehnt; zweiter gültiger Schlüssel → angenommen.
- [ ] Geänderter `beschreibung_hash` eines genutzten Werkzeugs im Register → Paket wird nicht geladen (`REGISTER_ABWEICHUNG`), andere Pakete laden.
- [ ] R1 ohne Einstufung, mit falschem `dsfa_delta_plan_hash` oder mit `risikoklasse: hoch` → `LAUFBEDINGUNG`.
- [ ] Testvektoren für Kanonisierung und Signatur in `dienste/runner/tests/vektoren/signatur/` (Manifest, Schlüssel, erwartete Signatur).
- [ ] Abdeckung `runner.paket` ≥ 85 %.

## Nicht in dieser Session

Upload-Endpunkt (S09), `uc eval` (S11; bis dahin wird `eval.json` in Tests von Hand erzeugt).

## Prüfung

```
make check
```
