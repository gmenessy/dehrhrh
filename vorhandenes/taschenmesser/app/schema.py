"""Schema-getriebene Extraktion: Teilmenge von JSON Schema, deterministische Prüfung der Werte (Stufe 0)."""
from __future__ import annotations

import re
from datetime import date

from .schwaerzen import EMAIL, iban_gueltig

TYPEN = {"string", "number", "integer", "boolean"}
FORMATE = {"date", "iban", "email", "plz", "betrag"}
MAX_FELDER = 40


class SchemaFehler(ValueError):
    pass


def pruefe_schema(schema: dict) -> dict[str, dict]:
    """Gibt die Felder zurück; erlaubt sind flache Objekte mit einfachen Typen."""
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise SchemaFehler("Schema muss ein Objekt mit type=object sein")
    felder = schema.get("properties")
    if not isinstance(felder, dict) or not felder:
        raise SchemaFehler("properties fehlt oder ist leer")
    if len(felder) > MAX_FELDER:
        raise SchemaFehler(f"höchstens {MAX_FELDER} Felder")
    for name, f in felder.items():
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", name):
            raise SchemaFehler(f"Feldname ungültig: {name}")
        if f.get("type", "string") not in TYPEN:
            raise SchemaFehler(f"{name}: type muss einer von {sorted(TYPEN)} sein")
        if "format" in f and f["format"] not in FORMATE:
            raise SchemaFehler(f"{name}: format muss einer von {sorted(FORMATE)} sein")
        if "pattern" in f:
            try:
                re.compile(f["pattern"])
            except re.error as e:
                raise SchemaFehler(f"{name}: pattern ungültig") from e
    for r in schema.get("required", []):
        if r not in felder:
            raise SchemaFehler(f"required nennt unbekanntes Feld {r}")
    return felder


def modell_schema(felder: dict[str, dict]) -> dict:
    """Schema für guided_json: je Feld Wert, Seite und wörtliches Zitat."""
    props = {}
    for name, f in felder.items():
        typ = "string" if f.get("format") in ("date", "betrag") else f.get("type", "string")
        wert: dict = {"type": [typ, "null"]}
        if "enum" in f:
            wert = {"enum": list(f["enum"]) + [None]}
        props[name] = {
            "type": "object",
            "properties": {"wert": wert, "seite": {"type": ["integer", "null"]}, "zitat": {"type": ["string", "null"], "maxLength": 300}},
            "required": ["wert", "seite", "zitat"],
        }
    return {"type": "object", "properties": props, "required": list(props)}


def _zahl(v) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip().replace("€", "").replace("EUR", "").replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})*(,\d+)?|-?\d+(,\d+)?", s):  # deutsch: 1.234,56
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _datum(v) -> str:
    s = str(v).strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})", s)
    if m:
        t, mo, j = int(m[1]), int(m[2]), int(m[3])
        j = j + 2000 if j < 100 and j <= date.today().year % 100 + 1 else (j + 1900 if j < 100 else j)
        return date(j, mo, t).isoformat()
    return date.fromisoformat(s).isoformat()


def normalisiere(wert, f: dict):
    """Stufe 0: Typ und Format prüfen. Gibt (wert, fehler) zurück."""
    if wert is None:
        return None, None
    typ, fmt = f.get("type", "string"), f.get("format")
    try:
        if fmt == "date":
            wert = _datum(wert)
        elif fmt == "betrag":
            wert = round(_zahl(wert), 2)
        elif typ == "number":
            wert = _zahl(wert)
        elif typ == "integer":
            z = _zahl(wert)
            if z != int(z):
                return wert, "keine ganze Zahl"
            wert = int(z)
        elif typ == "boolean":
            if not isinstance(wert, bool):
                s = str(wert).strip().casefold()
                if s not in ("ja", "nein", "true", "false"):
                    return wert, "kein Wahrheitswert"
                wert = s in ("ja", "true")
        else:
            wert = str(wert).strip()
    except (ValueError, TypeError):
        return wert, f"Format {fmt or typ} ungültig"
    if fmt == "iban":
        wert = re.sub(r"\s", "", str(wert)).upper()
        if not iban_gueltig(wert):
            return wert, "IBAN-Prüfziffer falsch"
    if fmt == "email" and not EMAIL.fullmatch(str(wert)):
        return wert, "E-Mail ungültig"
    if fmt == "plz" and not re.fullmatch(r"\d{5}", str(wert)):
        return wert, "PLZ muss fünfstellig sein"
    if "enum" in f and wert not in f["enum"]:
        return wert, "Wert nicht in enum"
    if "pattern" in f and not re.fullmatch(f["pattern"], str(wert)):
        return wert, "Wert passt nicht zu pattern"
    for grenze, op in (("minimum", lambda a, b: a >= b), ("maximum", lambda a, b: a <= b)):
        if grenze in f and isinstance(wert, (int, float)) and not op(wert, f[grenze]):
            return wert, f"{grenze} {f[grenze]} verletzt"
    return wert, None
