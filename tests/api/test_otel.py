"""Verify OTel init does not raise when collector is unreachable."""
import os
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:19999")

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry import metrics as otel_metrics


def test_init_otel_does_not_raise():
    from api.main import _init_otel
    _init_otel()
    assert isinstance(otel_metrics.get_meter_provider(), MeterProvider)
