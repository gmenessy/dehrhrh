# S10 – Zonen: Schwärzen, Platzhalter, Verschlüsselung

**Ziel:** Der Runner erzeugt vor jedem eigenen Modellschritt eine redigierte Fassung, hält die Platzhalterzuordnung verschlüsselt in Zone A, bildet Ergebnisse zurück und bildet den `klartext_hash`. Ein Test beweist, dass kein Klartext ein Modell erreicht.

**Voraussetzungen:** S09

**Kontext lesen:** Architektur 2 (Leitregel 4), 5.1, 7.2, 7.3, 8 (`klartext`), ADR-006, ADR-011

## Aufgaben

1. Schrittart `schwaerzen` in `runner.engine`: ruft `taschenmesser` `/intern/v1/schwaerzen` (Dienst-Token, Scope `tm.zuordnung`) mit Text und/oder Handles; Ergebnis: `Redigiert` je Seite, Handles `dok:<n>` zeigen ab jetzt auf die redigierte Fassung (`laufdaten/<mandant>/<anfrage>/redigiert/<n>.txt`), Platzhalterzuordnung als `Platzhaltertabelle`.
2. `runner.zonen.tabelle.Platzhaltertabelle`: unveränderlich, `rueck(wert)` für `str`, Listen, Dicts (wie Bestand `schwaerzen.rueck`), `typ(platzhalter)`, `platzhalter()`.
3. `runner.zonen.krypto`: AES-256-GCM; Datenschlüssel je `(mandant, zweck)` per HKDF-SHA256 aus dem Hauptschlüssel `KLARTEXT_SCHLUESSEL_DIR/<mandant>.key`; Zwecke `platzhalter`, `dokument`; AAD = `<mandant>|<lauf_oder_anfrage>|<art>`; zufällige 96-Bit-Nonce. Fehlt der Schlüssel eines Mandanten → Anfrage `INTERN`, Start meldet `/ready` falsch.
4. `runner.zonen.ablage`: Schnittstelle `KlartextAblage` mit Implementierung `Speicher` (synchron: nur im Prozess, nach der Antwort verworfen); die Datenbank-Implementierung folgt in S14.
5. `runner.zonen.rueckabbildung`: Ergebnis (`Redigiert`-Werte in Struktur) → Klartext-Struktur; `klartext_hash = sha256_praefix(kanonisch(klartext_ergebnis))`; erscheint in `meta`.
6. Durchsetzung:
   - nur `runner.zonen` darf `Redigiert(...)` konstruieren; Test per AST über `dienste/runner/src`;
   - `ModellClient` und die Schrittarten `entscheidung`, `llm`, `agent` (ab S12/S13) nehmen nur `Redigiert`/`Handle`;
   - Antworten nach außen werden zurückgebildet; Antworten mit Klartext tragen `Cache-Control: no-store`.
7. Taschenmesser-Werkzeuge mit `nutzt_modell` bleiben unverändert (schwärzen intern, S04); ein Integrationstest bestätigt über das Attrappen-Protokoll, dass auch dort kein Klartext ankommt.
8. Testkorpus `dienste/runner/tests/daten/zonen/` mit drei synthetischen Briefen (Namen, Adresse, IBAN, Steuer-ID, Telefon, Geburtsdatum).

## Akzeptanzkriterien

- [ ] Integration: Ein Testpaket `schwaerzen → skill (taschenmesser.zusammenfassen)` und ein Testpaket `schwaerzen → llm` (als `Plan`-Objekt direkt im Test gebaut, am Linter vorbei, weil `gate` und `llm` erst in S11/S12 entstehen; der Schritt ruft nur `ModellClient.chat`) laufen mit den drei Briefen; danach enthält **kein** Eintrag in `/_protokoll` der Attrappe einen Wert aus der Platzhalterzuordnung (Prüfung über die `zuordnung` aus `/intern/v1/schwaerzen`).
- [ ] AST-Test: `Redigiert(` kommt außerhalb `runner/zonen/` nicht vor.
- [ ] Krypto: Entschlüsseln mit falschem Mandanten oder falscher AAD scheitert; Testvektoren in `tests/vektoren/zonen/`.
- [ ] Rückabbildung: verschachtelte Strukturen, unbekannter Platzhalter bleibt unverändert und wird als Fehler im Ergebnis markiert (Vorbereitung VAL-008).
- [ ] `klartext_hash` stabil gegenüber Schlüsselreihenfolge.
- [ ] `mypy --strict` und Abdeckung ≥ 85 % für `runner.zonen`.

## Nicht in dieser Session

Datenbank-Ablage (S14), Gate (S11), vollständige Schrittarten `entscheidung`/`llm` (S12).

## Prüfung

```
make check && make up && make test-int
```
