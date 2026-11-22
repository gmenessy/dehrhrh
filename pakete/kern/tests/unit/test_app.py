import pytest
from fastapi.testclient import TestClient

from kern.app import neue_app


def test_health_und_ready(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 200


def test_sicherheitsheader(client: TestClient) -> None:
    antwort = client.get("/health")
    assert "server" not in antwort.headers
    assert antwort.headers["x-content-type-options"] == "nosniff"


def test_ready_ueberschreibbar(metrik_token: str) -> None:
    async def nicht_bereit() -> bool:
        return False

    with TestClient(neue_app("x", "0.1.0", bereit=nicht_bereit)) as c:
        antwort = c.get("/ready")
    assert antwort.status_code == 503
    assert antwort.json()["status"] == "nicht_bereit"


@pytest.mark.parametrize(("umgebung", "docs"), [("prod", False), ("dev", True)])
def test_docs_nur_ausserhalb_prod(
    metrik_token: str, monkeypatch: pytest.MonkeyPatch, umgebung: str, docs: bool
) -> None:
    monkeypatch.setenv("UMGEBUNG", umgebung)
    with TestClient(neue_app("x", "0.1.0")) as c:
        assert (c.get("/docs").status_code == 200) is docs
        assert (c.get("/openapi.json").status_code == 200) is docs
