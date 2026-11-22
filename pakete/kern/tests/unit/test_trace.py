import pytest
from opentelemetry import trace as ot_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from kern import trace


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    anbieter = TracerProvider()
    anbieter.add_span_processor(SimpleSpanProcessor(exp))
    ot_trace.set_tracer_provider(anbieter)
    return exp


def test_attribut_allowlist(exporter: InMemorySpanExporter) -> None:
    exporter.clear()
    with trace.span(
        "schritt",
        **{
            "plattform.paket": "frist",
            "gen_ai.request.model": "gemma",
            "gen_ai.usage.input_tokens": 12,
            "http.status_code": 200,
            "gen_ai.prompt": "Klartext",
            "text": "Klartext",
        },
    ):
        pass
    (span,) = exporter.get_finished_spans()
    assert dict(span.attributes) == {
        "plattform.paket": "frist",
        "gen_ai.request.model": "gemma",
        "gen_ai.usage.input_tokens": 12,
        "http.status_code": 200,
    }


def test_erlaubt() -> None:
    assert trace.erlaubt("plattform.x")
    assert trace.erlaubt("gen_ai.usage.output_tokens")
    assert not trace.erlaubt("gen_ai.completion")
    assert not trace.erlaubt("plattformx")


def test_einrichten_ohne_endpunkt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert trace.richte_ein("test", setzen=False).exportiert is False


def test_einrichten_mit_endpunkt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    einrichtung = trace.richte_ein("test", setzen=False)
    assert einrichtung.exportiert is True
    einrichtung.anbieter.shutdown()
