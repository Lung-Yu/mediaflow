"""OpenTelemetry initialisation for the pipeline (host-native process).

Call init() once at startup before any meters are acquired.
"""
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader


def init(endpoint: str = "localhost:4317") -> metrics.Meter:
    """Set the global OTel MeterProvider and return the pipeline meter.

    Idempotent — returns existing meter if provider already configured.
    """
    if isinstance(metrics.get_meter_provider(), MeterProvider):
        return metrics.get_meter("mediaflow.pipeline")

    exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=15_000)
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return metrics.get_meter("mediaflow.pipeline")
