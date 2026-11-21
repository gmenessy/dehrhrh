# S16 – Freigaben und Freigabeliste

**Ziel:** Berechtigte Personen sehen offene Freigaben ihres Mandanten mit rückgebildetem Klartext und Fundstellen, erteilen, korrigieren oder lehnen ab – gebunden an den `klartext_hash` und mit Token Exchange; die Freigabeliste ist eine barrierefreie Vanilla-Seite.

**Voraussetzungen:** S15

**Kontext lesen:** Architektur 6.2, 9.1, ADR-012 · Konzept: Leitsatz 5, Personalvertretung (k ≥ 5) · Implementierung 7 (CSP)

## Aufgaben

1. API (Scope `uc.freigeben`):
   - `GET /api/v1/approvals?status=OFFEN&paket=…` – Liste ohne Klartext (Paket, Lauf, Erstellt, Ablauf);
   - `GET /api/v1/approvals/{id}` – rückgebildetes Ergebnis, Fundstellen mit Seitenzahl und Zitat, `klartext_hash`, Gate-Ergebnis, `Cache-Control: no-store`;
   - `POST /api/v1/approvals/{id}` – `{entscheidung: erteilen|korrigieren|ablehnen, klartext_hash, grund?, korrektur?}`.
2. Regeln:
   - `klartext_hash` muss dem gespeicherten entsprechen, sonst 409 `HASH_ABWEICHUNG`;
   - nach Ablauf 409 `FREIGABE_ABGELAUFEN`;
   - Entscheidung nur einmal (409 bei zweitem Versuch);
   - `korrigieren`: Korrektur gegen das Ausgabeschema, erneut VAL-001, VAL-006, VAL-008 (auf der wieder redigierten Korrektur), neuer `klartext_hash`, dann wie `erteilen`;
   - `ablehnen` und `korrigieren` verlangen `grund` aus einer festen Liste (`FACHLICH_FALSCH`, `UNVOLLSTAENDIG`, `FALSCHE_ZUORDNUNG`, `FORMULIERUNG`, `SONSTIGES`).
3. Token Exchange: Der Runner tauscht das Token der freigebenden Person (`kern.auth.austausch`) gegen ein Token mit Zielgruppe `plattform-wirkung`; gespeichert werden `person_sub` und `act` (nie das Token). Keycloak-Realm um die Zielgruppe `plattform-wirkung` ergänzen. Audit `freigabe_erteilt|korrigiert|abgelehnt` ohne Inhalte.
4. Fortsetzung des Laufs über `freigabe_entschieden` aus S15; `ablehnen` → Lauf `ABGELEHNT`.
5. Freigabeliste unter `/freigaben/` (statisch, vom Runner ausgeliefert): `index.html`, `app.js`, `app.css`, ohne Build-Schritt und ohne Fremdbibliotheken:
   - Anmeldung per Authorization Code mit PKCE gegen den IdP (Client `freigabeliste`), Token nur im Speicher;
   - Liste, Detail mit Ergebnisfeldern und Fundstellen, Buttons Erteilen/Korrigieren/Ablehnen, Grundauswahl;
   - CSP `default-src 'self'; connect-src 'self' <idp>; script-src 'self'; style-src 'self'; frame-ancestors 'none'`, kein Inline-Script;
   - Barrierefreiheit nach WCAG 2.2 AA: `lang="de"`, Überschriftenhierarchie, Beschriftungen für alle Felder, sichtbarer Fokus, Tastaturbedienung, `aria-live` für Statusmeldungen, Kontrast ≥ 4,5:1, keine reine Farbcodierung.
6. Qualitätsauswertung: Metrik `plattform_freigabe_total{paket,entscheidung,grund}` ohne Personenbezug; Bericht `uc bericht freigaben --mandant` gibt Annahmequote je Paket nur aus, wenn mindestens fünf verschiedene Personen beteiligt sind (k ≥ 5).

## Akzeptanzkriterien

- [ ] Freigabe mit veraltetem `klartext_hash` → 409; nach Ablauf → 409; doppelte Entscheidung → 409.
- [ ] Korrektur mit erfundenem Platzhalter → 422 `GATE_VERSTOSS`/`VAL-008`.
- [ ] Token ohne `uc.freigeben` → 403; Freigabe eines anderen Mandanten → 404.
- [ ] Gespeicherte Freigabe enthält `act`; Audit enthält kein Token und keinen Klartext.
- [ ] Integration: `posteingang` über `/processes` → `WAITING_HUMAN` → Freigabe per API → Lauf `ABGESCHLOSSEN`.
- [ ] Automatische Prüfung der HTML-Dateien (Python-Test): `lang`, genau ein `h1`, jedes `input`/`select`/`textarea` mit `label`, kein Inline-Script, kein `style`-Attribut. Manuelle Prüfliste `dienste/runner/src/runner/freigaben/PRUEFLISTE.md` für Tastatur und Screenreader ausgefüllt.
- [ ] `uc bericht freigaben` gibt bei vier Personen keine Quote aus.

## Nicht in dieser Session

Wirkung nach der Freigabe (S18).

## Prüfung

```
make check && make up && make test-int
```
