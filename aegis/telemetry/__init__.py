"""CloudWatch Embedded Metric Format output. Stdlib only, no network."""

from aegis.telemetry.emf import ALLOWED_DIMENSIONS, METRICS_LOGGER, MetricError, emf_record, emit

__all__ = ["ALLOWED_DIMENSIONS", "METRICS_LOGGER", "MetricError", "emf_record", "emit"]
