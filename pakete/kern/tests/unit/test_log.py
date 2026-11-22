import json
import logging

import pytest

from kern import log
from kern.antwort import kontext_setzen
from kern.metrik import LOG_FELD_VERWORFEN


@pytest.fixture
def ausgabe(capsys: pytest.CaptureFixture[str]):
    logger = log.richte_ein("test")
    yield logger, capsys


def _zeile(capsys: pytest.CaptureFixture[str]) -> dict:
    zeilen = [z for z in capsys.readouterr().out.splitlines() if z.strip()]
    assert len(zeilen) == 1
    return json.loads(zeilen[0])


def test_inhaltsfelder_werden_entfernt(ausgabe) -> None:
    logger, capsys = ausgabe
    logger.info(
        "ereignis_test",
        extra={
            "text": "Max Mustermann",
            "prompt": "geheim",
            "inhalt": "x",
            "token": "eyJ",
            "status": "ok",
            "anzahl": 3,
        },
    )
    zeile = _zeile(capsys)
    for feld in ("text", "prompt", "inhalt", "token"):
        assert feld not in zeile
    roh = json.dumps(zeile)
    assert "Max Mustermann" not in roh and "geheim" not in roh and "eyJ" not in roh
    assert zeile["status"] == "ok"
    assert zeile["anzahl"] == 3
    assert zeile["ereignis"] == "ereignis_test"
    assert zeile["dienst"] == "test"
    assert zeile["stufe"] == "INFO"
    assert "zeit" in zeile


def test_verworfene_felder_werden_gezaehlt(ausgabe) -> None:
    logger, capsys = ausgabe
    vorher = LOG_FELD_VERWORFEN.labels(feld="prompt")._value.get()
    logger.info("x", extra={"prompt": "geheim"})
    capsys.readouterr()
    assert LOG_FELD_VERWORFEN.labels(feld="prompt")._value.get() == vorher + 1


def test_kontext_wird_uebernommen(ausgabe) -> None:
    logger, capsys = ausgabe
    with kontext_setzen(anfrage_id="01J0000000000000000000000A", mandant="musterhausen", lauf_id="l-1"):
        logger.info("mit_kontext")
    zeile = _zeile(capsys)
    assert zeile["anfrage_id"] == "01J0000000000000000000000A"
    assert zeile["mandant"] == "musterhausen"
    assert zeile["lauf_id"] == "l-1"


def test_ausnahmen_ohne_text(ausgabe) -> None:
    logger, capsys = ausgabe
    try:
        raise RuntimeError("Klartext im Fehler")
    except RuntimeError:
        logger.exception("fehlschlag")
    zeile = _zeile(capsys)
    assert "Klartext" not in json.dumps(zeile)
    assert zeile["fehler_code"] == "RuntimeError"


def test_nachricht_mit_argumenten_wird_nicht_formatiert(ausgabe) -> None:
    logger, capsys = ausgabe
    logger.info("ereignis %s", "Klartext")
    zeile = _zeile(capsys)
    assert "Klartext" not in json.dumps(zeile)
    assert zeile["ereignis"] == log.EREIGNIS_UNGUELTIG


def test_freitext_als_ereignis_wird_ersetzt(ausgabe) -> None:
    logger, capsys = ausgabe
    logger.info("Antrag von Max Mustermann eingegangen")
    assert _zeile(capsys)["ereignis"] == log.EREIGNIS_UNGUELTIG


def test_fremde_logger_laufen_durch_filter(ausgabe) -> None:
    _, capsys = ausgabe
    logging.getLogger("fremd").warning("x", extra={"prompt": "geheim"})
    assert "geheim" not in capsys.readouterr().out
