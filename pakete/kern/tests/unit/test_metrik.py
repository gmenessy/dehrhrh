from fastapi.testclient import TestClient

from kern.app import neue_app


def test_metrics_ohne_token(client: TestClient) -> None:
    assert client.get("/metrics").status_code == 401


def test_metrics_falsches_token(client: TestClient) -> None:
    assert client.get("/metrics", headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Basic abc"}).status_code == 401


def test_metrics_richtiges_token(client: TestClient, metrik_token: str) -> None:
    antwort = client.get("/metrics", headers={"Authorization": f"Bearer {metrik_token}"})
    assert antwort.status_code == 200
    assert "plattform_anfragen_total" in antwort.text


def test_route_als_muster(client: TestClient, metrik_token: str) -> None:
    client.get("/teile/12345")
    client.get("/gibt/es/nicht/987")
    antwort = client.get("/metrics", headers={"Authorization": f"Bearer {metrik_token}"}).text
    # nur Zeilen mit Routen-Label; Messwerte selbst koennen beliebige Ziffern enthalten
    text = "\n".join(z.rsplit("}", 1)[0] for z in antwort.splitlines() if "route=" in z)
    assert 'route="/teile/{nr}"' in text
    assert "12345" not in text
    assert "987" not in text
    assert 'route="unbekannt"' in text
    assert 'dienst="testdienst"' in text


def test_ohne_tokendatei_gesperrt(app, monkeypatch) -> None:
    monkeypatch.delenv("METRIK_TOKEN_DATEI")
    with TestClient(neue_app("ohne", "0.1.0")) as c:
        assert c.get("/metrics", headers={"Authorization": "Bearer "}).status_code == 401
