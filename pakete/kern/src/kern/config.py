"""Konfiguration aus der Umgebung, fail-closed beim Start."""

import dataclasses
import os
import types
import typing
from pathlib import Path
from typing import Any

_WAHR = {"true", "1"}
_FALSCH = {"false", "0"}
DATEI_ENDUNG = "_datei"


class _Ungueltig(Exception):
    """Wert passt nicht zum Typ; traegt bewusst keinen Wert."""


def _basistyp(typ: Any) -> tuple[Any, bool]:
    """Liefert (Typ ohne None, optional)."""
    if typing.get_origin(typ) in (typing.Union, types.UnionType):
        argumente = [a for a in typing.get_args(typ) if a is not type(None)]
        if len(argumente) == 1:
            return argumente[0], True
    return typ, False


def _wandle(roh: str, typ: Any) -> Any:
    if typ is str:
        return roh
    if typ is bool:
        klein = roh.strip().lower()
        if klein in _WAHR:
            return True
        if klein in _FALSCH:
            return False
        raise _Ungueltig
    if typ in (int, float):
        try:
            return typ(roh)
        except ValueError:
            raise _Ungueltig from None
    if typ is Path:
        return Path(roh)
    if typing.get_origin(typ) is list and typing.get_args(typ) == (str,):
        return [teil.strip() for teil in roh.split(",") if teil.strip()]
    raise TypeError(f"Nicht unterstuetzter Konfigurationstyp: {typ!r}")


def _lies_datei(pfad: str) -> str:
    try:
        return Path(pfad).read_text(encoding="utf-8").strip()
    except OSError:
        raise _Ungueltig from None


def lade[T](cls: type[T]) -> T:
    """Liest eine frozen Dataclass aus Umgebungsvariablen (Feldname in Grossbuchstaben).

    Felder mit Endung `_datei` erhalten den Inhalt der genannten Datei. Fehlende oder ungueltige
    Werte beenden den Prozess; die Meldung nennt nur Variablennamen, nie Werte.
    """
    if not dataclasses.is_dataclass(cls) or not cls.__dataclass_params__.frozen:  # type: ignore[attr-defined]
        raise TypeError("lade() erwartet eine @dataclass(frozen=True)")
    hinweise = typing.get_type_hints(cls)
    werte: dict[str, Any] = {}
    fehlend: list[str] = []
    ungueltig: list[str] = []
    for feld in dataclasses.fields(cls):
        name = feld.name.upper()
        typ, _ = _basistyp(hinweise[feld.name])
        roh = os.environ.get(name)
        if roh is None:
            hat_standard = feld.default is not dataclasses.MISSING or feld.default_factory is not dataclasses.MISSING
            if not hat_standard:
                fehlend.append(name)
            continue
        try:
            werte[feld.name] = _wandle(_lies_datei(roh) if feld.name.endswith(DATEI_ENDUNG) else roh, typ)
        except _Ungueltig:
            ungueltig.append(name)
    if fehlend or ungueltig:
        teile = []
        if fehlend:
            teile.append("fehlend: " + ", ".join(fehlend))
        if ungueltig:
            teile.append("ungueltig: " + ", ".join(ungueltig))
        raise SystemExit(f"Konfiguration {cls.__name__} unvollstaendig ({'; '.join(teile)})")
    return cls(**werte)
