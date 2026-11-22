from fastapi.testclient import TestClient

from kern.antwort import Antwort, Meta, anfrage_id, ist_ulid, neue_ulid

GUELTIG = "01J9ZQ3V8W6K2N4P5R7S8T9V0X"


def test_gueltige_anfrage_id_kommt_zurueck(client: TestClient) -> None:
    antwort = client.get("/health", headers={"X-Anfrage-Id": GUELTIG})
    assert antwort.headers["x-anfrage-id"] == GUELTIG


def test_ungueltige_anfrage_id_wird_ersetzt(client: TestClient) -> None:
    for ungueltig in ("abc", "x" * 26, "01J9ZQ3V8W6K2N4P5R7S8T9V0" + "U", "<script>", GUELTIG + "0"):
        antwort = client.get("/health", headers={"X-Anfrage-Id": ungueltig})
        neu = antwort.headers["x-anfrage-id"]
        assert neu != ungueltig
        assert ist_ulid(neu)


def test_ohne_anfrage_id_neu(client: TestClient) -> None:
    assert ist_ulid(client.get("/health").headers["x-anfrage-id"])


def test_ulid_format_und_ordnung() -> None:
    werte = [neue_ulid() for _ in range(50)]
    assert all(ist_ulid(w) for w in werte)
    assert len(set(werte)) == 50
    assert ist_ulid(GUELTIG.lower()) is False
    assert ist_ulid("81J9ZQ3V8W6K2N4P5R7S8T9V0X") is False  # Ueberlauf 128 Bit


def test_antwort_generisch() -> None:
    meta = Meta(anfrage_id=GUELTIG, paket="frist", stufen={"0": 3})
    antwort = Antwort[dict[str, int]](ergebnis={"x": 1}, meta=meta)
    daten = antwort.model_dump(exclude_none=True)
    assert daten == {
        "ergebnis": {"x": 1},
        "fundstellen": [],
        "meta": {"anfrage_id": GUELTIG, "paket": "frist", "stufen": {"0": 3}},
    }


def test_anfrage_id_ausserhalb_leer() -> None:
    assert anfrage_id() is None
