import datetime
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from kern import server


def test_dev_ohne_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMGEBUNG", "dev")
    granian = server.baue("kern.beispiel:app", 8000)
    assert granian.bind_port == 8000
    assert granian.ssl_ctx[0] is False


def test_prod_ohne_tls_bricht_ab(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMGEBUNG", "prod")
    with pytest.raises(SystemExit):
        server.baue("kern.beispiel:app", 8000)


def test_unbekannte_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMGEBUNG", "irgendwas")
    with pytest.raises(SystemExit):
        server.baue("kern.beispiel:app", 8000)


def _selbstsigniert(ordner: Path) -> tuple[Path, Path]:
    schluessel = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    jetzt = datetime.datetime.now(datetime.UTC)
    zert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(schluessel.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(jetzt)
        .not_valid_after(jetzt + datetime.timedelta(days=1))
        .sign(schluessel, hashes.SHA256())
    )
    zert_pfad, schluessel_pfad = ordner / "z.pem", ordner / "s.pem"
    zert_pfad.write_bytes(zert.public_bytes(serialization.Encoding.PEM))
    schluessel_pfad.write_bytes(
        schluessel.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return zert_pfad, schluessel_pfad


def test_mit_tls_und_client_pruefung(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UMGEBUNG", "prod")
    zert, schluessel = _selbstsigniert(tmp_path)
    granian = server.baue(
        "kern.beispiel:app", 8443, tls=server.TlsEinstellung(zert=zert, schluessel=schluessel, ca=zert)
    )
    aktiv, *_, client_pruefung = granian.ssl_ctx
    assert aktiv is True
    assert client_pruefung is True
