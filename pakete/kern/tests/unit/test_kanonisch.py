import hashlib
import json
import math
from pathlib import Path

import pytest

from kern.kanonisch import kanonisch, sha256_hex, sha256_praefix

VEKTOREN = json.loads((Path(__file__).parents[1] / "vektoren" / "kanonisch.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vektor", VEKTOREN, ids=[v["name"] for v in VEKTOREN])
def test_vektoren(vektor: dict) -> None:
    assert kanonisch(vektor["eingabe"]) == vektor["erwartet"].encode("utf-8")


def test_reihenfolge_egal() -> None:
    a = {"x": 1, "y": {"b": "ü", "a": [1, 2]}}
    b = {"y": {"a": [1, 2], "b": "ü"}, "x": 1}
    assert kanonisch(a) == kanonisch(b)


@pytest.mark.parametrize("wert", [math.nan, math.inf, -math.inf])
def test_nicht_endliche_floats(wert: float) -> None:
    with pytest.raises(ValueError):
        kanonisch({"x": wert})


def test_hashes() -> None:
    erwartet = hashlib.sha256(b'{"a":1}').hexdigest()
    assert sha256_hex(kanonisch({"a": 1})) == erwartet
    assert sha256_praefix(b'{"a":1}') == f"sha256:{erwartet}"
