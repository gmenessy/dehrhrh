"""Infrastruktur nach make up: Datenbanken und Buckets (Architektur 8.1)."""

import os
from pathlib import Path

import boto3
import psycopg
import pytest

pytestmark = pytest.mark.integration

SCHLUESSEL = Path(__file__).resolve().parents[2] / "config" / "schluessel"
# Host der veroeffentlichten Dev-Ports (in CI mit Docker-in-Docker: "docker")
HOST = os.environ.get("PLATTFORM_TEST_HOST", "127.0.0.1")
BUCKETS = {"laufdaten", "ergebnisse", "wissen-quellen", "backups"}


def _geheimnis(name: str) -> str:
    return (SCHLUESSEL / name).read_text(encoding="utf-8").strip()


@pytest.fixture(scope="module")
def s3():
    return boto3.client(
        "s3",
        endpoint_url=f"http://{HOST}:9000",
        aws_access_key_id=_geheimnis("minio-benutzer.dev"),
        aws_secret_access_key=_geheimnis("minio-passwort.dev"),
        region_name="us-east-1",
    )


def test_datenbanken_existieren() -> None:
    with psycopg.connect(
        host=HOST, port=5432, user="postgres", password=_geheimnis("postgres.dev"), dbname="postgres"
    ) as verbindung:
        namen = {zeile[0] for zeile in verbindung.execute("SELECT datname FROM pg_database").fetchall()}
    assert {"plattform", "rag"} <= namen


def test_buckets_existieren(s3) -> None:
    namen = {b["Name"] for b in s3.list_buckets()["Buckets"]}
    assert BUCKETS <= namen


def test_laufdaten_lifecycle_24h(s3) -> None:
    regeln = s3.get_bucket_lifecycle_configuration(Bucket="laufdaten")["Rules"]
    assert any(r["Status"] == "Enabled" and r["Expiration"]["Days"] == 1 for r in regeln)


def test_ergebnisse_lifecycle_7_tage(s3) -> None:
    regeln = s3.get_bucket_lifecycle_configuration(Bucket="ergebnisse")["Rules"]
    assert any(r["Status"] == "Enabled" and r["Expiration"]["Days"] == 7 for r in regeln)


def test_wissen_quellen_versioniert(s3) -> None:
    assert s3.get_bucket_versioning(Bucket="wissen-quellen").get("Status") == "Enabled"
