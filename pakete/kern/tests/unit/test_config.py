from dataclasses import dataclass, field
from pathlib import Path

import pytest

from kern.config import lade


@dataclass(frozen=True)
class Beispiel:
    pflicht_wert: str
    zahl: int = 5
    anteil: float = 0.5
    aktiv: bool = False
    pfad: Path = Path("/tmp")
    liste: list[str] = field(default_factory=list)
    geheimnis_datei: str | None = None


def test_fehlende_pflicht_nennt_namen_ohne_wert(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("PFLICHT_WERT", raising=False)
    monkeypatch.setenv("ZAHL", "keine-zahl-geheim")
    with pytest.raises(SystemExit) as info:
        lade(Beispiel)
    meldung = str(info.value)
    assert "PFLICHT_WERT" in meldung
    assert "ZAHL" in meldung
    assert "keine-zahl-geheim" not in meldung
    assert "keine-zahl-geheim" not in capsys.readouterr().err


def test_typen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    geheim = tmp_path / "g"
    geheim.write_text("s3cr3t\n", encoding="utf-8")
    monkeypatch.setenv("PFLICHT_WERT", "a")
    monkeypatch.setenv("ZAHL", "7")
    monkeypatch.setenv("ANTEIL", "0.25")
    monkeypatch.setenv("AKTIV", "true")
    monkeypatch.setenv("PFAD", "/srv")
    monkeypatch.setenv("LISTE", "a, b ,,c")
    monkeypatch.setenv("GEHEIMNIS_DATEI", str(geheim))
    wert = lade(Beispiel)
    assert wert == Beispiel("a", 7, 0.25, True, Path("/srv"), ["a", "b", "c"], "s3cr3t")


def test_standardwerte(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ZAHL", "ANTEIL", "AKTIV", "PFAD", "LISTE", "GEHEIMNIS_DATEI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PFLICHT_WERT", "a")
    assert lade(Beispiel) == Beispiel("a")


@pytest.mark.parametrize("roh", ["ja", "1x", ""])
def test_bool_streng(monkeypatch: pytest.MonkeyPatch, roh: str) -> None:
    monkeypatch.setenv("PFLICHT_WERT", "a")
    monkeypatch.setenv("AKTIV", roh)
    with pytest.raises(SystemExit) as info:
        lade(Beispiel)
    assert "AKTIV" in str(info.value)


def test_fehlende_geheimnisdatei(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PFLICHT_WERT", "a")
    monkeypatch.setenv("GEHEIMNIS_DATEI", str(tmp_path / "fehlt"))
    with pytest.raises(SystemExit) as info:
        lade(Beispiel)
    assert "GEHEIMNIS_DATEI" in str(info.value)


def test_nur_frozen_dataclass() -> None:
    @dataclass
    class Offen:
        x: str = "a"

    with pytest.raises(TypeError):
        lade(Offen)
