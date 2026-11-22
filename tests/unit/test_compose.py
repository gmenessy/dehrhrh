"""Compose-Dateien: Netze und internal-Werte nach Architektur 5.2."""

from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

ERWARTET_BASIS = {
    "rand": False,
    "idp": True,
    "intern": True,
    "parse": True,
    "modell": True,
    "aus_zusteller": False,
    "beobachtung": True,
}


def _lade(name: str) -> dict:
    return yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))


def _zusammen() -> dict[str, bool]:
    netze = {k: bool(v.get("internal", False)) for k, v in _lade("compose.yaml")["networks"].items()}
    for k, v in _lade("compose.dev.yaml").get("networks", {}).items():
        if "internal" in v:
            netze[k] = bool(v["internal"])
    return netze


def test_basis_hat_alle_sieben_netze_mit_internal() -> None:
    netze = _lade("compose.yaml")["networks"]
    assert set(netze) == set(ERWARTET_BASIS)
    for name, intern in ERWARTET_BASIS.items():
        assert netze[name]["internal"] is intern, name


def test_dev_schliesst_aus_zusteller() -> None:
    netze = _zusammen()
    assert netze["aus_zusteller"] is True
    for name, intern in ERWARTET_BASIS.items():
        if name != "aus_zusteller":
            assert netze[name] is intern, name


def test_dienste_sind_gehaertet() -> None:
    dienste = _lade("compose.yaml")["services"] | {
        k: v for k, v in _lade("compose.dev.yaml")["services"].items() if "image" in v
    }
    for name, dienst in dienste.items():
        assert dienst["read_only"] is True, name
        assert dienst["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in dienst["security_opt"], name
        assert dienst["user"] == "10001:10001", name


def test_host_ports_nur_lokal() -> None:
    for name, dienst in _lade("compose.dev.yaml")["services"].items():
        for port in dienst.get("ports", []):
            assert str(port).startswith("${DEV_BIND:-127.0.0.1}:"), name
