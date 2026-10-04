"""CloudWatch Embedded Metric Format (EMF): metrics written as one JSON log line each.

CloudWatch Logs extracts the metrics from these lines, with no PutMetricData calls and no metrics
SDK. AWS Lambda does that from stdout natively. Elsewhere the lines must reach CloudWatch Logs
flagged as EMF (`x-amzn-logs-format: json/emf`), e.g. via Fluent Bit (see infrastructure/README.md).

Stdlib only, no network: this module only formats lines and hands them to a logger.
Dimensions are restricted to an allowlist of low-cardinality, non-personal keys.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import Final, Literal

type Unit = Literal["Milliseconds", "Seconds", "Count", "Bytes", "None"]

NAMESPACE: Final = "Aegis"
METRICS_LOGGER: Final = "aegis.metrics"
# Low-cardinality keys only. Never a voicemail id, phone number, token, transcript or user identifier.
ALLOWED_DIMENSIONS: Final = frozenset({"Service", "Tool", "Status", "Stream", "Outcome", "Kind", "From", "To", "Model"})
MAX_DIMENSION_VALUE: Final = 128  # Bedrock model ids can be long

_logger = logging.getLogger(METRICS_LOGGER)


class MetricError(ValueError):
    pass


def emf_record(
    metrics: Mapping[str, tuple[float, Unit]],
    dimensions: Mapping[str, str],
    *,
    namespace: str = NAMESPACE,
    timestamp_ms: int | None = None,
) -> dict[str, object]:
    """Build one EMF document. Raises MetricError for disallowed or unsafe dimensions."""
    if not metrics:
        raise MetricError("at least one metric is required")
    for key, value in dimensions.items():
        if key not in ALLOWED_DIMENSIONS:
            raise MetricError(f"dimension {key!r} is not allowlisted")
        if not value or len(value) > MAX_DIMENSION_VALUE or not value.isprintable():
            raise MetricError(f"dimension {key!r} has an unsafe value")
    record: dict[str, object] = {
        "_aws": {
            "Timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [sorted(dimensions)],
                    "Metrics": [{"Name": name, "Unit": unit} for name, (_, unit) in metrics.items()],
                }
            ],
        },
        **dimensions,
    }
    for name, (value, _) in metrics.items():
        record[name] = value
    return record


def emit(metrics: Mapping[str, tuple[float, Unit]], **dimensions: str) -> None:
    """Log one EMF line on the `aegis.metrics` logger. Bad metric calls are dropped, never raised."""
    try:
        record = emf_record(metrics, {"Service": "aegis", **dimensions})
    except MetricError:
        logging.getLogger("aegis.telemetry").warning("dropped a metric with invalid dimensions", exc_info=True)
        return
    _logger.info(json.dumps(record, separators=(",", ":")))
