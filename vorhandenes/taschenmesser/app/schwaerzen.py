"""Personenbezug vor dem Modellaufruf durch Platzhalter ersetzen und danach zurückabbilden.

Regeln (immer):  E-Mail, IBAN (Prüfziffer), Steuer-ID (Prüfziffer), SV-Nummer, Ausweisnummer (mit Kontext),
                 Telefon, Geburtsdatum (mit Kontext), Straße + Hausnummer, Personennamen mit Auslöser
                 (Herr/Frau, "Name:", "Antragsteller:", Grußformel ...).
Abgleich:        Jeder erkannte Name wird samt seiner Bestandteile auf allen Seiten ersetzt.
LLM (optional):  findet Namen und Anschriften im Fließtext; nur wörtlich belegte Treffer werden übernommen.
Bewusst nicht:   PLZ und Ort (wenig identifizierend, für Zuständigkeit gebraucht), Firmen, Behörden.
"""
from __future__ import annotations

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")
TELEFON = re.compile(r"(?<![\w+])(?:\+49|0049|0)[1-9][\d /()-]{5,}\d")
STEUERID = re.compile(r"(?<!\d)[1-9]\d(?: ?\d{3}){3}(?!\d)")
SVNR = re.compile(r"\b\d{2} ?(?:0[1-9]|[12]\d|3[01])(?:0[1-9]|1[0-2])\d{2} ?[A-Z] ?\d{2} ?\d\b")
AUSWEIS = re.compile(r"(?:Ausweis|Personalausweis|Reisepass|Pass)[- ]?(?:nr\.?|nummer|Nr\.?)\s*:?\s*([A-Z0-9]{9,10})\b", re.I)
GEBDATUM = re.compile(r"(?:geb\.|geboren(?: am)?|Geburtsdatum|Geb\.-Datum|Geburtstag)\s*:?\s*(\d{1,2}\. ?\d{1,2}\. ?(?:19|20)?\d{2})", re.I)

_HNR = r"[ \t]+\d{1,3}(?!\d)(?:[ \t]?[a-zA-Z](?![a-zA-Z]))?(?:[ \t]?[-/][ \t]?\d{1,3}[a-z]?)?"
_SUFFIX = r"(?:[Ss]tra(?:ße|sse)|[Ss]tr\.|[Ww]eg|[Gg]asse|[Pp]latz|[Aa]llee|[Rr]ing|[Dd]amm|[Uu]fer|[Ss]teige?|[Pp]fad|[Cc]haussee|[Mm]arkt|[Gg]raben|[Ss]tieg|[Kk]amp|[Ww]all)"
_NICHT_ORT = r"(?!(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag|Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|Jahr|Ende|Anfang|Tag)\b)"
ADRESSE = re.compile(
    rf"\b[A-ZÄÖÜ][\wäöüß-]*?{_SUFFIX}{_HNR}"                        # Hauptstraße 12, Karl-Marx-Str. 3
    rf"|\b[A-ZÄÖÜ][\wäöüß.-]*[ \t]+{_SUFFIX}{_HNR}"                  # Stuttgarter Str. 5, Lange Straße 1
    rf"|\b(?:Am|An der|An den|Im|In der|Auf der|Auf dem|Zum|Zur|Hinter der|Unter den)[ \t]+{_NICHT_ORT}[A-ZÄÖÜ][\wäöüß-]+{_HNR}")

_TOK = r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?\b(?![ \t]*:)"
_NAME = rf"{_TOK}(?:[ \t]+(?:(?:von|van|de|zu|von der|van der)[ \t]+)?{_TOK}){{0,3}}"
_TITEL = r"(?:(?:Dr\.|Prof\.|Dipl\.-[\wä]+\.?)[ \t]+)*"
NAMEN = [
    re.compile(rf"\b(?:Herrn?|Frau|Hr\.|Fr\.)[ \t]+{_TITEL}({_NAME})"),
    re.compile(rf"\b(?:Name|Vorname|Vornamen|Nachname|Familienname|Geburtsname|Name,[ \t]*Vorname|Vor- und (?:Nach|Zu)name|"
               rf"Antragsteller(?:in)?|Inhaber(?:in)?|Ansprechpartner(?:in)?|Bevollmächtigte[rn]?|Erziehungsberechtigte[rn]?|"
               rf"Unterschrift|Kontoinhaber(?:in)?)[ \t]*:[ \t]*{_TITEL}({_NAME}(?:,[ \t]*{_NAME})?)"),
    re.compile(rf"\bgez\.[ \t]*{_TITEL}({_NAME})"),
    re.compile(rf"(?:Grüßen|Grüße|Gruß),?[ \t]*\n(?:[ \t]*\n)*[ \t]*{_TITEL}({_NAME})[ \t]*$", re.M),
]
STOP = {"Straße", "Strasse", "Geburtsdatum", "Geb", "Telefon", "Tel", "Anschrift", "Datum", "Ort", "Wohnort", "Abteilung",
        "Amt", "Stadt", "Gemeinde", "Landratsamt", "Aktenzeichen", "Betreff", "Mit", "Und", "Der", "Die", "Das", "Sehr",
        "Geehrte", "Geehrter", "Ihr", "Ihre", "Bitte", "Vielen", "Dank", "Damen", "Herren"}
AMTSTITEL = {"Bürgermeister", "Bürgermeisterin", "Oberbürgermeister", "Oberbürgermeisterin", "Landrat", "Landrätin",
             "Kollegin", "Kollege", "Sachbearbeiter", "Sachbearbeiterin", "Rechtsanwalt", "Rechtsanwältin", "Notar",
             "Notarin", "Pfarrer", "Pfarrerin", "Ortsvorsteher", "Ortsvorsteherin", "Amtsleiter", "Amtsleiterin"}
_PARTIKEL = {"von", "van", "de", "zu", "der"}

RECHTSFORM = re.compile(r"[ \t]*(?:GmbH|gGmbH|AG|KG|OHG|UG|GbR|PartG|eG|e\.[ \t]?V\.|mbH|&[ \t]*Co)(?![\wäöüß])")
PLATZHALTER = re.compile(r"\[[A-Z]+_\d+\]")


def iban_gueltig(wert: str) -> bool:
    s = re.sub(r"\s", "", wert).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    if s.startswith("DE") and len(s) != 22:
        return False
    zahl = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(zahl) % 97 == 1


def steuerid_gueltig(wert: str) -> bool:
    """Steuerliche Identifikationsnummer: 11 Ziffern, Prüfziffer nach ISO 7064 MOD 11,10."""
    z = re.sub(r"\s", "", wert)
    if not re.fullmatch(r"[1-9]\d{10}", z):
        return False
    produkt = 10
    for c in z[:10]:
        summe = (int(c) + produkt) % 10 or 10
        produkt = summe * 2 % 11
    pruef = (11 - produkt) % 10
    return pruef == int(z[10])


def _bereinigen(name: str) -> str | None:
    """Stoppwörter am Anfang verwerfen, am Ende abschneiden."""
    toks = re.split(r"[ \t]+", name.strip())
    while toks and toks[0] in AMTSTITEL:   # "Herrn Bürgermeister Klein" -> "Klein"
        toks.pop(0)
    if not toks or toks[0].rstrip(",") in STOP:
        return None
    aus = []
    for t in toks:
        if t.rstrip(",") in STOP:
            break
        aus.append(t)
    while aus and aus[-1] in _PARTIKEL:
        aus.pop()
    s = " ".join(aus).strip(" ,")
    return s if len(s) >= 3 else None


class Schwaerzung:
    """Eine Instanz je Anfrage: gleicher Wert -> gleicher Platzhalter über alle Seiten und Dokumente."""

    def __init__(self, aktiv: bool = True):
        self.aktiv = aktiv
        self.zuordnung: dict[str, str] = {}   # platzhalter -> wert
        self._rueck: dict[str, str] = {}      # wert -> platzhalter
        self._zaehler: dict[str, int] = {}
        self._literale: dict[str, str] = {}   # gefundener Klartext -> typ (NAME, ADRESSE)
        self._literal_re: re.Pattern | None = None

    def _ph(self, typ: str, wert: str) -> str:
        if wert in self._rueck:
            return self._rueck[wert]
        n = self._zaehler.get(typ, 0) + 1
        self._zaehler[typ] = n
        ph = f"[{typ}_{n}]"
        self.zuordnung[ph] = wert
        self._rueck[wert] = ph
        return ph

    # ---------------------------------------------------------------- Erkennen (vor dem Ersetzen, über alle Seiten)
    def name_hinzu(self, name: str) -> None:
        """Vollen Namen und jeden Bestandteil (ab 3 Zeichen) für alle Seiten vormerken."""
        for teil in [name, *re.split(r"[ \t,]+", name)]:
            teil = teil.strip(" ,")
            if len(teil) >= 3 and teil not in _PARTIKEL and teil not in STOP and teil not in AMTSTITEL and teil[0].isupper():
                self._literale.setdefault(teil, "NAME")
        self._literal_re = None

    def adresse_hinzu(self, adresse: str) -> None:
        a = re.split(r",|\b\d{5}\b", adresse)[0].strip()   # PLZ und Ort bleiben stehen
        if len(a) >= 5:
            self._literale.setdefault(a, "ADRESSE")
            self._literal_re = None

    def lernen(self, texte: list[str]) -> None:
        if not self.aktiv:
            return
        for t in texte:
            for muster in NAMEN:
                for m in muster.finditer(t):
                    if RECHTSFORM.match(t, m.end(1)):   # "Fahrrad Huber GmbH" ist keine Person
                        continue
                    for teil in re.split(r",[ \t]*", m.group(1)):
                        n = _bereinigen(teil)
                        if n:
                            self.name_hinzu(n)

    def _literal_muster(self) -> re.Pattern | None:
        if self._literal_re is None and self._literale:
            alt = "|".join(re.escape(w) for w in sorted(self._literale, key=len, reverse=True))
            self._literal_re = re.compile(rf"(?<![\wäöüß])(?:{alt})(?![\wäöüß])")
        return self._literal_re

    # ---------------------------------------------------------------- Ersetzen
    def anwenden(self, text: str) -> str:
        if not self.aktiv:
            return text
        g = lambda typ: (lambda m: self._ph(typ, m.group(0)))  # noqa: E731
        text = EMAIL.sub(g("EMAIL"), text)
        text = IBAN.sub(lambda m: self._ph("IBAN", m.group(0)) if iban_gueltig(m.group(0)) else m.group(0), text)
        text = SVNR.sub(g("SVNR"), text)
        text = STEUERID.sub(lambda m: self._ph("STEUERID", m.group(0)) if steuerid_gueltig(m.group(0)) else m.group(0), text)
        text = _gruppe(AUSWEIS, text, lambda w: self._ph("AUSWEIS", w))
        text = _gruppe(GEBDATUM, text, lambda w: self._ph("GEBDATUM", w))
        text = ADRESSE.sub(g("ADRESSE"), text)
        lit = self._literal_muster()
        if lit:
            text = lit.sub(lambda m: self._ph(self._literale[m.group(0)], m.group(0)), text)
        text = TELEFON.sub(g("TELEFON"), text)
        return text

    def rueck(self, wert):
        """Platzhalter in Strings, Listen und dicts zurück auf Klartext abbilden."""
        if isinstance(wert, str):
            return PLATZHALTER.sub(lambda m: self.zuordnung.get(m.group(0), m.group(0)), wert)
        if isinstance(wert, list):
            return [self.rueck(v) for v in wert]
        if isinstance(wert, dict):
            return {k: self.rueck(v) for k, v in wert.items()}
        return wert

    @property
    def literale(self) -> dict[str, str]:
        """Alle erlernten Namen/Anschriften samt Bestandteilen (auch die, die beim Ersetzen nicht einzeln griffen)."""
        return dict(self._literale)

    def zaehlung(self) -> dict[str, int]:
        return dict(self._zaehler)


def _gruppe(muster: re.Pattern, text: str, ersetze) -> str:
    """Nur die erste Gruppe ersetzen, den Auslöser (z. B. 'Geburtsdatum:') stehen lassen."""
    def f(m):
        s, e = m.span(1)
        return m.group(0)[: s - m.start()] + ersetze(m.group(1)) + m.group(0)[e - m.start():]
    return muster.sub(f, text)
