"""Tracing mit OpenTelemetry; Span-Attribute nur aus der Allowlist, nie Inhalte."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from opentelemetry import trace as ot_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

_ERLAUBT_PRAEFIX = ("plattform.", "gen_ai.usage.", "http.")
_ERLAUBT_GENAU = frozenset({"gen_ai.request.model"})

type Attributwert = str | bool | int | float


@dataclass(frozen=True)
class Einrichtung:
    anbieter: TracerProvider
    exportiert: bool


def erlaubt(name: str) -> bool:
    return name in _ERLAUBT_GENAU or name.startswith(_ERLAUBT_PRAEFIX)


def richte_ein(dienst: str, *, setzen: bool = True) -> Einrichtung:
    """Export ueber OTLP/HTTP nur, wenn `OTEL_EXPORTER_OTLP_ENDPOINT` gesetzt ist."""
    anbieter = TracerProvider(resource=Resource.create({"service.name": dienst}))
    exportiert = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    if exportiert:
        anbieter.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if setzen:
        ot_trace.set_tracer_provider(anbieter)
    return Einrichtung(anbieter, exportiert)


@contextmanager
def span(name: str, **attribute: Attributwert) -> Iterator[Span]:
    gefiltert = {k: v for k, v in attribute.items() if erlaubt(k)}
    with ot_trace.get_tracer("kern").start_as_current_span(name, attributes=gefiltert) as aktuell:
        yield aktuell
