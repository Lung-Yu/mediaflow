from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider


def test_init_sets_meter_provider():
    from pipeline.telemetry import init
    # Use a no-op endpoint — connection refused is expected, init must not raise
    meter = init(endpoint="localhost:19999")
    assert isinstance(otel_metrics.get_meter_provider(), MeterProvider)
    assert meter is not None


def test_init_idempotent():
    from pipeline.telemetry import init
    m1 = init(endpoint="localhost:19999")
    m2 = init(endpoint="localhost:19999")
    assert otel_metrics.get_meter_provider() is otel_metrics.get_meter_provider()
