"""Weitere Eingangsformate ohne schwere Abhängigkeiten: E-Mail (.eml, Outlook .msg), DOCX, ODT, XLSX.

Office-Formate sind ZIP-Container mit XML; gelesen wird nur der Text. E-Mails liefern Kopf + Text als erstes Dokument
und jeden Anhang als eigenes Dokument (rekursiv, begrenzt).
"""
from __future__ import annotations

import email
import email.policy
import io
import re
import zipfile
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree as ET

MAX_ENTPACKT = 60 * 1024 * 1024   # Schutz gegen ZIP-Bomben
MAX_ZEILEN = 3000                 # je Tabellenblatt


class FormatFehler(Exception):
    pass


def _lokal(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _zip_lesen(z: zipfile.ZipFile, name: str) -> bytes:
    info = z.getinfo(name)
    if info.file_size > MAX_ENTPACKT:
        raise FormatFehler("Datei entpackt zu groß")
    return z.read(name)


def zip_art(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            namen = set(z.namelist())
            if sum(i.file_size for i in z.infolist()) > MAX_ENTPACKT * 3:
                raise FormatFehler("Archiv entpackt zu groß")
    except zipfile.BadZipFile:
        return None
    if "word/document.xml" in namen:
        return "docx"
    if "xl/workbook.xml" in namen:
        return "xlsx"
    if "content.xml" in namen and "mimetype" in namen:
        return "odt"
    return None


# ---------------------------------------------------------------- DOCX
def docx_seiten(data: bytes) -> list[str]:
    """Absätze und Tabellen in Lesereihenfolge; Seiten an Seitenumbrüchen (auch Words zuletzt gerenderte)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(_zip_lesen(z, "word/document.xml"))
    seiten, akt = [], []

    def absatz(p) -> str:
        nonlocal akt
        teile = []
        for el in p.iter():
            t = _lokal(el.tag)
            if t == "t" and el.text:
                teile.append(el.text)
            elif t == "tab":
                teile.append("\t")
            elif t in ("br", "cr"):
                if any(v == "page" for k, v in el.attrib.items() if _lokal(k) == "type"):
                    akt.append("".join(teile))
                    seiten.append("\n".join(akt))
                    akt, teile = [], []
                else:
                    teile.append("\n")
            elif t == "lastRenderedPageBreak" and (teile or akt):
                akt.append("".join(teile))
                seiten.append("\n".join(akt))
                akt, teile = [], []
        return "".join(teile)

    body = next(el for el in root if _lokal(el.tag) == "body")
    for el in body:
        t = _lokal(el.tag)
        if t == "p":
            if any(_lokal(x.tag) == "pageBreakBefore" for x in el.iter()) and any(s.strip() for s in akt):
                seiten.append("\n".join(akt))
                akt = []
            zeile = absatz(el)  # erst aufrufen: ein Seitenumbruch im Absatz ersetzt 'akt'
            akt.append(zeile)
        elif t == "tbl":
            for tr in (x for x in el.iter() if _lokal(x.tag) == "tr"):
                zellen = [" ".join(absatz(p) for p in tc.iter() if _lokal(p.tag) == "p").strip()
                          for tc in tr if _lokal(tc.tag) == "tc"]
                zeile = " | ".join(zellen)
                akt.append(zeile)
    seiten.append("\n".join(akt))
    return [s for s in seiten if s.strip()] or [""]


# ---------------------------------------------------------------- ODT
def odt_seiten(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(_zip_lesen(z, "content.xml"))
    zeilen = []

    def text(el) -> str:
        out = [el.text or ""]
        for c in el:
            t = _lokal(c.tag)
            out.append("\t" if t == "tab" else "\n" if t == "line-break" else " " * int(
                next((v for k, v in c.attrib.items() if _lokal(k) == "c"), 1)) if t == "s" else text(c))
            out.append(c.tail or "")
        return "".join(out)

    for el in root.iter():
        t = _lokal(el.tag)
        if t in ("p", "h"):
            zeilen.append(text(el))
    return ["\n".join(zeilen)]


# ---------------------------------------------------------------- XLSX
def _spalte(ref: str) -> int:
    n = 0
    for c in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(c) - 64
    return n - 1


def xlsx_seiten(data: bytes) -> list[str]:
    """Je Tabellenblatt eine Seite, Zeilen als 'A | B | C'. Datumszahlen bleiben Excel-Seriennummern."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        namen = z.namelist()
        geteilt = []
        if "xl/sharedStrings.xml" in namen:
            for si in ET.fromstring(_zip_lesen(z, "xl/sharedStrings.xml")):
                geteilt.append("".join(t.text or "" for t in si.iter() if _lokal(t.tag) == "t"))
        wb = ET.fromstring(_zip_lesen(z, "xl/workbook.xml"))
        blattnamen = [s.get("name") for s in wb.iter() if _lokal(s.tag) == "sheet"]
        dateien = sorted((n for n in namen if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                         key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group(0)))
        seiten = []
        for i, n in enumerate(dateien):
            root = ET.fromstring(_zip_lesen(z, n))
            zeilen = [f"Tabellenblatt: {blattnamen[i] if i < len(blattnamen) else i + 1}"]
            for row in (r for r in root.iter() if _lokal(r.tag) == "row"):
                werte: dict[int, str] = {}
                for c in row:
                    if _lokal(c.tag) != "c":
                        continue
                    typ, v = c.get("t"), None
                    for x in c:
                        if _lokal(x.tag) == "v":
                            v = x.text
                        elif _lokal(x.tag) == "is":
                            v = "".join(t.text or "" for t in x.iter() if _lokal(t.tag) == "t")
                    if v is None:
                        continue
                    if typ == "s":
                        v = geteilt[int(v)]
                    werte[_spalte(c.get("r", "A1"))] = v
                if werte:
                    zeilen.append(" | ".join(werte.get(j, "") for j in range(max(werte) + 1)))
                if len(zeilen) > MAX_ZEILEN:
                    zeilen.append(f"[… gekürzt nach {MAX_ZEILEN} Zeilen]")
                    break
            seiten.append("\n".join(zeilen))
    return seiten or [""]


# ---------------------------------------------------------------- E-Mail
def ist_eml(data: bytes) -> bool:
    kopf = data[:4096].decode("utf-8", errors="replace")
    return bool(re.match(r"(?:[A-Za-z][\w-]*:[^\n]*\r?\n(?:[ \t][^\n]*\r?\n)*){2,}", kopf)) and \
        bool(re.search(r"^(?:From|Subject|Date|Message-ID|Received|MIME-Version):", kopf, re.M | re.I))


def _html_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", h)
    return unescape(re.sub(r"<[^>]+>", "", h))


def _kopf_text(kopf: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in [("Von", kopf.get("von")), ("An", kopf.get("an")), ("Datum", kopf.get("datum_roh")),
                                                ("Betreff", kopf.get("betreff"))] if v)


def eml_lesen(data: bytes) -> tuple[dict, str, list[tuple[str, bytes]]]:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    kopf = {"von": str(msg.get("From", "") or ""), "an": str(msg.get("To", "") or ""),
            "betreff": str(msg.get("Subject", "") or ""), "datum_roh": str(msg.get("Date", "") or "")}
    try:
        kopf["datum"] = parsedate_to_datetime(kopf["datum_roh"]).date().isoformat() if kopf["datum_roh"] else None
    except (TypeError, ValueError):
        kopf["datum"] = None
    koerper = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if koerper is not None:
        text = koerper.get_content()
        if koerper.get_content_type() == "text/html":
            text = _html_text(text)
    anhaenge = []
    for teil in msg.iter_attachments():
        name = teil.get_filename() or "anhang"
        if teil.get_content_type() == "message/rfc822":
            inner = teil.get_payload(0) if teil.is_multipart() else teil.get_content()
            anhaenge.append((name if name.endswith(".eml") else name + ".eml", bytes(inner)))
        else:
            nutz = teil.get_payload(decode=True)
            if nutz:
                anhaenge.append((name, nutz))
    return kopf, _kopf_text(kopf) + "\n\n" + text.strip(), anhaenge


def msg_lesen(data: bytes) -> tuple[dict, str, list[tuple[str, bytes]]]:
    """Outlook .msg (OLE-Container); Eigenschaften als Unicode-Streams (…001F) oder Binär (…0102)."""
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        def lese(pfad: str) -> str | None:
            for endung, kod in (("001F", "utf-16-le"), ("001E", "cp1252")):
                name = f"{pfad}{endung}"
                if ole.exists(name):
                    return ole.openstream(name).read().decode(kod, errors="replace").rstrip("\x00")
            return None

        s = "__substg1.0_"
        kopf = {"betreff": lese(s + "0037"), "von": " ".join(x for x in [lese(s + "0C1A"), f"<{lese(s + '5D01') or lese(s + '0C1F') or ''}>"] if x and x != "<>"),
                "an": lese(s + "0E04"), "datum_roh": None, "datum": None}
        kopfzeilen = lese(s + "007D") or ""
        m = re.search(r"^Date:\s*(.+)$", kopfzeilen, re.M)
        if m:
            kopf["datum_roh"] = m.group(1).strip()
            try:
                kopf["datum"] = parsedate_to_datetime(kopf["datum_roh"]).date().isoformat()
            except (TypeError, ValueError):
                pass
        text = lese(s + "1000") or ""
        if not text.strip():
            html = None
            if ole.exists(s + "10130102"):
                html = ole.openstream(s + "10130102").read().decode("utf-8", errors="replace")
            text = _html_text(html) if html else ""
        anhaenge = []
        for eintrag in ole.listdir(streams=False, storages=True):
            if len(eintrag) == 1 and eintrag[0].startswith("__attach_version1.0_"):
                basis = eintrag[0] + "/" + s
                name = lese(basis + "3707") or lese(basis + "3704") or "anhang"
                if ole.exists(basis + "37010102"):
                    anhaenge.append((name, ole.openstream(basis + "37010102").read()))
        return kopf, _kopf_text(kopf) + "\n\n" + text.strip(), anhaenge
    finally:
        ole.close()
