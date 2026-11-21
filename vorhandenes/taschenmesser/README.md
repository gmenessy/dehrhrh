# Taschenmesser – Dokumentdienste für Fachverfahren

Ein zustandsloser Dienst (FastAPI) auf dem vorhandenen vLLM-Stack. Er hat keine Datenbank, keine Queue und keine Orchestrierung.
Das Fachverfahren schickt Dokumente und bekommt strukturiertes JSON mit Fundstellen zurück.

| Endpoint | Zweck | Modell |
|---|---|---|
| `POST /v1/text` | Text je Seite (PDF-Textebene, OCR nur für Scan-Seiten, Bilder) | – |
| `POST /v1/schwaerzen` | Namen, Anschriften, Geburtsdatum, Steuer-ID, SV-Nr., Ausweis-Nr., IBAN, E-Mail, Telefon → Platzhalter | Regel + LLM |
| `POST /v1/zusammenfassen` | Zusammenfassung + Kernpunkte mit Zitatbeleg (`laenge` kurz/mittel/lang, `fokus`) | LLM |
| `POST /v1/extrahieren` | Schema-getriebene Extraktion mit Normalisierung und Fundstelle je Feld | Regel + LLM |
| `POST /v1/klassifizieren` | Dokumenttyp aus fester Liste mit Wahrscheinlichkeit, `pfad` auto/mensch | Decision |
| `POST /v1/pruefen` | Vollständigkeitsprüfung: Nachweise ja/nein + Pflichtfelder → Nachforderungsliste | Decision + LLM |
| `POST /v1/abgleichen` | Kreuzprüfung: dasselbe Schema aus jedem Dokument, Feld für Feld verglichen (Antrag ↔ Nachweise) | LLM je Dokument |
| `POST /v1/ampel` | Prüfkorb grün/gelb/rot aus gewichteten Regeln über Felder und Nachweise; ohne Dokument rein regelbasiert | Regel (+ LLM/Decision) |
| `POST /v1/zerlegen` | Scan-Stapel in Einzeldokumente trennen und typisieren, optional als ZIP mit einer PDF je Dokument | Regel + Decision |
| `POST /v1/nachforderung` | Entwurf für Nachforderung oder Anhörung; Frist, Liste und Rahmen per Vorlage, LLM formuliert nur die Punkte | Vorlage (+ LLM) |
| `POST /v1/markieren` | PDF mit markierten Fundstellen (Kommentar „feld: wert“); Textebene oder OCR-Wortpositionen | – |
| `POST /v1/posteingang` | Eingangsdaten (Absender, Datum, Betreff, Bezug, Anliegen), Dringlichkeit, optional Zuständigkeit, Fristen | Mailkopf + LLM + Decision |
| `POST /v1/fristen` | Fristangaben finden, beschreiben und mit `beginn` berechnen | Regel (+ LLM) |
| `POST /v1/fristende` | Fristrechner (§ 31 VwVfG, §§ 187 ff. BGB, Feiertage BW) | – |
| `POST /v1/chronik` | Zeitstrahl eines Vorgangs über alle Dokumente, je Ereignis mit Fundstelle | LLM |
| `POST /v1/uebersetzen` | Arbeitsübersetzung (nicht amtlich); deutscher Text wird erkannt und nicht übersetzt | LLM |
| `POST /v1/vergleichen` | Fassungsvergleich alt/neu: satzweise Synopse, optional wesentliche Änderungen | Regel (+ LLM) |
| `POST /v1/antwort` | Antwortentwurf aus dem Bausteinkatalog des Amts, kein frei formulierter Text | Decision (+ LLM für Lücken) |
| `POST /v1/vorlage` | Text- oder DOCX-Vorlage mit `{{feld}}` füllen | – |
| `POST /v1/beschluesse` | Beschlüsse, Stimmen und Aufträge (wer, was, bis) aus Sitzungsprotokollen | LLM |
| `POST /v1/rechnung` | XRechnung/ZUGFeRD lesen und prüfen, Abgleich mit Bestellung, Kontierungsvorschlag | Regel (+ Decision) |
| `POST /v1/aktenplan` | Aktenzeichen-Vorschlag stufenweise über den Aktenplan (Hauptgruppe → Gruppe → Untergruppe) | Decision |
| `POST /v1/herausgabe` | Schwärzen zur Herausgabe: Vorschau zur Prüfung, dann gerasterte PDF mit Kontroll-OCR | Regel (+ LLM-NER) |
| `POST /v1/einwendungen` | Einwendungen bündeln: Kernpunkte nach Thema, gleichlautende erkannt, Abwägungstabelle (JSON/CSV) | LLM |
| `POST /v1/kriterien` | Kriterien-Mapping: Geht der Antrag auf jede Anforderung der Richtlinie ein, und wo? | Decision + LLM |
| `python -m app.goldset` | Goldset-Messung gegen den laufenden Dienst; schlägt AUTO_AB, JA_AB, NEIN_BIS vor | – |
| `GET /health` | Lebenszeichen | – |

Alle POST-Endpoints sind multipart: `dateien` (mehrfach) **oder** `text`, dazu `schwaerzen=true` (Standard). Gelesen werden PDF (Textebene, OCR nur für Scan-Seiten), PNG/JPEG/TIFF, DOCX, ODT, XLSX, E-Mails als `.eml` und Outlook-`.msg` sowie UTF-8-Text. Jeder E-Mail-Anhang wird als eigenes Dokument gelesen (`mail.eml/anhang.pdf`).
Die Authentifizierung läuft über `X-API-Key`. Jeder Schlüssel ist einem Mandanten zugeordnet.

## Regeln, die überall gelten

- **Stufenregel:** Was sich per Regel lösen lässt (IBAN, E-Mail, Datums- und Betragsformat), geht nicht ans Modell. Auswahlfragen beantwortet das Decision Model per Logprobs, Freitext das LLM per `guided_json`. Ist ein Ergebnis unsicher, steht in der Antwort `pfad: mensch` bzw. `status: unsicher`.
- **Kein Wert ohne Fundstelle:** Jeder extrahierte Wert und jeder Kernpunkt braucht ein wörtliches Zitat aus dem Dokument. Fehlt es, wird der Wert nicht übernommen (`ohne_fundstelle`, mit `vorschlag`).
- **Fail-closed:** Ist kein Modell erreichbar, liefern Extraktion und Zusammenfassung `503` mit `fail_closed: true`. Klassifikation und Prüfung liefern in diesem Fall `pfad: mensch` bzw. `pruefen`.
- **Datenschutz:** Die Werkzeug-Aufrufe sehen nur die geschwärzte Fassung (`[NAME_1]`, `[ADRESSE_1]`, …). Die Platzhalter werden in der Antwort zurückübersetzt, Zitate werden gegen beide Fassungen geprüft. Das Audit-Log ist eine JSON-Zeile ohne Inhalte (Hashes, Zähler, Dauer).

## Schwärzung

| Kategorie | Erkennung |
|---|---|
| Namen | Regel mit Auslöser: `Herr/Frau (Dr.)`, `Name:`, `Name, Vorname:`, `Antragsteller:`, `Inhaber:`, `Ansprechpartner:`, `Bevollmächtigter:`, `gez.`, Name unter der Grußformel. Amtstitel („Bürgermeister Klein“) werden übersprungen. Firmen mit Rechtsform (GmbH, AG, KG, e. V. …) bleiben stehen. |
| Abgleich | Jeder gefundene Name und jeder Namensbestandteil wird auf **allen** Seiten und Dokumenten der Anfrage ersetzt, also auch „Musterfrau“ allein im Fließtext. |
| Anschriften | Straße + Hausnummer (`…straße/str./weg/gasse/platz/allee/ring/damm …`, `Am/Im/An der … 4`). **PLZ und Ort bleiben stehen**, weil sie für Zuständigkeit und Extraktion gebraucht werden. |
| Kennungen | Geburtsdatum (mit Auslöser), Steuer-ID (Prüfziffer), SV-Nummer, Ausweis-/Passnummer (mit Auslöser), IBAN (Prüfziffer), E-Mail, Telefon |
| LLM (`SCHWAERZEN_NER=llm`, Standard) | Ein zusätzlicher Aufruf je Textfenster findet Namen und Anschriften im Fließtext („für meine Mutter Anna Schmidt“). Übernommen wird nur, was wörtlich im Text steht. Fällt das Modell aus, gibt es 503 (fail-closed), statt unvollständig zu schwärzen. |

Wichtig: Der NER-Aufruf sieht den **Klartext**. Er läuft auf demselben vLLM im selben Netz. Das Prompt-Logging im vLLM muss abgeschaltet sein. Wer das nicht will, setzt `SCHWAERZEN_NER=regel`: Dann schwärzen nur die Regeln, und Namen ohne Auslöser im Fließtext bleiben stehen.

## Start

```bash
API_KEYS="musterhausen:$(openssl rand -hex 24)" LLM_URL=http://gpu-host:8000/v1 docker compose up -d --build
# Modelle auf demselben Host: docker compose --profile gpu up -d
```

Lokal ohne Container:

```bash
pip install -e .[test] && DEV=1 uvicorn app.main:app --port 8080
pytest   # 82 Tests (Office-Testdateien unter tests/daten wurden mit LibreOffice erzeugt), Modelle gefakt, OCR echt (Tesseract nötig)
```

## Beispiele

```bash
K="X-API-Key: <schluessel>"
curl -s -H "$K" -F dateien=@antrag.pdf localhost:8080/v1/text
curl -s -H "$K" -F dateien=@antrag.pdf -F laenge=kurz -F "fokus=Fristen" localhost:8080/v1/zusammenfassen
curl -s -H "$K" -F dateien=@antrag.pdf -F schema=@beispiele/gewerbeanzeige.schema.json localhost:8080/v1/extrahieren
curl -s -H "$K" -F dateien=@post.pdf -F optionen=@beispiele/dokumenttypen.json localhost:8080/v1/klassifizieren
curl -s -H "$K" -F dateien=@antrag.pdf -F dateien=@anlage.pdf -F checkliste=@beispiele/gewerbeanzeige.checkliste.json localhost:8080/v1/pruefen
```

Antwort der Extraktion (gekürzt):

```json
{"daten": {
   "iban": {"wert": "DE89370400440532013000", "status": "ok", "stufe": 0, "dokument": "antrag.pdf", "seite": 2},
   "betriebsbeginn": {"wert": "2026-03-01", "status": "ok", "stufe": 2, "seite": 1, "zitat": "Betriebsbeginn: 01.03.2026"},
   "taetigkeit": {"wert": null, "status": "ohne_fundstelle", "vorschlag": "Fahrradverleih"}},
 "vollstaendig": false, "fehlende_pflichtfelder": ["taetigkeit"],
 "meta": {"anfrage": "…", "mandant": "musterhausen", "modellaufrufe": {"llm": 1, "decision": 0}}}
```

## Paket 1 im Detail

**abgleichen**: Braucht mindestens zwei Dokumente und ein Schema. Je Feld steht einer dieser Status in der Antwort: `gleich`, `abweichung`, `einmal` (nur ein Dokument nennt das Feld) oder `fehlt`. Bei jedem Wert stehen Dokument, Seite und Zitat.
Texte werden ohne Groß-/Kleinschreibung, Satzzeichen und Reihenfolge verglichen, deshalb gilt „Musterfrau, Erika“ = „Erika Musterfrau“. Für Zahlen gibt es je Feld `"toleranz": 1`. Ergebnis ist `stimmig`, `abweichung` oder `pruefen` (wenn ein Wert ohne Fundstelle oder ungültig ist).

**ampel**: Der Prüfplan ist JSON: `felder` (Schema), `nachweise`, `regeln`, `schwellen` (Standard gelb ≥ 1, rot ≥ 3).
- Felder, die das Fachverfahren schon kennt, kommen über `daten` und werden nicht extrahiert. Ohne Dokument läuft die Ampel rein regelbasiert, ohne Modellaufruf.
- Eine Regel sieht so aus: `{"id", "wenn", "gewicht", "ko", "hinweis"}`. Auslösende Regeln addieren ihr Gewicht, `ko: true` macht den Korb sofort rot.
- Bedingungen:
  - `{"feld": "umsatz", "op": ">", "wert": 100000}` mit den Operatoren `== != > >= < <= in nicht_in enthaelt fehlt vorhanden ungueltig`
  - Datumsangaben relativ zu heute: `"wert": "heute-90"`
  - Vergleich zweier Felder: `"feld2": "gesamtsumme", "toleranz": 0.01`
  - Nachweise: `{"nachweis": "hr", "status": "fehlt"}`
  - Verknüpfungen: `{"alle": [...]}`, `{"eine": [...]}`, `{"nicht": {...}}`
- **Unklares ist nie grün.** Ist ein Wert unklar oder ein Nachweis unsicher, gilt die Regel als `nicht_pruefbar`, und der Korb ist mindestens gelb.
- Jede Regel liefert ihre Belege mit (Wert, Quelle, Seite, Zitat). Beispiel: `beispiele/gewerbeanzeige.pruefplan.json`.

**zerlegen**: Für jede Seite fällt eine Entscheidung: Entweder beginnt ein neues Dokument (dann mit Typ), oder die Seite setzt das vorherige fort.
- Ohne Modell entscheiden: „Seite 2 von 3“ auf der Seite bedeutet Fortsetzung, und eine Leerseite wird dem vorigen Dokument angehängt.
- Ein neues Upload-Dokument beginnt immer ein neues Segment.
- `pfad: auto` gibt es nur, wenn jede Seite die Schwelle `AUTO_AB` erreicht und der Typ bekannt ist.
- Mit `als_zip=true` kommt ein ZIP mit `01_typ.pdf …` und `zerlegung.json` zurück.

**nachforderung**: Eingabe ist `punkte` (Liste) oder `pruefergebnis` (die Antwort von `/v1/pruefen`).
- Rahmen, Liste, Aktenzeichen und Frist kommen aus der Vorlage. Fällt das Fristende auf ein Wochenende, gilt der Montag; Feiertage folgen in Paket 2.
- Das LLM formuliert nur die Punkte in einfache Sprache. Enthält ein umformulierter Punkt neue Zahlen oder ist er auffällig länger, bleibt das Original stehen. Ist kein Modell erreichbar, entsteht das Schreiben aus den Originalpunkten (`stufe: 0`).
- Anrede, Aktenzeichen und Namen sieht das Modell nie.
- Mit `art=anhoerung` und optional `rechtsgrundlage` wird ein Anhörungsschreiben daraus. In der Checkliste kann jeder Nachweis einen fertigen `nachforderung`-Text tragen.

**markieren**: Eingabe ist genau eine PDF oder ein Bild, dazu `fundstellen`. Das ist eine Liste `{seite, zitat, text}` oder direkt die Antwort von `/v1/extrahieren`, `/v1/zusammenfassen` oder `/v1/abgleichen`; Abweichungen werden rot markiert.
- Auf Seiten mit Textebene wird das Zitat über die Zeichenpositionen gefunden, auch über Silbentrennung und Zeilenumbruch hinweg. Auf Scan-Seiten liefern die OCR-Wortpositionen die Lage.
- Die Markierungen sind Annotationen und lassen sich im Betrachter ausblenden. Das Original bleibt sonst unverändert.
- Die Header `X-Markiert` und `X-Nicht-Gefunden` geben die Zählung zurück.
- Hinweis: Die Markierungen werden ohne eigenes Aussehen geschrieben, der Betrachter erzeugt es. Geprüft ist das nur mit PDFium (Chrome, Edge); Acrobat und pdf.js vor dem Einsatz testen.

```bash
curl -s -H "$K" -F dateien=@antrag.pdf -F dateien=@lohnabrechnung.pdf -F schema=@beispiele/abgleich.antrag-nachweise.schema.json localhost:8080/v1/abgleichen
curl -s -H "$K" -F dateien=@antrag.pdf -F pruefplan=@beispiele/gewerbeanzeige.pruefplan.json -F 'daten={"plz":"73728"}' localhost:8080/v1/ampel
curl -s -H "$K" -F dateien=@scanstapel.pdf -F typen=@beispiele/posteingang.typen.json -F als_zip=true -o zerlegung.zip localhost:8080/v1/zerlegen
curl -s -H "$K" -F "pruefergebnis=<pruefen.json" -F "anrede=Sehr geehrte Frau Musterfrau" -F aktenzeichen=32.1-2026/17 localhost:8080/v1/nachforderung
curl -s -H "$K" -F dateien=@antrag.pdf -F "fundstellen=<extraktion.json" -o antrag_markiert.pdf localhost:8080/v1/markieren
```

Typische Kette: `zerlegen` → `pruefen` oder `ampel` → `nachforderung`; bei Antrag plus Nachweisen zusätzlich `abgleichen` → `markieren` für die Sachbearbeitung.

## Paket 2 im Detail

- **posteingang**: Liefert Absender, Datum, Betreff, Bezugsaktenzeichen, Zeichen des Absenders und Anliegen, jeweils mit Fundstelle.
  - Bei E-Mails gelten Absender, Datum und Betreff aus dem Mailkopf (`stufe: 0`), nicht aus der Extraktion.
  - Die Dringlichkeit entscheidet das Decision Model.
  - Mit `aemter=@beispiele/aemter.json` kommt ein Zuständigkeitsvorschlag dazu; ohne Schwelle geht er an einen Menschen.
  - Fristangaben im Text werden gleich mitgeliefert.
- **fristen**: Erkennt Fristangaben per Regel, nämlich Daten („bis zum 15.12.2026“, „spätestens am 4. Dezember 2026“) und Dauern („innerhalb eines Monats“, „binnen 14 Tagen“). Das LLM beschreibt nur, wofür die Frist gilt und womit sie beginnt; es kann keine Frist erfinden.
  - Mit `beginn=JJJJ-MM-TT` wird das Fristende berechnet: Ereignisfrist, Monatsende-Regel, Verschiebung auf den nächsten Werktag. Die Feiertage in Baden-Württemberg werden mit Ostern berechnet.
  - Fällt eine Datumsangabe auf ein Wochenende oder einen Feiertag, kommt ein Hinweis.
  - Bekanntgabefiktion und Fristverlängerung entscheidet die Sachbearbeitung.
- **chronik**: Ereignisse mit Datum, Satz und Fundstelle über alle Dokumente, chronologisch sortiert und ohne Doppelungen. Unbelegte Ereignisse sind markiert.
- **uebersetzen**: Übersetzt seitenweise; Platzhalter bleiben erhalten und werden zurückübersetzt.
  - Eine Seite meldet `fehlende_platzhalter`, wenn das Modell einen Platzhalter verschluckt hat.
  - Deutscher Text wird ohne Modell erkannt und nicht übersetzt.
  - Ziel ist `Deutsch` oder eine andere Sprache über `ziel`.
- **vergleichen**: Zwei Dateien oder `alt` und `neu` als Text. Die Synopse ist satzweise und robust gegen Zeilenumbrüche und gängige Abkürzungen.
  - Jede Änderung ist `geaendert` (mit Wortmarkierung `[-50-] {+60+}`), `eingefuegt` oder `entfernt`. Paare, die sich kaum ähneln, gelten als Streichung plus Einfügung.
  - `bewerten=true` lässt das LLM die inhaltlich wesentlichen Änderungen nennen, mit Verweis auf die Änderungsnummern. Unbekannte Nummern werden verworfen.
- **antwort**: Der Bausteinkatalog ist eine Liste `[{id, titel, wann, text, immer}]`, siehe `beispiele/antwort.bausteine.json`.
  - Für jeden Baustein entscheidet das Decision Model ja oder nein; `immer`-Bausteine kommen ohne Modell in die Antwort.
  - Unsichere Bausteine erscheinen unter `zu_pruefen`.
  - Platzhalter werden aus `daten` gefüllt, sonst aus dem Schreiben extrahiert. Offene Platzhalter bleiben als `{{…}}` stehen und werden gemeldet.
  - Das Modell formuliert keinen eigenen Text.
- **vorlage**: Ohne Modell. Syntax: `{{feld}}`, `{{feld|datum}}`, `{{feld|betrag}}`, `{{#feld}}…{{/feld}}`, `{{^feld}}…{{/feld}}`, `{{a.b}}`.
  - DOCX-Vorlagen kommen gefüllt als DOCX zurück, auch wenn Word die Platzhalter auf mehrere Läufe verteilt hat. Der erste Lauf bestimmt dann die Formatierung.
  - Blöcke wirken in DOCX nur innerhalb eines Absatzes.
  - `streng=true` meldet fehlende Werte mit 422.
- **beschluesse**: TOP, Gegenstand, Wortlaut, Ergebnis, Stimmen und Aufträge mit Termin, dazu eine Auftragsliste nach Termin. Die Prüfung einer Beschlussvorlage auf Pflichtabschnitte läuft über `/v1/pruefen` mit `beispiele/beschlussvorlage.checkliste.json`.
- **rechnung**: XRechnung (UBL Invoice und CreditNote, CII) und ZUGFeRD/Factur-X (XML in der PDF) werden ohne Modell gelesen.
  - Geprüft werden Pflichtangaben, Leitweg-ID, Positionen (Menge × Preis), Summe der Positionen, Netto + Steuer = Brutto, Zahlbetrag und IBAN.
  - Optional kommen der Abgleich mit `bestellung` (`bestellnummer`, `lieferant`, `brutto_max`, `leitweg_id`) und ein Kontierungsvorschlag über `konten` dazu.
  - Eine PDF ohne XML wird extrahiert; das Ergebnis ist dann immer `pruefen`.
  - Es gibt keine Schema- oder Schematron-Validierung, dafür bleibt der KoSIT-Validator zuständig.
  - `beispiele/konten.json` enthält Beispielkonten, nicht euren Kontenplan.

```bash
curl -s -H "$K" -F dateien=@eingang.msg -F aemter=@beispiele/aemter.json localhost:8080/v1/posteingang
curl -s -H "$K" -F dateien=@bescheid.pdf -F beginn=2026-12-01 localhost:8080/v1/fristen
curl -s -H "$K" -F beginn=2026-12-24 -F wert=2 -F einheit=wochen localhost:8080/v1/fristende
curl -s -H "$K" -F dateien=@satzung_alt.pdf -F dateien=@satzung_neu.pdf localhost:8080/v1/vergleichen
curl -s -H "$K" -F dateien=@buergerbrief.pdf -F bausteine=@beispiele/antwort.bausteine.json -F 'daten={"sachbearbeitung":"Ihr Ordnungsamt"}' localhost:8080/v1/antwort
curl -s -H "$K" -F vorlage=@bescheid.docx -F 'daten={"name":"Weber","gebuehr":45.5}' -o bescheid_gefuellt.docx localhost:8080/v1/vorlage
curl -s -H "$K" -F dateien=@rechnung.xml -F 'bestellung={"bestellnummer":"B-4711","brutto_max":2000}' -F konten=@beispiele/konten.json localhost:8080/v1/rechnung
```

## Paket 3 im Detail

- **aktenplan**: Der Aktenplan kommt als flache Liste `[{az, titel, beschreibung}]`; die Ebenen ergeben sich aus den Präfixen (`1` > `12` > `122` > `122.3`).
  - Auf jeder Ebene wählt das Decision Model unter den Kindknoten, immer mit „keine passt“. Bei mehr als 18 Kandidaten wird in Gruppen vorausgewählt, die Sieger treten im Finale gegeneinander an.
  - Der Abstieg endet bei „keine passt“ oder wenn p unter `min_p` (Standard 0,5) fällt. Ergebnis ist dann die tiefste sichere Ebene mit `vollstaendig: false`.
  - `pfad_status: auto` gibt es nur, wenn ein Blatt erreicht ist und jede Ebene p ≥ `AUTO_AB` hat. Jede Ebene liefert die drei besten Alternativen mit.
  - `beispiele/aktenplan.beispiel.json` ist ein erfundener Ausschnitt, nicht euer Aktenplan.
- **herausgabe**: Zwei Schritte. Erst `vorschau=true`: Die Fundliste mit Klartext, Kategorie und Seiten ist eine **interne** Prüfliste. Dann die Ausgabe, wobei `zusatz` weitere Begriffe schwärzt (z. B. Betriebsgeheimnisse) und `ausnahmen` Begriffe stehen lässt (z. B. Amtsträger).
  - Geschwärzt werden Namen samt Bestandteilen (auch „Frau Musterfrau“ allein), Anschriften, Kontaktdaten und Kennungen. Die Kategorien lassen sich über `kategorien` wählen.
  - Betroffene Seiten werden gerastert und schwarz überdeckt; dort gibt es danach keine Textebene mehr. Unbetroffene Seiten bleiben unverändert.
  - Die neue PDF enthält keine Metadaten, Anhänge oder Formulare des Originals.
  - **Kontroll-OCR:** Ist ein geschwärzter Begriff auf einer gerasterten Seite noch lesbar, gibt es 409 und keine Datei.
  - Grenzen: Text in eingebetteten Bildern einer Textseite wird nicht erkannt. Handschrift erkennt die OCR nur unzuverlässig. Die Vorschau ersetzt nicht die Sichtprüfung vor der Herausgabe.
- **einwendungen**: Jede Datei ist eine Einwendung. Jede wird in Kernpunkte zerlegt und vorgegebenen Themen zugeordnet (siehe `beispiele/einwendungen.themen.json`), jeder Kernpunkt mit Zitat.
  - Gleichlautende Einwendungen (Formbriefe, ab 90 % Übereinstimmung) werden ohne Modell erkannt und nur einmal gelesen; ihre Anzahl wird mitgeführt.
  - Einwender erscheinen nur als Nummern. `format=csv` liefert die Abwägungstabelle mit leeren Spalten „Abwägung“ und „Beschlussvorschlag“.
- **kriterien**: Die Anforderungen kommen als `kriterien` (Liste) oder werden aus einer `richtlinie` (Datei) gelesen, dann mit Zitat aus der Richtlinie.
  - Je Kriterium entscheidet das Decision Model, ob der Antrag darauf eingeht, und das LLM liefert ein Zitat, das geprüft wird.
  - Der Status ist `belegt` (beides passt), `nicht_belegt` oder `pruefen` (unsicher oder widersprüchlich).
  - Über Förderfähigkeit wird nicht entschieden.
- **goldset**: `python -m app.goldset faelle.jsonl --url … --key … --ziel 0.98 --bericht bericht.md`, Format siehe `beispiele/goldset.beispiel.jsonl`.
  - Gemessen werden je Feld richtig, falsch, nicht geliefert, fälschlich, Genauigkeit und Abdeckung, außerdem die Trefferkurve von Klassifikation und Aktenplan.
  - Die Empfehlung ist die kleinste Schwelle, ab der die automatisch durchgelassenen Fälle die Zielgenauigkeit erreichen, bei mindestens 20 Fällen.
  - Es läuft gegen den echten Dienst, misst also OCR, Schwärzung und Modelle zusammen.

```bash
curl -s -H "$K" -F dateien=@eingang.pdf -F aktenplan=@beispiele/aktenplan.beispiel.json localhost:8080/v1/aktenplan
curl -s -H "$K" -F dateien=@akte.pdf -F vorschau=true localhost:8080/v1/herausgabe
curl -s -H "$K" -F dateien=@akte.pdf -F 'ausnahmen=["Klein"]' -F 'zusatz=["Projekt Lindenhof"]' -o akte_geschwaerzt.pdf localhost:8080/v1/herausgabe
curl -s -H "$K" $(for f in einwendungen/*.pdf; do echo -F dateien=@$f; done) -F themen=@beispiele/einwendungen.themen.json -F format=csv -o abwaegung.csv localhost:8080/v1/einwendungen
curl -s -H "$K" -F dateien=@antrag.pdf -F richtlinie=@foerderrichtlinie.pdf localhost:8080/v1/kriterien
```

## Schema (bewusst klein)

Erlaubt ist ein flaches Objekt mit höchstens 40 Feldern. Typen: `string`, `number`, `integer`, `boolean`. Mögliche Angaben: `format` (`date`, `iban`, `email`, `plz`, `betrag`), `enum`, `pattern`, `minimum`, `maximum`, `required`, `description`. Die `description` ist die Anweisung ans Modell, deshalb lohnt es sich, sie konkret zu schreiben.

## Konfiguration (Umgebung)

`SCHWAERZEN_NER` (`llm` oder `regel`), `LLM_URL`, `LLM_MODEL`, `DECISION_URL`/`DECISION_MODEL` (leer bedeutet: das LLM beantwortet die Auswahlfragen), `VLLM_STRUCTURED` (`guided` oder `structured_outputs`), `API_KEYS`, `MAX_MB` 20, `MAX_SEITEN` 100, `OCR_DPI` 200, `KONTEXT_ZEICHEN` 40000, `JA_AB` 0.85, `NEIN_BIS` 0.15, `AUTO_AB` 0.80, `PARALLEL` 8.

## Bewusst weggelassen

- Die Schwärzung ist Datenminimierung vor dem Modell, keine Anonymisierung: PLZ, Ort, Firmennamen, Kfz-Kennzeichen und Aktenzeichen bleiben stehen. Für eine Herausgabe an Dritte ist sie nicht gedacht.
- Es gibt keine Persistenz, kein Caching und keine Jobs. Lange Dokumente laufen synchron über Seitenfenster (Map-Reduce).
- Die Wahrscheinlichkeiten sind nicht kalibriert. Deshalb sind die Schwellen konservativ gesetzt und es gibt die Option „keine passt“. Vor dem produktiven `auto`-Pfad sollten die Schwellen an einem Goldset gemessen werden.
- Es gibt keine Tabellenextraktion, keine verschachtelten Schemata, keine Layoutanalyse und kein Ausfüllen von Formularfeldern. Offen sind Positionslisten im Schema und der Prüfvermerk.
- Outlook-`.msg` ist nach der Spezifikation umgesetzt, aber nicht mit einer echten Outlook-Datei getestet. XLSX-Datumswerte erscheinen als Excel-Seriennummern.
